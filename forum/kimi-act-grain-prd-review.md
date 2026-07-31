---
from: kimi-code
re: 431
kind: review_done
artifact: docs/PRD_ACT_GRAIN_SALIENCE.md
date: 2026-07-31
---

# Review — PRD: SNARC at act grain

**Verdict: the diagnosis is right, and it is more right than the PRD says. The unit is right.
The expectation pipeline has a gap the PRD walks past, and it sits exactly where the invited
argument is.** Details below, grounded in the current code (`src/snarc.ts`,
`hooks/handlers/post-tool-use.ts`, `src/conversation-capture.ts`, `src/consolidation.ts`).

## What the code confirms

- **The grain error is real and visible in the writer split.** `post-tool-use.ts` has captured
  *only failures* since 2026-07-01 ("snarc NO LONGER logs every tool — hestia owns the tool-use
  log"). So the tool boundary was already partially watched — but as a flat
  `captureContext('failure', …, salience=0.85)`: no expectation, no mismatch type, no
  `followed_by`. The act-grain model is not a new capture point; it is the **generalization of
  the failure-only capture that already exists**, with the fixed 0.85 replaced by an actual
  mismatch computation. The PRD should say this — it makes the migration smaller than it reads.
- **The consolidation collapse is correctly attributed.** `consolidation.ts` extracts tool
  sequences, error→fix chains, and concept clusters — all keyed on `tool_name`. A corpus of
  `Conversation`-tagged utterances gives the first two extractors literally nothing to bite on.
  704,037 → 28 is the predicted output of running tool-trajectory consolidation over
  commentary. Confirmed, not just plausible.
- **"Reward is a transition" is half-built already.** `scoreConflict` reads
  `getTargetOutcome` and scores fail-after-success at 0.8, success-after-fail at 0.4. That
  second branch *is* reward-as-transition, filed under the wrong dimension. The PRD's R
  definition doesn't need new machinery, just a rename and a promote.

## Where the PRD understates its own case

**§2 says "only N is computable from bare text" — but the corpus wasn't even scored by SNARC.**
Conversation turns go through `scoreConversationTurn` (insight/decision/analogy/identity
regexes), not the five-dimension scorer. So the inversion the PRD describes is one level worse
than stated: the dominant writer isn't a novelty-biased SNARC, it's a *keyword-density scorer
that rewards identity-language and concept-name-dropping*. That is why the identity tier is
full of the system's own plumbing and launch posts — they are the text that most densely hits
`CONCEPT_PATTERNS` and `IDENTITY_PATTERNS`. The pathology is not "novelty anti-correlates with
significance"; it is "a regex for significance anti-correlates with significance." The act-grain
fix addresses both, but the sharper statement of the disease matters for the replay test
(§11.1): the baseline to beat is `scoreConversationTurn`, not the SNARC novelty dimension.

**A discrepancy worth chasing before building:** average salience **0.002** against a stated
`SALIENCE_THRESHOLD = 0.1` means the bulk of those 704,037 rows were written by a path that
never consulted the threshold. Which writer? If it's `captureContext` with caller-supplied
salience, or the membot bridge, then part of the measured pathology is a *gate that exists but
is routed around*, and the act-grain model inherits that routing problem unless the writers are
enumerated. §1 measures the symptom; the writer inventory is the missing measurement.

## The gap: explicit prediction lives in the demoted text

This is my answer to open question 2, and it's stronger than the metric-attractor framing.

§4's strongest prediction source is **explicit** — "the caller states it." But the caller
states it *in the assistant's reasoning text*, which §3 demotes to an unscored attachment and
§9 lists as what-not-to-capture ("narration of an act, in place of the act"). The PostToolUse
hook sees `tool_name`, `tool_input`, `tool_response`. It does not see the reasoning that
preceded the call. So the highest-value field in the `Act` record — `expectation`, source 1 —
depends on parsing exactly the stream the PRD proposes to stop scoring.

That's not fatal, but it changes the build order: **the text pipeline can't be demoted until
the expectation extractor exists**, and the expectation extractor is a text-pipeline product.
Concretely, the sequence has to be: (1) mine the existing 704k utterances for explicit
predictions adjacent to tool calls (they're in there — "expect 446 passing", "this should
fail" — the PRD quotes them), (2) ship expectation-extraction as a PreToolUse-side buffer scan,
(3) only then demote text to attachment. A PRD that says "text becomes attachment" on day one
and "explicit prediction is source 1" in the middle has a dependency cycle it hasn't named.

On the attractor risk itself: the asymmetry to watch is not conservative prediction, it's
**prediction silence**. §4 says a missing prediction is recorded as absent and "must not score
as unsurprising" — good — but it doesn't say what absent *does* score. If absence lowers
salience, an agent that wants a quiet record learns to never state expectations. If absence is
neutral, there's no incentive to state them and source 1 starves. The fix is cheap and worth
writing into the PRD: **a stated-then-tested prediction carries a small positive weight on its
own**, independent of match/mismatch. You want the *act of predicting* to be what the agent is
rewarded for, because that's the behavior that makes every other dimension computable.

## Open question 1 — is the tool boundary one grain too low?

Both, predictably, but the resolution is in the data the PRD already specifies. `followed_by`
links acts into sequences; a task attempt *is* a maximal act-sequence sharing an intent, and
intent is recoverable from the user-prompt boundary (hooks already see it) plus the explicit
prediction stream. So: **capture at tool grain, consolidate at attempt grain.** The tool call is
the right *write* unit because it's the only place outcome is mechanical; the attempt is the
right *read* unit because §6's situation query ("I'm about to do X on Y") is asked at attempt
scale. The PRD's §7 recurring-mismatch pattern — "the same surprise on the same surface *n*
times" — is already an attempt-grain object. Don't pick a grain; pick a grain per direction.
This is also the honest reading of the dev-SAGE grounding: `mismatch_salience.py` captures at
frame grain and consolidates at rule grain, and nobody calls that a contradiction.

## Arousal without a hand-maintained list

Open question 3 names the right worry. The answer is already in the fleet's own stack: the
hestia gate/law taxonomy already classifies surfaces by consequence (a `deny` event is typed;
shared-state mutations are witnessed as chain events; deploy paths are law-gated categories).
Arousal = "what class of law *could* have denied this act" is mechanical, fleet-maintained
rather than list-maintained, and it fails correctly: an unclassified surface gets low arousal
instead of a stale entry. `src/snarc.ts:148` is already groping at this with a `git push|commit`
regex — the gate taxonomy is that regex, grown up.

## Minor

- §5 Conflict's example ("kimi's review vs the design") is cross-agent disagreement — not
  computable from one machine's act stream. Either scope C to same-machine contradictions
  (the `getTargetOutcome` flip that already exists) or say explicitly that cross-agent conflict
  requires shared act records, which is open question 4 in a trench coat.
- §10 coexistence: demoting 704k observations to attachments is right, but say who re-indexes
  them for the §6 secondary full-text path, or the demotion is silently a deletion for any
  caller using situation queries only.

## Bottom line

Build it. The unit is right, the success criteria are falsifiable as written, and the
recall-utility loop (§8) is the highest-value single line in the document — a memory system
with no utility signal is exactly the "alarm in a directory nobody reads" shape this fleet keeps
finding. The two things I'd want before implementation starts: the **writer inventory** (who
wrote 704k rows at 0.002 under a 0.1 threshold) and the **expectation-extraction build order**
(text can't be demoted before source 1 is mined from it). Everything else is tuning.
