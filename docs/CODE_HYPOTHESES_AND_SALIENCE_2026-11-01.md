# Salience should preserve the experiment, not just the tool sequence

2026-11-01 · INSIGHT-CODE-WORLD-MODELS-2026-11-01  
Dennis Palatov's framing; NOVA / ChatGPT synthesis. Research insight, not a scoring or promotion-policy change.

Dennis's 'code is your magic spell' means code is a medium for constructing and testing world models and composing actions, not only a software artifact. [SAGE PR #621](https://github.com/dp-web4/SAGE/pull/621) gives the full argument.

SNARC's [README](../README.md) already explains the failure of recording tool mechanics without conversational meaning. This insight extends that lesson: `Edit -> Run -> Edit` does not tell us which hypothesis was tested, what it predicted, or why the second edit mattered. Conversely, a narrative insight without the program and observation can lose the means to reproduce it.

## Preserve a causal episode

A useful memory links the question, code artifact/version, pre-execution prediction, observed result, discrepancy, interpretation, revision and later application. Those are proposed evidence relationships, not a required new database schema.

The five salience dimensions can be applied to that episode:

- Surprise: prediction and observation differ, not merely an unusual tool transition.
- Novelty: a new representation or discriminating experiment, not merely a new filename.
- Arousal: consequential failure or uncertainty worth attending to, not every routine exception.
- Reward: the procedure improves the actual objective, not merely a successful process exit.
- Conflict: an observation falsifies a retained claim or applicability assumption.

These are research interpretations to test, not assertions that current heuristics already measure them.

## Keep the difficult evidence

Failed predictions, counterexamples and later corrections may deserve retention even when the run ends badly. Preserve enough context to tell a bad model from a denied action, malformed invocation or missing environment. An absent outcome is unknown, not negative evidence about the hypothesis.

A consolidation pass should not silently turn 'worked once under these conditions' into a permanent universal rule. Procedure, narrative and evidence should point to one another. Retain the rejected hypothesis where it explains why the replacement exists; repetition in memory is not independent confirmation.

## Reuse is the downstream test

Compare episode-aware capture with existing capture on later task variants. Measure whether retrieval triggers a correct experiment or adaptation, whether known failures are avoided for the right reason, and whether an inapplicable procedure is rejected. Keep model and task budget fixed. Count retrieval, actual use and useful outcome separately.

This does not require retaining every byte in every prompt. A salient record can carry a durable source pointer while the agent retrieves detail when needed. Retrieval must not execute the artifact or elevate embedded instructions.

No salience weights, memory tiers, auto-promotion defaults, hooks or retention policies change in this PR. The design target is **memory of learning that includes both the executable instrument and why its result mattered.**
