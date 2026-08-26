"""
Pipeline 2 — nightly PDF review loop.

    collect : each morning, gather last night's PDFs from the nine manager
              channels and send them to the reviewer's DM, one per restaurant.
    route   : every 15 minutes, forward her thread replies back into the
              restaurant's own channel, as a reply under the original PDF.

No server and no database. Two ideas make that possible:

1. The mirrored DM carries a permalink to the original message. A Slack
   permalink already encodes the origin channel and timestamp, so the link the
   reviewer uses to jump to the source is also the mapping the router reads back.

2. A forwarded reply gets a ✅ reaction. Slack refuses a duplicate reaction, so
   "have I already sent this one?" is answered by Slack itself — and the reviewer
   gets a visible receipt that her comment went out.
"""

import argparse
import datetime as dt
import os
import re
import sys
import traceback
import zoneinfo

import config
import slack_api

PARIS = zoneinfo.ZoneInfo("Europe/Paris")

REVIEWER_ID = os.environ.get("ALERT_DESTINATION", "")
ALERT_DESTINATION = os.environ.get("ALERT_DESTINATION", "")
FORWARDED = "white_check_mark"

# Restaurant code -> the manager channel its PDFs are posted in.
# Overridable with PDF_CHANNELS="PB=C0133HV2QSV,GB=GR3JU1HJ5,…" so you can
# change routing without editing code.
DEFAULT_CHANNELS = {
    "PB":   "C0133HV2QSV",   # #bf-managers-pb
    "PBSG": "C0B0Z8B5Z33",   # #bf-managers-pbsg
    "GB":   "GR3JU1HJ5",     # #bf-managers-gb
    "BB":   "C02TT6NHW8G",   # #bf-managers-bb
    "PBT":  "C09B7JZ5X6X",   # #bf-managers-pbt
    "FSD":  "C07CDE7L4HM",   # #bf-managers-pbfsd
    "GBM":  "C071BSC2T9A",   # #bf-managers-gbm
    "PBBy": "C0AHVRGLC3X",   # #bf-manager-pbby
    "PBB":  "C04FSKASQF8",   # #bf-managers-be-office (Bastille, aka "BE")
}

PERMALINK_RE = re.compile(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})")


def channels() -> dict:
    raw = os.environ.get("PDF_CHANNELS", "").strip()
    if not raw:
        return dict(DEFAULT_CHANNELS)
    out = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_permalink(text: str):
    """Pull (channel, ts) back out of a Slack permalink. Returns None if absent."""
    m = PERMALINK_RE.search(text or "")
    if not m:
        return None
    return m.group(1), f"{m.group(2)}.{m.group(3)}"


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

def collect(since_hours: int, dry_run: bool = False) -> list:
    if not REVIEWER_ID:
        raise RuntimeError("REVIEWER_ID is not set — nobody to send the PDFs to.")

    oldest = (dt.datetime.now(tz=dt.timezone.utc)
              - dt.timedelta(hours=since_hours)).timestamp()
    dm = None if dry_run else slack_api.open_dm(REVIEWER_ID)
    sent = []

    for code, channel in channels().items():
        try:
            msgs = slack_api.history(channel, oldest=f"{oldest:.6f}")
        except slack_api.SlackError as e:
            print(f"  {code:5s} channel unreadable: {e}")
            continue

        pdfs = [
            (m, f)
            for m in msgs
            for f in (m.get("files") or [])
            if (f.get("filetype") == "pdf"
                or (f.get("mimetype") or "").endswith("pdf"))
        ]
        if not pdfs:
            print(f"  {code:5s} no PDF in the last {since_hours}h")
            continue

        # Newest first from the API; take the most recent per restaurant so a
        # re-upload by the manager supersedes their earlier attempt.
        msg, f = pdfs[0]
        posted = dt.datetime.fromtimestamp(float(msg["ts"]), PARIS)
        link = slack_api.permalink(channel, msg["ts"])
        caption = (
            f"*{code}* — rapport du {posted:%d/%m/%Y} "
            f"(déposé à {posted:%H:%M})\n"
            f"Répondez dans ce fil : votre commentaire sera publié sous le "
            f"rapport d'origine.\n{link}"
        )

        if dry_run:
            print(f"  {code:5s} would send {f.get('name')} — {link}")
            sent.append((code, None))
            continue

        data = slack_api.download_file(f["url_private_download"])
        slack_api.upload_file(
            dm, f.get("name") or f"{code}.pdf", data,
            title=f"{code} — {posted:%d/%m/%Y}",
            initial_comment=caption,
        )
        print(f"  {code:5s} sent ({len(data)//1024} KB)")
        sent.append((code, link))

    return sent


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------

def route(lookback_hours: int, dry_run: bool = False) -> int:
    if not REVIEWER_ID:
        raise RuntimeError("REVIEWER_ID is not set.")

    dm = slack_api.open_dm(REVIEWER_ID)
    oldest = (dt.datetime.now(tz=dt.timezone.utc)
              - dt.timedelta(hours=lookback_hours)).timestamp()
    reviewer_name = _display_name(REVIEWER_ID)
    forwarded = 0

    for parent in slack_api.history(dm, oldest=f"{oldest:.6f}"):
        origin = parse_permalink(parent.get("text", ""))
        if not origin or not parent.get("thread_ts"):
            continue
        origin_channel, origin_ts = origin

        for reply in slack_api.replies(dm, parent["thread_ts"]):
            if reply["ts"] == parent["thread_ts"]:
                continue
            # Only her words travel. Anything the bot itself posted in the
            # thread must never be forwarded, or the loop feeds itself.
            if reply.get("user") != REVIEWER_ID or reply.get("bot_id"):
                continue
            text = (reply.get("text") or "").strip()
            if not text:
                continue

            if dry_run:
                print(f"  would forward to {origin_channel}: {text[:70]}")
                forwarded += 1
                continue

            # Claim it first. If the reaction was already there, another run
            # sent this reply and we must not send it twice.
            if not slack_api.add_reaction(dm, reply["ts"], FORWARDED):
                continue
            try:
                slack_api.call("chat.postMessage", {
                    "channel": origin_channel,
                    "thread_ts": origin_ts,
                    "text": f"💬 *{reviewer_name}* — {text}",
                    "unfurl_links": False,
                })
                forwarded += 1
                print(f"  forwarded to {origin_channel}: {text[:60]}")
            except slack_api.SlackError as e:
                # Undo the claim so the next run retries rather than losing it.
                slack_api.call("reactions.remove", {
                    "channel": dm, "timestamp": reply["ts"], "name": FORWARDED})
                print(f"  FAILED to forward to {origin_channel}: {e}",
                      file=sys.stderr)

    return forwarded


def _display_name(user_id: str) -> str:
    try:
        p = slack_api.call("users.info", {"user": user_id}, get=True)["user"]
        prof = p.get("profile", {})
        return prof.get("display_name") or prof.get("real_name") or "Ops"
    except slack_api.SlackError:
        return "Ops"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["collect", "route"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hours", type=int,
                    help="collect: how far back to look for PDFs (default 24). "
                         "route: how far back to scan threads (default 72).")
    args = ap.parse_args()

    if args.mode == "collect":
        sent = collect(args.hours or 24, dry_run=args.dry_run)
        print(f"\n{len(sent)} rapport(s) envoyé(s).")
        missing = [c for c in channels() if c not in {code for code, _ in sent}]
        if missing and not args.dry_run and ALERT_DESTINATION:
            slack_api.call("chat.postMessage", {
                "channel": ALERT_DESTINATION,
                "text": "📄 *PDF non déposés hier soir* : " + ", ".join(missing),
            })
    else:
        n = route(args.hours or 72, dry_run=args.dry_run)
        print(f"\n{n} commentaire(s) transmis.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        if ALERT_DESTINATION:
            try:
                slack_api.call("chat.postMessage", {
                    "channel": ALERT_DESTINATION,
                    "text": f"🚨 *Mirror PDF en échec*\n```{tb[-2000:]}```",
                })
            except Exception:
                pass
        sys.exit(1)
