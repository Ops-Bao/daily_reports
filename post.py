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


def format_service(data: dict, shift: str) -> str:
    fin = data["finance"]

    manager = _txt(data["staff"]["manager"][shift])
    ca_ht = fin["ca_ht"][shift]
    ca_ht_pct = data["ca_ht_wow_pct"][shift]
    tm_on_site = _txt(data["tm_ht_on_site"][shift])
    panier = fin["panier_outside"][shift]
    top3 = _txt(data["top3"][shift])
    general = _txt(data["narrative"]["general"][shift])
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
