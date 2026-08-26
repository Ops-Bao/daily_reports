"""
Deterministic daily-report extractor for restaurant shift reports.

Design principles
-----------------
- NUMBERS NEVER TOUCH AN LLM. Every figure is read verbatim from a known
  labelled row and parsed with a strict French-number parser. No interpretation.
- LABEL-BASED, not row-index-based. We locate each field by its label text in
  column C, so inserting/removing a row in one restaurant's sheet does not
  silently shift every value.
- Fail loud, not silent. If a required label is missing, we record it in
  `_warnings` rather than guessing.

Swapping to live Google Sheets
------------------------------
`load_grid_from_csv()` returns a list-of-lists grid. Replace it with
`load_grid_from_sheets()` (stub at bottom) which returns the same shape from the
Sheets API. Everything downstream is identical.
"""

import csv
import io
import json
import re
import unicodedata
from typing import Optional


# Column indices within the grid (0-based)
COL_LABEL = 2   # column C — field labels
COL_MIDI = 3    # column D — lunch value
COL_SOIR = 5    # column F — evening value
COL_TOTAL = 7   # column H — daily total (where present)
COL_WTD = 8     # column I — week-to-date
COL_WTD_PRIOR = 9   # column J — prior week-to-date
COL_DELTA = 10      # column K — delta EUR
COL_PCT = 11        # column L — % change WoW


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _clean(s: Optional[str]) -> str:
    if s is None:
        return ""
    # normalise narrow/regular no-break spaces to plain, strip
    s = s.replace("\u202f", " ").replace("\u00a0", " ")
    return s.strip()


def parse_euro(s: Optional[str]) -> Optional[float]:
    """'2 464,40 €' -> 2464.40 ; '' -> None ; returns None if not numeric."""
    s = _clean(s)
    if not s or s in {"//", "-", "RAS"}:
        return None
    s = s.replace("€", "").replace(" ", "").strip()
    s = s.replace(",", ".")
    # keep only leading sign, digits, dot
    m = re.match(r"^-?\d+(\.\d+)?$", s)
    return float(s) if m else None


def parse_int(s: Optional[str]) -> Optional[int]:
    s = _clean(s)
    if not s or s in {"//", "-"}:
        return None
    s = s.replace(" ", "")
    return int(s) if re.match(r"^-?\d+$", s) else None


def parse_pct(s: Optional[str]) -> Optional[float]:
    """'56,62%' -> 56.62"""
    s = _clean(s).replace("%", "").replace(",", ".")
    m = re.match(r"^-?\d+(\.\d+)?$", s)
    return float(s) if m else None


def _parse_fr_date(s: Optional[str]) -> Optional[str]:
    """'25/08/2026' -> '2026-08-25'. Returns None if unparseable.

    Used for the staleness guard: if the sheet's date is not today, the
    manager never updated it and we must not post yesterday's numbers.
    """
    s = _clean(s)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if not m:
        return None
    d, mo, y = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _norm_label(s: str) -> str:
    """Normalise a label for matching: strip accents, upper, collapse spaces."""
    s = _clean(s)
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s).upper()
    return s


# ---------------------------------------------------------------------------
# Grid loading
# ---------------------------------------------------------------------------

def load_grid_from_csv(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.reader(f))


class Report:
    """Label-indexed view over the grid."""

    def __init__(self, grid: list):
        self.grid = grid
        self._warnings = []
        # map normalised label -> ALL row indices carrying that label.
        # Some labels legitimately repeat (TOP 3 spans three rows), so keeping
        # only the first occurrence silently drops data.
        self._label_rows = {}
        for i, row in enumerate(grid):
            if len(row) > COL_LABEL:
                lab = _norm_label(row[COL_LABEL])
                if lab:
                    self._label_rows.setdefault(lab, []).append(i)

    def _rows(self, label: str) -> list:
        idxs = self._label_rows.get(_norm_label(label))
        if not idxs:
            self._warnings.append(f"Label introuvable: {label!r}")
            return []
        return [self.grid[i] for i in idxs]

    def _row(self, label: str) -> Optional[list]:
        idxs = self._label_rows.get(_norm_label(label))
        if not idxs:
            self._warnings.append(f"Label introuvable: {label!r}")
            return None
        return self.grid[idxs[0]]

    def cell(self, label: str, col: int) -> Optional[str]:
        row = self._row(label)
        if row is None or len(row) <= col:
            return None
        return _clean(row[col])

    # convenience typed getters for a MIDI/SOIR/TOTAL numeric row
    def money_row(self, label: str) -> dict:
        return {
            "midi": parse_euro(self.cell(label, COL_MIDI)),
            "soir": parse_euro(self.cell(label, COL_SOIR)),
            "total": parse_euro(self.cell(label, COL_TOTAL)),
            "wtd": parse_euro(self.cell(label, COL_WTD)),
            "wtd_prior": parse_euro(self.cell(label, COL_WTD_PRIOR)),
            "pct_wow": parse_pct(self.cell(label, COL_PCT)),
        }

    def count_row(self, label: str) -> dict:
        return {
            "midi": parse_int(self.cell(label, COL_MIDI)),
            "soir": parse_int(self.cell(label, COL_SOIR)),
            "total": parse_int(self.cell(label, COL_TOTAL)),
            "wtd": parse_int(self.cell(label, COL_WTD)),
        }

    def text_row(self, label: str) -> dict:
        return {
            "midi": self.cell(label, COL_MIDI),
            "soir": self.cell(label, COL_SOIR),
        }

    def text_row_multi(self, label: str, sep: str = " • ") -> dict:
        """Join every row carrying this label (TOP 3 occupies three rows)."""
        rows = self._rows(label)
        out = {}
        for shift, col in (("midi", COL_MIDI), ("soir", COL_SOIR)):
            vals = [_clean(r[col]) for r in rows if len(r) > col and _clean(r[col])]
            out[shift] = sep.join(vals)
        return out

    def wow_pct_row(self, label: str) -> dict:
        """The per-service W-1 % sits immediately right of each service value:
        col E (idx 4) for MIDI, col G (idx 6) for SOIR."""
        return {
            "midi": parse_pct(self.cell(label, COL_MIDI + 1)),
            "soir": parse_pct(self.cell(label, COL_SOIR + 1)),
        }


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract(grid: list) -> dict:
    r = Report(grid)

    # header meta (date is in row 0 col C, restaurant name row 1 col D)
    date_raw = _clean(grid[0][COL_LABEL]) if grid and len(grid[0]) > COL_LABEL else ""
    restaurant = ""
    for row in grid[:3]:
        for cell in row:
            if "RAPPORT JOURNALIER" in _clean(cell).upper():
                restaurant = _clean(cell).replace("RAPPORT JOURNALIER", "").strip()

    data = {
        "meta": {
            "restaurant": restaurant,
            "date": date_raw,
            "date_iso": _parse_fr_date(date_raw),
        },
        "finance": {
            "ca_ttc": r.money_row("CA TTC"),
            "ca_ht": r.money_row("CA HT"),
            "ca_ht_on_site": r.money_row("CA HT ON SITE"),
            "ca_ht_take_away": r.money_row("CA HT TAKE AWAY"),
            "ca_ht_delivery": r.money_row("CA HT DELIVERY"),
            "panier_outside": r.money_row("PANIER OUTSIDE"),
            "remise": r.text_row("REMISE"),
            "perte": r.text_row("PERTE"),
            "ecart_de_caisse": r.money_row("ECART DE CAISSE"),
        },
        "covers": {
            "on_site": r.count_row("COUVERTS ON SITE"),
            "take_away": r.count_row("NOMBRE TAKE AWAY"),
            "delivery": r.count_row("NOMBRE LIVRAISON"),
        },
        "tm_ht_on_site": r.text_row("TM HT ON SITE"),
        "top3": r.text_row_multi("TOP 3"),
        "ca_ht_wow_pct": r.wow_pct_row("CA HT"),
        "staff": {
            "manager": r.text_row("MANAGER:"),
            "pass_master": r.text_row("PASS MASTER:"),
            "staff": r.text_row("STAFF:"),
        },
        "context": {
            "meteo": r.text_row("EVENEMENT / METEO"),
            "briefing": r.text_row("BRIEFING TOPICS"),
        },
        "narrative": {
            "general": r.text_row("GENERAL"),
            "foh": r.text_row("FOH"),
            "boh": r.text_row("BOH"),
            "glitch": r.text_row("GLITCH"),
            "commentaires": r.text_row("COMMENTAIRES"),
        },
        "operations": {
            "reception_ok": r.text_row("RECEPTION DE MARCHANDISES - OK"),
            "reception_bad": r.text_row("RECEPTION DE MARCHANDISES - BAD"),
            "reception_comments": r.text_row("RECEPTION DE MARCHANDISES - IF BAD, WHY"),
            "qualite_food": r.text_row("QUALITE FOOD"),
            "resa": r.text_row("#RESA"),
            "walkouts": r.text_row("#WALKOUTS"),
            "besoin": r.text_row("BESOIN"),
            "ruptures": r.text_row("RUPTURES FOOD & DRINKS"),
        },
        "_warnings": r._warnings,
    }
    return data


# ---------------------------------------------------------------------------
# Live Sheets stub (fill in when wiring to Google API)
# ---------------------------------------------------------------------------

def load_grid_from_sheets(spreadsheet_id: str, tab: str, service) -> list:
    """Return the same list-of-lists grid as load_grid_from_csv, from a live sheet.

    FORMATTED_VALUE is deliberate: it hands back '2 464,40 €' exactly as the
    manager sees it, which is what the French parsers above expect. Switching to
    UNFORMATTED_VALUE would return raw floats and silently change parsing.

    The API right-trims rows, so a row ending in blanks comes back short. We pad
    every row to a fixed width, otherwise `len(row) <= col` checks turn a real
    empty cell into a missing-label warning.
    """
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab}'!A1:M80",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    rows = resp.get("values", [])
    width = 13  # columns A..M
    return [list(r) + [""] * (width - len(r)) for r in rows]


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "/mnt/user-data/uploads/2026_-_PBBy_Suivi_de_performance_-_Rapport_Jour_New.csv"
    grid = load_grid_from_csv(path)
    result = extract(grid)
    print(json.dumps(result, ensure_ascii=False, indent=2))
