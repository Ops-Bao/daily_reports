// Cloudflare Worker — the clock and the doorbell for both pipelines.
//
// Why this exists at all: GitHub's own `schedule` event is low priority. In
// September 2026 the 05:00 UTC cron was observed starting between 11:30 and
// 13:20 Paris, days in a row. A run started through the API begins within
// seconds, so the timing lives here and GitHub only does the work.
//
// Two entry points:
//
//   scheduled()  05:45 Paris — start the digest and the PDF collect.
//                Also a slow route tick as a safety net (see below).
//
//   fetch()      Slack Events API. The moment the reviewer posts a threaded
//                reply in her DM, Slack calls this and we start `route`
//                immediately instead of waiting for the next tick.
//
// Everything downstream is idempotent (the digest looks for today's header in
// Slack, collect skips permalinks already in her DM, route claims each reply
// with a ✅ reaction), so firing twice is harmless and firing late still works.

const enc = new TextEncoder();

// --- GitHub -----------------------------------------------------------------

/** Start a workflow via repository_dispatch. Needs a fine-grained PAT with
 *  "Contents: read and write" on this repo (NOT "Actions" — that permission is
 *  for the other dispatch endpoint). repository_dispatch always runs main. */
async function dispatch(env, eventType, payload = {}) {
  const res = await fetch(`https://api.github.com/repos/${env.REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "bao-dispatch",
    },
    body: JSON.stringify({ event_type: eventType, client_payload: payload }),
  });
  if (res.status !== 204) {
    throw new Error(`${eventType}: HTTP ${res.status} ${await res.text()}`);
  }
  console.log(`dispatched ${eventType} ${JSON.stringify(payload)}`);
}

// --- Slack request signing ---------------------------------------------------

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Verify Slack's v0 signature over the RAW body.
 *
 *  Without this the Worker is an open endpoint: anyone who learns the URL could
 *  make it start workflows. The 5-minute timestamp window also stops a captured
 *  request from being replayed later. */
async function verifySlack(request, rawBody, signingSecret) {
  const ts = request.headers.get("x-slack-request-timestamp");
  const sig = request.headers.get("x-slack-signature");
  if (!ts || !sig) return false;
  if (Math.abs(Date.now() / 1000 - Number(ts)) > 300) return false;

  const key = await crypto.subtle.importKey(
    "raw", enc.encode(signingSecret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, enc.encode(`v0:${ts}:${rawBody}`));
  const expected = "v0=" + [...new Uint8Array(mac)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  return timingSafeEqual(expected, sig);
}

// --- Which Slack events are worth a run --------------------------------------

// Edits and deletions are not new comments; forwarding them would post her
// text a second time. Everything else is let through: mirror_pdfs.route does
// the authoritative filtering in Python, so a needless run is cheap while a
// missed event loses a comment.
const IGNORED_SUBTYPES = new Set(["message_changed", "message_deleted"]);

function isReviewerReply(e, reviewerId) {
  return !!e
    && e.type === "message"
    && e.channel_type === "im"
    && e.user === reviewerId
    && !e.bot_id
    && !IGNORED_SUBTYPES.has(e.subtype)
    // A threaded reply, not a new top-level DM: the parent carries the
    // permalink that tells route where the comment belongs.
    && !!e.thread_ts && e.thread_ts !== e.ts;
}

// --- Entry points -------------------------------------------------------------

const parisHour = () =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Paris", hour: "2-digit", hour12: false,
  }).format(new Date());

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("bao-dispatch", { status: 200 });

    const raw = await request.text();
    if (!await verifySlack(request, raw, env.SLACK_SIGNING_SECRET)) {
      return new Response("bad signature", { status: 401 });
    }

    let body;
    try { body = JSON.parse(raw); } catch { return new Response("bad json", { status: 400 }); }

    // One-off handshake when you paste the URL into Slack's Event Subscriptions.
    if (body.type === "url_verification") {
      return new Response(body.challenge, { headers: { "Content-Type": "text/plain" } });
    }

    if (body.type === "event_callback" && isReviewerReply(body.event, env.REVIEWER_ID)) {
      // Slack retries anything not answered within 3 seconds, and a retry would
      // mean a second workflow run. Answer now, dispatch after.
      ctx.waitUntil(
        dispatch(env, "run-cron-job", { mode: "route", reason: "slack-event" })
          .catch((err) => console.error(String(err))),
      );
    }
    return new Response("", { status: 200 });
  },

  async scheduled(event, env, ctx) {
    // Route safety net: only for events Slack never delivered (Worker cold at
    // the wrong moment, Slack giving up after its 3 retries). Not the main path.
    if (event.cron === env.ROUTE_CRON) {
      await dispatch(env, "run-cron-job", { mode: "route", reason: "safety-net" });
      return;
    }

    // Morning start. Both UTC crons fire year-round; only the one that is
    // currently 05:xx in Paris does anything, so DST needs no edits.
    if (parisHour() !== String(env.DIGEST_HOUR_PARIS).padStart(2, "0")) {
      console.log(`Paris hour ${parisHour()} — the other cron handles this.`);
      return;
    }
    await dispatch(env, "run-digest", { reason: "morning" });
    await dispatch(env, "run-cron-job", { mode: "collect", reason: "morning" });
  },
};
