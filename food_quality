"""
"Food quality report" — food-quality focus, for F&B.

qualite_food is always shown. The other fields (TOP 3, GLITCH, FOH, BOH,
COMMENTAIRES, GENERAL, BESOIN, RUPTURES) frequently CONTAIN food-quality signal
but are mixed with other operational content. A deterministic script cannot
reliably decide "is this line about food quality?" from free text, so by default
this report SURFACES those fields raw for the food person to read.

An optional AI hook (commented out below) can filter each field down to only its
food-quality-relevant content. Same principle as the GENERAL summary: the model
only reads/filters prose — there are no numbers to protect here.
"""

from extract_report import load_grid_from_csv, extract


# Fields to scan for food-quality signal, in display order.
# key path within the extracted `data` dict -> display label
SCAN_FIELDS = [
    (("operations", "qualite_food"), "QUALITE FOOD"), # always primary
    (("operations", "ruptures"), "RUPTURES"),
    (("operations", "besoin"), "BESOINS"),
    (("top3",), "TOP 3"),
    (("narrative", "glitch"), "GLITCH"),
    (("narrative", "foh"), "FOH"),
    (("narrative", "boh"), "BOH"),
    (("narrative", "commentaires"), "COMMENTAIRES"),
    (("narrative", "general"), "GENERAL"),
]


def _get(data, path, shift):
    node = data
    for k in path:
        node = node.get(k, {})
    val = (node.get(shift) or "").strip() if isinstance(node, dict) else ""
    return val


def _is_empty(val):
    return not val or val.upper() in {"N/A", "RAS", "//", "-"}


# ---------------------------------------------------------------------------
# OPTIONAL AI HOOK — food-quality filtering
# ---------------------------------------------------------------------------
# When ready, uncomment and route each scanned field through this. It returns
# ONLY the food-quality-relevant portion, or "" if none. This turns the report
# from "read these raw fields" into "here is exactly the food-quality signal".
#
# import os, json, urllib.request
#
# def filter_food_quality(field_label: str, text: str) -> str:
#     if _is_empty(text):
#         return ""
#     prompt = (
#         "Tu prépares un rapport QUALITÉ PRODUIT / FOOD pour le responsable food. "
#         "Extrais UNIQUEMENT ce qui concerne la qualité des plats, des produits, "
#         "des ingrédients, la fraîcheur, les ruptures produit, ou les retours "
#         "clients sur la nourriture. Ignore le service, la salle, la logistique "
#         "non-food. Si rien n'est pertinent, réponds exactement 'RAS'. "
#         f"Champ source : {field_label}.\n\nTexte :\n{text}"
#     )
#     req = urllib.request.Request(
#         "https://api.anthropic.com/v1/messages",
#         data=json.dumps({
#             "model": "claude-sonnet-5",
#             "max_tokens": 300,
#             "messages": [{"role": "user", "content": prompt}],
#         }).encode("utf-8"),
#         headers={
#             "x-api-key": os.environ["ANTHROPIC_API_KEY"],
#             "anthropic-version": "2023-06-01",
#             "content-type": "application/json",
#         },
#     )
#     with urllib.request.urlopen(req) as resp:
#         out = json.loads(resp.read())
#     res = "".join(b.get("text", "") for b in out["content"]
#                   if b["type"] == "text").strip()
#     return "" if res.upper() in {"RAS", ""} else res


def format_food_quality(data: dict, shift: str) -> str:
    lines = [f"*Service: {shift.upper()}*"]
    any_content = False
    for path, label in SCAN_FIELDS:
        val = _get(data, path, shift)

        # --- AI filtering (optional): replace the raw passthrough below ---
        # val = filter_food_quality(label, val)
        # if not val:
        #     continue
        # any_content = True
        # lines.append(f"{label}: {val}")
        # -----------------------------------------------------------------

        # Default deterministic passthrough: show qualite_food always,
        # other fields only when non-empty (so the food person isn't
        # wading through "//" and "RAS").
        if label == "QUALITE FOOD":
            lines.append(f"{label}: {val if not _is_empty(val) else 'RAS'}")
            any_content = True
        elif not _is_empty(val):
            lines.append(f"{label}: {val}")
            any_content = True

    if not any_content:
        lines.append("_Aucun signal qualité food ce service._")
    return "\n".join(lines)


def build_food_report(data: dict) -> str:
    header = f"🥢 *RAPPORT QUALITÉ FOOD — {data['meta']['restaurant']}* — {data['meta']['date']}"
    return (header + "\n\n"
            + format_food_quality(data, "midi") + "\n\n"
            + format_food_quality(data, "soir"))


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "/mnt/user-data/uploads/2026_-_PBBy_Suivi_de_performance_-_Rapport_Jour_New.csv"
    grid = load_grid_from_csv(path)
    data = extract(grid)
    print(build_food_report(data))
