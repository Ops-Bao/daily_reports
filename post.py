"""
Format the extracted report into a Slack message and post it.

Runs on top of extract_report.py. No LLM, no audio — just clean text to Slack.
When ElevenLabs is ready later, generate the MP3 from `build_digest_text()` and
attach it with files_upload; nothing else changes.

Slack setup (once)
------------------
1. Create a Slack app at api.slack.com/apps (or reuse your existing one).
2. Bot Token Scopes: chat:write, files:write (files:write only needed later
   for audio/PDF upload).
3. Install to workspace, copy the Bot User OAuth Token (xoxb-...).
4. Invite the bot to the central channel: /invite @YourBot
5. Set env vars: SLACK_BOT_TOKEN, SLACK_CENTRAL_CHANNEL (channel ID, e.g. C0123).
"""

import os
import json
import urllib.request
import urllib.error

from extract_report import load_grid_from_csv, extract  # reuse the extractor


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _eur(v):
    if v is None:
        return "—"
    return f"{v:,.2f} €".replace(",", " ").replace(".", ",")  # 6 908,85 €


def _n(v):
    return "—" if v is None else str(v)


def build_digest_text(data: dict) -> str:
    """A compact, human-readable digest. This is also the future TTS input."""
    m = data["meta"]
    fin = data["finance"]
    cov = data["covers"]
    staff = data["staff"]
    ctx = data["context"]
    ops = data["operations"]

    ca = fin["ca_ttc"]
    total_covers = cov["on_site"].get("total")

    lines = []
    lines.append(f"*🍜 {m['restaurant']}* — {m['date']}")
    lines.append("")
    lines.append(f"* CA TTC* : {_eur(ca['total'])}  "
                 f"(midi {_eur(ca['midi'])} · soir {_eur(ca['soir'])})")
    if ca.get("pct_wow") is not None:
        arrow = "🔺" if ca["pct_wow"] >= 0 else "🔻"
        lines.append(f"   {arrow} {ca['pct_wow']:+.1f}% vs S-1  "
                     f"· WTD {_eur(ca['wtd'])}")
    lines.append("")
    lines.append(f"*Couverts* : {_n(total_covers)} sur place  "
                 f"· {_n(cov['take_away'].get('total'))} TA  "
                 f"· {_n(cov['delivery'].get('total'))} livraison")
    lines.append("")
    lines.append(f"*Managers* : midi {staff['manager']['midi']} · "
                 f"soir {staff['manager']['soir']}")
    lines.append(f"*Météo* : midi {ctx['meteo']['midi']} · "
                 f"soir {ctx['meteo']['soir']}")

    # only surface operational flags when non-empty
    flags = []
    for label, key in [("Glitch", "glitch")]:
        g = data["narrative"].get(key, {})
        for shift in ("midi", "soir"):
            val = (g.get(shift) or "").strip()
            if val and val.upper() not in {"RAS", "//"}:
                flags.append(f"⚠️ {label} ({shift}) : {val}")
    for label, key in [("Ruptures", "ruptures"), ("Besoin", "besoin")]:
        o = ops.get(key, {})
        for shift in ("midi", "soir"):
            val = (o.get(shift) or "").strip()
            if val and val.upper() not in {"RAS", "//"}:
                flags.append(f"⚠️ {label} ({shift}) : {val}")
    if flags:
        lines.append("")
        lines.extend(flags)

    if data.get("_warnings"):
        lines.append("")
        lines.append("_⚙️ Alertes extraction : "
                     + "; ".join(data["_warnings"]) + "_")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Slack posting
# ---------------------------------------------------------------------------

def post_to_slack(text: str, channel: str, token: str) -> dict:
    payload = json.dumps({
        "channel": channel,
        "text": text,
        "unfurl_links": False,
        "mrkdwn": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Slack error: {result.get('error')}")
    return result


# ---------------------------------------------------------------------------
# Orchestration — one run over all restaurants
# ---------------------------------------------------------------------------

# For live use, replace this with the 9 spreadsheet IDs and
# load_grid_from_sheets(). For now it takes local CSV paths.
def run(csv_paths: list, channel: str, token: str, dry_run: bool = False):
    for path in csv_paths:
        grid = load_grid_from_csv(path)
        data = extract(grid)
        text = build_digest_text(data)
        if dry_run:
            print("=" * 60)
            print(text)
        else:
            post_to_slack(text, channel, token)
            print(f"Posté: {data['meta']['restaurant']}")


if __name__ == "__main__":
    import sys
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_CENTRAL_CHANNEL", "")
    paths = sys.argv[1:] or [
        "/mnt/user-data/uploads/2026_-_PBBy_Suivi_de_performance_-_Rapport_Jour_New.csv"
    ]
    # no token -> dry run so you can preview the message
    run(paths, channel, token, dry_run=not (token and channel))
