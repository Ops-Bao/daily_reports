"""
Orchestrator — the thing the scheduler actually runs.

Flow: read the Control Panel → pull each included restaurant's report tab →
extract deterministically → build the two digests → post them to Slack → write a
line back to the Control Panel's log tab.

Guiding rule: one restaurant must never be able to take down the morning. A sheet
that is unreachable, unfilled, or stale becomes a visible line inside the digest
("⚠️ PBT — rapport non rempli") instead of an exception. The digest still goes
out, and the gap is obvious to the reader — which is the actual point of a 7am
check-in.
"""

import argparse
import datetime as dt
import os
import sys
import traceback
import zoneinfo

import config
import extract_report
import food_quality
import overall_quality
import post_digest

PARIS = zoneinfo.ZoneInfo("Europe/Paris")
SEP = "\n\n———\n\n"

OPS_DESTINATION = os.environ.get("OPS_DESTINATION", "C0A6VHL0CCF")   # #shortyshort
FOOD_DESTINATION = os.environ.get("FOOD_DESTINATION", "U078L6FSV8T")  # Jisoo (DM)
ALERT_DESTINATION = os.environ.get("ALERT_DESTINATION", OPS_DESTINATION)


def today_paris() -> dt.date:
    return dt.datetime.now(PARIS).date()


def fetch_location(service, loc, tab, target_date):
    """Return (data, status). status is 'ok' | 'stale' | 'error'."""
    try:
        grid = extract_report.load_grid_from_sheets(loc.spreadsheet_id, tab, service)
    except Exception as e:
        return None, f"error: {type(e).__name__}: {e}"

    if not grid:
        return None, "error: onglet vide ou introuvable"

    data = extract_report.extract(grid)
    sheet_date = data["meta"].get("date_iso")

    # The staleness guard. Without it, a manager who forgets to roll the date
    # forward means we confidently republish yesterday's numbers as today's —
    # the single worst failure mode for a report people act on.
    if sheet_date != target_date.isoformat():
        shown = data["meta"].get("date") or "(vide)"
        return data, f"stale: la feuille indique {shown}"

    return data, "ok"


def build_digests(results, target_date):
    """Return (ops_text, food_text) covering every location in one message each."""
    header_date = target_date.strftime("%d/%m/%Y")
    ops_blocks = [f"*DAILY OPS CHECK-IN* — {header_date}"]
    food_blocks = [f"*RAPPORT QUALITÉ FOOD* — {header_date}"]

    missing = []
    posted = 0
    for loc, data, status in results:
        label = f"{loc.name} ({loc.code})"
        if status != "ok":
            missing.append(f"⚠️ *{label}* — {status.split(':', 1)[-1].strip()}")
            continue
        ops_blocks.append(overall_quality.build_message(data))
        food_blocks.append(food_quality.build_food_report(data))
        posted += 1

    # Count real report blocks, not list length — the "missing" tail below would
    # otherwise make an all-failed morning look like it had content.
    if posted == 0:
        ops_blocks.append("_Aucun rapport disponible ce matin._")
        food_blocks.append("_Aucun rapport disponible ce matin._")

    if missing:
        tail = "*Rapports manquants ou non à jour*\n" + "\n".join(missing)
        ops_blocks.append(tail)
        food_blocks.append(tail)

    return SEP.join(ops_blocks), SEP.join(food_blocks)


def collect_warnings(results) -> list:
    """Extractor warnings mean a label moved or was renamed in someone's sheet.

    These are quiet, cumulative failures — a renamed row just starts returning
    N/A forever — so they are surfaced to the operator rather than left in a dict
    nobody reads.
    """
    out = []
    for loc, data, status in results:
        if data and data.get("_warnings"):
            for w in sorted(set(data["_warnings"])):
                out.append(f"{loc.code}: {w}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print both digests instead of posting to Slack.")
    ap.add_argument("--date", help="Override the target date (YYYY-MM-DD).")
    ap.add_argument("--check-hour", action="store_true",
                    help="Exit quietly unless the Paris hour matches Run Hour. "
                         "Lets one UTC cron pair cover both DST offsets.")
    ap.add_argument("--only", help="Restrict to one restaurant code, e.g. PB.")
    args = ap.parse_args()

    target_date = (dt.date.fromisoformat(args.date) if args.date else today_paris())

    locations, settings, service = config.load_config()

    if args.check_hour:
        now_hour = dt.datetime.now(PARIS).hour
        want = config.run_hour(settings)
        if now_hour != want:
            print(f"Paris hour {now_hour} != Run Hour {want}; skipping.")
            return 0

    if args.only:
        locations = [l for l in locations if l.code.upper() == args.only.upper()]

    if not locations:
        raise RuntimeError("Control Panel returned no included restaurants.")

    tab = config.report_tab(settings)
    print(f"Target date {target_date} | tab '{tab}' | {len(locations)} restaurants")

    results = []
    for loc in locations:
        data, status = fetch_location(service, loc, tab, target_date)
        print(f"  {loc.code:5s} {status}")
        results.append((loc, data, status))

    ops_text, food_text = build_digests(results, target_date)

    post_digest.post(OPS_DESTINATION, ops_text, dry_run=args.dry_run)
    post_digest.post(FOOD_DESTINATION, food_text, dry_run=args.dry_run)

    warnings = collect_warnings(results)
    if warnings and not args.dry_run:
        post_digest.post(
            ALERT_DESTINATION,
            "*Digest posté, mais des libellés sont introuvables* — une ligne a "
            "probablement été renommée ou déplacée :\n"
            + "\n".join(f"• {w}" for w in warnings),
        )
    elif warnings:
        print("\nWarnings:\n" + "\n".join(warnings))

    failed = [loc.code for loc, _, s in results if s.startswith("error")]
    if failed:
        print(f"Locations in error: {', '.join(failed)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A crash here means nothing was posted at all. Silence would look
        # identical to "no reports today", so shout before dying.
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        try:
            post_digest.post(
                ALERT_DESTINATION,
                "🚨 *Le digest du matin a échoué* — aucun message n'a été envoyé.\n"
                f"```{tb[-2500:]}```",
            )
        except Exception:
            pass
        sys.exit(1)
