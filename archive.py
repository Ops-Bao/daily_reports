"""
Archive tab — the one place the extracted data is kept.

Until this existed, every figure the extractor read was formatted into two Slack
messages and then thrown away with the container. The digests show about a dozen
fields per service; the extractor reads roughly forty. Covers, take-away and
delivery splits, week-to-date columns, écart de caisse, météo, #WALKOUTS — all
of it was being discarded every morning.

One row per restaurant per day, one column per extracted field, appended to an
"Archive" tab so the team can pivot on it in the tool they already use.

Two rules carried over from the extractor:

- **Column-name-based, never column-index-based.** Rows are written in the order
  of the header row that is actually in the sheet, not the order of `COLUMNS`
  here. Someone dragging a column sideways, or a future field added to this
  file, therefore cannot silently shift every value into the wrong column.
- **Idempotent.** (date, code) already present means this day was archived by an
  earlier run; it is skipped rather than duplicated.
"""

import datetime as dt

ARCHIVE_TAB_DEFAULT = "Archive"

# (column name, how to read it out of the extracted dict).
# Built programmatically below so the header row and the values can never drift
# apart — they are generated from this one list.
COLUMNS = []


def _add(name, fn):
    COLUMNS.append((name, fn))


def _path(data, path):
    node = data
    for k in path:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node


def _sub(path, key):
    return lambda d: _path(d, path).get(key) if isinstance(_path(d, path), dict) else None


# --- meta -------------------------------------------------------------------
_add("date", lambda d: d["meta"].get("date_iso") or "")
_add("date_sheet", lambda d: d["meta"].get("date") or "")
# code and restaurant come from the Control Panel, not the sheet, so they are
# injected by `row()` rather than read from `data`.
_add("code", lambda d: "")
_add("restaurant", lambda d: d["meta"].get("restaurant") or "")

# --- money rows: midi / soir / total / week-to-date -------------------------
MONEY_ROWS = [
    ("ca_ttc", ("finance", "ca_ttc")),
    ("ca_ht", ("finance", "ca_ht")),
    ("ca_ht_on_site", ("finance", "ca_ht_on_site")),
    ("ca_ht_take_away", ("finance", "ca_ht_take_away")),
    ("ca_ht_delivery", ("finance", "ca_ht_delivery")),
    ("panier_outside", ("finance", "panier_outside")),
    ("ecart_de_caisse", ("finance", "ecart_de_caisse")),
]
for _name, _p in MONEY_ROWS:
    for _k in ("midi", "soir", "total", "wtd", "wtd_prior", "pct_wow"):
        _add(f"{_name}_{_k}", _sub(_p, _k))

# --- counts -----------------------------------------------------------------
COUNT_ROWS = [
    ("couverts_on_site", ("covers", "on_site")),
    ("nombre_take_away", ("covers", "take_away")),
    ("nombre_livraison", ("covers", "delivery")),
]
for _name, _p in COUNT_ROWS:
    for _k in ("midi", "soir", "total", "wtd"):
        _add(f"{_name}_{_k}", _sub(_p, _k))

# --- per-service text and percentages ---------------------------------------
TEXT_ROWS = [
    ("tm_ht_on_site", ("tm_ht_on_site",)),
    ("ca_ht_wow_pct", ("ca_ht_wow_pct",)),
    ("top3", ("top3",)),
    ("remise", ("finance", "remise")),
    ("perte", ("finance", "perte")),
    ("manager", ("staff", "manager")),
    ("pass_master", ("staff", "pass_master")),
    ("staff", ("staff", "staff")),
    ("meteo", ("context", "meteo")),
    ("briefing", ("context", "briefing")),
    ("general", ("narrative", "general")),
    ("foh", ("narrative", "foh")),
    ("boh", ("narrative", "boh")),
    ("glitch", ("narrative", "glitch")),
    ("commentaires", ("narrative", "commentaires")),
    ("reception_ok", ("operations", "reception_ok")),
    ("reception_bad", ("operations", "reception_bad")),
    ("reception_comments", ("operations", "reception_comments")),
    ("qualite_food", ("operations", "qualite_food")),
    ("resa", ("operations", "resa")),
    ("walkouts", ("operations", "walkouts")),
    ("besoin", ("operations", "besoin")),
    ("ruptures", ("operations", "ruptures")),
]
for _name, _p in TEXT_ROWS:
    for _k in ("midi", "soir"):
        _add(f"{_name}_{_k}", _sub(_p, _k))

# --- provenance --------------------------------------------------------------
# Which labels drifted the day this row was written. Without it you cannot tell,
# months later, whether an empty cell means "nothing happened" or "the row was
# renamed and we silently wrote N/A".
_add("warnings", lambda d: " | ".join(sorted(set(d.get("_warnings") or []))))
_add("archived_at", lambda d: "")

HEADERS = [name for name, _ in COLUMNS]


def row(loc, data, now=None) -> dict:
    """Flatten one restaurant's extracted dict into {column name: value}."""
    out = {}
    for name, fn in COLUMNS:
        try:
            v = fn(data)
        except (KeyError, AttributeError, TypeError):
            v = None
        out[name] = "" if v is None else v
    out["code"] = loc.code
    out["restaurant"] = out["restaurant"] or loc.name
    out["archived_at"] = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    return out


# ---------------------------------------------------------------------------
# Sheet plumbing
# ---------------------------------------------------------------------------

def _col_letter(idx: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _tab_exists(service, spreadsheet_id, tab) -> bool:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
    ).execute()
    return tab in [s["properties"]["title"] for s in meta.get("sheets", [])]


def _create_tab(service, spreadsheet_id, tab):
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
    ).execute()


def _read_header(service, spreadsheet_id, tab) -> list:
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{tab}'!1:1"
    ).execute()
    vals = resp.get("values", [])
    return [c.strip() for c in vals[0]] if vals else []


def _write_header(service, spreadsheet_id, tab, header):
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab}'!A1",
        valueInputOption="RAW",
        body={"values": [header]},
    ).execute()


def _existing_keys(service, spreadsheet_id, tab, header) -> set:
    """The (date, code) pairs already archived.

    Only the two key columns are read, by their position in the *sheet's* header
    — reading the whole tab would grow into a megabyte-sized request after a
    couple of years.
    """
    if "date" not in header or "code" not in header:
        return set()
    d_col = _col_letter(header.index("date"))
    c_col = _col_letter(header.index("code"))
    resp = service.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=[f"'{tab}'!{d_col}2:{d_col}", f"'{tab}'!{c_col}2:{c_col}"],
    ).execute()
    ranges = resp.get("valueRanges", [])
    dates = [r[0] if r else "" for r in ranges[0].get("values", [])] if ranges else []
    codes = [r[0] if r else "" for r in ranges[1].get("values", [])] if len(ranges) > 1 else []
    return {(d.strip(), c.strip()) for d, c in zip(dates, codes)}


def save(service, spreadsheet_id: str, rows: list, tab: str = ARCHIVE_TAB_DEFAULT) -> int:
    """Append rows (dicts from `row()`) to the archive tab. Returns rows written.

    Creates the tab and its header on first use, and extends the header if this
    file gained a column since the sheet was created — old rows keep their
    values because everything is written by column name.
    """
    if not rows:
        return 0

    if not _tab_exists(service, spreadsheet_id, tab):
        _create_tab(service, spreadsheet_id, tab)

    header = _read_header(service, spreadsheet_id, tab)
    if not header:
        header = list(HEADERS)
        _write_header(service, spreadsheet_id, tab, header)
    else:
        missing = [h for h in HEADERS if h not in header]
        if missing:
            header = header + missing
            _write_header(service, spreadsheet_id, tab, header)

    done = _existing_keys(service, spreadsheet_id, tab, header)
    fresh = [r for r in rows if (str(r.get("date", "")), str(r.get("code", ""))) not in done]
    if not fresh:
        return 0

    values = [[r.get(col, "") for col in header] for r in fresh]
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab}'!A1",
        valueInputOption="RAW",          # numbers stay numbers, so Sheets can sum them
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()
    return len(fresh)
