"""
Flat per-service (MIDI / SOIR) digest formatter.

One block per service with the exact fields requested. All figures pulled
verbatim by the extractor. The GENERAL line currently passes the raw narrative
through; later, an LLM can condense it to the one-line summary style — that is
the ONLY place AI touches this, and it never sees or re-derives the numbers.
"""

from extract_report import load_grid_from_csv, extract


def _eur(v):
    return "N/A" if v is None else f"{v:,.2f} €".replace(",", " ").replace(".", ",")


def _pct(v):
    return "N/A" if v is None else f"{v:+.2f}%".replace(".", ",")


def _txt(v):
    v = (v or "").strip()
    return v if v else "N/A"


# ---------------------------------------------------------------------------
# OPTIONAL AI HOOK — GENERAL one-line summary
# ---------------------------------------------------------------------------
# When ready, uncomment and wire an API key. This feeds Claude the RAW narrative
# plus the ALREADY-EXTRACTED CA figures so it condenses to one line WITHOUT ever
# re-deriving numbers — it only rephrases prose. The numbers in the digest still
# come from the extractor, never from the model.
#
# import os, json, urllib.request
#
# def summarize_general(raw_general: str, ca_ht_eur: str, ca_ht_pct: str) -> str:
#     if not raw_general or raw_general.strip() in {"", "N/A", "RAS"}:
#         return "N/A"
#     prompt = (
#         "Résume ce rapport de service en UNE phrase concise (français), "
#         "en gardant le ton opérationnel. N'invente aucun chiffre. "
#         f"Chiffres officiels à réutiliser tels quels si pertinent : "
#         f"CA HT {ca_ht_eur}, variation vs S-1 {ca_ht_pct}.\n\n"
#         f"Rapport brut :\n{raw_general}"
#     )
#     req = urllib.request.Request(
#         "https://api.anthropic.com/v1/messages",
#         data=json.dumps({
#             "model": "claude-sonnet-5",
#             "max_tokens": 200,
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
#     return "".join(b.get("text", "") for b in out["content"] if b["type"] == "text").strip()


def format_service(data: dict, shift: str) -> str:
    fin = data["finance"]

    manager = _txt(data["staff"]["manager"][shift])
    ca_ht = fin["ca_ht"][shift]
    ca_ht_pct = data["ca_ht_wow_pct"][shift]
    tm_on_site = _txt(data["tm_ht_on_site"][shift])
    panier = fin["panier_outside"][shift]
    top3 = _txt(data["top3"][shift])
    general = _txt(data["narrative"]["general"][shift])
    # To condense GENERAL via AI later, replace the line above with:
    # general = summarize_general(
    #     data["narrative"]["general"][shift],
    #     _eur(ca_ht), _pct(ca_ht_pct),
    # )
    recep_ok = _txt(data["operations"]["reception_ok"][shift])
    recep_bad = _txt(data["operations"]["reception_bad"][shift])
    recep_comments = _txt(data["operations"]["reception_comments"][shift])
    besoin = _txt(data["operations"]["besoin"][shift])
    ruptures = _txt(data["operations"]["ruptures"][shift])
    glitch = _txt(data["narrative"]["glitch"][shift])

    if recep_ok != "N/A" and recep_bad != "N/A":
        recep_status = f"OK: {recep_ok} | BAD: {recep_bad}"
    elif recep_ok != "N/A":
        recep_status = f"OK — {recep_ok}"
    elif recep_bad != "N/A":
        recep_status = f"BAD — {recep_bad}"
    else:
        recep_status = "N/A"

    lines = [
        f"*Service: {shift.upper()}*",
        f"Responsable du site: {manager}",
        f"GENERAL: {general}",
        f"CA HT: {_eur(ca_ht)}",
        f"CA HT W-1: {_pct(ca_ht_pct)}",
        f"TM ON SITE: {tm_on_site}",
        f"PANIER OUTSIDE: {_eur(panier)}",
        f"TOP 3: {top3}",
        f"MERCHANDISE RECEPTION STATUS: {recep_status}",
        f"MERCH RECEPTION COMMENTS: {recep_comments}",
        f"BESOINS: {besoin}",
        f"RUPTURES: {ruptures}",
        f"GLITCH: {glitch}",
    ]
    return "\n".join(lines)


def build_message(data: dict) -> str:
    header = f"🍜 *{data['meta']['restaurant']}* — {data['meta']['date']}"
    return (header + "\n\n"
            + format_service(data, "midi") + "\n\n"
            + format_service(data, "soir"))


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "/mnt/user-data/uploads/2026_-_PBBy_Suivi_de_performance_-_Rapport_Jour_New.csv"
    grid = load_grid_from_csv(path)
    data = extract(grid)
    print(build_message(data))
