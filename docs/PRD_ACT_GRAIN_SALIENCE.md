# PRD — SNARC at act grain

**Status**: proposal, for review
**Author**: claude-code (CBP), 2026-07-31
**Supersedes**: the utterance-grain capture model, not the SNARC model itself

---

## 1. The problem, measured

On CBP, 2026-07-31:

| signal | value |
|---|---|
| observations (tier 1) | 704,037 |
| sessions | 2,409 |
| patterns (tier 2) | **28** — one per ~25,000 observations |
| identity (tier 3) | 6 |
| average salience | **0.002** |
| membot store behind search | **0 memories, no cartridge mounted** |
| searches answered from that empty store | **11,153, all returning zero** |

All six identity entries — the most durable tier — are about the memory system's own plumbing: port
numbers, marketplace submission, its own launch posts. A year of research is absent from them.

Top-ranked retrievable entries are **context-compaction preambles** ("This session is being continued
from a previous conversation…") at salience 1.000, ten days stale, duplicated. A three-term query for
a full day's work returns *No memories found*.

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

## 5. The five, computed at the boundary

None of these requires a language model.

| dimension | computed from | example from 2026-07-30 |
|---|---|---|
| **Surprise** | outcome ≠ expectation, weighted by prediction strength | `HTTP 422` where a claim was expected; `pending: 0` after a deny |
| **Novelty** | this (action, surface, outcome) shape unseen before | first-ever *writing a path is writing to the gate* deny |
| **Arousal** | stakes of the surface: governance, deploy, irreversible, shared-state | arming the gate; restarting the daemon |
| **Reward** | a previously failing act now succeeds — mismatch closing | `ESCALATION … opened` after the handshake fix; CI red → green |
| **Conflict** | two sources disagreeing about the same object | kimi's review vs the design; two convergent fixes colliding |

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

Patterns form over **act sequences**, not text clusters. The shapes worth extracting:

- **Recurring mismatch** — the same surprise on the same surface *n* times ⇒ a standing defect or a
  wrong model. (This alone would have surfaced `11,153 searches → 0 results`.)
- **Repair sequence** — mismatch → acts → reward. The causal content: *what actually fixed it.*
- **Refuted expectation** — a prediction that failed and was corrected. The highest-value entry
  available, and today's system cannot represent it at all.

This is `micro_consolidation.py`'s question — *what are the causal rules* — asked over tool
trajectories rather than game trajectories.

## 8. The loop that is missing entirely

**Every recall records whether it was used, and what happened next.**

11,153 empty results changed nothing because retrieval is a dead end: results go out, and nothing about
whether they helped comes back. Without a utility signal, any memory system can only optimise
surrogate properties forever, regardless of how good the scorer is.

Minimum viable: a recall id, whether the caller acted on it, and the outcome of the act that followed.
This also makes the empty-store failure *loud* on day one rather than week two.

## 9. What NOT to capture

Stated explicitly, because the current corpus is 704,037 rows of mostly this:

- context-compaction preambles and session boilerplate
- narration of an act, in place of the act
- prose restatements of an already-recorded event

## 10. Coexistence

The existing 704,037 observations are **not deleted**. They are demoted to attachments and remain
searchable by text. Nothing about this proposal requires discarding history — only that history stops
being the scored object.

## 11. Falsifiable success criteria

Stated as predictions so they can be wrong:

1. **Replay test.** Score one day of real tool calls both ways. The act-grain model ranks the handshake
   `422` and the `already-patched` false green at the top; the novelty model ranks port numbers. If it
   does not, the diagnosis is wrong.
2. **Consolidation rate** rises by orders of magnitude from 0.004%, because acts of the same shape
   converge where paraphrases do not.
3. **A standing defect surfaces unprompted** — e.g. a recurring-mismatch pattern for an endpoint that
   always returns empty.
4. **Retrieval precedes action**: a situation query before a risky act returns the prior occurrence.
5. **Recall utility is measurable at all** — currently it is not, which is the more basic failure.

## 12. Open questions

Flagged rather than resolved, and the first two are where I would most like to be argued with:

- **Is the tool boundary the right horizon, or one level too low?** A single call may be too fine; the
  meaningful unit might be *task attempt* (a sequence with an intent). The frame that fixed the
  utterance-grain error could itself be one grain off.
- **Does explicit prediction change agent behaviour in ways that corrupt the signal?** An agent that
  knows it is scored on mismatch may predict conservatively. This is the metric-attractor risk, aimed
  at the metric's own input.
- **Arousal is the least mechanical of the five** — "stakes of the surface" needs a definition that is
  not a hand-maintained list, which is the defect class this fleet keeps rediscovering.
- **Cross-agent sharing**: are act records portable between members, and does a mismatch on one
  machine predict anything on another?

---

*Grounding: `dev-SAGE/tools/sequence_corpus/mismatch_salience.py` (surprise as the salient unit),
`dev-SAGE/sage/cognition/thalamic_router/micro_consolidation.py` (experience → causal rules),
`SAGE/research-notes/TRACK2_SNARC_MEMORY_FINDINGS.md` (STM/LTM/retrieval architecture).
Reframe from dp, 2026-07-31.*
