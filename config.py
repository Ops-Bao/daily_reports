"""
Configuration loader.

Everything operational lives in the "Daily Report - Control Panel" spreadsheet,
not in this repo. Ops people add a restaurant or flip Include=FALSE in the sheet;
nobody has to touch code or redeploy. This module is the only thing that knows
the Control Panel's shape.

It deliberately does NOT hardcode tab names. It lists the tabs, then finds the
one holding the restaurant table (has an "Include" header) and the one holding
the settings table (has a "Setting" header). Renaming a tab therefore doesn't
break the run.
"""

import os
import re
from dataclasses import dataclass

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CONTROL_PANEL_ID = os.environ.get(
    "CONTROL_PANEL_ID", "1-8ep5svVmINDD0pzp4BciRai0zYsnlSXiZCzeO0ZSwU"
)

# Fallbacks used only if the Control Panel omits the setting.
DEFAULTS = {
    "sheet tab name": "Rapport Jour New",
    "run hour (paris time)": "7",
}


@dataclass
class Location:
    code: str
    name: str
    spreadsheet_id: str


def _sheets_service():
    """Build a Sheets client from the service-account JSON in the environment.

    GOOGLE_SERVICE_ACCOUNT_JSON holds the key file's *contents* (a GitHub secret),
    not a path — CI runners have no persistent filesystem to put a key file on.
    """
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. In GitHub Actions this comes "
            "from repository secrets; locally, export it before running."
        )
    import json

    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _tab_titles(service, spreadsheet_id: str) -> list:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
    ).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def _read_tab(service, spreadsheet_id: str, tab: str) -> list:
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab}'!A1:Z100",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return resp.get("values", [])


def _cell(row, i):
    return (row[i] if len(row) > i else "").strip()


_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")


def extract_spreadsheet_id(url: str):
    m = _SHEET_ID_RE.search(url or "")
    return m.group(1) if m else None


def load_config(service=None):
    """Return (locations, settings, service).

    locations: list[Location] for rows where Include is truthy.
    settings:  dict of lowercased setting name -> value.
    """
    service = service or _sheets_service()
    locations, settings = [], {}

    for tab in _tab_titles(service, CONTROL_PANEL_ID):
        rows = _read_tab(service, CONTROL_PANEL_ID, tab)
        if not rows:
            continue
        header_idx = {}
        for i, row in enumerate(rows):
            cells = [c.strip().lower() for c in row]
            if "include" in cells and "code" in cells:
                header_idx = {c: j for j, c in enumerate(cells)}
                # restaurant rows follow the header until a blank Include cell
                for r in rows[i + 1:]:
                    include = _cell(r, header_idx.get("include", 0))
                    if not include:
                        break
                    if include.strip().upper() not in {"TRUE", "OUI", "YES", "1"}:
                        continue
                    url_key = next(
                        (k for k in header_idx if "url" in k or "sheet" in k), None
                    )
                    sid = extract_spreadsheet_id(_cell(r, header_idx.get(url_key, 3)))
                    if not sid:
                        continue
                    locations.append(
                        Location(
                            code=_cell(r, header_idx.get("code", 1)),
                            name=_cell(r, header_idx.get("restaurant name", 2)),
                            spreadsheet_id=sid,
                        )
                    )
                break
            if "setting" in cells and "value" in cells:
                s_col = cells.index("setting")
                v_col = cells.index("value")
                for r in rows[i + 1:]:
                    key = _cell(r, s_col).lower()
                    if key:
                        settings[key] = _cell(r, v_col)
                break

    for k, v in DEFAULTS.items():
        settings.setdefault(k, v)
    return locations, settings, service


def report_tab(settings) -> str:
    return settings.get("sheet tab name") or DEFAULTS["sheet tab name"]


def run_hour(settings) -> int:
    try:
        return int(str(settings.get("run hour (paris time)")).strip())
    except (TypeError, ValueError):
        return 7
