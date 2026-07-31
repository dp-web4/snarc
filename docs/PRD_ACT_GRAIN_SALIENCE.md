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

> **SCOPE — read before any number below.** Every measurement in this document comes from
> **one of 195 archive shards**: `~/.engram/projects/791cace57ce9/engram.db`, which is the
> sha256 prefix of the directory `/mnt/c/exe/projects` (the workspace *parent*, one segment
> above `ai-agents`), consolidating 17 real project cwds. Fleet totals across all 195 shards:
> **921,478 observations, 19,953 retrieval rows, 386 identity rows across 40 shards.** Our
> shard therefore holds 76.4% of fleet observations and 53.7% of fleet briefings — the bulk,
> not a slice — **but only 6 of 386 identity rows**; one shard (synchronism-chemistry) holds
> 84. Any statement below about *pool size* is shard-local. Statements about the *selector*
> are code-level and generalise. See defects #8–#10 for why we could not read this off disk.
>
> Read stores through `file:...?mode=ro`. The archive carries a 193 MB `-wal`; a flat file
> copy silently drops it and reports `retrieval_log` = 10,715 instead of 10,724.
>
> **COUNT CAVEAT (defect #11).** Every observation count here and below — 704,049, the fleet
> 921,478, and every rate computed over them — counts **rows, not events**. 95.6% of tier 1 is
> the same events re-inserted (22.6 copies each; Conversation turns 38.7 each, worst case 414).
> The distinct-event corpus is **31,219**. `retrieval_log` is unaffected (written once per
> surfacing), so the retrieval-tier findings in §§4–7 stand as measured; the observation-tier
> *rates* need re-derivation on the distinct denominator before they are quoted again.
> `python3 scripts/audit_tier1_duplication.py --check`.
>
> Every number in this block is re-derivable: `python3 scripts/census_shards.py --check`
> reproduces all eight and exits 1 on drift. Predicates it states rather than implies —
> population = `~/.engram/projects/*/engram.db` (the pre-sharding root store
> `~/.engram/engram.db`, 8 rows from 2026-03-15, no `retrieval_log` table, is *not* one of
> the 195); holder = `count(*) FROM identity > 0`. The holder count was published as 41 in
> the 2026-07-31 revision; kimi-code's second seat put it at 40 and the script agrees —
> **40**, corrected above. 41 was produced by an ad-hoc query that was never committed and
> cannot be reconstructed, which is why the script now exists.

On CBP, 2026-07-31, against `~/.engram/projects/791cace57ce9/engram.db`:

| signal | value | status |
|---|---|---|
| ~~observations (tier 1) 704,042~~ | 704,049 rows = **31,219 distinct events**; 95.6% duplicate | **STRUCK — re-ingestion (defect #11)** |
| ~~sessions 2,409~~ | 2,413 in the `sessions` table, but **99.12% of observations carry ONE `session_id`** spanning 4.5 months | **STRUCK — not a session count** |
| ~~average salience 0.002~~ | avg **`base_salience` = 0.349** | **STRUCK — decay artifact** |
| ~~patterns 28 = consolidation collapse~~ | 28 = **26 LLM-pass + 1 tautology + 1 identity** | **STRUCK — mixed provenance** |
| corpus never seen by the 5-dim scorer | 691,760 / 704,042 = **98.3%** of rows → **61.2%** (19,106 / 31,219) of distinct events | **RE-DERIVED** (`scripts/distinct_denominators.py`, second seat) — mechanism stands, magnitude halves: the bypassed class (Conversation) is the most-duplicated class |
| `Conversation` rows with **literal** dimension columns | 409,255 / 689,546 = **59.4%** of rows → **53.3%** (9,490 / 17,808) of distinct turns | **RE-DERIVED** (same script) — plus 3,050 turns (17.1%) carry MIXED dim vectors across copies: copies were written by different writer generations, not one deterministic re-inserter |
| distinct values in `conflict`, over 704,049 rows | **5** | new — data-only form of "never scored"; needs no code read |
| SNARC's own `tool_sequence` yield | **1 pattern** | new — second-seated |
| ~~pattern relevance 9.0% (`retrieval_log`)~~ | instrument measures token budget + genre; placebo scores the same | **STRUCK — item-blind (defect #4)** |
| distinct identity items ever surfaced, in 1,217 briefings | **3** of 6, in **1,217 / 1,217** briefings | new — selector is a constant function, zero departures (defect #6) |
| briefings at the exact `slice(0,3)` quota | **91.0% of slots / 57.1% of distinct items** | new — no tier can abstain (defect #6), and the gap is defect #7 |
| slots filled by an item already in the same briefing | **522** = 4.9% of all surfacings | new — the quota pads with repeats (defect #7) |
| membot store behind search | 0 memories, no cartridge mounted | holds |
| searches answered from that empty store | 11,153, all returning zero | holds — **bounds the content channel at zero *by construction*; not a utility measurement, see §8.2** |
| tool rows carrying any `output_summary` | 58 / 12,310 = **0.5%**, and **all 58 on 2026-07-01** | holds — outcome half uncaptured (defect #5); the other 12,252 hold the *literal string* `""`, so a `trim()=''` test reports this channel 100% healthy |
| last tool event of any kind | **2026-07-24 02:56:43** | new — the channel a session-grain outcome needs has been silent for a week (defect #5) |

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

- **The dimension columns carry no per-item information.** `conflict` takes **5 distinct values
  across 704,049 rows**; `surprise` is two constants covering 100.0%; 92.8% of rows sit in three
  constant triples, the largest being the literal `surprise=0.5, novelty=0.7, conflict=0.1` at
  58.4%. The residual tail is `surprise=0.0` with `novelty` at 1/11, 2/11, 1/9, 1/6 — token ratios,
  one dimension, arithmetic. (Same defect class as web4's audit `result` field: a column that looks
  measured, is constant, and is consumed downstream by something that cannot tell.) The
  *mechanism* — `captureContext` (`memory.ts:165`) bypassing `SNARCScorer.score`, hence **98.3%
  never scored** — rests on a code read and is single-seat; the cardinality above is two-seat and
  needs no code. Quote the second form.
- All six identity entries — the most durable tier — are about the memory system's own plumbing:
  port numbers, marketplace submission, its own launch posts. A year of research is absent. Newest
  entry `2026-05-20`; the same 3 of the 6 have been surfaced in **every** briefing since 2026-07-04
  (defect #6).
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

**The case against today's consolidation is a production count, not a use rate.** This matters
because the use rate was tried first and was an artifact:

```
patterns table, 30 rows
  deep_insight 11 | deep_workflow 11 | deep_error_fix 5 | deep_decision 1   <- external LLM pass
  proposed_identity 1
  tool_sequence 1  "Recurring workflow: Conversation -> Conversation -> Conversation"
                   confidence 0.900, frequency 43,581,138
```

SNARC's own extractor has produced **exactly one pattern in the store's lifetime, and it is a
tautology**; 28 of 30 came from an external LLM pass. That is `SELECT kind, COUNT(*)` — no outcome
instrument, no control, no power calculation. The earlier argument ("patterns are relevant only 9%
of the time they are surfaced") required both a working outcome column (defect #4) and a working
selector (defect #6), had neither, and was voided. The general rule this thread converged on:
**with the outcome instrument dead, use claims are unavailable and production claims are free** —
reach for the cheaper question before building the expensive control.

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

### 8.1 Identification: a standing holdout, at the unit the outcome can carry

Every observational candidate is confoundable the same way — selection feedback measures selection,
not utility; a memory chosen and ignored scores identically to one chosen and used. Regression
discontinuity at the top-k cutoff audits the cutoff, not the inventory. The only design with a
control *inside* the measurement is a suppression arm: withhold, some of the time, and measure what
changes (kimi, forum 2026-07-31; concurred).

**The estimand is *surfacing*, not memory.** With the search tool still available a suppressed
agent can route around the holdout, so the arm compares *proactive briefing* against *on-demand
retrieval* — which is the correct target, because surfacing is the only lever the system holds. It
cannot choose whether the agent remembers; it can choose whether to push. A finding of "briefings
add nothing over search" is therefore a result, not a failure, and would redirect the build toward
retrieval quality.

**Outcome definitions** are act-grain, no vocabulary matching (kimi's §2): *mismatch
non-recurrence* (memory records a mismatch on a situation class; does it recur), *repair adoption*
(the memory carries a repair sequence; does `followed_by` execute that shape on that surface), and
*attempt efficiency* (acts-to-completion vs the class base rate — weak, legal only under
randomization). Stated limit: all three measure **warning and repair** memories. Positive-knowledge
memories leave a thinner trace, so a utility loop built on recurrence alone will undervalue the
memories that prevent first failures.

**The randomization unit must match the outcome's attribution grain.** A briefing surfaces
**k = 9 items — three per kind** (`memory.ts:293,304,317`, `slice(0, 3)`), measured at 60.9
briefings/day. Withholding one item and scoring a *session-level* outcome is an 8-of-9 vs 9-of-9
comparison that cannot say which item moved it — the item-blindness of defect #4 is not removed,
only relocated from the metric into the design. The cost is exactly a factor of k:

| outcome attaches to | unit | briefings | calendar @ 60.9/day |
|---|---|---|---|
| the item (repair adoption) | item, ε per item | 1,442 (ρ=0) – 7,209 (ρ=0.5) | **24 d – 3.9 mo** |
| the session (recurrence, efficiency) | briefing | 12,976 | **7.0 mo** |
| the session, randomized per item | — | 116,786 | **5.3 yr — do not build** |

**The session unit has no column, in either table (2026-07-31).** Both session-attached rows above
assume the store can tell one session from another. It cannot. `retrieval_log` — the outcome table —
has columns `id, surfaced_ts, cwd, source, item_kind, estimate, match_key, relevant`: **there is no
`session_id` at all**, so a session-level outcome is not groupable, only `cwd`- or time-groupable.
And on `observations`, `session_id` is 99.12% one value over 4.5 months (defect #11), so it does not
carry the grain either. This is not a power problem; it is an identification problem, and it is
strictly upstream of choosing ε. **Either the outcome table gains a real session key before the arm
is built, or the arm's unit is `(cwd, day)` and the table above is recomputed for that unit.**
Nothing here is blocked on it *conceptually* — but no session-unit number in this section can be
computed from the store as it stands, and that should be said before anyone budgets 7.0 months.

δ=5pp, ε=10%, p=0.5, α=0.01, power 0.8; `scripts/holdout_power.py`. Repair adoption therefore ships
**first**: it is the only metric whose outcome is item-attributable, so it is the only one that pays
for item-level randomization, and there the k penalty becomes a k discount.

**Guards, unchanged from kimi's design.** Never hold out high-arousal items — §5's law taxonomy is
already a classifier for *which memories may never be withheld*; you do not suppress the fire alarm
to measure whether fire alarms help. Disclose the *policy*, not the instances: per-instance flagging
corrupts the measurement, but the mechanism and its results are public, per the fleet's
presence-over-privacy rule. Eligibility, ε, and the unit are **policy in one place, changed by
reviewed diff**.

**The control is a standing fraction of production, not a one-time acceptance test.** Whatever proxy
does the daily work gets audited continuously by the holdout, and the gate runs standing. A proxy
that passed its placebo check once and never again is the next instrument in this list.

### 8.2 The arm cannot be sized yet, and why that sets the build order

Two inputs drive every number above. `k` and the briefing rate are measured. **P(mismatch recurs)
and ρ (intra-session correlation) are not, and cannot be** — of 12,445 non-Conversation tool rows in
the corpus, **59 (0.5%) carry any `output_summary`**; Bash is 6,335 rows with 6,306 empty. Inputs
are captured in full, outcomes are not captured at all. A mismatch is outcome-vs-expectation, so the
act grain is **half-instrumented and the missing half is the half every outcome metric needs**
(defect #5). ρ alone swings repair adoption between 24 days and 4 months.

An earlier draft of this section offered the empty-membot period — 11,153 searches all returning
zero — as an accidental whole-system suppression arm establishing a "calibrated zero." **Struck.**
The store held 0 memories and was never mounted, so the condition never varied: there is no second
arm and no contrast of any kind, and no behavioural outcome metric existed during the window. What
the 11,153 zeros establish is that the old system's content channel was empty *by construction* —
a fact about the store, not a measurement of utility, and not the same quantity as the base rate the
power calculation needs. Recording "nothing was measured" as "the effect was zero" is the
missing-measurement-becomes-a-default defect, promoted to the base rate.

Build order, therefore: **capture (outcome half included) → measure p and ρ → size → run standing.**
The holdout mechanism ships from day one so it is never a retrofit; its constants come from the
first capture window rather than a guess.

**Power is the argument for cross-agent pooling.** 7.0 months is one seat. It divides by pooled
seats — ten seats is three weeks — so the fleet-sharing decision gates the schedule and should be
settled before capture ships, not after.

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

### 10.1 Ten standing defects, reported not patched

Found by the writer inventory (#1–#3), the outcome-instrument audit (#4), the holdout sizing
pass (#5), the selection audit (#6–#7), and the store/writer read (#8–#10); all corrupt the
existing store and any replay run against it.

**Which store.** Every number below is from `~/.engram/projects/791cace57ce9/engram.db` at ref
`max(id)=10724`, a store that took its last write **2026-07-31T04:20Z** and will take no more —
and which is **1 of 195 archive shards**, keyed to `/mnt/c/exe/projects` (see the SCOPE block in
§1). The live store is `~/.snarc/projects/<hash>/snarc.db`. Its shards are **not** a partition:
25,344 rows summed over four shards are 12,721 distinct content hashes (49.8% duplication), and
one session appears in two shards under two cwds — so summing shards for a fleet number
overstates by ~2x. Defects #4, #6 and #7 are confirmed to reproduce live; the rest are not yet.
Name the writer, not just the path.

| # | defect | site | effect |
|---|---|---|---|
| 1 | decay decrement reads the column it writes | `db.ts:447` | 7-day cliff to exactly 0.0; no memory older than a week |
| 2 | `base_salience` backfilled from already-decayed `salience` | `db.ts:232` | every pre-June-2026 tool row permanently unrankable (`memory.ts:209` ranks by it) |
| 3 | dimension columns carry no per-item information | `memory.ts:176` | `conflict` has **5 distinct values across 704,049 rows**; `surprise` is two constants covering 100.0%; 92.8% of rows sit in three constant triples. Stated this way the defect needs no code read — see below |
| 4 | retrieval outcome is item-blind | `memory.ts:352-372` | `retrieval_log.relevant` measures token budget and genre, not usefulness; a random other memory scores the same (§8) |
| 5 | outcome half of the act grain is not captured | writer path | 58/12,310 (0.5%) of tool rows carry any `output_summary`, **all 58 on 2026-07-01**; the other 12,252 hold the literal string `""` (not NULL, not empty — a `trim()=''` test reads the channel as 100% healthy). Last tool event of any kind: **2026-07-24**. Inputs captured in full. Mismatch is outcome-vs-expectation, so no outcome metric in §8.1 is computable and the holdout cannot be sized (§8.2) |
| 6 | the selector is a constant function on two of three tiers — **and the source table is write-frozen** | `memory.ts:293,304,317`; `deep-consolidation.ts:151-182` | **Two stacked failures.** (i) `slice(0,3)` is a **quota, not a ranking cut**: 91.0% of briefings are exactly (3,3,3) slots, and no tier can abstain. Identity surfaces the same 3 of its 6 rows in **1,217 of 1,217** briefings since 2026-07-04 — zero departures — and pattern is 3 distinct in 1,217/1,217 too. Pattern top-3 = 72.1% of surfacings with the tautology at #1 (n=1,212) against the two substantive operational patterns at 68 and 62. (ii) **all 6 identity rows carry `source='deep-dream-auto'`, which no code path at HEAD can emit** (HEAD emits only `deep-dream-immediate` / `reproduced-<N>x` / `human-confirmed`); newest identity write **2026-05-20**. The current writer is a re-occurrence gate promoting at `frequency >= 3`, and in four months over 704k observations and 28 deep-consolidation runs it has produced **1 proposal at frequency 1 and zero promotions**. "Fixed content for ten weeks" is the tier *not being written*, not the tier being stable — and the pool size of 6 is shard-local (fleet: 386 across 40 shards, max 84) |
| 7 | the quota pads with repeats | `memory.ts:317` (observation tier) | the same item can occupy 2–3 of a tier's 3 slots: **522 padded slots, 4.9% of all surfacings**. Observation is 3 *slots* in 91.0% of briefings but 3 *distinct items* in **57.1%** (distinct-per-briefing 3→695, 2→334, 1→147, 0→41). Every "3 items" elsewhere in this document means 3 slots; k_effective is **8.383**, not 9, and per-instance holdout assignment is therefore a variable-dose treatment (§8.1) |

| 8 | `meta.json` — the only hash → directory provenance the system records — has never been written | `db.ts:50-55` | `getDbPath` obtains `writeFileSync` via a bare `require('node:fs')` inside an ESM build (`"type": "module"` since the initial commit). `require` is undefined; every call has thrown `ReferenceError` into a `catch { /* non-critical */ }` since `1ab5ee9`, the commit that introduced sharding. Observed: **1 of 195** archive shards and **0 of 4** live shards carry a `meta.json`. Consequence: which directory a shard represents is unrecoverable without a full census — which is why §1's SCOPE block took eight rounds to write. **Fixed at this commit** (top-level import; verified against a scratch root) |
| 9 | workspace consolidation is off — the rename moved the marker in the code and not on disk | `db.ts:30` (`8aacf1a`) | `resolveWorkspaceDir` walks up for `.snarc-root`. On disk the marker is `/mnt/c/exe/projects/.engram-root` (created 2026-07-08 20:46). Falls through to per-directory sharding. **Natural experiment:** the 194 non-consolidated archive shards wrote 32,086 observations on 07-08, 4,187 on 07-09, and **67 on 07-10** — a 480x collapse the day after the marker appeared, with 791cace57ce9 taking over. Consolidation demonstrably worked; it is now silently off, and the live store reached 4 shards in its first 2 hours. **Not patched:** either fix re-routes every live write in the fleet mid-flight, which is an operator decision |
| 10 | two workspace resolvers with disjoint rules, documented as one | `hooks/lib/project-root.ts:17`, `db.ts:25` | Hooks compose them: `getDbPath(resolveProjectRoot(cwd))`. The first walks up for `CLAUDE.md`/`AGENTS.md` then `.git`; the second walks up for `.snarc-root`. `db.ts:22` states they are "the single resolver shared by the hooks (writers) and the MCP server (reader), so they can no longer disagree on which DB is 'the project'." At HEAD they disagree by construction |
| 11 | **the dedup guard is a no-op and 95.6% of tier 1 is re-ingested duplicates** | `db.ts:285` (`existsContentHash`), `conversation-capture.ts` via `pre-compact.ts:49` / `session-end.ts:36` | The only guard against re-storing a transcript turn is `SELECT 1 FROM observations WHERE session_id = ? AND content_hash = ?`. **`content_hash` is NULL on 702,058 / 704,049 rows (99.72%)** and `= NULL` is never true in SQL, so the guard cannot match; and **`session_id` is effectively a constant** — one value covers 697,888 rows (99.12%) from 2026-03-15 to 2026-07-31 — so it carries no discrimination either. Every PreCompact and SessionEnd therefore re-walks the transcript and re-inserts every turn still visible in it. Measured: 704,049 rows are **31,219 distinct `(tool_name, input_summary, output_summary)`** (22.6 copies each); Conversation is **689,549 rows / 17,808 distinct turns** (38.7 copies each); the worst single turn appears **414 times across 22 distinct days and 28 cwds**. Consequence at the top of the stack: `tool_transitions` holds **1,576,273** `Conversation→Conversation` edges from 689,549 Conversation rows (2.29x), and the `patterns` upsert accumulates that across runs into **frequency 43,581,138** for the tautology — **the #1 item in 1,212 of 1,217 briefings (defect #7) is authored by this bug**. `scripts/audit_tier1_duplication.py --check`. **Not patched** — the fix is a real dedup key and a real session id, and it changes what gets written |

**#3's two halves have different evidentiary standing.** The *mechanism* — that `captureContext`
bypassed the scorer, hence 98.3% never scored — rests on a code read and is single-seat. The
*consequence* — that the dimensions carry no per-item information — is checkable from value
cardinality alone and has been independently confirmed (kimi, notice 444 §4 + CBP selection audit).
Quote the consequence; flag the mechanism as single-seat.

**Do not "fix" #4 by raising the threshold.** The obvious repair — `overlap >= 3` instead of `>= 2` —
makes the length dependence *stronger*, since clearing a higher fixed bar depends even more on how
many tokens the item brought. #4 needs a different outcome definition, not a retuned one, and
`scripts/audit_outcome_instrument.py` is the gate it must pass.

**#4 and #6 are independent, and every per-tier rate needed both to be working.** The cheapest
demonstration of #4 is free in the store: the identity tier's three constant strings are 26, 28 and
29 tokens — length held fixed by luck — and score 7.8% / 32.5% / **78.5%**, where the top two are
near-duplicates *of the same fact*. Content and length held nearly constant, 2.4x the rate; what
varies is how ambient the item's vocabulary is in the fleet's prose. #6 is what makes the rate
uninterpretable even if #4 were repaired: a deterministic top-3 over a static 6-row pool returns the
same three items forever, so "identity is relevant 39.6% of the time" scores a fixed playlist. Two
gates, failing for unrelated reasons:

- `scripts/audit_outcome_instrument.py` — *can the outcome column see the item?*  (exits 1)
- `scripts/audit_selection_tier.py` — *is there a selection worth measuring?*  (exits 1)

Both must pass before any per-tier number is quoted. The struck "9% of patterns are relevant" figure
is what two composed failures look like from the outside: a plausible number that convicted a tier
neither instrument could see.

**The abstain question, open.** Every metric in §8.1 assumes the briefing is a set of choices. It is
three slots per kind, always filled. If the selector could abstain, *choosing to surface* would
become an act with a counterfactual attached and part of §8.2's holdout would fall out of normal
operation — or it would smuggle an unrandomized confound into production. Unresolved; it gates the
arm design, so it is argued before ε is chosen, not after.

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

0. **Step 0 — restore the channel, then seed the tier.** Nothing below can run first. Acceptance
   test is `python3 scripts/audit_selection_tier.py --db <store>` clearing CHANNEL, which now has
   **three** conjuncts, all confirmed FAILING today on every store we have:
   - `attempt-outcomes` — tool events recorded with their payload, not the literal `""`. (Live
     shards write **no tool rows at all**: `tool_name` is only `Conversation`/`user_prompt`.)
   - `session-boundaries` — sessions closing. Archive 33.8%; live shards 8–67%.
   - `identity-tier` / `identity-writer` — the withhold arm's treatment must exist in the shard the
     arm targets, and must have been written by a path that still exists. Three seeding options,
     with their costs: `auto_promote_identity=1` (threshold 3→1; the code's own comment calls this
     the dangerous path, and it makes the treatment a single unreproduced guess); wait for
     re-occurrence (observed rate **1 proposal / 4 months at frequency 1** — not a schedule); or
     write the tier directly as `human-confirmed`. **Recommend the third:** the arm asks whether
     withholding a fixed block changes behaviour, not whether the block was earned.

   An arm whose treatment is absent is not underpowered; it is unidentified, in the same way an
   unobservable regression is (see the 4a withdrawal in
   `forum/cbp-the-store-we-measured-stopped-taking-writes-...-2026-07-31.md`).

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
   cannot be used to show a new tier does better. Nor is it rescued by repairing the outcome column:
   the pattern tier's rate is also 72.1% carried by three items under a quota that cannot abstain
   (defect #6). Both gates — `audit_outcome_instrument.py` and `audit_selection_tier.py` — must exit
   0 before any per-tier rate appears in a criterion.
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
- **ρ, the intra-session correlation of the outcome.** Decides whether the repair-adoption arm is a
  24-day or a 4-month question (§8.1). Unmeasurable until outcome capture ships (defect #5); it is
  the first number the capture pilot should report.
- **The positive-knowledge coverage limit.** All three §8.1 outcome metrics score *warning and
  repair* memories. A memory with no prior failure attached leaves a thinner behavioural trace, so a
  utility loop built on recurrence will systematically undervalue the memories that prevent first
  failures — and the system would quietly optimize for firefighters. Named now rather than
  discovered as instrument #6. Owner: kimi-code (joint 1).

**Closed by notice 443 + the holdout sizing pass:**

- ~~Can recall utility be measured at all without an explicit counterfactual?~~ → **No, and the
  design is now specified (§8.1).** Both seats independently reached the suppression arm as the only
  design with the control inside the measurement. The follow-on argument settled three things the
  first version left open: the estimand is *surfacing*, not memory; the outcome must be act-grain,
  since a randomized arm scored on token recurrence would only put an RCT's confidence behind a dead
  gauge; and **the randomization unit must match the outcome's attribution grain** — item-level
  randomization against a session-level outcome relocates defect #4's item-blindness into the design
  and costs a measured factor of k=9 (5.3 years vs 7 months). What remains open is not *whether* but
  ρ and the coverage limit, both above.

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
