"""Regression tests for the extractor.

These exist because the failure mode of a label-based extractor is silent: a
renamed or moved row starts returning N/A and the digest keeps going out looking
plausible. Run from the repo root with `python -m tests.test_extract` (no pytest needed).
"""

import datetime as dt

import extract_report as E
import food_quality as F
import overall_quality as O
import ai_summary as AI
import archive as A
import recap as RC
import post_digest as P
import run_daily as R
from config import Location
from tests.test_fixture import GRID


class FakeSheets:
    """Just enough of the Sheets client to exercise archive.save() offline."""

    def __init__(self, header, rows):
        self.header, self.rows = list(header), [list(r) for r in rows]

    # the client is service.spreadsheets().values().get(...).execute()
    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range=None, fields=None):
        if range and range.endswith("!1:1"):
            return _Exec({"values": [self.header]} if self.header else {})
        # spreadsheets().get(...) — the tab always exists in these tests
        return _Exec({"sheets": [{"properties": {"title": "Archive"}}]})

    def batchGet(self, spreadsheetId=None, ranges=None):
        out = []
        for rng in ranges:
            # "'Archive'!A2:A" -> "A"
            col = "".join(c for c in rng.split("!")[1].split(":")[0] if c.isalpha())
            idx = 0
            for ch in col:
                idx = idx * 26 + (ord(ch) - 64)
            idx -= 1
            out.append({"values": [[r[idx]] for r in self.rows if len(r) > idx]})
        return _Exec({"valueRanges": out})

    def update(self, spreadsheetId=None, range=None, valueInputOption=None, body=None):
        self.header = list(body["values"][0])
        return _Exec({})

    def append(self, spreadsheetId=None, range=None, valueInputOption=None,
               insertDataOption=None, body=None):
        self.rows.extend(body["values"])
        return _Exec({})

    def batchUpdate(self, spreadsheetId=None, body=None):
        return _Exec({})


class _Exec:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
    print(f"  ok  {name}")


def main():
    d = E.extract(GRID)

    print("extraction")
    check("no missing labels", d["_warnings"], [])
    check("restaurant", d["meta"]["restaurant"], "PETIT BAO EM")
    check("date parsed", d["meta"]["date_iso"], "2026-08-25")

    print("numbers read verbatim")
    check("ca_ht midi", d["finance"]["ca_ht"]["midi"], 1246.0)
    check("ca_ht soir", d["finance"]["ca_ht"]["soir"], 2914.13)
    check("ca_ht total", d["finance"]["ca_ht"]["total"], 4160.13)
    check("couverts soir", d["covers"]["on_site"]["soir"], 108)
    check("wow pct midi", d["ca_ht_wow_pct"]["midi"], -23.64)
    check("ecart de caisse", d["finance"]["ecart_de_caisse"]["midi"], 0.0)

    print("multi-row labels")
    # TOP 3 spans three sheet rows; first-occurrence-only would drop two of them.
    check("top3 joins rows", d["top3"]["soir"],
          "Reprise d'activité • Pas mal de groupes")

    print("french formatting round-trip")
    check("euro format", O._eur(1246.0), "1 246,00 €")
    check("pct format", O._pct(-23.64), "-23,64%")
    check("none is N/A", O._eur(None), "N/A")


    print("label drift tolerance")
    from tests.test_fixture import row as _row
    g = [r[:] for r in GRID]
    for r in g:
        if r[2] == "PERTE":
            r[2] = "PERTES"
    dd = E.extract(g)
    check("alias resolves and says so",
          any("trouv" in w and "PERTE" in w for w in dd["_warnings"]), True)

    g = [r[:] for r in GRID]
    for r in g:
        if r[2] == "GENERAL":
            r[2] = "GENERAL (RESUME DU SERVICE)"
    dd = E.extract(g)
    check("prefix match still reads the value",
          dd["narrative"]["general"]["midi"].startswith("Service à 2"), True)

    g = [r for r in GRID if r[2] != "GLITCH"]
    dd = E.extract(g)
    check("a genuinely absent label still warns",
          any("introuvable: 'GLITCH'" in w for w in dd["_warnings"]), True)

    # Guessing between two plausible rows would put the wrong text in a digest,
    # so ambiguity must fail loudly rather than pick the first hit.
    g = [r[:] for r in GRID] + [_row("BESOIN URGENT", "x"), _row("BESOIN SECONDAIRE", "y")]
    for r in g:
        if r[2] == "BESOIN":
            r[2] = "ZZZ"
    dd = E.extract(g)
    check("ambiguous prefix refuses to guess",
          any("introuvable: 'BESOIN'" in w for w in dd["_warnings"]), True)

    print("split consistency")
    # A TOTAL cell pointing at the wrong row is invisible in the digest and
    # silently wrong in the recap, so it has to be caught at extraction.
    g = [r[:] for r in GRID]
    for r in g:
        if r[2] == "CA HT ON SITE":
            r[3] = "0,00 €"            # a service figure that stops adding up
    dd = E.extract(g)
    check("a split that does not add up to CA HT is flagged",
          any("Incohérence CA HT" in w for w in dd["_warnings"]), True)
    check("a consistent sheet is not flagged",
          any("Incohérence CA HT" in w for w in d["_warnings"]), False)

    # The channels' own TOTAL column is unreliable in the real sheets, so it
    # must not be what either the check or the recap reads.
    g = [r[:] for r in GRID]
    for r in g:
        if r[2] in ("CA HT ON SITE", "CA HT TAKE AWAY", "CA HT DELIVERY"):
            r[7] = "1,00 €"
    dd = E.extract(g)
    check("a wrong channel TOTAL column is ignored",
          any("Incohérence CA HT" in w for w in dd["_warnings"]), False)
    check("recap reads the services, not that column",
          RC.channel_total(dd, "ca_ht_on_site"), 3414.77)

    print("recap arithmetic")
    # Two restaurants with figures chosen so every aggregate can be checked by
    # hand — the live sheets only ever hold one day, so this is the only place
    # the group maths is verifiable.
    def fake(ca, on, ta, de, cov, wtd, wtdp):
        return {"meta": {"restaurant": "X", "date": "01/01/2026"},
                "finance": {"ca_ht": {"total": ca, "wtd": wtd, "wtd_prior": wtdp},
                            "ca_ht_on_site": {"total": on},
                            "ca_ht_take_away": {"total": ta},
                            "ca_ht_delivery": {"total": de}},
                "covers": {"on_site": {"total": cov}}}
    oks = [(Location("A", "Aaa", "1"), fake(1000.0, 700.0, 200.0, 100.0, 50, 5000.0, 4000.0)),
           (Location("B", "Bbb", "2"), fake(1000.0, 500.0, 300.0, 200.0, 50, 5000.0, 6000.0))]
    t = RC.totals(oks)
    check("group CA HT is the sum", t["ca_ht_total"], 2000.0)
    check("ventilated total is the sum of the three channels", t["ventile"], 2000.0)
    check("on-site share", round(t["on_site_pct"], 2), 60.0)
    check("take-away share", round(t["take_away_pct"], 2), 25.0)
    check("delivery share", round(t["delivery_pct"], 2), 15.0)
    # 1200 € on-site over 100 covers, not 2000 € over 100.
    check("average ticket uses on-site revenue only", t["tm_on_site"], 12.0)
    check("week-to-date comparison", t["wtd_pct"], 0.0)

    # A blank column must read N/A, never a confident zero.
    blank = [(Location("A", "Aaa", "1"), fake(1000.0, None, None, None, None, None, None))]
    tb = RC.totals(blank)
    check("missing channels give no share", tb["on_site_pct"], None)
    check("missing covers give no average ticket", tb["tm_on_site"], None)

    print("date targeting")
    # The 7am run must ask for yesterday: today's sheet is either not rolled
    # over yet, or rolled over with the evening service still open — in which
    # case the SOIR cells read as negative, being computed as total minus midi.
    check("default target is D-1", R.target_paris(),
          R.today_paris() - dt.timedelta(days=1))

    print("digest assembly")
    target = dt.date(2026, 8, 25)
    results = [
        (Location("PB", "PETIT BAO EM", "x"), d, "ok"),
        (Location("PBT", "PETIT BAO TERNES", "y"), None, "error: onglet introuvable"),
        (Location("GB", "GROS BAO PARIS", "z"), d, "stale: la feuille indique 24/08"),
    ]
    ops, food = R.build_digests(results, target)
    check("ok location present", "PETIT BAO EM" in ops, True)
    check("errored location flagged", "PETIT BAO TERNES" in ops, True)
    check("stale location excluded from body", ops.count("GROS BAO PARIS"), 1)
    check("food digest built", "RAPPORT QUALITÉ FOOD" in food, True)

    print("idempotency key")
    # The header is what a later run searches Slack for. If it drifts from
    # what build_digests writes, every fallback cron posts a duplicate.
    check("ops digest starts with its header", ops.startswith(R.ops_header(target)), True)
    check("food digest starts with its header", food.startswith(R.food_header(target)), True)
    check("headers differ per day",
          R.ops_header(target) == R.ops_header(target - dt.timedelta(days=1)), False)
    # Slack rewrites a literal emoji to its shortcode, so history never
    # contains the emoji we posted. Three duplicate digests went out on
    # 23/09 because the key started with one.
    as_slack_stores_it = ops.replace("📊", ":bar_chart:").replace("🍜", ":ramen:")
    history = [{"text": "hello"}, {"text": as_slack_stores_it[:200]},
               {"text": ""}, {}]
    check("the key survives Slack's emoji rewriting",
          P.contains_header(history, R.ops_key(target)), True)
    check("yesterday's key is not found",
          P.contains_header(history, R.ops_key(target - dt.timedelta(days=1))), False)
    check("the displayed header still carries the emoji",
          R.ops_header(target).startswith("📊"), True)
    check("but the key does not", any(ord(c) > 0x2500 for c in R.ops_key(target)), False)
    check("nor does the food key", any(ord(c) > 0x2500 for c in R.food_key(target)), False)
    check("nor does the recap key", any(ord(c) > 0x2500 for c in RC.key(target)), False)

    # And a key that did carry one must fail loudly rather than post twice.
    try:
        P.already_posted("C123", R.ops_header(target))
        raise AssertionError("an emoji key should have been rejected")
    except P.EmojiInKey:
        print("  ok  an emoji in the key is rejected before any Slack call")

    print("empty-day handling")
    empty, _ = R.build_digests(
        [(Location("PB", "PETIT BAO EM", "x"), None, "error: x")], target)
    check("no crash on all-missing", "Aucun rapport disponible" in empty, True)

    print("archive row")
    loc = Location("PB", "PETIT BAO EM", "x")
    r = A.row(loc, d)
    check("every column has a value", sorted(r) == sorted(A.HEADERS), True)
    check("code comes from the Control Panel", r["code"], "PB")
    check("date is the ISO one", r["date"], "2026-08-25")
    # The digests never show these; the archive existing is the whole point.
    check("covers survive", r["couverts_on_site_soir"], 108)
    check("ca_ttc survives", r["ca_ttc_total"], 4586.40)
    check("week-to-date survives", r["ca_ht_wtd"], 8593.16)
    check("ecart de caisse survives", r["ecart_de_caisse_midi"], 0.0)
    check("narrative survives", r["general_midi"].startswith("Service à 2"), True)
    check("missing value becomes blank, not None", r["walkouts_soir"], "")
    check("numbers stay numbers for Sheets", isinstance(r["ca_ht_midi"], float), True)

    print("archive writing")
    fake = FakeSheets(header=[], rows=[])
    n = A.save(fake, "SID", [r], tab="Archive")
    check("first write creates the header", fake.header[:4],
          ["date", "date_sheet", "code", "restaurant"])
    check("one row appended", n, 1)
    check("row is aligned to the header",
          fake.rows[0][fake.header.index("couverts_on_site_soir")], 108)

    # Re-running the morning must not double the day.
    n2 = A.save(fake, "SID", [r], tab="Archive")
    check("same day is not archived twice", n2, 0)
    check("still one row", len(fake.rows), 1)

    # Someone reorders the columns in the sheet; values must follow the sheet,
    # not this file's order, or every cell shifts.
    shuffled = FakeSheets(header=["code", "date"] + [h for h in A.HEADERS
                                                     if h not in ("code", "date")],
                          rows=[])
    A.save(shuffled, "SID", [r], tab="Archive")
    check("reordered header still lands correctly",
          shuffled.rows[0][0:2], ["PB", "2026-08-25"])

    # A column added to archive.py later must extend the header, not shift it.
    trimmed = FakeSheets(header=A.HEADERS[:5], rows=[])
    A.save(trimmed, "SID", [r], tab="Archive")
    check("new columns are appended to the header", trimmed.header, A.HEADERS)

    print("ai briefing guardrails")
    payload = AI.build_payload(results, target)
    check("only ok sites are summarised", [x["code"] for x in payload["sites"]], ["PB"])
    check("failed sites are listed as missing",
          sorted(x["code"] for x in payload["rapports_manquants"]), ["GB", "PBT"])
    check("figures are passed already extracted",
          payload["sites"][0]["ca_ht_total"], 4160.13)
    check("empty narrative fields are dropped", "glitch" in payload["sites"][0], False)
    check("real narrative survives", "general" in payload["sites"][0], True)

    allowed = AI.allowed_numbers(payload)
    # The model may quote what we gave it, in French formatting.
    check("verbatim figure accepted",
          AI.verify_numbers("CA HT de 4 160,13 € hier soir.", allowed), [])
    check("rounded figure accepted",
          AI.verify_numbers("CA HT d'environ 4 160 €.", allowed), [])
    check("percentage accepted",
          AI.verify_numbers("En hausse de 12,13% le soir.", allowed), [])
    check("small counts accepted",
          AI.verify_numbers("3 sites, 2 ruptures, rapport du 25/08.", allowed), [])
    # And it may not invent one. This is the check that keeps the repo's
    # "numbers never pass through an LLM" promise honest.
    check("invented figure is caught",
          AI.verify_numbers("CA HT de 9 999,99 € hier.", allowed), ["9 999,99"])
    check("plausible-but-wrong figure is caught",
          AI.verify_numbers("Le CA HT atteint 4 161,13 €.", allowed), ["4 161,13"])
    check("a derived total the model computed itself is caught",
          bool(AI.verify_numbers("Total groupe : 37 441,17 €.", allowed)), True)

    # Narratives are full of non-figures the model may legitimately quote.
    # Rejecting those made the guard fire on real briefings ("12h40", "13h45").
    narrative = {"sites": [{"general": {"midi": "Rush de 12h40 a 13h45, "
                                                "table 19 fermee, 250 couverts"}}]}
    n_allowed = AI.allowed_numbers(narrative)
    check("a time quoted from the narrative is accepted",
          AI.verify_numbers("Rush entre 12h40 et 13h45.", n_allowed), [])
    check("a count quoted from the narrative is accepted",
          AI.verify_numbers("Environ 250 couverts.", n_allowed), [])
    check("a number absent from the narrative is still caught",
          AI.verify_numbers("Rush jusqu'a 14h55.", n_allowed), ["55"])

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
