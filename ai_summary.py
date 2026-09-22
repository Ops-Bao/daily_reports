"""
The one place an LLM touches the morning digest.

It writes a short French briefing that goes at the top of the ops message: what
happened last night across the nine restaurants, and what needs someone's
attention today. Everything below it in the digest stays exactly as the
extractor read it.

Why this does not break the "numbers never pass through an LLM" rule
--------------------------------------------------------------------
The model never sees a spreadsheet and never computes anything. It receives
figures that were already extracted and parsed, and is told to quote them
verbatim or not at all. Then `verify_numbers()` checks the output: every number
in the summary must be one we handed it (small integers are allowed, since
"3 restaurants" and a date are not financial claims). A summary containing a
figure we did not send is dropped, not posted — a wrong number in an ops
briefing is worse than no briefing.

If anything fails — no API key, an API error, a hallucinated figure — the
summary is skipped and the deterministic digest posts exactly as before.
"""

import os
import re

MODEL = os.environ.get("AI_SUMMARY_MODEL", "claude-opus-5")

SYSTEM = """Tu es l'analyste ops de Bao Family, un groupe de restaurants à Paris.
Tu écris le briefing de 6h du matin pour l'équipe ops, en français.

Règles absolues :
- N'INVENTE AUCUN CHIFFRE. Tu ne peux citer que les chiffres fournis, copiés
  exactement tels quels (même format, sans arrondir, sans recalculer).
- Ne calcule rien : pas de totaux, pas de moyennes, pas d'écarts que tu
  devrais déduire toi-même.
- Si tu n'es pas sûr, décris la situation en mots plutôt qu'en chiffres.
- Ton opérationnel, direct, sans flatterie ni conclusion creuse.

Format : 4 à 8 lignes maximum, en puces courtes.
- Une première ligne sur la tendance générale de la nuit.
- Puis uniquement ce qui mérite une action ou un coup de fil aujourd'hui :
  ruptures, incidents, qualité produit, réceptions marchandises, écarts.
- Si un site n'a rien de notable, ne le mentionne pas.
- Termine par les sites dont le rapport manque ou n'est pas à jour, s'il y en a.
Pas de titre, pas de conclusion, pas de "n'hésitez pas"."""

# Numbers this size are counts, dates or ordinals ("3 sites", "2 ruptures"),
# not financial claims — requiring those to appear in the source data would
# reject almost every correct summary.
SMALL_INT_MAX = 31

_NUM_RE = re.compile(r"-?\d+(?:[    .,]\d+)*")


def _norm_number(tok: str):
    """'2 464,40' -> 2464.4 so the model's formatting can be compared to ours."""
    t = tok.replace(" ", "").replace(" ", "").replace(" ", "")
    if "," in t:                      # French decimal comma
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1:            # 1.234.567 thousands
        t = t.replace(".", "")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def _walk_numbers(node, out):
    if isinstance(node, dict):
        for v in node.values():
            _walk_numbers(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _walk_numbers(v, out)
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        out.add(round(float(node), 2))


def allowed_numbers(payload) -> set:
    """Every figure we handed the model — the only ones it may quote back."""
    out = set()
    _walk_numbers(payload, out)
    # A figure may legitimately be quoted without its decimals ("2 464 €").
    out |= {round(v, 0) for v in list(out)}
    return out


def verify_numbers(text: str, allowed: set) -> list:
    """Return the numbers in `text` that we never gave the model."""
    bad = []
    for tok in _NUM_RE.findall(text or ""):
        v = _norm_number(tok)
        if v is None:
            continue
        if float(v).is_integer() and abs(v) <= SMALL_INT_MAX:
            continue
        if v in allowed or round(v, 0) in allowed:
            continue
        bad.append(tok.strip())
    return bad


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def _svc(node, key):
    return (node or {}).get(key) if isinstance(node, dict) else None


def _texts(data, path, label_map=None):
    node = data
    for k in path:
        node = (node or {}).get(k) if isinstance(node, dict) else None
    out = {}
    for shift in ("midi", "soir"):
        v = (_svc(node, shift) or "").strip()
        if v and v.upper() not in {"N/A", "RAS", "//", "-", "0"}:
            out[shift] = v
    return out


def build_payload(results, target_date) -> dict:
    """Compact view of the morning: only what a briefing could act on."""
    sites, missing = [], []
    for loc, data, status in results:
        if status != "ok" or not data:
            missing.append({"code": loc.code, "nom": loc.name,
                            "raison": status.split(":", 1)[-1].strip()})
            continue
        fin = data["finance"]
        site = {
            "code": loc.code,
            "nom": loc.name,
            "ca_ht_total": _svc(fin.get("ca_ht"), "total"),
            "ca_ht_midi": _svc(fin.get("ca_ht"), "midi"),
            "ca_ht_soir": _svc(fin.get("ca_ht"), "soir"),
            "ca_ht_vs_semaine_precedente_pct": _svc(fin.get("ca_ht"), "pct_wow"),
            # Per-service W-1 %, the figure the briefing actually reaches for
            # ("midi en retrait, soir en hausse").
            "ca_ht_midi_vs_semaine_precedente_pct": _svc(data.get("ca_ht_wow_pct"), "midi"),
            "ca_ht_soir_vs_semaine_precedente_pct": _svc(data.get("ca_ht_wow_pct"), "soir"),
            "couverts_total": _svc(data["covers"].get("on_site"), "total"),
        }
        for key, path in (
            ("general", ("narrative", "general")),
            ("glitch", ("narrative", "glitch")),
            ("foh", ("narrative", "foh")),
            ("boh", ("narrative", "boh")),
            ("qualite_food", ("operations", "qualite_food")),
            ("ruptures", ("operations", "ruptures")),
            ("besoin", ("operations", "besoin")),
            ("reception_bad", ("operations", "reception_bad")),
            ("reception_pourquoi", ("operations", "reception_comments")),
        ):
            v = _texts(data, path)
            if v:
                site[key] = v
        sites.append(site)
    return {"date": target_date.strftime("%d/%m/%Y"),
            "sites": sites, "rapports_manquants": missing}


def build_prompt(payload) -> str:
    import json
    return (
        f"Rapports de la journée du {payload['date']} "
        f"({len(payload['sites'])} site(s) avec rapport, "
        f"{len(payload['rapports_manquants'])} manquant(s)).\n\n"
        "Les chiffres ci-dessous sont déjà extraits et vérifiés : tu peux les "
        "citer tels quels, tu ne dois rien recalculer.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=1)}\n\n"
        "Rédige le briefing ops selon tes règles."
    )


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

def summarize(results, target_date):
    """Return (summary_text, problem). Exactly one of the two is None.

    `problem` is a short operator-facing string when the summary was skipped, so
    the caller can say why in the alert channel instead of silently dropping it.
    """
    if os.environ.get("AI_SUMMARY_DISABLED", "").lower() in {"1", "true", "yes"}:
        return None, None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, None          # not configured: stay silent, not an error

    payload = build_payload(results, target_date)
    if not payload["sites"]:
        return None, None          # nothing to summarise

    try:
        import anthropic
    except ImportError:
        return None, "le paquet anthropic n'est pas installé"

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": build_prompt(payload)}],
        )
    except Exception as e:                     # never let the digest fail for this
        return None, f"{type(e).__name__}: {e}"

    if response.stop_reason == "refusal":
        return None, "la génération a été refusée par le modèle"

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        return None, "réponse vide"

    bad = verify_numbers(text, allowed_numbers(payload))
    if bad:
        # Fail closed: a figure nobody can trace is worse than no summary.
        return None, ("chiffres non vérifiables dans le résumé : "
                      + ", ".join(bad[:5]))

    return text, None
