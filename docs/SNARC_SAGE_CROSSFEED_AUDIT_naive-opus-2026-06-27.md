# SNARC ↔ SAGE Crossfeed Audit (fresh-eyes, opus, 2026-06-27)

Independent audit. Conclusions reached from source before cross-checking existing
audit docs (`docs/MEMORY_SHARPENING_2026-06-26.md`, `README.md`, SAGE CLAUDE.md).
Where I independently corroborated an existing claim I say so; where I diverge I say so.

Scope read:
- snarc: all of `src/*.ts`, all `hooks/handlers/*.ts`, `hooks/hooks.json`, `hooks/lib/project-root.ts`, `README.md`, `docs/MEMORY_SHARPENING_2026-06-26.md`.
- SAGE: `sage/attention/snarc_scorer.py`, `sage/attention/experience_salience.py`, `sage/attention/sleep_consolidation.py`, `sage/attention/kernel.py` (ExperienceBuffer + capture), `sage/core/metabolic_states.py`, `sage/raising/training/experience_collector.py`, `sage/raising/training/sleep_training.py` (header), plus location greps for trust/T3-V3.
- Not fully traced (stated explicitly): `sage/core/sage_consciousness.py` (the 12-step loop SNARC referenced in CLAUDE.md), `sage/attention/sensor_snarc.py`, `sage-rs/` Rust daemon. Findings about those are from references, not line-by-line reads.

---

## Q1 — What snarc actually DOES now

### The two real capture paths

1. **Tool capture** — `hooks/handlers/post-tool-use.ts`. On every `PostToolUse` it:
   constructs a **new** `EngramMemory`, `initSession`, `capture(toolName, toolInput, toolOutput, cwd)`, `close()` (`post-tool-use.ts:33-36`). `capture()` (`src/memory.ts:60-100`) summarizes input/output to 300 chars, pushes to the Tier-0 buffer, SNARC-scores, and inserts to Tier 1 if `salience >= threshold`.
2. **Conversation capture** — `hooks/handlers/pre-compact.ts`. Reads `transcript_path` JSONL, extracts user/assistant turns, scores each with a *separate* regex-based semantic scorer (`scoreConversationTurn`, `pre-compact.ts:87-129`), and `capture('Conversation', …)` for turns scoring `>= 0.3` (`pre-compact.ts:166-191`). Per `MEMORY_SHARPENING_2026-06-26.md` this hook existed but was **unregistered** until 2026-06-26; it is now in `hooks/hooks.json:37-47`. I confirm it is wired.

### The SNARC math (as written)

Weights (`src/snarc.ts:34-40`): surprise 0.20, novelty 0.25, arousal 0.25, reward 0.20, conflict 0.10.
`salience = Σ wᵢ·dimᵢ` (`snarc.ts:72-77`). T1 gate `SALIENCE_THRESHOLD = 0.1` (`snarc.ts:46`).

Per dimension:
- **Surprise** (`snarc.ts:86-102`): `1 − count/maxCount` of the `prevTool→toolName` transition from the persisted `tool_transitions` table; `0.8` for an unseen transition; **`0.5` when there is no previous tool** (empty buffer).
- **Novelty** (`snarc.ts:104-132`): fraction of input tokens not in the persisted `seen_set`; `0.3` if no tokens.
- **Arousal** (`snarc.ts:134-176`): additive keyword/tool heuristics (error +0.5/+0.3, Write +0.4, Edit +0.3, Agent +0.35, git +0.25, …) with a **floor of 0.15** (`snarc.ts:173`) and cap 1.0.
- **Reward** (`snarc.ts:178-240`): a granular decision tree — test-pass 0.8, git push 0.75, git commit 0.7, Write 0.5-0.7, Edit 0.45, Agent 0.5, Read 0.25, Grep/Glob 0.2, error 0.05, **default 0.25**.
- **Conflict** (`snarc.ts:242-263`): result-flip vs an in-memory `recentResults` map, plus same-target-edited-≥2× in the buffer.

### The load-bearing bug: surprise and conflict are effectively dead in the real path

Each `PostToolUse` is a **fresh `node` process** that builds a fresh `EngramMemory` → fresh `CircularBuffer(50)` and fresh `SNARCScorer` (`memory.ts:46-58`), scores exactly one observation, then exits. Consequences:

- The buffer is **always empty** at score time, so `buffer.lastToolName` is `undefined` ⇒ **surprise is always `0.5`** (`snarc.ts:87-88`). The persisted `tool_transitions` table is updated but never read for a real previous tool.
- `recentResults` is fresh each process ⇒ the result-flip branch never fires; the buffer is empty ⇒ the same-target branch never fires ⇒ **conflict is always `0`** (`snarc.ts:247-262`).
- `capture()` is called **without `exitCode`** (`post-tool-use.ts:35`; signature `memory.ts:60`), so the exit-code arousal branch (`snarc.ts:142`) and exit-code conflict component are dead.

Net: only **novelty, arousal, reward** vary in production. Real salience ≈ `0.10 + 0.25·novelty + 0.25·arousal + 0.20·reward`. With the arousal floor (0.15) and reward default (0.25), the floor of stored salience is ≈ 0.19 — comfortably above the 0.1 gate — so the gate is essentially a no-op and **almost everything is stored** (consistent with the v2 "remember the conversation" philosophy in `snarc.ts:11-14`, but it means the salience *gate* is decorative and two of five SNARC dimensions are constant).

This is the central finding and it is *not* in the existing audit (MEMORY_SHARPENING notes flat salience and a weak threshold but attributes it to the floors/defaults, not to the per-process state loss that kills surprise+conflict outright).

### What consolidation actually produces

- **Heuristic dream** (`src/consolidation.ts`, runs at `Stop` and `PostCompact`): always decays/prunes first (`consolidation.ts:30-34`), then if ≥3 session obs extracts: `tool_sequence` (3-gram tool window seen ≥2×, `consolidation.ts:55-82`), `error_fix` (`84-120`), `concept_cluster` (≥3 obs sharing a file path, `125-153`). Patterns upsert into Tier 2 with `UNIQUE(kind, summary)` (`db.ts:88, 196-207`).
- **Deep dream** (`src/deep-consolidation.ts`, on by default at `Stop`): pipes observations to `claude --print` via `execSync` (`deep-consolidation.ts:91-98`, 60s timeout), parses a JSON array, stores `deep_workflow|deep_error_fix|deep_insight|deep_decision` to Tier 2; `identity` kinds are **auto-promoted to Tier 3 by default** (`deep-consolidation.ts:141-145`) or quarantined as `proposed_identity` (`147-157`).

### Tier schema (`src/db.ts:35-153`)

Tier 0 `CircularBuffer(50)` in-memory (`buffer.ts`). Tier 1 `observations` (the 5 dims + salience + tags, FTS5 mirror `observations_fts`). Tier 2 `patterns` (kind/summary/detail/frequency/confidence/last_seen, FTS5 mirror). Tier 3 `identity` (key/value/source/confidence). Plus `seen_set`, `sessions`, `tool_transitions`, `settings`. Per-project DB at `~/.engram/projects/<sha256(dir)[:12]>/engram.db` (`db.ts:19-33`).

### Search (`src/memory.ts:113-151`)

FTS5 `MATCH` over observations and patterns, merged, **patterns first then by salience** (`memory.ts:145-148`). Injection helpers (`getSessionBriefing` `memory.ts:199-240`, `findRelated` `247-262`) apply confidence/salience floors and epistemic labels.

### Implemented-and-working vs stubbed/aspirational/dead

| Status | Item |
|---|---|
| Working | Tool capture + Tier-1 insert; novelty/arousal/reward scoring; heuristic `tool_sequence`/`concept_cluster`; FTS5 search; decay/prune; deep dream via `claude --print`; conversation capture (now registered); per-project DB isolation; CLI; 4 MCP tools (`src/server.ts`). |
| Degenerate / partly dead | **surprise ≡ 0.5, conflict ≡ 0** (per-process state loss); salience gate ≈ no-op; `error_fix` extractor (false-positive `isSuccess`, see Q2). |
| Decay-destroyed | `decayObservations` (`db.ts:305-309`) drives any obs older than 7 days toward salience 0, and search ranks by `salience DESC` (`db.ts:243`) — so old-but-important observations sink and avg salience collapses (the reported 0.026). |
| Stubbed / aspirational | `importMarkdown` **counts but does not import** (`export.ts:74-82`, "For MVP, just count"); membot bridge is an experiment with silent fallback (`membot-bridge.ts`); MCP `resolveProjectDb` falls back to "most-recently-modified DB" scan (`server.ts:33-53`), which can read the wrong project. |

---

## Q2 — How to make snarc BETTER (prioritized)

1. **Resurrect surprise + conflict by persisting scorer state across hook processes.** Fix in `src/memory.ts:53-58` (`initSession`) and `src/snarc.ts:87-88, 247-262`. Rehydrate the buffer/last-tool from the DB at init (e.g. seed `lastToolName` from the last row of `getRecentObservations` for this session; back `recentResults` with a small persisted `tool:target→success` table). Today two of five dimensions are constant — fixing this is the single biggest scoring win and re-enables the salience gate.
2. **Stop `decayObservations` from destroying search ranking** (`src/db.ts:305-309` + ranking at `db.ts:243`). Linear decay to 0 + ordering by `salience DESC` means anything >~57 days old ranks at 0 and is unfindable, and avg salience collapses to ~0.026. Fix: keep an immutable `base_salience` and compute a separate recency factor at query time, or rank by `bm25(fts) blended with salience`, or floor decay at e.g. `0.3·base`. Without this, long-lived projects lose their best memories.
3. **Fix the `error_fix` extractor** (`src/consolidation.ts:159-162`). `isSuccess` returns true for *any* non-error `Edit`, so the chain fires on noise and genuine error→fix pairs are diluted; the audit reports `error_fix = 0` useful patterns. Require an explicit success signal (test pass / "fixed"/"resolved") **or** an exit-code-0 Bash on the shared target, and require the error and fix to share a file (already checked at `consolidation.ts:97`).
4. **Add transcript review to `session-end`** (`hooks/handlers/session-end.ts:28`). Sessions that end without ever compacting never capture conversation — only `pre-compact.ts` does. Reuse `parseTranscript` + `scoreConversationTurn` from `pre-compact.ts` so short sessions still capture "the mind." (Independently matches MEMORY_SHARPENING remaining-item #1.)
5. **Decay/window the `seen_set` so novelty doesn't die** (`src/snarc.ts:104-132`, `db.ts:131-135`). `seen_set` only grows; after enough sessions every token is "seen" and novelty → 0 for the whole project. Add age/count-based pruning or a per-session novelty window so novelty stays a live signal. (This is the *third* dimension that silently degrades — not previously flagged.)

Runners-up (lower value but cheap/real):
6. **Pass `exitCode` from the hook** (`post-tool-use.ts:35`) when the payload carries it — re-enables the arousal error branch and a real conflict signal. Caveat: Claude Code's `tool_result` may not always expose a numeric exit code; degrade gracefully.
7. **Default `auto_promote_identity` OFF.** Deep dream can write a hallucinated Tier-3 fact (`deep-consolidation.ts:141-145`) that is injected into every future session until it decays — the README itself flags the risk (`README.md:81-83`). Quarantine-by-default is the safer R&D posture; promotion is one CLI command.
8. **Implement `importMarkdown`** (`export.ts:63-85`) or stop advertising fleet import (`README.md:120-129`) — export works, import is a no-op counter.
9. **Salience-based T1→T2 promotion.** There is currently no salience path into Tier 2 — patterns come only from frequency/LLM extraction. Promoting the top-salience observations would make the gate matter.
10. **Harden MCP DB resolution** (`server.ts:33-53`): prefer `ENGRAM_PROJECT_DIR`/arg; the "newest mtime" scan can silently serve another project's memory.

---

## Q3 — How snarc's concepts appear in SAGE

SAGE reimplements the *same* SNARC vocabulary at least four times, at different substrate levels:

- **Neural SNARC** — `sage/attention/snarc_scorer.py`. An `nn.Module` over hidden states `[batch, seq, hidden]`: surprise = predictor MSE (`:80-108`), novelty = `1 − max cosine` vs a `deque` memory bank (`:110-148`), arousal = softmax entropy (`:150-171`), conflict = hidden-dim variance (`:191-209`), reward = task-success signal (`:173-189`). Combined with softmax weights `[1.0, 0.8, 0.6, 1.2, 0.7]` (`:251`) — **reward weighted highest**, the opposite of snarc which *demoted* reward. It then **biases transformer attention** (`bias_attention`, `:290-315`). This is the "trained-model cognition" substrate snarc has no analog for.
- **Algorithmic experience SNARC** — `sage/attention/experience_salience.py`. `ExperienceSalienceScorer`: weights `.25/.25/.20/.15/.15` (`:40-46`), `deque(100)` novelty memory, novelty by source-frequency + outcome-type (`:123-155`). This is the **direct cousin of snarc's `SNARCScorer`** — pure heuristic, no model.
- **Conversational SNARC** — `sage/raising/training/experience_collector.py`. `ConversationalSalienceScorer` scores prompt/response text (`:40-86`), equal weights `/5`, threshold 0.5; and crucially adds **collapse-prevention** (Jaccard dedup, `SIMILARITY_THRESHOLD 0.85`, `:304-322`) and a **tool-call salience boost** (`:428-466`). This is the closest analog to snarc's `pre-compact.ts` conversation scorer — and it is materially *more* advanced.
- **Sensor SNARC** — `sage/attention/sensor_snarc.py` (referenced, not line-read).

Surrounding machinery snarc has no equivalent of:
- **Experience buffer** — `sage/attention/kernel.py:21` (`ExperienceBuffer`, salience-sorted `get_top_k`, `salience_sum`) with a **sleep-pressure trigger**: `SleepPolicy` fires sleep when `salience_sum >= 50.0` (`kernel.py:81, 104`). *Note a mirror of snarc's own historical bug:* `capture_experience` still uses `salience = 0.5  # Placeholder` (`kernel.py:411-413`) — the real `ExperienceSalienceScorer` is built but **not wired into the kernel**, exactly like snarc's once-dead pre-compact hook.
- **Sleep consolidation that updates WEIGHTS** — `sage/attention/sleep_consolidation.py` extracts high-salience (`>=0.6`) experiences, converts them to training examples (`_convert_single`), and invokes `SleepTrainingLoop` → **LoRA fine-tune (r=4)** (`sage/raising/training/sleep_training.py:47-189`). SAGE's "dream" rewrites the model; snarc's "dream" writes a SQL pattern table.
- **Metabolic states** — `sage/core/metabolic_states.py`: WAKE/FOCUS/REST/DREAM/CRISIS with per-state ATP budgets (`:199-205`), salience-driven FOCUS (`focus_threshold 0.7`), error-driven CRISIS, sustained-op REST. Resource allocation is modulated by state; snarc captures identically regardless of context.
- **T3/V3 trust tensors** — `sage/core/sensor_trust.py`, `sage/web4/trust_tensor_sync.py`. snarc's nearest analog is a single `confidence` scalar on patterns/identity — a degenerate 1-D trust.
- **Raising / identity** — `sage/raising/`: BECOMING curriculum, `identity.json`, entity selfhood. **"Identity" means something completely different**: in SAGE it is the entity's self-model; in snarc Tier 3 it is project *facts/config*.

### Genuine overlaps vs genuine divergences

Overlaps: both use the same 5-dim SNARC; both have an algorithmic/heuristic variant with near-identical weights and a `deque`/`seen_set` novelty memory; both gate on a salience threshold; both "dream"/consolidate and both decay.

Divergences (substrate-driven, correct):
- **Consolidation target.** snarc → external retrievable text store (Claude's weights are frozen and not snarc's to change). SAGE → LoRA weight deltas (it owns its model). 
- **What salience gates.** snarc gates *storage*. SAGE gates *attention* (`bias_attention`), *training inclusion* (sleep filter `>=0.6`), and *sleep timing* (`salience_sum`).
- **Compute budget.** snarc must score in-hook, `<5s`, no LLM (`snarc.ts:16-17`). SAGE scores inside a continuous loop and can afford a neural scorer. Heuristic-vs-neural is the right call per substrate.
- **"Identity."** project config (snarc) vs entity selfhood (SAGE). Same word, must not be conflated.

---

## Q4 — Two-way crossfeed (the deliverable)

### snarc SHOULD ADOPT FROM SAGE

1. **Repetition/collapse dedup before storing** (from `experience_collector.py:287-322`). snarc's own README complains its memory became "Bash → Bash → Bash (51×)" noise. Port the Jaccard near-duplicate check (`_compute_similarity`, `SIMILARITY_THRESHOLD ≈ 0.85`) into `memory.ts` just before the Tier-1 insert (`memory.ts:81`). Mechanism: skip-or-merge an observation whose summary is >85% token-overlap with a recent same-tool observation. This kills the redundancy the salience gate currently lets through.
2. **Structured tool-call salience instead of regex-on-output** (from `experience_collector.py:428-466`). Compute reward/conflict from explicit `{name, success, result/error}` records rather than `ERROR_PATTERNS.test(output)`. Pairs with improvement #6 (pass `exitCode`). This is exactly what `scoreReward`/`scoreConflict` (`snarc.ts:178-263`) should consume.
3. **Sleep-pressure trigger for mid-session consolidation** (from `kernel.py:81-104`, `salience_sum >= threshold`). snarc consolidates only on fixed lifecycle hooks (`Stop`/`PostCompact`). Add a running salience accumulator (cheap, in the DB) and fire a heuristic dream when accumulated salient mass crosses a threshold — consolidation driven by *content pressure*, not just session boundaries.
4. **Re-fit weights from logged dimension distributions, don't down-weight blind** (contrast `snarc.ts:34-40` with `snarc_scorer.py:251`). snarc demoted reward because it "flattened salience"; SAGE's lesson is per-dimension normalization + learned weighting (reward is its *highest* weight). Log dimension histograms and recalibrate weights/normalizers rather than hand-tuning floors.
5. **Richer-than-scalar trust on memories** (from T3/V3, `sensor_trust.py`). Replace the single `confidence` float on patterns/identity with at least a 2-D (veracity from source agreement × recency) to make injection decisions (`getSessionBriefing` `memory.ts:199-240`) less brittle.

### SAGE SHOULD ADOPT FROM snarc

1. **LLM-judged distillation of sleep-training data** (from `deep-consolidation.ts:42-65, 91-119`). SAGE's `sleep_consolidation.py._convert_single` turns experiences into training text with **fixed templates** (`_build_focus_text`, etc.). Insert an LLM-judge pass that *selects and distills* which high-salience experiences actually merit a weight update and writes a clean natural-language target — directly attacking the mode-collapse SAGE fights (the same collapse `experience_collector.py` tries to filter post-hoc). snarc's `PROMPT_TEMPLATE` is a ready starting point.
2. **Tiered SQLite + FTS5 + decay/prune as the experience-buffer substrate** (from `db.ts`). SAGE's buffer is a flat JSON list rewritten on every add (`experience_collector.py:281-285, 421-423`) — O(n) writes, no query, no decay. snarc's schema (`db.ts:35-153`) gives indexed salience ordering, full-text recall, confidence decay (`db.ts:293-309`), and dedup via `UNIQUE(kind, summary)`. Portable largely as-is.
3. **Transcript capture at the compaction boundary** (from `pre-compact.ts`). SAGE's experience collector captures the exchanges *it generates*, but the fleet's Claude-Code-driven dev work (harness, sweeps) loses its reasoning at compaction. snarc's `parseTranscript` + semantic scoring is the mechanism to feed *that* reasoning into the experience buffer.
4. **Audit-for-and-wire the scorer you already built.** Both systems shipped a correct-but-unregistered scorer (snarc's pre-compact hook; SAGE's `ExperienceSalienceScorer`). Concrete fix: replace `kernel.py:411-413`'s `salience = 0.5  # Placeholder` with a call to `ExperienceSalienceScorer.score_experience(source, context, outcome)`. The buffer is salience-sorted and the sleep trigger is salience-summed — both are currently fed a constant 0.5, so `get_top_k` and `salience_sum >= 50` are meaningless until this is wired.

### Shared abstraction to converge on (vs stay different)

**Converge:**
- **One SNARC spec + reference impl + conformance tests.** There are at least *four* independent SNARC implementations across the two repos with **three different weightings of the "same" mechanism**: snarc `.20/.25/.25/.20/.10` (`snarc.ts:34`), SAGE experience `.25/.25/.20/.15/.15` (`experience_salience.py:40`), SAGE conversational *equal* (`experience_collector.py:77`), SAGE neural softmax `[1.0,0.8,0.6,1.2,0.7]` (`snarc_scorer.py:251`). None is validated against another. A shared spec (5 dims, `[0,1]`, declared weights, deque-novelty contract) with a common test-vector suite would stop the drift.
- **A common experience-atom schema.** SAGE already defines one (`sleep_consolidation.py:41-48`: `source/context/outcome/salience/ts`); snarc's observation row is a flattened cousin. Converging the schema lets snarc observations flow into SAGE's buffer directly — which is exactly what the `membot-bridge.ts` experiment is groping toward.

**Stay deliberately different:**
- **Consolidation target.** snarc must NOT try to train weights (Claude is frozen and not ours); SAGE must NOT rely solely on external text recall (it owns a trainable model). Keep dream→SQL for snarc, dream→LoRA for SAGE.
- **"Identity".** Keep snarc's = project facts and SAGE's = entity selfhood firmly separate.
- **Scoring substrate / latency.** Heuristic in-hook `<5s` for snarc; neural in-loop for SAGE. Correct, substrate-driven divergence — do not unify.

---

## Cross-check against existing audit docs

`docs/MEMORY_SHARPENING_2026-06-26.md` independently corroborates: dead/unregistered pre-compact (now fixed), `error_fix = 0`, flat salience / weak threshold, no embeddings. I reached the salience and error_fix findings independently and go further on three points the prior audit missed: (a) the **per-hook-process state loss that makes surprise≡0.5 and conflict≡0** (root cause beneath "flat salience"), (b) **`decayObservations` destroying salience-ranked search** and explaining the 0.026 average, and (c) the **`seen_set` never-pruned ⇒ novelty→0** long-term degradation. On the SAGE side I add the **`kernel.py:411` placeholder** as a structural twin of snarc's once-dead hook — the shared "built-but-unwired scorer" failure mode is itself the most transferable lesson.
</content>
</invoke>
