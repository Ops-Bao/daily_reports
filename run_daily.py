"""
Orchestrator — the thing the scheduler actually runs.

Flow: read the Control Panel → pull each included restaurant's report tab →
extract deterministically → build the two digests → prepend an AI briefing to
the ops one → post them to Slack → append every extracted field to the Archive
tab.

The digests are lossy on purpose (a dozen fields out of forty); the archive is
the complete record, and the only thing that survives the run.

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

import ai_summary
import archive
import config
import extract_report
import food_quality
import overall_quality
import post_digest
import recap

PARIS = zoneinfo.ZoneInfo("Europe/Paris")
SEP = post_digest.SECTION_SEP

OPS_DESTINATION = os.environ.get("OPS_DESTINATION")   # #shortyshort
FOOD_DESTINATION = os.environ.get("FOOD_DESTINATION")  # Jisoo (DM)
# Alerts fall back to the ops channel: an alert that goes nowhere is how three
# weeks of failed runs went unnoticed.
ALERT_DESTINATION = os.environ.get("ALERT_DESTINATION") or OPS_DESTINATION


# At 7am the completed report is YESTERDAY's — the evening service has to close
# before the day's figures exist. Targeting today would either withhold every
# sheet as "stale" (because it still shows yesterday) or, worse, publish a
# half-finished day where the SOIR cells still read as negative because they are
# computed as total minus midi.
REPORT_OFFSET_DAYS = int(os.environ.get("REPORT_OFFSET_DAYS", "1"))


class _SkipArchive(Exception):
    """Control flow for --no-archive, so the skip reuses the same guarded block
    that protects the digest from an archive failure."""


def today_paris() -> dt.date:
    return dt.datetime.now(PARIS).date()


def target_paris() -> dt.date:
    return today_paris() - dt.timedelta(days=REPORT_OFFSET_DAYS)


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
        # Two very different situations wear the same mask. A date AFTER the
        # target means the manager has already rolled the tab forward for
        # today's service — i.e. we are running too late, and the figures we
        # want have been overwritten. A date BEFORE it means nobody filled it.
        if sheet_date and sheet_date > target_date.isoformat():
            return data, f"future: la feuille indique déjà {shown}"
        return data, f"stale: la feuille indique {shown}"

    return data, "ok"


# The key must contain NO emoji. Slack rewrites a literal emoji to its
# shortcode (📊 becomes ":bar_chart:") in the stored message, so a key that
# starts with one can never be found again in conversations.history — which
# is exactly how three identical digests went out on 23/09.
def ops_key(target_date) -> str:
    return f"*DAILY OPS CHECK-IN* — {target_date:%d/%m/%Y}"


def food_key(target_date) -> str:
    return f"*RAPPORT QUALITÉ FOOD* — {target_date:%d/%m/%Y}"


def ops_header(target_date) -> str:
    return f"📊 {ops_key(target_date)}"


def food_header(target_date) -> str:
    return f"🥢 {food_key(target_date)}"


def build_digests(results, target_date):
    """Return (ops_text, food_text) covering every location in one message each.

    The header line doubles as the idempotency key: `already_posted` looks for
    it in the destination before posting, so build it here and nowhere else.
    """
    ops_blocks = [ops_header(target_date)]
    food_blocks = [food_header(target_date)]

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
    # Digests are full of emoji and French punctuation; a Windows console is
    # cp1252 by default and raises on the first one. Harmless on the runner.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print both digests instead of posting to Slack.")
    ap.add_argument("--date", help="Override the target date (YYYY-MM-DD).")
    ap.add_argument("--force", action="store_true",
                    help="Post even if today's digest is already in Slack.")
    ap.add_argument("--only", help="Restrict to one restaurant code, e.g. PB.")
    ap.add_argument("--to", metavar="CHANNEL",
                    help="Send everything (both digests and any alert) to this "
                         "channel or user ID instead of the real destinations. "
                         "For rehearsing against a test channel without "
                         "touching the production variables.")
    ap.add_argument("--no-archive", action="store_true",
                    help="Skip the Archive tab. Use with --to so a rehearsal "
                         "does not append test rows to real data.")
    args = ap.parse_args()

    # One override for all three, so a test run cannot leak into a real channel
    # by way of an alert.
    ops_dest = args.to or OPS_DESTINATION
    food_dest = args.to or FOOD_DESTINATION
    alert_dest = args.to or ALERT_DESTINATION
    if args.to:
        print(f"Redirecting all output to {args.to}")

    target_date = (dt.date.fromisoformat(args.date) if args.date else target_paris())

    # Idempotency, not clock-gating. Several crons fire (DST pair + spares for
    # GitHub's scheduling delays); whichever arrives first posts, the rest see
    # the header already in Slack and stop. Checked before touching Sheets so a
    # duplicate run costs one API call.
    # Keyed by name, not by destination: pointing both at the same channel
    # while testing would otherwise collapse the two into one and silently
    # drop the ops digest.
    plan = [("recap", ops_dest, recap.key(target_date)),
            ("ops", ops_dest, ops_key(target_date)),
            ("food", food_dest, food_key(target_date))]
    if not args.dry_run and not args.force:
        plan = [p for p in plan if not post_digest.already_posted(p[1], p[2])]
        if not plan:
            print(f"Digest for {target_date} already posted; nothing to do.")
            return 0

    locations, settings, service = config.load_config()

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

    rolled = [loc.code for loc, _, s in results if s.startswith("future")]
    if not any(s == "ok" for _, _, s in results) and rolled and not args.force:
        # Every sheet already shows a later day: this run is too late, not a
        # morning where nobody filled anything in. Posting would publish nine
        # ⚠️ lines AND consume the header that stops duplicates, so the real
        # digest could never go out afterwards. Say so instead.
        msg = (f"⏰ *Digest non publié pour le {target_date:%d/%m/%Y}* — les "
               f"feuilles affichent déjà un jour plus récent ({', '.join(rolled)}). "
               "Le run est parti trop tard et les chiffres visés ont été écrasés.\n"
               "Relancer avec *force* et la date voulue pour rattraper.")
        print(msg)
        if not args.dry_run:
            post_digest.post(alert_dest, msg)
        return 0

    ops_text, food_text = build_digests(results, target_date)

    # Numbers in the recap are computed here; the model only supplies the
    # per-service prose. Without it the recap still goes out, showing the
    # managers' own words instead.
    prose, summary_problem = ai_summary.service_prose(results, target_date)
    if prose:
        print(f"AI prose for {len(prose)} service(s).")
    elif summary_problem:
        print(f"AI prose skipped: {summary_problem}")
    recap_text = recap.build(results, target_date, prose)

    texts = {"recap": recap_text, "ops": ops_text, "food": food_text}
    for name, dest, _ in plan:
        post_digest.post(dest, texts[name], dry_run=args.dry_run)

    # Archive after posting: the digest is the job people are waiting for, so a
    # broken archive must not stop it. It is idempotent on (date, code), so a
    # re-run with --force fills in a day this step missed.
    archived = 0
    archive_error = None
    try:
        if args.no_archive:
            raise _SkipArchive()
        rows = [archive.row(loc, data) for loc, data, status in results if status == "ok"]
        if args.dry_run:
            print(f"\n(dry run) would archive {len(rows)} row(s) "
                  f"with {len(archive.HEADERS)} columns")
        else:
            archived = archive.save(
                service,
                config.archive_spreadsheet_id(settings),
                rows,
                tab=config.archive_tab(settings),
            )
            print(f"Archived {archived} row(s).")
    except _SkipArchive:
        print("Archive skipped (--no-archive).")
    except Exception as e:
        archive_error = f"{type(e).__name__}: {e}"
        print(f"Archive failed: {archive_error}", file=sys.stderr)

    if summary_problem and not args.dry_run:
        post_digest.post(
            alert_dest,
            "🧠 *Recap posté sans les résumés IA* — tous les chiffres sont "
            "calculés et intacts ; les notes des managers remplacent la prose."
            f"\nRaison : {summary_problem}",
        )

    if archive_error and not args.dry_run:
        post_digest.post(
            alert_dest,
            "🗄️ *Digest posté, mais l'archivage a échoué* — les chiffres du jour "
            "ne sont pas dans l'onglet Archive. Relancer avec *force* pour "
            f"rattraper.\n```{archive_error}```",
        )

    warnings = collect_warnings(results)
    if warnings and not args.dry_run:
        post_digest.post(
            alert_dest,
            "🔧 *Digest posté, mais des libellés sont introuvables* — une ligne a "
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
