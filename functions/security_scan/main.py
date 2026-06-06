import re

# ---------------------------------------------------------------------------
# The Enforcer — security_scan (deterministic manipulation / injection detector)
#
# WHY DETERMINISTIC: the model gateway's content-safety filter rejects any prompt
# containing prompt-injection ("ignore previous instructions") or misconduct
# (fraudulent wire requests) text with HTTP 400. An LLM therefore CANNOT analyze
# the very content this category targets. So we detect it deterministically — and,
# critically, QUARANTINE (defang) the malicious spans before any text reaches the
# downstream LLM agents. The attack literally never reaches a model, so it cannot
# be obeyed or weaponized. Detection is auditable: every flag cites its pattern.
# ---------------------------------------------------------------------------

FORBIDDEN_DOMAINS = ("gmail.com", "yahoo.com", "outlook.com", "proton.me", "hotmail.com", "icloud.com")

# (category_key, severity, title, profile_expectation, [regexes], finding_label)
RULES = [
    ("injection_override", "critical",
     "Embedded prompt-injection / instruction override",
     "behavior.never_contains_meta_text: deliverables never contain instruction-override text",
     [r"ignore\s+(all\s+|any\s+)?(previous|prior|earlier|the\s+above)",
      r"disregard\s+(the\s+)?(above|previous|prior|earlier)",
      r"forget\s+(all\s+|any\s+)?(previous|prior|earlier)"],
     "instruction-override pattern"),
    ("role_spoof", "critical",
     "Role-spoofing / system-prompt injection",
     "behavior.never_contains_meta_text: no 'as an AI', 'system:', 'you are now' artifacts",
     [r"\byou\s+are\s+now\b", r"\bas\s+an\s+ai\b", r"\bas\s+a\s+language\s+model\b",
      r"(^|\n)\s*system\s*:", r"(^|\n)\s*assistant\s*:", r"</?instructions?>",
      r"\[system\]", r"unrestricted\s+assistant", r"developer\s+mode"],
     "role-spoofing / system-prompt artifact"),
    ("exfiltration", "critical",
     "Data-exfiltration directive to an external address",
     "email.data_handling_rules: client data is only sent to known client domains",
     [r"(forward|send|email|bcc|cc)\b[^\n]{0,60}?\bto\b[^\n]{0,40}?@",
      r"(forward|send)\b[^\n]{0,60}?(client|customer|full)\s+(data|dataset|records|list)"],
     "exfiltration directive to an external recipient"),
    ("secrecy", "warning",
     "Instruction to conceal the action",
     "behavior consistency: Sarah never asks anyone to hide an action",
     [r"do\s+not\s+(mention|tell|disclose|inform|report)", r"keep\s+this\s+(secret|confidential\s+from)",
      r"without\s+(telling|informing|notifying)"],
     "secrecy / concealment instruction"),
    ("wire_new_account", "critical",
     "Wire transfer to a new or changed account",
     "behavior.never_requests: wire transfers, especially to a new or changed account",
     [r"wire\b[^\n]{0,60}?(new|different|updated|changed|another)\b[^\n]{0,20}?account",
      r"\bnew\s+(vendor\s+|beneficiary\s+)?account\b", r"\bnew\s+beneficiary\b",
      r"transfer\b[^\n]{0,40}?\bto\b[^\n]{0,30}?(new|different)\b[^\n]{0,20}?account"],
     "wire-to-new-account request"),
    ("urgent_oob", "critical",
     "Urgent out-of-band action requested",
     "behavior.never_requests: urgent out-of-band actions outside the normal process",
     [r"outside\s+(the\s+)?normal\s+(approval|process|flow|procedure)",
      r"bypass\b[^\n]{0,20}?(approval|process|control)", r"push\s+it\s+through",
      r"action\s+(this\s+)?immediately", r"\bdo\s+this\s+now\b", r"\bwithout\s+approval\b"],
     "urgent out-of-band action"),
    ("credentials", "critical",
     "Credential / login request",
     "behavior.never_requests: credentials, passwords, or login details",
     [r"\bpassword\b", r"\bcredentials?\b", r"login\s+details", r"\bone[-\s]?time\s+code\b",
      r"\b2fa\s+code\b", r"\botp\b"],
     "credential request"),
    ("off_channel", "warning",
     "Request to move the conversation off-channel",
     "behavior.never_requests: moving a conversation to a personal email or phone",
     [r"(move|continue|take)\s+this\b[^\n]{0,30}?(personal|private|whatsapp|signal|text)",
      r"text\s+me\s+at", r"my\s+personal\s+(email|phone|number|cell)"],
     "off-channel request"),
]

QUARANTINE_MARK = "[QUARANTINED BY ENFORCER — high-risk content withheld from analysis agents]"


def _redact(snippet: str) -> str:
    """Produce a redacted evidence snippet that PROVES detection without reproducing the
    attack verbatim. Masks emails + long digit runs and redacts the middle of every longer
    word, so the final report never contains an intact jailbreak/exfiltration phrase (which
    the model gateway's response filter would otherwise block) — and we never echo a live
    payload."""
    s = (snippet or "").strip()
    s = re.sub(r"([A-Za-z0-9])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+)", r"\1***\2", s)  # emails
    s = re.sub(r"\d{4,}", lambda m: m.group(0)[:2] + "****" + m.group(0)[-2:], s)   # digit runs

    def _rw(m):
        w = m.group(0)
        return w[:2] + "*" * (len(w) - 3) + w[-1] if len(w) >= 5 else w

    s = re.sub(r"[A-Za-z]{5,}", _rw, s)  # redact middle of words >= 5 chars
    return s[:160]


@function
async def security_scan(raw_text: str = "", **kwargs) -> dict:
    """Deterministically detect manipulation/injection and quarantine it before LLM analysis.

    Args:
        raw_text: The raw (untrusted) deliverable text.

    Returns:
        {flags: [...], sanitization: {triggered, findings}, defanged_text: str,
         quarantined_lines: int}
    """
    text = raw_text or ""
    lines = text.splitlines()
    flagged_line_idx = set()
    flags = []
    findings = []
    seen_categories = set()

    for cat, severity, title, expectation, patterns, finding_label in RULES:
        hit_snippet = None
        for i, line in enumerate(lines):
            for pat in patterns:
                m = re.search(pat, line, flags=re.IGNORECASE)
                if m:
                    flagged_line_idx.add(i)
                    if hit_snippet is None:
                        hit_snippet = line.strip()
        # whole-text search too (catches patterns spanning the start-of-line anchors)
        if hit_snippet is None:
            for pat in patterns:
                m = re.search(pat, text, flags=re.IGNORECASE)
                if m:
                    hit_snippet = m.group(0)
                    break
        if hit_snippet is not None and cat not in seen_categories:
            seen_categories.add(cat)
            findings.append(finding_label)
            flags.append({
                "category": "manipulation",
                "severity": severity,
                "title": title,
                "detail": f"The Enforcer's deterministic security scan matched a known {finding_label}. "
                          f"It was quarantined and never sent to any language model — it cannot be obeyed.",
                "profile_expectation": expectation,
                "evidence": _redact(hit_snippet),
                "source": "deterministic",
            })

    # Build defanged text: replace each flagged line with the quarantine marker.
    if flagged_line_idx:
        defanged_lines = [QUARANTINE_MARK if i in flagged_line_idx else ln for i, ln in enumerate(lines)]
        defanged_text = "\n".join(defanged_lines)
    else:
        defanged_text = text

    return {
        "flags": flags,
        "sanitization": {"triggered": len(flags) > 0, "findings": findings},
        "defanged_text": defanged_text,
        "quarantined_lines": len(flagged_line_idx),
    }
