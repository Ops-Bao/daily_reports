"""
The one place an LLM touches the morning output.

It writes the one- or two-sentence description of how each service went, which
sits under each MIDI / SOIR line in the recap. That is all it does.

Why it is scoped that narrowly
------------------------------
Every number in the recap — the group total, the on-site / take-away /
delivery split, the percentages, the average ticket, the week comparison — is
computed in `recap.py` from values the extractor read verbatim. A model
producing those would be handing the ops team arithmetic nobody can check.
So the model gets the manager's free text and returns prose.

Even then the output is verified: `verify_numbers()` rejects any number that
does not appear somewhere in what we sent. Times and counts quoted out of a
manager's note are fine — they are copied, not invented — but a euro amount
that appears nowhere in the input is not, and the whole batch is dropped.

If anything fails — no API key, an API error, a hallucinated figure — the recap
falls back to the managers' own words and still goes out.
"""

import os
import re

MODEL = os.environ.get("AI_SUMMARY_MODEL", "claude-opus-5")

# An API key created at the organisation level is not tied to a workspace, and
# the API then refuses the request unless it is told which workspace to bill.
# Set this (it is an ID, not a secret) or use a workspace-scoped key instead.
WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()

SYSTEM = """Tu rédiges le récapitulatif quotidien de Bao Family, un groupe de
restaurants à Paris, à partir des notes écrites par les managers.

Pour CHAQUE service qu'on te donne, écris UNE à DEUX phrases en français qui
résument comment le service s'est passé : l'affluence et son rythme, ce qui a
bien marché, et tout incident ou point d'attention. Style factuel et
opérationnel, comme un chef de service qui débriefe — pas de superlatifs, pas
de conclusion, pas de recommandation.

Règles absolues :
- N'INVENTE AUCUN CHIFFRE. Tu peux reprendre un horaire ou un nombre déjà
  présent dans la note du manager ("rush à 12h30", "table de 11"), mais tu ne
  calcules rien et tu n'ajoutes aucun montant.
- Ne parle pas du chiffre d'affaires ni des pourcentages : ils sont affichés
  au-dessus de ta phrase, les répéter est inutile.
- Si la note du manager est vide ou dit seulement RAS, écris exactement :
  Service sans particularité.

Réponds UNIQUEMENT avec un objet JSON, sans texte autour, de la forme :
{"CODE|midi": "…", "CODE|soir": "…"}
en utilisant exactement les clés fournies dans la demande."""

# Counts the model legitimately derives rather than copies: "3 sites",
# "2 ruptures", "le 22". Anything larger has to be traceable to the input.
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
    elif isinstance(node, str):
        # Narratives are full of numbers that are not figures — "12h40",
        # "table 19", "2 tickets". Quoting one back is not an invention, so
        # every number we sent counts, whatever field it arrived in. What the
        # guard still catches is a number that appears nowhere in the input:
        # a fabricated euro amount, or a total the model added up itself.
        for tok in _NUM_RE.findall(node):
            v = _norm_number(tok)
            if v is not None:
                out.add(v)


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


def _json_dump(payload) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

def _explain(e) -> str:
    """Error text plus, where we recognise it, the fix — the alert lands in
    Slack at 6am and should not need a developer to decode it."""
    msg = f"{type(e).__name__}: {e}"
    hints = {
        "anthropic-workspace-id": (
            "la clé API n'est pas rattachée à un workspace — définir la "
            "variable ANTHROPIC_WORKSPACE_ID, ou créer une clé dans un "
            "workspace"),
        "credit balance": "le solde de crédits Anthropic est épuisé",
        "authentication_error": "la clé API est invalide ou révoquée",
        "rate_limit": "limite de débit atteinte, le prochain run réessaiera",
    }
    for needle, hint in hints.items():
        if needle in msg:
            return f"{hint} ({type(e).__name__})"
    return msg

def _parse_json_object(text: str):
    """Pull the JSON object out of the reply, tolerating stray prose around it."""
    import json
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def service_prose(results, target_date):
    """Return (prose, problem) where prose is {"CODE|midi": "…"} or None.

    Exactly one of the two is None. `problem` is a short operator-facing string
    so the alert says what to do rather than dumping a stack trace at 6am.
    """
    if os.environ.get("AI_SUMMARY_DISABLED", "").lower() in {"1", "true", "yes"}:
        return None, None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, None          # not configured: stay silent, not an error

    payload = build_payload(results, target_date)
    if not payload["sites"]:
        return None, None

    keys = [f"{s['code']}|{shift}" for s in payload["sites"]
            for shift in ("midi", "soir")]

    try:
        import anthropic
    except ImportError:
        return None, "le paquet anthropic n'est pas installé"

    prompt = (
        f"Services du {payload['date']}. Rédige une entrée pour chacune de ces "
        f"{len(keys)} clés :\n{', '.join(keys)}\n\n"
        "Notes des managers et contexte :\n"
        f"{_json_dump(payload)}\n\n"
        "Réponds avec le seul objet JSON."
    )

    try:
        client = anthropic.Anthropic(
            default_headers={"anthropic-workspace-id": WORKSPACE_ID}
            if WORKSPACE_ID else None,
        )
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:                     # never let the recap fail for this
        return None, _explain(e)

    if response.stop_reason == "refusal":
        return None, "la génération a été refusée par le modèle"

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    prose = _parse_json_object(text)
    if not isinstance(prose, dict) or not prose:
        return None, "réponse illisible (JSON attendu)"

    prose = {k: " ".join(str(v).split()) for k, v in prose.items()
             if isinstance(v, str) and v.strip()}

    bad = verify_numbers(" ".join(prose.values()), allowed_numbers(payload))
    if bad:
        # Fail closed: a figure nobody can trace is worse than no prose.
        # Print the draft to the job log (never to Slack) — without it you
        # cannot tell a hallucination from a guard that is too strict.
        print("--- prose rejetée ---\n" + text + "\n---------------------")
        return None, ("chiffres non vérifiables dans le résumé : "
                      + ", ".join(bad[:5]))

    missing = [k for k in keys if k not in prose]
    if missing:
        # Not fatal: recap.py falls back to the manager's own words per service.
        print(f"prose manquante pour {len(missing)} service(s): "
              f"{', '.join(missing[:6])}")

    return prose, None
