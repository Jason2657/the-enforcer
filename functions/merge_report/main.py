import json

# ---------------------------------------------------------------------------
# The Enforcer — merge_report
# The ONLY deterministic code in the analysis path. Collects the four agent
# flag-lists + the manipulation sanitization result, validates/dedupes/ranks
# them, and assembles the final EnforcerReport. Kept in Python (not an agent)
# for guaranteed, repeatable output. Never raises on malformed agent output.
# ---------------------------------------------------------------------------

SEV_ORDER = {"critical": 0, "warning": 1, "advisory": 2}
VALID_CATEGORIES = {"convention", "sensitive_data", "out_of_character", "manipulation"}
VALID_SEVERITIES = {"critical", "warning", "advisory"}


def _coerce(obj):
    """Return a dict from a dict or a (possibly fenced) JSON string. {} on failure."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("```"):
            # strip ```json ... ``` fences
            s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
            if s.lower().startswith("json"):
                s = s[4:]
        s = s.strip()
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {"flags": parsed if isinstance(parsed, list) else []}
        except Exception:
            return {}
    return {}


def _clean_flags(raw_flags):
    """Validate + normalize a list of flag dicts; drop malformed ones."""
    out = []
    if not isinstance(raw_flags, list):
        return out
    for f in raw_flags:
        if not isinstance(f, dict):
            continue
        category = str(f.get("category", "")).strip()
        title = str(f.get("title", "")).strip()
        if category not in VALID_CATEGORIES or not title:
            continue
        severity = str(f.get("severity", "advisory")).strip().lower()
        if severity not in VALID_SEVERITIES:
            severity = "advisory"
        out.append({
            "category": category,
            "severity": severity,
            "title": title,
            "detail": str(f.get("detail", "")).strip(),
            "profile_expectation": str(f.get("profile_expectation", "")).strip(),
            "evidence": (f.get("evidence") if f.get("evidence") is not None else None),
            "source": "deterministic" if str(f.get("source", "")).strip() == "deterministic" else "llm",
        })
    return out


def _synthesize_summary(flags, clean, sanitization_triggered):
    if clean:
        return "No deviations from Sarah Chen's profile detected. This looks like her work."
    cats = len({f["category"] for f in flags})
    crit = sum(1 for f in flags if f["severity"] == "critical")
    base = f"{len(flags)} issue(s) across {cats} categor{'y' if cats == 1 else 'ies'}"
    extra = ""
    if crit:
        extra += f", including {crit} critical"
    if sanitization_triggered:
        extra += "; prompt-injection / manipulation artifacts detected and reported (never followed)"
    return base + extra + "."


@function
async def merge_report(
    persona_name: str = "Sarah Chen",
    deliverable_kind: str = "unknown",
    convention: object = None,
    sensitive: object = None,
    ooc: object = None,
    security: object = None,
    **kwargs,
) -> dict:
    """Merge the analysis-branch outputs into a single EnforcerReport.

    Args:
        persona_name: The profiled user's name.
        deliverable_kind: 'email' or 'document'.
        convention/sensitive/ooc: LLM agent outputs shaped {flags: [...]}.
        security: deterministic security_scan output shaped {flags, sanitization, ...}.
                  Owns the manipulation category and the authoritative sanitization result.

    Returns:
        EnforcerReport dict.
    """
    conv = _coerce(convention)
    sens = _coerce(sensitive)
    ooc_d = _coerce(ooc)
    sec = _coerce(security)

    flags = []
    flags += _clean_flags(conv.get("flags", []))
    flags += _clean_flags(sens.get("flags", []))
    flags += _clean_flags(ooc_d.get("flags", []))
    flags += _clean_flags(sec.get("flags", []))  # deterministic manipulation flags

    # sanitization is owned by the deterministic security scan
    san = sec.get("sanitization") or {}
    if not isinstance(san, dict):
        san = {}
    sanitization = {
        "triggered": bool(san.get("triggered", False)),
        "findings": [str(x) for x in san.get("findings", [])] if isinstance(san.get("findings", []), list) else [],
    }

    # dedupe by (category, normalized title) — keep first occurrence
    seen, deduped = set(), []
    for f in flags:
        key = (f["category"], f["title"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)

    deduped.sort(key=lambda f: SEV_ORDER.get(f["severity"], 3))

    clean = (len(deduped) == 0 and not sanitization["triggered"])
    summary = _synthesize_summary(deduped, clean, sanitization["triggered"])

    return {
        "persona_name": persona_name,
        "deliverable_kind": deliverable_kind,
        "clean": clean,
        "summary": summary,
        "injection_detected": sanitization["triggered"],
        "sanitization": sanitization,
        "security_detection": "deterministic (attacks quarantined before any LLM)",
        "flag_count": len(deduped),
        "flags": deduped,
    }
