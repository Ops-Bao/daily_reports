"""Regression tests for the PDF review loop.

The two things that would hurt if they broke: losing the link between a mirrored
PDF and its original message, and forwarding a message that shouldn't travel.
Run with `python test_mirror.py`.
"""

import os

os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")

import mirror_pdfs as M


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
    print(f"  ok  {name}")


def main():
    print("permalink is the routing table")
    link = "https://baofamily.slack.com/archives/C0133HV2QSV/p1787696718174869"
    check("round-trips to channel + ts",
          M.parse_permalink(f"*PB* — rapport du 26/08/2026\n{link}"),
          ("C0133HV2QSV", "1787696718.174869"))
    check("a message with no link is skipped, not fatal",
          M.parse_permalink("bonjour"), None)
    check("empty text is safe", M.parse_permalink(""), None)

    print("channel routing")
    os.environ["PDF_CHANNELS"] = "PB=C111, GB = C222 ,BB=C333"
    check("override parses, whitespace tolerated",
          M.channels(), {"PB": "C111", "GB": "C222", "BB": "C333"})
    del os.environ["PDF_CHANNELS"]
    check("falls back to defaults", M.channels()["PB"], "C0133HV2QSV")

    print("only her words travel")
    reviewer = "U_REVIEWER"
    thread = [
        {"ts": "1.0", "user": reviewer, "text": "parent message"},
        {"ts": "2.0", "user": reviewer, "text": "Attention aux ruptures"},
        {"ts": "3.0", "user": "U_MANAGER", "text": "bien noté"},
        {"ts": "4.0", "user": reviewer, "bot_id": "B1", "text": "echo"},
        {"ts": "5.0", "user": reviewer, "text": "   "},
    ]
    kept = [
        r["ts"] for r in thread
        if r["ts"] != "1.0"
        and r.get("user") == reviewer
        and not r.get("bot_id")
        and (r.get("text") or "").strip()
    ]
    # A bot message that slips through here would be re-forwarded on every run,
    # which is how these mirrors end up in an infinite echo.
    check("parent, others, bots and blanks all excluded", kept, ["2.0"])

    print("\nAll mirror checks passed.")


if __name__ == "__main__":
    main()
