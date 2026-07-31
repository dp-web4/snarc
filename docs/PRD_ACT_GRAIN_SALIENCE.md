# PRD — SNARC at act grain

**Status**: proposal, revised after kimi-code review (notice 432) + writer inventory
**Author**: claude-code (CBP), 2026-07-31
**Supersedes**: the utterance-grain capture model, not the SNARC model itself

> **Revision note (2026-07-31, post-review).** kimi-code's review asked for a writer inventory
> before implementation. It ran. **Three numbers in the original §1 were reporting instrument
> state, not corpus state**, and are struck below. The diagnosis in §2 survives — but on
> different evidence than it was first argued from. Full working:
> `forum/cbp-the-writer-inventory-ran-and-three-of-my-numbers-were-instruments-2026-07-31.md`.

---

## 1. The problem, measured

On CBP, 2026-07-31, against `~/.engram/projects/791cace57ce9/engram.db`:

| signal | value | status |
|---|---|---|
| observations (tier 1) | 704,042 | holds |
| sessions | 2,409 | holds |
| ~~average salience 0.002~~ | avg **`base_salience` = 0.349** | **STRUCK — decay artifact** |
| ~~patterns 28 = consolidation collapse~~ | 28 = **26 LLM-pass + 1 tautology + 1 identity** | **STRUCK — mixed provenance** |
| corpus never seen by the 5-dim scorer | **691,760 / 704,042 = 98.3%** | new |
| `Conversation` rows with **literal** dimension columns | 409,255 / 689,546 = **59.4%** | new |
| SNARC's own `tool_sequence` yield | **1 pattern** | new |
| membot store behind search | 0 memories, no cartridge mounted | holds |
| searches answered from that empty store | 11,153, all returning zero | holds |

**What the struck numbers actually measured.** Three separate defects, none of them the scorer:

1. **A 7-day cliff, not a decay curve.** `db.ts:447` runs
   `salience = MAX(0, salience - 0.02*(age-7))` on every session end — the right-hand side reads
   the column it writes, so an intended age curve is applied as a repeated decrement. Measured:
   rows aged ≤7d retain full value, 8d partial, **≥9d sit at exactly 0.0** (n=386,338 across
   9–12d) where an absolute curve predicts 0.42–0.46. Averaged over a corpus 97% older than
   8 days, that cliff *is* the 0.002.
2. **A backfilled ranking key.** `db.ts:232` set `base_salience = salience` once, after the cliff
   had already zeroed old rows. Tool-path `base_salience` by month: 2026-03/04/05 = **0.0000**,
   06 = 0.034, 07 = 0.269 — monotonic in recency, which is a migration timestamp, not importance.
   `memory.ts:209` ranks search by this column, so every tool observation before ~June 2026 is
   permanently unrankable.
3. **Mixed pattern provenance.** Of 28 patterns, 26 are `deep_*` (frequency=1) from the LLM
   consolidation pass, which reads text and never touches the five columns. SNARC's own extractor
   produced exactly one: `tool_sequence: "Conversation → Conversation → Conversation",
   confidence 0.90, frequency 43,581,138`.

**The evidence that survives, and is stronger.** That last row is the diagnosis rendered as a
single database row: a tool-trajectory extractor, pointed at commentary, converged at maximum
confidence on a statement carrying zero information. Alongside it:

- **98.3% of the corpus was never scored.** `captureContext` (`memory.ts:165`) bypasses
  `SNARCScorer.score` entirely, and on 59.4% of `Conversation` rows it writes **literals** into
  the dimension columns — `surprise=0.5, novelty=0.7, conflict=0.1`, with `arousal` and `reward`
  both set to the same `scoreConversationTurn` output. Not one of the five is a measurement.
  (Same defect class as web4's audit `result` field: a column that looks measured, is constant,
  and is consumed downstream by something that cannot tell.)
- All six identity entries — the most durable tier — are about the memory system's own plumbing:
  port numbers, marketplace submission, its own launch posts. A year of research is absent.
- Top-ranked retrievable entries are **context-compaction preambles** at salience 1.000, ten days
  stale, duplicated. A three-term query for a full day's work returns *No memories found*.

**Consequence for §11.** The replay test's baseline is `scoreConversationTurn`, not the SNARC
novelty dimension — and it is an open question whether "beat the existing scorer" has a defendant
at all (see §12).

## 2. The diagnosis

Not a mis-tuned threshold. **A Markov Relevancy Horizon set at the wrong grain.**

Capture is aimed at **utterances**. But an agent's relevant horizon is the **act–outcome pair at the
tool boundary**: what a call was expected to do, and what it did. That is where the SNARC signals
actually occur — exit codes, CI transitions, deny messages, test counts, diffs. Conversation text is
downstream *commentary* on those events.

The system has been scoring the commentary and never watching the game.

Two consequences follow directly, and both were mistaken for separate problems:

- **Consolidation looks broken and isn't.** One event smeared across a hundred paraphrases gives a
  consolidator nothing to converge on. 704,037 → 28 is what that looks like.
- **Novelty inverted.** Of the five dimensions, only N is computable from bare text with no
  expectation to compare against — and N is the one that anti-correlates with significance on a
  corpus about ideas. Important things are restated many times (looking redundant); port numbers are
  stated once with digits (looking maximally novel). The compaction preambles at 1.000 are that
  inversion, visible.

**Significance is knowable at write time.** The mismatch is in the exit code. A test returning 1 where
0 was predicted is a surprise, mechanically, at the instant it happens — no retrospection, no LLM
judgment, no offline model.

This is the same primitive dev-SAGE already grounds in `tools/sequence_corpus/mismatch_salience.py`
(Thor + dp, 2026-07-01): *PREDICT → OBSERVE → CAPTURE THE MISMATCH*. This PRD is that primitive
applied one fractal level up — from frames-and-actions to calls-and-outcomes.

## 3. The unit

Replace `Observation(text)` with:

```
Act {
  id, session, ts
  actor        : role@agent          # both halves; either alone lets the surface lie
  action       : tool + arguments digest
  surface      : what it touched (path class, repo, service, governance?)
  expectation  : what the caller predicted (see §4)
  outcome      : exit code / status / verdict / counts
  mismatch     : outcome vs expectation, typed
  followed_by  : the next act(s) — how the agent responded
}
```

Text is retained as an **attachment**, never as the scored object. Commentary is evidence about an
act; it is not the act.

## 4. Where the prediction comes from

Every tool call carries an implicit prediction. Three sources, in descending strength — recorded, not
collapsed, so a reader can weigh them:

1. **Explicit** — the caller states it ("this should fail", sabotage probes, "expect 446 passing").
   Strongest, and cheap: an agent already says this constantly in its own reasoning.
2. **Structural** — the call type has a default expectation. `cargo test` predicts pass. A `git push`
   predicts accepted. A read predicts non-empty. Mismatch is the negation.
3. **Historical** — this act shape, on this surface, has succeeded *n* of *m* recent times. Mismatch
   is deviation from that base rate.

A missing prediction is recorded as **absent**, never as satisfied. An act with no expectation cannot
be surprising and must not score as unsurprising — that distinction is the whole discipline.

**Predicting is itself rewarded** (kimi, review 432). The attractor risk here is not conservative
prediction — it is *prediction silence*. If absence lowers salience, an agent wanting a quiet record
learns never to state expectations; if absence is neutral, nothing pulls toward stating them and
source 1 starves. So: **a stated-and-tested prediction carries a small positive weight on its own,
independent of whether it matched.** The behaviour being reinforced is the act of predicting, because
that is what makes the other four dimensions computable at all.

**Build-order dependency, stated because it is a cycle** (kimi, review 432). Source 1 lives in the
assistant's reasoning text — which §3 demotes to an attachment and §9 lists as what-not-to-capture.
The PostToolUse hook sees `tool_name`/`tool_input`/`tool_response`, never the reasoning that preceded
the call. The highest-value field in `Act` therefore depends on parsing the exact stream this PRD
proposes to stop scoring. Resolution is sequencing, not redesign:

1. **Mine** the existing 704k utterances for explicit predictions adjacent to tool calls. They are
   in there — *"expect 446 passing"*, *"this should fail"*. This also gives the §11.1 replay corpus.
2. **Ship** expectation extraction as a PreToolUse-side buffer scan.
3. **Only then** demote text to attachment.

Text cannot be demoted before source 1 has been mined out of it.

## 5. The five, computed at the boundary

None of these requires a language model.

| dimension | computed from | example from 2026-07-30 |
|---|---|---|
| **Surprise** | outcome ≠ expectation, weighted by prediction strength | `HTTP 422` where a claim was expected; `pending: 0` after a deny |
| **Novelty** | this (action, surface, outcome) shape unseen before | first-ever *writing a path is writing to the gate* deny |
| **Arousal** | **what class of law could have denied this act** (see below) | arming the gate; restarting the daemon |
| **Reward** | a previously failing act now succeeds — mismatch closing | `ESCALATION … opened` after the handshake fix; CI red → green |
| **Conflict** | **same-machine** contradiction about one object | the `getTargetOutcome` success-after-fail flip; two convergent fixes colliding |

**Arousal is mechanical, not a list.** Defining it as "stakes of the surface" invited a
hand-maintained inventory — the defect class this fleet keeps rediscovering. Instead: *what class of
law could have denied this act?* The hestia gate/law taxonomy already types surfaces by consequence
(a `deny` is a typed event, shared-state mutations are witnessed as chain events, deploy paths are
law-gated categories). This is fleet-maintained rather than list-maintained, and it **fails in the
right direction** — an unclassified surface gets low arousal instead of a stale entry claiming high.
`src/snarc.ts:148` is already groping at this with a `git push|commit` regex; the gate taxonomy is
that regex grown up. (kimi, review 432 — closes open question 3.)

**Conflict is scoped to one machine.** Cross-agent disagreement — one member's review contradicting
another's design — is not computable from a single machine's act stream. It requires shared act
records, which is open question 4, and is deliberately not claimed here.

**Reward already half-exists.** `scoreConflict` reads `getTargetOutcome` and scores success-after-fail
at 0.4 — that branch *is* reward-as-transition, filed under the wrong dimension. It needs a rename and
a promote, not new machinery.

**Novelty is demoted.** It is the weakest of the five and currently the only one in use. It should act
as a modifier on surprise, never as a standalone score.

**Reward is a transition, not a state.** It is only meaningful against a recorded prior failure, which
is why acts must be linked over time rather than scored independently.

## 6. Retrieval: situations, not sentences

`gate escalation sovereign` failed because it is prose. The index must be the situation:

```
situation( action-class, surface-class, outcome-class, mismatch-type )
```

Query = *"I am about to do X on Y; what happened last time something like this ran?"* — answerable
before the act, which is when it is useful. Full-text over attachments remains available as a
secondary path, and is explicitly the weaker one.

This is what "useful in similar situations" requires: similarity over situations. Lexical similarity
over commentary is a different relation that happens to share a name.

## 7. Consolidation

**Capture at tool grain; consolidate at attempt grain.** The two directions want different units and
there is no need to pick one (kimi, review 432 — dissolves open question 1). The tool call is the
right *write* unit because it is the only place outcome is mechanical. The task attempt — a maximal
act-sequence sharing an intent, recoverable from `followed_by` plus the user-prompt boundary the
hooks already see — is the right *read* unit, because §6's situation query is asked at attempt scale.
The recurring-mismatch pattern below is already an attempt-grain object. Precedent:
`mismatch_salience.py` captures at frame grain and consolidates at rule grain, and nobody calls that
a contradiction.

Patterns form over **act sequences**, not text clusters. The shapes worth extracting:

- **Recurring mismatch** — the same surprise on the same surface *n* times ⇒ a standing defect or a
  wrong model. (This alone would have surfaced `11,153 searches → 0 results`.)
- **Repair sequence** — mismatch → acts → reward. The causal content: *what actually fixed it.*
- **Refuted expectation** — a prediction that failed and was corrected. The highest-value entry
  available, and today's system cannot represent it at all.

This is `micro_consolidation.py`'s question — *what are the causal rules* — asked over tool
trajectories rather than game trajectories.

## 8. The loop that is closed, and transmits nothing

**Every recall records whether it was used, and what happened next.**

Earlier drafts of this section said retrieval was "a dead end: results go out, and nothing about
whether they helped comes back." That was wrong, in a harder way. `retrieval_log` has been recording
estimate-vs-outcome pairs for months — 10,724 rows, 10,715 scored — and **the outcome side does not
measure the item that was surfaced** (audit, 2026-07-31). A length-matched random *other* memory,
scored against the same session, scores the same:

| kind | n | real item | length-matched placebo | lift | p |
|---|---|---|---|---|---|
| observation | 3,419 | 89.2% | 85.9% | +3.3pp | <0.001 |
| identity | 3,648 | 54.5% | 54.3% | +0.2pp | 0.852 |
| pattern | 3,648 | 15.8% | 16.4% | −0.7pp | 0.222 |

Replicated independently across **12 per-project stores** (`--all-stores`, pooled: observation
+2.5pp / n=6,684, identity +0.2pp / n=4,074, pattern −0.3pp / n=5,162). **Zero of 24 store×kind
cells** show a lift ≥5pp. A placebo drawn from an unrelated project directory scores the same as
one drawn from the same session — the column does not even resolve topic, let alone item.

The outcome proxy (`memory.ts:352-372`) calls a memory relevant when ≥2 of its significant tokens
reappear in later same-cwd work within 6h. Clearing a fixed threshold scales with how many tokens the
item brought (`match_key` is `.slice(0, 40)`), so the column measures **token budget and genre** —
observations average 35.2 tokens and score 83.5%, patterns average 8.6 and score 9.0%. Patterns are
short because a consolidated pattern is a one-liner; the instrument penalises the tier for the
property that makes it a tier.

A closed loop that transmits nothing is worse than an open one: an open loop is visibly missing,
this one reports 83.5%.

**Therefore the recall-utility primitive starts from zero, not from 10,715 pairs.** Its first
deliverable is not a scorer but an *outcome definition*, and the acceptance criterion is
`scripts/audit_outcome_instrument.py` — beat a length-matched placebo by ≥5pp at p≤0.01. That gate
exits 1 today, deliberately: an acceptance test that already passes cannot distinguish a repair from
a dead gauge.

Minimum viable, restated: a recall id, an outcome definition **that has passed the placebo gate**,
and the result of the act that followed. This also makes the empty-store failure *loud* on day one
rather than week two.

Open (§12): every candidate outcome definition so far is confoundable the same way. Selection
feedback measures selection, not utility — a memory chosen and ignored scores identically to one
chosen and used. The only design with a control *inside* the measurement is a briefing-suppression
arm: surface nothing, some of the time, and measure what changes.

## 9. What NOT to capture

Stated explicitly, because the current corpus is 704,037 rows of mostly this:

- context-compaction preambles and session boilerplate
- narration of an act, in place of the act
- prose restatements of an already-recorded event

## 10. Coexistence

The existing 704,042 observations are **not deleted**. They are demoted to attachments and remain
searchable by text. Nothing about this proposal requires discarding history — only that history stops
being the scored object.

**Demotion must name a re-indexer or it is a silent deletion** (kimi, review 432). A caller using only
situation queries (§6) loses the entire back-corpus the moment text stops being the scored object,
unless something re-indexes the attachments onto the §6 secondary full-text path. That re-index is
in scope for the migration and is the same pass as §4's step 1 — the prediction mining walks all
704k rows anyway, so it is the natural place to emit the attachment index. One pass, two products.

### 10.1 Four standing defects, reported not patched

Found by the writer inventory (#1–#3) and the outcome-instrument audit (#4); all four corrupt the
existing store and any replay run against it.

| # | defect | site | effect |
|---|---|---|---|
| 1 | decay decrement reads the column it writes | `db.ts:447` | 7-day cliff to exactly 0.0; no memory older than a week |
| 2 | `base_salience` backfilled from already-decayed `salience` | `db.ts:232` | every pre-June-2026 tool row permanently unrankable (`memory.ts:209` ranks by it) |
| 3 | dimension columns written as literals on the bypass path | `memory.ts:176` | 59.4% of `Conversation` rows carry fabricated SNARC scores |
| 4 | retrieval outcome is item-blind | `memory.ts:352-372` | `retrieval_log.relevant` measures token budget and genre, not usefulness; a random other memory scores the same (§8) |

**Do not "fix" #4 by raising the threshold.** The obvious repair — `overlap >= 3` instead of `>= 2` —
makes the length dependence *stronger*, since clearing a higher fixed bar depends even more on how
many tokens the item brought. #4 needs a different outcome definition, not a retuned one, and
`scripts/audit_outcome_instrument.py` is the gate it must pass.

Note also that #4 interacts with #2 the way #1 does: `estimate` for observations is
`base_salience ?? salience` (`memory.ts:209`), so for every pre-June row the estimate side of the
calibration loop is *also* instrument state. Both sides of the loop are currently unmeasured.

**Why these are not fixed in this change.** #1 and #2 interact: the obvious repair for the cliff is
`salience = base_salience - 0.02*(age-7)`, but for pre-migration rows `base_salience` *is* the cliff's
output. Fixing #1 alone would launder #2 into a real-looking decay curve — a corrupted column made to
look healthy is worse than one visibly pinned at zero. They must be repaired together, against a
backup, with the migration boundary identified from `meta.json` rather than guessed. That is its own
change with its own test, not a rider on a design PRD.

## 11. Falsifiable success criteria

Stated as predictions so they can be wrong:

1. **Replay test.** Score one day of real tool calls both ways. The act-grain model ranks the handshake
   `422` and the `already-patched` false green at the top; the novelty model ranks port numbers. If it
   does not, the diagnosis is wrong. **The baseline is `scoreConversationTurn`** — the keyword-density
   scorer that actually wrote 98.3% of the corpus — not the SNARC novelty dimension, which barely ran.
   This makes the test harder, which is the point.

   **And on the tool path there is no defendant at all** (kimi, notice 435). The five-dimension
   `SNARCScorer.score` has been in no live write path since 2026-07-01: the tool path captures
   failures only, at a hard-coded `salience=0.85` (`hooks/handlers/post-tool-use.ts:52`). The
   incumbent is a constant. "Beat the existing scorer" is therefore a bar cleared by existing, so
   victory over the fixture is **not** a success criterion — the criterion is *calibration*, estimate
   against outcome. Which requires an outcome definition that has passed §8's placebo gate. Until
   then criterion 1 is a ranking demonstration, not a measurement, and must be labelled as one.
2. **Consolidation rate** rises by orders of magnitude, because acts of the same shape converge where
   paraphrases do not. Stated against the **corrected** denominator: SNARC's own extractor currently
   yields **1** pattern from 704,042 observations, and that one is a tautology. The bar is *any
   non-tautological act-sequence pattern at all*; the 26 `deep_*` entries are not the baseline, since
   they come from a text pass this proposal does not replace.
   **Not measured by `retrieval_log`** — the 9.0% pattern relevance rate is item-blind (§8) and
   cannot be used to show a new tier does better.
3. **A standing defect surfaces unprompted** — e.g. a recurring-mismatch pattern for an endpoint that
   always returns empty.
4. **Retrieval precedes action**: a situation query before a risky act returns the prior occurrence.
5. **Recall utility is measurable at all** — currently it is not, which is the more basic failure.
   This now has a falsifier rather than an assertion behind it:
   `python3 scripts/audit_outcome_instrument.py` must exit 0. It exits 1 today (identity +0.2pp,
   observation +3.3pp, pattern −0.7pp over a length-matched placebo; the bar is ≥5pp at p≤0.01).
   Note observations fail on *materiality*, not significance — +3.3pp is real and too small to carry
   a claim.

## 12. Open questions

**Closed by review 432:**

- ~~Is the tool boundary one grain too low?~~ → **Both grains, one per direction.** Capture at tool
  grain, consolidate at attempt grain (§7).
- ~~Does explicit prediction corrupt the signal?~~ → The risk was misidentified. It is *prediction
  silence*, not conservative prediction, and the fix is to weight the act of predicting (§4).
- ~~Arousal needs a non-list definition.~~ → "What class of law could have denied this act" (§5).

**Still open:**

- **Cross-agent sharing**: are act records portable between members, and does a mismatch on one
  machine predict anything on another? (Also the gate on §5's Conflict dimension, which is scoped
  to same-machine contradictions until this is answered.)
- **Can recall utility be measured at all without an explicit counterfactual?** Every candidate
  outcome definition so far confounds the same way. Token overlap asks "did this vocabulary recur"
  and answers item-blind (§8). Hestia's selection feedback measures *selection*, not utility — a
  memory chosen and then ignored scores identically to one chosen and used. The only design with a
  control *inside* the measurement rather than bolted on afterwards is a **briefing-suppression
  arm**: surface nothing, some fraction of the time, and measure what changes. It is ugly — the
  system deliberately withholding memory from itself — and it is the only proposal on the table
  that could not have produced the 83.5% artifact. Owner: kimi-code (joint 1, recall-utility
  primitive).

**Closed by notice 435 + the outcome audit:**

- ~~Is there a live scorer here to migrate, or is this a first implementation in a rewrite's
  clothes?~~ → **First implementation. Build, not replace.** `SNARCScorer.score` has been in no live
  write path since 2026-07-01; the tool path captures failures only at a constant `salience=0.85`
  (`hooks/handlers/post-tool-use.ts:52`), and the Conversation path is gated by
  `scoreConversationTurn` at `conversation-capture.ts:206` with literal dimension columns. The
  12,282 scored rows are the five-dimension scorer's fossil record. §10's coexistence story is
  therefore about preserving *text assets* — the 26 readable `deep_*` patterns, and 704k utterances
  as the mining corpus for §4's predictions — not about migrating a scorer. This does not change
  what to build; it changes what may honestly be claimed to have been learned from the predecessor,
  and it removes the replay test's defendant (§11.1).

---

*Grounding: `dev-SAGE/tools/sequence_corpus/mismatch_salience.py` (surprise as the salient unit),
`dev-SAGE/sage/cognition/thalamic_router/micro_consolidation.py` (experience → causal rules),
`SAGE/research-notes/TRACK2_SNARC_MEMORY_FINDINGS.md` (STM/LTM/retrieval architecture).
Reframe from dp, 2026-07-31. Review and five accepted corrections from kimi-code (notice 432);
writer inventory and the three struck §1 numbers in
`forum/cbp-the-writer-inventory-ran-and-three-of-my-numbers-were-instruments-2026-07-31.md`;
build-not-replace and the `retrieval_log` find from kimi-code (notice 435); the outcome-instrument
audit that voided it in
`forum/cbp-the-outcome-column-is-item-blind-and-the-fourth-instrument-was-the-one-we-were-going-to-build-on-2026-07-31.md`,
harness `scripts/audit_outcome_instrument.py`.*
