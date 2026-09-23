// ============================================================================
//  CLOUDFLARE WORKER "daily-reports" — the alarm clock + doorbell
// ============================================================================
//
//  This Worker does NOT do the real work. It only decides WHEN, then asks
//  GitHub to run a workflow (repository_dispatch). GitHub does the work.
//
//  Why: GitHub's own `schedule` trigger is low priority (in Sept 2026 the
//  05:00 UTC cron started between 11:30 and 13:20 Paris). A run started
//  through the API begins within seconds.
//
//  WHAT SENDS WHAT
//    scheduled()  05:45 Paris ...... run-digest               → daily-digest.yml
//                 05:45 Paris ...... run-pdf-review (collect) → pdf-review.yml
//                 every 30 min ..... run-pdf-review (route)   → pdf-review.yml  (safety net)
//    fetch()      Hélène replies ... run-pdf-review (route)   → pdf-review.yml
//    fetch()      GET /health ...... checks secrets + GitHub access, sends nothing
//
//  Everything downstream is idempotent (digest checks Slack for today's
//  header, collect skips PDFs already in her DM, route marks each reply ✅),
//  so firing twice is harmless and firing late still works.
// ============================================================================

// Event names. Each must match `types:` in the workflow it starts.
const EVENT_DIGEST = "run-digest";          // → .github/workflows/daily-digest.yml
const EVENT_PDF_REVIEW = "run-pdf-review";  // → .github/workflows/pdf-review.yml

const enc = new TextEncoder();

// --- GitHub: send an order ticket --------------------------------------------

/** Start a workflow via repository_dispatch.
 *  Needs GITHUB_TOKEN = fine-grained PAT with "Contents: Read and write" on
 *  this repo. repository_dispatch always runs the workflow file on `main`. */
async function dispatch(env, eventType, payload = {}) {
  const res = await fetch(`https://api.github.com/repos/${env.REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": env.WORKER_NAME,   // GitHub rejects requests without one
    },
    body: JSON.stringify({ event_type: eventType, client_payload: payload }),
  });
  if (res.status !== 204) {
    throw new Error(`${eventType}: HTTP ${res.status} ${await res.text()}`);
  }
  console.log(`dispatched ${eventType} ${JSON.stringify(payload)}`);
}

// --- Slack: check the doorbell is really Slack --------------------------------

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Verify Slack's v0 signature over the RAW body. Without this, anyone who
 *  learns the URL could start workflows. The 5-minute window stops replays. */
async function verifySlack(request, rawBody, signingSecret) {
  const ts = request.headers.get("x-slack-request-timestamp");
  const sig = request.headers.get("x-slack-signature");
  if (!ts || !sig || !signingSecret) return false;
  if (Math.abs(Date.now() / 1000 - Number(ts)) > 300) return false;

  const key = await crypto.subtle.importKey(
    "raw", enc.encode(signingSecret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, enc.encode(`v0:${ts}:${rawBody}`));
  const expected = "v0=" + [...new Uint8Array(mac)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  return timingSafeEqual(expected, sig);
}

// --- Slack: is this event a reply from Hélène? -------------------------------

// Edits and deletions are not new comments. Everything else is let through:
// mirror_pdfs.py route does the real filtering, so a needless run is cheap
// while a missed event loses a comment.
const IGNORED_SUBTYPES = new Set(["message_changed", "message_deleted"]);

function isReviewerReply(e, reviewerId) {
  return !!e
    && e.type === "message"
    && e.channel_type === "im"
    && e.user === reviewerId
    && !e.bot_id
    && !IGNORED_SUBTYPES.has(e.subtype)
    // Threaded reply only: the parent message tells route where it belongs.
    && !!e.thread_ts && e.thread_ts !== e.ts;
}

// --- Helpers -----------------------------------------------------------------

const parisHour = () =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Paris", hour: "2-digit", hour12: false,
  }).format(new Date());

const digestHour = (env) => String(env.DIGEST_HOUR_PARIS).padStart(2, "0");

// --- /health: make invisible failures visible --------------------------------

async function health(env) {
  const report = {
    worker: env.WORKER_NAME,
    repo: env.REPO,
    parisHourNow: parisHour(),
    morningDispatchAt: `${digestHour(env)}:45 Paris`,
    sends: { digest: EVENT_DIGEST, pdfReview: EVENT_PDF_REVIEW },
    bindings: {
      GITHUB_TOKEN: Boolean(env.GITHUB_TOKEN),
      SLACK_SIGNING_SECRET: Boolean(env.SLACK_SIGNING_SECRET),
      REVIEWER_ID: Boolean(env.REVIEWER_ID),
    },
  };
  if (env.GITHUB_TOKEN) {
    // Same auth as dispatch(), read-only, so it has no side effects.
    const res = await fetch(`https://api.github.com/repos/${env.REPO}`, {
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": env.WORKER_NAME,
      },
    });
    report.githubAuth = res.status;
    report.githubAuthMeans = {
      200: "token OK",
      401: "token missing or invalid",
      403: "token lacks Contents: Read and write",
      404: "token not granted on this repo",
    }[res.status] || "unexpected";
  } else {
    report.githubAuthMeans = "no GITHUB_TOKEN on this Worker — add it as a secret";
  }
  return new Response(JSON.stringify(report, null, 2),
    { headers: { "Content-Type": "application/json" } });
}

// --- Entry points -------------------------------------------------------------

export default {
  // Called for every web request: /health, or Slack's doorbell.
  async fetch(request, env, ctx) {
    if (new URL(request.url).pathname === "/health") return health(env);

    if (request.method !== "POST") return new Response(env.WORKER_NAME, { status: 200 });

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
      // Slack retries anything not answered within 3 s (= a duplicate run).
      // So: answer Slack now, send the ticket in the background.
      ctx.waitUntil(
        dispatch(env, EVENT_PDF_REVIEW, { mode: "route", reason: "slack-event" })
          .catch((err) => console.error(String(err))),
      );
    }
    return new Response("", { status: 200 });
  },

  // Called by the crons in wrangler.toml.
  async scheduled(event, env, ctx) {
    // 1. Safety-net alarm → one route ticket, done.
    if (event.cron === env.ROUTE_CRON) {
      await dispatch(env, EVENT_PDF_REVIEW, { mode: "route", reason: "safety-net" });
      return;
    }

    // 2. Morning alarm. Two UTC crons fire; only the one that is currently
    //    the digest hour in Paris continues. DST needs no edits.
    if (parisHour() !== digestHour(env)) {
      console.log(`Paris hour ${parisHour()} — not ${digestHour(env)}, the other cron handles this.`);
      return;
    }

    // 3. Send both morning tickets independently: if one fails, the other
    //    still goes out. Then fail loudly so Cloudflare's logs show an error.
    const results = await Promise.allSettled([
      dispatch(env, EVENT_DIGEST, { reason: "morning" }),
      dispatch(env, EVENT_PDF_REVIEW, { mode: "collect", reason: "morning" }),
    ]);
    const failed = results.filter((r) => r.status === "rejected").map((r) => String(r.reason));
    if (failed.length) throw new Error(`Morning dispatch failed: ${failed.join(" | ")}`);
  },
};