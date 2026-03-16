# engram — Review Summary & Forward Plan

## What this document is

A consolidated record of the four-round review process engram went through, what was found, what got fixed, what's still open, and where we go from here.

---

## Review history

engram was reviewed four times by Nova, each round examining the latest state of the repo. The reviews got progressively more specific as early issues were addressed and deeper ones surfaced.

### Review 1 — Initial assessment

**Verdict:** *"Good bones, good instinct, but still too eager to believe its own summaries."*

**What looked strong:**
- Core premise (salience-gated capture vs. log-everything) is genuinely solid
- Architecture is unusually clean for an early repo — complete product shape, not a toy
- SNARC scorer is real, not vaporware — actual heuristic scoring with explicit weights
- Right instinct to avoid LLM calls in the hot path

**What looked weak:**
- Epistemic confidence — system re-injects its own compressed interpretations with no labeling of certainty
- No evaluation discipline — no benchmarks, no precision/recall, no ablations
- Thresholds tuned by taste, not evidence (0.3 salience, SNARC weights, confidence formulas)
- Pattern extraction is semantically shallow — catches surface routines, misses intent
- Automatic context injection is the highest-risk surface — wrong memory injected with authority creates self-reinforcing mistakes

**Recommended priorities (in order):**
1. Add evaluation before adding features
2. Separate observed fact from inferred pattern
3. Make injection conservative by default
4. Add explicit forgetting / decay / invalidation
5. Harden secret handling and project isolation

### Review 2 — After first round of fixes

**Verdict:** *"Good bones, better skepticism, still under-instrumented and a little sloppier than the new README posture implies."*

**What improved:**
- Reactive recall path described as conservative, session-start injection got stricter
- Epistemic labeling added (Tier 1 = "observed", Tier 2 = "inferred", Tier 3 = "verify if unsure")

**What didn't improve enough:**
- Reactive Tier 2 confidence filter looser than advertised — code comment promises more skepticism than the filter implements
- Probable double-decay bug in consolidation (`stmts.decayPatterns.run()` called twice per dream cycle)

**Still unresolved:**
- Evaluation framework
- Secret filtering / sensitive path scrubbing
- Provenance chain from Tier 2 patterns back to source observations
- Salience weight calibration from real usage data

### Review 3 — After second round of fixes

**Verdict:** *"Good bones, increasingly self-correcting, still not validated enough to trust at scale."*

**What improved:**
- Specific bugs from review 2 appeared fixed
- Project now shows a pattern of responding to critique with narrower, safer behavior — a strong sign of seriousness

**Assessment updated:**
- Concept: strong
- Implementation discipline: improving
- Safety posture: better, but incomplete
- Scientific / product validation: still weak

**The shift:** No longer "sloppy optimism with a good idea." Now closer to "promising memory substrate with early signs of engineering maturity, but still missing the evidence layer."

**Sharpest next move:** Build a tiny evaluation harness before adding anything else.

### Review 4 — After deep dream was added

**Verdict:** *"Valuable upgrade, risky memory fiction engine, keep it opt-in and quarantined until it earns trust."*

**Key concerns addressed:**
- Identity auto-promotion → quarantined. Deep dream identity facts go to Tier 2 as `proposed_identity`, not Tier 3
- Shell interpolation → fixed. Prompt passed via stdin instead of bash string interpolation
- Source ID validation → added. Fabricated IDs lower confidence by 0.2

**New risk introduced:**
- "Narrative overreach" — deep dream can produce output that looks like improvement while quietly reducing truthfulness (summary-on-summary epistemic risk)

**Still open:**
- Evaluation harness
- Deduplication of patterns across sessions
- Contradiction handling for conflicting patterns

---

## Current state of known issues

### Fixed
- [x] Epistemic labeling on injected memories
- [x] Conservative injection thresholds (patterns >= 0.6, observations >= 0.6, identity >= 0.7)
- [x] Confidence decay (patterns -0.05/day, pruned below 0.1)
- [x] Observation decay after 7 days
- [x] Identity quarantine for deep dream proposals
- [x] Shell interpolation vulnerability in deep dream
- [x] Source ID validation for deep dream output
- [x] Double-decay bug in consolidation
- [x] Reactive Tier 2 confidence filter

### Open
- [ ] **No evaluation harness** — no way to measure whether memory helps or hurts task outcomes. Flagged in all four reviews. The single most important gap.
- [ ] **No secret filtering** — tool I/O summaries may contain API keys, credentials, .env contents. Export amplifies this. No scrubbing policy exists.
- [ ] **No provenance chain** — Tier 2 patterns have `source_ids` but no real audit trail back to the observations that produced them. Can't inspect why a pattern exists.
- [ ] **No threshold calibration** — SNARC weights (0.25/0.20/0.20/0.25/0.10), salience threshold (0.3), confidence formulas all chosen by instinct. No evidence they're optimal or even approximately correct.
- [ ] **Shallow pattern extraction** — heuristic dream sees 3-step tool windows and file clusters. Can't distinguish TDD from debugging from refactoring. Sees shape, not intent.
- [ ] **Brittle error detection** — regex-based arousal scoring misses language-specific diagnostics and produces false positives on log lines containing error keywords in non-error contexts.
- [ ] **Incomplete fleet portability** — `importMarkdown()` is a stub that counts records and returns. Export works; import doesn't.
- [ ] **No test coverage** — zero tests. No regression protection against future changes breaking scoring, injection, or consolidation logic.
- [ ] **No pattern deduplication** — same pattern can be extracted across multiple sessions without merging.
- [ ] **No contradiction handling** — conflicting patterns from different sessions coexist without resolution.

---

## Forward plan

Ordered by impact and dependency. Each phase should be completed before moving to the next.

### Phase 1 — Prove it works (evaluation)

The single most important thing. Everything else is optimization of a system we can't yet prove is useful.

- [ ] **Build a minimal evaluation harness**
  - Define 5–10 representative tasks (bug fix, feature add, refactor, etc.)
  - Run each task with and without engram memory
  - Measure: relevant recall count, irrelevant injection count, task completion quality
  - Establish baseline numbers before any further feature work

- [ ] **Add harmful-recall regression tests**
  - Craft scenarios where stale or wrong memories would degrade performance
  - Verify injection thresholds prevent them from surfacing
  - Run as part of CI

- [ ] **Threshold sensitivity analysis**
  - Vary salience threshold (0.2, 0.3, 0.4, 0.5) across evaluation tasks
  - Vary SNARC weights
  - Find the configuration that maximizes useful recall and minimizes noise

### Phase 2 — Safety and hygiene

Before any user beyond us trusts this system with real projects.

- [ ] **Secret filtering**
  - Detect and redact common secret patterns (API keys, tokens, passwords, connection strings) before storing observation summaries
  - Strip `.env` file contents, credential file paths
  - Apply to both storage and export paths

- [ ] **Basic test suite**
  - Unit tests for SNARC scoring (known inputs → expected scores)
  - Integration tests for capture → store → retrieve pipeline
  - Regression tests for injection thresholds
  - Dream cycle tests (heuristic and deep)

- [ ] **Provenance tracking**
  - Tier 2 patterns should link back to specific Tier 1 observation IDs
  - CLI command to inspect a pattern's source observations
  - "Why did engram remember this?" should be answerable

### Phase 3 — Quality improvements

Make the memories better, not just safer.

- [ ] **Pattern deduplication**
  - Merge functionally equivalent patterns across sessions
  - Boost confidence when the same pattern is independently discovered

- [ ] **Contradiction handling**
  - Detect when a new pattern conflicts with an existing one
  - Lower confidence on the older pattern or flag for human review

- [ ] **Richer error detection**
  - Language-aware arousal scoring (Rust diagnostics, Go error returns, Python tracebacks with non-standard formatting)
  - Reduce false positives from log lines containing "error" in benign contexts

- [ ] **Complete fleet import**
  - Implement `importMarkdown()` for real — parse exported markdown, merge into local Tier 2/3
  - Handle conflicts between imported and local patterns

### Phase 4 — Calibration and maturity

Once we have evaluation data and real usage patterns.

- [ ] **Data-driven threshold tuning**
  - Use evaluation harness results to set SNARC weights and salience threshold empirically
  - Per-project calibration if usage patterns vary significantly

- [ ] **Deep dream quality controls**
  - Measure deep dream pattern accuracy against heuristic baseline
  - Tighten or loosen quarantine policy based on measured false-positive rate

- [ ] **Usage telemetry (opt-in, local)**
  - Track which injected memories get acted on vs. ignored
  - Feed back into scoring weights over time

---

## The throughline

Nova's consistent observation across four reviews: **the concept is right, the execution is improving, the evidence is absent.**

The forward plan addresses that directly. Phase 1 exists because nothing else matters until we can answer: *does remembered context help more than it distorts?*

Everything after Phase 1 assumes the answer is yes — or tells us specifically what to fix so it becomes yes.

---

*engram v0.3.0 — salience-gated memory for Claude Code*
