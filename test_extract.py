"""Regression tests for the extractor.

These exist because the failure mode of a label-based extractor is silent: a
renamed or moved row starts returning N/A and the digest keeps going out looking
plausible. Run with `python test_extract.py` (no pytest needed).
"""

import datetime as dt

import extract_report as E
import food_quality as F
import overall_quality as O
import run_daily as R
from config import Location
from test_fixture import GRID


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
    from test_fixture import row as _row
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

    print("empty-day handling")
    empty, _ = R.build_digests(
        [(Location("PB", "PETIT BAO EM", "x"), None, "error: x")], target)
    check("no crash on all-missing", "Aucun rapport disponible" in empty, True)

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
