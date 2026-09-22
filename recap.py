"""
RECAP QUOTIDIEN — the executive view of the morning.

Layout mirrors the recap the ops team asked for: a group overview, then one
block per restaurant with a MIDI and a SOIR line.

Division of labour, and the reason for it
-----------------------------------------
Every number here is computed in Python from values the extractor read
verbatim: the group total, the on-site / take-away / delivery split and its
percentages, the global average ticket, the week-to-date comparison. An LLM
computing those would be producing figures nobody can check, and a wrong total
in a recap is worse than no recap.

The model contributes exactly one thing: the one- or two-sentence description
of how each service went, condensed from the manager's own free text. That is
prose, and prose is what it is good at.

`prose` is a dict keyed "CODE|midi" / "CODE|soir". Missing keys simply render
the manager's raw narrative instead, so the recap still works with no API key.
"""

SEP_LINE = "────────────────────────────"


def _eur(v):
    if v is None:
        return "N/A"
    return f"{v:,.2f} €".replace(",", " ").replace(".", ",")


def _pct(v, signed=False):
    if v is None:
        return "N/A"
    fmt = f"{v:+.2f}%" if signed else f"{v:.2f}%"
    return fmt.replace(".", ",")


def _get(data, path, shift=None):
    node = data
    for k in path:
        node = (node or {}).get(k) if isinstance(node, dict) else None
    if shift is None:
        return node
    return (node or {}).get(shift) if isinstance(node, dict) else None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _sum(values):
    """Sum, ignoring blanks — but None if nothing was readable at all, so an
    empty column shows as N/A instead of a confident 0,00 €."""
    vals = [v for v in values if _num(v) is not None]
    return sum(vals) if vals else None


def _share(part, whole):
    if _num(part) is None or not _num(whole):
        return None
    return part / whole * 100.0


# ---------------------------------------------------------------------------
# Group overview
# ---------------------------------------------------------------------------

def totals(oks) -> dict:
    """Group aggregates. `oks` is [(loc, data), ...] for locations that parsed."""
    ca = [_get(d, ("finance", "ca_ht", "total")) for _, d in oks]
    on = [_get(d, ("finance", "ca_ht_on_site", "total")) for _, d in oks]
    ta = [_get(d, ("finance", "ca_ht_take_away", "total")) for _, d in oks]
    de = [_get(d, ("finance", "ca_ht_delivery", "total")) for _, d in oks]
    cov = [_get(d, ("covers", "on_site", "total")) for _, d in oks]
    wtd = [_get(d, ("finance", "ca_ht", "wtd")) for _, d in oks]
    wtd_prior = [_get(d, ("finance", "ca_ht", "wtd_prior")) for _, d in oks]

    ca_total = _sum(ca)
    on_t, ta_t, de_t = _sum(on), _sum(ta), _sum(de)
    ventile = _sum([on_t, ta_t, de_t])
    cov_t = _sum(cov)
    w, wp = _sum(wtd), _sum(wtd_prior)

    return {
        "ca_ht_total": ca_total,
        "on_site": on_t, "take_away": ta_t, "delivery": de_t,
        "ventile": ventile,
        "on_site_pct": _share(on_t, ventile),
        "take_away_pct": _share(ta_t, ventile),
        "delivery_pct": _share(de_t, ventile),
        "couverts": cov_t,
        # Average ticket is on-site revenue over on-site covers: mixing in
        # take-away would divide by a count that does not include it.
        "tm_on_site": (on_t / cov_t) if (_num(on_t) and _num(cov_t)) else None,
        "wtd": w, "wtd_prior": wp,
        "wtd_pct": ((w - wp) / wp * 100.0) if (_num(w) and _num(wp)) else None,
    }


def _overview(oks, t) -> list:
    lines = ["*VUE D'ENSEMBLE*", "CA HT par restaurant :"]
    for loc, d in oks:
        name = _get(d, ("meta", "restaurant")) or loc.name
        lines.append(f"• {name} : {_eur(_get(d, ('finance', 'ca_ht', 'total')))}")
    lines.append(f"*CA HT total ({len(oks)} restaurants) : {_eur(t['ca_ht_total'])}*")
    lines.append(
        f"Répartition (sur total ventilé {_eur(t['ventile'])}) : "
        f"sur place {_eur(t['on_site'])} ({_pct(t['on_site_pct'])}) · "
        f"à emporter {_eur(t['take_away'])} ({_pct(t['take_away_pct'])}) · "
        f"livraison {_eur(t['delivery'])} ({_pct(t['delivery_pct'])})"
    )
    couverts = "N/A" if t["couverts"] is None else f"{int(t['couverts'])}"
    lines.append(
        f"Ticket moyen global (sur place) : {_eur(t['tm_on_site'])} "
        f"({couverts} couverts sur place)"
    )
    # Labelled for what it actually is. The sheets carry week-to-date and prior
    # week-to-date; a same-day-last-week total is not in them, and deriving one
    # by dividing through a rounded percentage would invent precision.
    lines.append(
        f"Semaine à date vs S-1 : {_pct(t['wtd_pct'], signed=True)} "
        f"({_eur(t['wtd'])} vs {_eur(t['wtd_prior'])})"
    )
    return lines


# ---------------------------------------------------------------------------
# Per-restaurant detail
# ---------------------------------------------------------------------------

def _fallback_prose(data, shift) -> str:
    """No model output: use the manager's own words, trimmed."""
    for path in (("narrative", "general"), ("narrative", "foh"),
                 ("narrative", "boh")):
        v = (_get(data, path, shift) or "").strip()
        if v and v.upper() not in {"RAS", "N/A", "//", "-"}:
            return " ".join(v.split())
    return "—"


def _service_line(data, shift) -> str:
    ca = _get(data, ("finance", "ca_ht"), shift)
    tm = (_get(data, ("tm_ht_on_site",), shift) or "").strip() or "N/A"
    on = _num(_get(data, ("finance", "ca_ht_on_site"), shift))
    ta = _num(_get(data, ("finance", "ca_ht_take_away"), shift))
    de = _num(_get(data, ("finance", "ca_ht_delivery"), shift))
    ventile = _sum([on, ta, de])
    return (
        f"*{shift.upper()}* — CA HT {_eur(ca)} | TM sur place {tm} | "
        f"sur place {_pct(_share(on, ventile))} / "
        f"à emporter {_pct(_share(ta, ventile))} / "
        f"livraison {_pct(_share(de, ventile))}"
    )


def _restaurant_block(loc, data, prose) -> str:
    name = _get(data, ("meta", "restaurant")) or loc.name
    lines = [f"*{name}*"]
    for shift in ("midi", "soir"):
        lines.append(_service_line(data, shift))
        text = (prose or {}).get(f"{loc.code}|{shift}") or _fallback_prose(data, shift)
        lines.append(text)
    return "\n".join(lines)


# ---------------------------------------------------------------------------

def header(target_date) -> str:
    return f"*RECAP QUOTIDIEN — BAO FAMILY* — {target_date:%d/%m/%Y}"


def build(results, target_date, prose=None) -> str:
    oks = [(loc, d) for loc, d, status in results if status == "ok" and d]
    missing = [(loc, status) for loc, _, status in results if status != "ok"]

    parts = [header(target_date),
             f"Date : {target_date:%Y-%m-%d} (services midi + soir cumulés)"]

    if not oks:
        parts.append("\n_Aucun rapport disponible ce matin._")
    else:
        parts.append("\n" + "\n".join(_overview(oks, totals(oks))))
        parts.append(SEP_LINE)
        parts.append("*DETAIL PAR RESTAURANT*")
        for loc, d in oks:
            parts.append(_restaurant_block(loc, d, prose))
            parts.append(SEP_LINE)

    if missing:
        parts.append("*Rapports manquants ou non à jour*\n" + "\n".join(
            f"⚠️ {loc.name} ({loc.code}) — {s.split(':', 1)[-1].strip()}"
            for loc, s in missing))

    return "\n".join(parts)
