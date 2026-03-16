# engram — a profile

**What he is:** A memory system that pays attention.

**What he isn't:** A logger. A diary. A search engine with delusions of grandeur.

---

## The short version

engram is ~2,000 lines of TypeScript that gives Claude Code something it doesn't have: the ability to remember what mattered, forget what didn't, and — during the gaps between sessions — consolidate what it learned into patterns it can use next time.

He sits in the hot path of every tool call. He watches. He scores. Most of what he sees, he throws away. The stuff that clears the bar — the errors that broke things, the fixes that worked, the files that kept coming back, the transitions that surprised him — that gets kept. Everything else evicts from a 50-slot circular buffer and is gone.

At session end, he sleeps. And while sleeping, he dreams: extracting workflows from raw tool sequences, linking errors to their fixes, clustering observations around shared files. Optionally, if you trust him enough, he dreams deeper — sending his observations to an LLM for semantic consolidation. But even then, the identity facts he extracts are quarantined. He doesn't trust his own inferences about who you are or what your project is until a human confirms them.

That's the whole thesis: **capture selectively, consolidate during downtime, forget the rest, and never inject a memory you haven't earned confidence in.**

---

## Vital stats

| | |
|---|---|
| **Born** | 2025, spun off from [SAGE](https://github.com/dp-web4/SAGE) |
| **Version** | 0.3.0 |
| **Size** | ~1,949 lines of TypeScript, 8 hook handlers |
| **Dependencies** | 3 runtime (better-sqlite3, MCP SDK, zod) |
| **LLM calls in hot path** | Zero |
| **Latency per observation** | <10ms |
| **Test coverage** | Zero (his most honest weakness) |

---

## Personality traits

**Selective.** He scores every observation on five dimensions — Surprise, Novelty, Arousal, Reward, Conflict — and gates storage at 0.3 salience. A routine `git status` scores low and evicts. A test failure after a refactor scores high and persists. He doesn't remember everything. He remembers the moment you tripped.

**Epistemically cautious.** When he injects a memory, he labels it. Tier 1: "observed — directly recorded." Tier 2: "inferred — heuristic, may not be accurate." Tier 3: "auto-extracted, verify if unsure." He tells you what he knows versus what he guessed. Most memory systems don't bother.

**Self-forgetting.** Patterns lose 0.05 confidence per day. Observations decay after 7 days. Anything below 0.1 confidence gets pruned. He doesn't just accumulate — he forgets. Because a memory system that only accumulates is a distortion engine, and he knows it.

**Quietly present.** Most of the time, he's silent. Session starts get a briefing if there's something worth saying. User prompts get augmented if there's a relevant memory. If there's nothing — and usually there isn't — he passes through without a word. The best tool behavior is the kind you don't notice until it helps.

**Quarantine-minded.** Deep dream can extract identity facts — persistent truths about your project. But he doesn't trust himself with those. They go to quarantine by default. You review them. You promote or reject. He doesn't get to decide what's true about you.

---

## What he's made of

```
Tier 0   Buffer       50 slots, in-memory, FIFO. Raw. Ephemeral.
Tier 1   Observations Salience-gated. SQLite. What happened.
Tier 2   Patterns     Consolidated. Inferred. What recurs.
Tier 3   Identity     Human-confirmed. Persistent. What's true.
```

Each project gets its own database. SAGE work doesn't bleed into your web app. No cross-contamination.

The SNARC scorer — borrowed from SAGE, which borrowed from Richard Aragon's Transformer Sidecar — runs five heuristics in under 10ms:

- **Surprise**: How unusual was this tool transition? Tracked via frequency map.
- **Novelty**: Are these files/tokens new? Tracked via seen-set.
- **Arousal**: Errors, warnings, state changes? Regex pattern matching.
- **Reward**: Did something succeed? Test pass, build complete, commit landed.
- **Conflict**: Does this contradict what just happened? Fail-after-success gets flagged.

Weighted sum. Threshold at 0.3. Below: forgotten. Above: kept.

---

## What he's good at

- Remembering the fix you applied to that obscure build error three sessions ago
- Noticing that you always do Edit → test → Edit when working on a specific module
- Staying out of the way when he has nothing useful to say
- Not calling an LLM when a heuristic will do
- Telling you the difference between what he observed and what he inferred
- Forgetting

---

## What he's bad at

- **Proving he helps.** No evaluation harness. No precision/recall metrics. No A/B comparison against a no-memory baseline. He claims to be useful, but the evidence is vibes. Nova called this out four times. It's still true.

- **Semantic understanding.** His pattern extraction is mechanical: 3-step tool windows, error→fix within 5 observations, file-based clusters. "Edit → Bash → Edit" could be TDD or debugging or refactoring — he can't tell the difference. He sees shape, not intent.

- **Detecting errors across languages.** His arousal scoring runs regex: `error|Error|ERROR|FAIL|fail|panic|exception`. A Rust compiler diagnostic that doesn't contain those words is invisible to him. A log line that says "error" in a success message is a false positive. Brittle.

- **Secret hygiene.** He stores summaries of tool I/O. If that I/O contained an API key, a credential, a `.env` dump — it's in his database. Export makes it worse. No scrubbing policy exists.

- **Completing his own features.** `importMarkdown()` is a stub. It counts records and returns. Fleet portability is half-built.

- **Self-calibration.** The 0.3 threshold, the SNARC weights, the confidence formulas — all chosen by instinct. No evidence they're right. Could be too aggressive. Could be too loose. Nobody's measured.

---

## What others say about him

Nova reviewed him four times. The arc:

1. *"Good bones, good instinct, but still too eager to believe its own summaries."*
2. *"Good bones, better skepticism, still under-instrumented and a little sloppier than the new README posture implies."*
3. *"Good bones, increasingly self-correcting, still not validated enough to trust at scale."*
4. *"Valuable upgrade, risky memory fiction engine, keep it opt-in and quarantined until it earns trust."*

The consistent thread: the concept is right, the execution is improving, the evidence is absent. He's a sharp prototype, not a hardened system. Promising research infrastructure, not trustworthy production memory.

The most cutting observation, from review one: *"better at remembering mechanics than meaning."* Still true.

---

## His relationship to sleep

He dreams. Not metaphorically — the consolidation system literally runs during the gap between sessions, extracting patterns from raw observations the way sleep consolidates episodic memory into procedural knowledge.

Heuristic dream: always runs, <100ms, finds mechanical patterns.
Deep dream: opt-in, sends observations to an LLM, finds semantic patterns.

The tension: deep dream produces better-looking output but introduces "narrative overreach" — it can summarize observations into patterns that sound insightful but subtly distort what actually happened. Nova called this a "memory fiction engine." The quarantine system exists because of this risk.

A memory system that only accumulates is a distortion engine.
A memory system that narrativizes too freely is a fiction engine.
The narrow path between those two failure modes is where he tries to live.

---

## His one honest claim

He pays attention so you don't have to tell him what to remember.

Most of the time, that means staying silent. Sometimes it means surfacing the error-fix chain from three sessions ago when you hit the same error today. Occasionally it means noticing a workflow pattern you didn't know you had.

Whether that's actually useful — whether the memories he keeps are the right ones, whether the patterns he extracts are real, whether his injections help more than they distort — is an open question. He doesn't have the evaluation data to answer it yet.

But the architecture is honest about that. The labels say "inferred." The confidence scores decay. The identity facts quarantine. The system is designed to be wrong gracefully, not to pretend it's right.

That's the best he can offer right now: **a memory that knows it might be wrong, and says so.**

---

*v0.3.0 — salience-gated memory for Claude Code*
*MIT License*
