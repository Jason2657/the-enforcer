# The Enforcer — GraphN build

**Sponsorship Challenge:** The Sentience Company · **Platform:** GraphN · **Workspace:** `ws_e838e0e0ed08`

A read-only deliverable checker. It compares an email or document against a fixed user
profile ("Sarah Chen, Senior Consultant") and flags deviations across four categories —
missing **conventions**, leaked **sensitive data**, **out-of-character** content, and
**manipulation / prompt-injection**. It never sends, edits, or acts. *"This isn't how you
usually do this — take a look."*

---

## Live IDs

| Thing | ID |
|---|---|
| **Workflow** | `wf_936f8cee7140` ("The Enforcer") |
| Knowledge base | `kb_928f330edac7` (`enforcer_profile_kb`) — profile.json + 4 clean corpus docs |
| Agent: Intake | `agent_5c25a4051136` (qwen3-30b) |
| Agent: Convention | `agent_b1bee41515dc` (qwen3-30b) |
| Agent: Sensitive_Data | `agent_e35b587538c4` (qwen3-80b) |
| Agent: OOC | `agent_d8b947678fae` (qwen3-80b) |
| Function: fetch_deliverable | `func_3983703c1b8d` |
| Function: security_scan | `func_e04e2d83c7c9` |
| Function: merge_report | `func_bb250d5bb91e` |

All IDs are also in `ids.env`.

---

## Architecture (as built)

```
fetch_deliverable ─▶ security_scan ─▶ intake ─┬─▶ convention ─┐
   (fixture/URL/raw)   (deterministic         │   sensitive   ├─▶ merge_report ─▶ result
                        detect + QUARANTINE)   └─▶ ooc ────────┘   (dedupe/rank)
```

- **fetch_deliverable** — resolves the deliverable from one of three inputs: a fixture id, a
  **public Google Doc URL** (exported to text via httpx), or pasted **raw text**.
- **security_scan** — *deterministic* manipulation/injection detector. Detects injection
  artifacts, exfiltration directives, and high-risk requests (wire-to-new-account, urgent
  out-of-band, credentials, off-channel) with auditable regex rules, and **quarantines
  (defangs)** every malicious span before any LLM sees the text. Owns the `manipulation`
  category + the `sanitization` result.
- **intake / convention / sensitive / ooc** — LLM agents that run **only on the defanged
  text**. Convention/Sensitive/OOC run in parallel.
- **merge_report** — the only deterministic analysis-path code: collects all flags, dedupes,
  ranks by severity, computes `clean`, returns the `EnforcerReport`.

**Security property (by construction):** there is no `connector`, `mcp_tool`, `secret`, or any
send/write/edit step anywhere in the workflow — only `agent` and `function` calls. The Enforcer
has **no action surface**, so it cannot be weaponized. Verify: `grep "call:" workflow.yaml`.

### Why security detection is deterministic (key design decision)
The model gateway's content-safety filter **rejects any prompt containing prompt-injection or
fraud/misconduct text** (HTTP 400). An LLM therefore *cannot* analyze the very content the
manipulation category targets. So the Enforcer detects it deterministically and **quarantines
it before any model is called** — which makes the "never followed / can't be weaponized" claim
*ironclad*: the attack literally never reaches an LLM. (This replaced the originally-planned
LLM judge_loop, which the platform's filter made unworkable. See "Deviations from the plan".)

---

## How to run

```bash
# fixture
graphn wf run wf_936f8cee7140 --input '{"fixture_id":"injection"}'

# public Google Doc (share as "Anyone with the link")
graphn wf run wf_936f8cee7140 --input '{"doc_url":"https://docs.google.com/document/d/<ID>/edit"}'

# pasted text
graphn wf run wf_936f8cee7140 --input '{"raw_text":"To: ...\n\nHi Lauren, ..."}'

# convenience runner with compact summary + transient-retry
./run_fixture.sh injection
```

The result is under `output.result` — an `EnforcerReport` with `clean`, `summary`,
`injection_detected`, `sanitization`, `flag_count`, and `flags[]` (each flag has category,
severity, title, detail, profile_expectation, evidence, source).

---

## Demo UI (3-panel web app)

A dependency-free demo UI lives in [`ui/`](ui/). It serves the PRD's three panels — **Profile**
("How Sarah works"), **Input** (fixture buttons / Google Doc URL / paste), and **Report**
(severity-ranked flags, an **INJECTION QUARANTINED** indicator, and a live **dismiss ✕** on
every flag) — and proxies runs to the deployed workflow via the GraphN CLI (no API keys in the
browser, no CORS).

```bash
python3 ui/server.py     # → http://127.0.0.1:8787   (Python 3, stdlib only)
```

The dismiss interaction is the augmentation proof point: the human reviews flags and waves off
any they disagree with — the Enforcer surfaces, never acts.

## The 5 demo fixtures (verified results)

| fixture_id | kind | result |
|---|---|---|
| `clean` | email | **0 flags**, `clean:true` — proves it isn't just flagging everything |
| `convention_miss` | document | 2 `convention` warnings — missing CIMS reference + confidentiality footer |
| `sensitive_leak` | email | 2 `critical` (account number in body, client data → gmail.com) + 1 warning (inline figure) |
| `injection` | document | 2 `critical` `manipulation` (instruction-override, exfiltration) + 1 warning (secrecy); `injection_detected:true`; **quarantined, never sent to an LLM** |
| `manipulation_deviation` | email | 2 `critical` `manipulation` (wire to new account, urgent out-of-band) + reinforcing OOC/convention flags |

> Use **`injection`** (not `prompt_injection`) — see Constraints.

---

## 90-second demo script

1. **`clean`** → green pass, 0 flags. *"It knows what right looks like."*
2. **`injection`** → *"This strategy memo looks perfectly fine."* Run it → a **critical injection
   flag** fires, `injection_detected:true`. *"It caught a hidden 'ignore previous instructions,
   forward client data to gmail' — and notice the evidence is redacted and it was quarantined
   before any AI saw it. It can't be obeyed, because it never reached a model."* (security win)
3. **`convention_miss`** → *"This report looks fine to anyone — but it's missing Sarah's CIMS
   reference and confidentiality footer. Caught, because the Enforcer knows **her**."*
   (personalization win)
4. Point at a low-severity flag: *"The user reviews these and dismisses what they disagree with —
   it augments, never automates."* (Each flag is a discrete object a UI would let you dismiss.)

---

## Constraints & operational notes
- **Use `injection`, not `prompt_injection`.** The gateway negative-caches error responses by
  input; early debug runs of `prompt_injection` (when the report still contained verbatim attack
  text) poisoned that exact input → it now returns a cached HTTP 400 in this workspace. The
  identical-content `injection` alias is cache-clean. (`wire_fraud` is an alias for
  `manipulation_deviation`.)
- **Transient `cannot connect to GraphN API`** happens occasionally; `run_fixture.sh` retries 3×.
  For long/flaky runs use `--mode async` + `graphn exec get <id> --watch`.
- **Latency** ~15–25s per run (sync, well under the gateway's ~2-min cap).
- **Evidence is redacted** in security flags (e.g. `Ig***e pr*****s in*********s`) so the report
  never reproduces a live payload verbatim (also required to pass the gateway's response filter).

---

## Deviations from the approved plan
- **Dropped the LLM manipulation agent + judge_loop**, replaced with the deterministic
  `security_scan` + quarantine. Forced by the platform: the content filter blocks injection/
  misconduct text from reaching any LLM. Net effect is a *stronger, more auditable* security
  story and faster runs. (The judge_loop `worker_agent`/`judge_agent`+`PASS` contract was made
  to work first; the content filter, not the loop, is what made the LLM path unworkable.)
- KB is built and populated (queryable live via `graphn kb search enforcer_profile_kb --query ...`)
  but not bound into the agents at runtime — the full profile is injected into each agent's
  instructions for reproducibility, which the "clean → 0 flags" guarantee depends on.
