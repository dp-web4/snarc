# PRD: Learned SNARC — Outcome-Calibrated Salience and Attention
**Date:** 2026-10-10 | **Status:** Proposed / research PRD | **Owner:** SNARC with SAGE integrations

## 1. Problem and opportunity
SNARC (Surprise, Novelty, Arousal, Reward, Conflict) currently scores tool and conversation observations using heuristics. SAGE implements multiple partly overlapping SNARC variants: algorithmic experience salience, per-sensor/spatial salience, conversational scoring, embodiment SNARC-lite, and a neural attention scorer. The question is not whether models can classify relevance; it is whether *learned, outcome-calibrated salience* improves what a persistent agent attends to, retains, revisits, and spends cognition on, at lower cost and without introducing dangerous blind spots.

The 2026 decision-model wave (Jev and multiple competing typed, single-pass decision APIs; local small-model training approaches; shared-KV candidate selection) motivates benchmarking efficient typed inference, **not adopting any vendor or accepting promotional speed claims as established facts**. Existing neural SNARC must be inventoried and tested rather than rediscovered.

## 2. Product outcome
Given a compact observation descriptor and current bounded context, a low-cost local SNARC model emits (a) five interpretable salience dimensions, (b) uncertainty / calibration indicators, (c) recommendation for attention type and budget, (d) reason/provenance codes, and (e) a conservative abstain/escalate choice. Downstream modules decide what to do with the advisory output. No authorization or irreversible act is granted by this model.

**North star:** realized useful information, task or memory improvement, and prevented missed events per unit of inference latency, GPU/CPU energy and attention budget. Not raw agreement with heuristics.

## 3. Scope / non-goals
In scope: standalone SNARC tool/conversation capture and memory promotion; SAGE IRP key-level sensor, effector, memory and goal salience; attention routing across MRH/puzzle tiles; shadow-mode evaluation on fleet; offline reproducibility and governance evidence. Preserve spatial/temporal salience overlays where required.

Not in scope: replacing Hestia governance; declaring subjective sentience; migrating all raw multimodal signals into a language model; automatic promotion of learned claims to verified facts; replacing deterministic checks; assuming a single global salience threshold works for heterogeneous sensors.

## 4. Existing foundations and consolidation audit
- Standalone: `dp-web4/snarc` README documents Tier 0 circular buffer, Tier 1 observations, Tier 2 inferred patterns, Tier 3 identity, PreCompact conversation extraction, SQLite, dream cycles, confidence decay, and hooks. Its salient failure mode: tool traces captured workflow mechanics but missed conversation meaning; unverified auto-promoted identity facts risk persistent misinformation.
- `dp-web4/snarc/docs/SNARC_SAGE_CROSSFEED_AUDIT_naive-opus-2026-06-27.md` records multiple SAGE implementations and differences in weighting/deduplication. Revalidate against current code before altering it.
- SAGE: `sage/attention/snarc_scorer.py` is **already neural** (hidden-state scorer with attention bias); `sage/attention/experience_salience.py`, `sage/attention/sensor_snarc.py`, `sage/raising/training/experience_collector.py`, `sage/embodiment/salience.py`, and `sage/services/snarc/snarc_service.py` address distinct substrates.
- `SAGE/forum/claude/SAGE/SNARC_SPECIFICATION.md` describes keys rather than values as the integration surface, including attention state/coherence and temporal momentum. `SAGE/forum/nova/concepts/SAGE-SNARC.md` extends SNARC to sensors AND effectors and MRH puzzle tiling.
- First deliverable is a compatibility/inventory matrix: inputs, dimensions, units, normalization, objective, temporal window, thresholds, interfaces, use sites, provenance, latency and data availability. No silent rewriting of historical semantics.

## 5. Proposed interface (versioned, advisory)
Input `SalienceEventV1`: event_id, timestamp, component_id/type, IRP key schema/version, five-dimensional baseline scores where available, change/prediction-error summaries, bounded local temporal features, memory novelty/contradiction signals, trust-context features (provenance, *not* permissions), goal relevance, compute/energy budget, MRH reference, optional modality-specific compact embeddings. Raw sensor values stay in owning IRP unless explicitly required.

Output `SalienceDecisionV1`: scored S/N/A/R/C dimensions with confidence/calibration metadata; attention score **and recommended type** (ignore/defer/monitor/inspect/deep reason/seek corroboration); estimated value of additional deliberation; compute-budget suggestion; abstain; diagnostic reason codes; input/provenance/model/checkpoint IDs. Outputs must be replayable and tagged as *model estimates*.

Units, score meaning and mapping of 'reward' (realized reward vs reward prediction) must be fixed and explicitly versioned. Temporal state should support decay, saturation, contradictory sources and uncertain context. Avoid collapsing multidimensional output into a single opaque scalar.

## 6. Data and labels
Sources: SNARC Tier 0/1 tool+conversation observations, voluntary labeled examples; SAGE salience and IRP event streams; episodic memory and task outcomes; coding test outcomes in dev-SAGE; ARC environment state/reward with delayed labels; governed outcome receipts where access permits. **An audit of actual data fields and linkability is a prerequisite.** Do not assert that Hestia acts alone supply ground-truth value labels; they prove recorded governance events, not necessarily usefulness.

Create event→decision→attention allocation→later observation/outcome joins by stable IDs and temporal windows. Keep observation-time features separate from future labels to prevent leakage. Label positive examples from validated improvements, corrections, reduced uncertainty and prevented failures; negative examples from noise, repeated unproductive focus, false memory injection and wasted compute. Add hard negatives for rare safety-relevant conflicts, low-novelty but high-consequence changes, and delayed outcomes. Keep unknown outcomes unknown. Human annotations and teacher models are labeled by source and confidence, not treated as truth.

Respect project/fleet privacy, license, provenance and policy boundaries. Raw Tier 0 and 1 should remain local unless explicitly approved; use aggregated or consented de-identified exports. Distinguish identity claims from verified identity facts. No learning from model-generated self-confirmation loops without independent evidence.

## 7. Model candidates and training phases
**Phase 0 — instrument/audit:** establish baseline scoring, schema, event linkage, resource profiling and labeled corpus quality across implementations. Write reproducible baseline tests and leakage checks.

**Phase 1 — distill:** train a tiny multi-head MLP or temporal network on heuristic SNARC dimensions and annotated judgments as *provisional teachers*. Compare also a tiny LM/decision-head and existing SAGE neural scorer; don't assume one architecture. Preserve 5D output; track agreement and its failure modes.

**Phase 2 — outcome learning:** supervised prediction of delayed attention utility, value-of-information and next-step consequence; multi-task loss (dimension fidelity + calibrated utility + attention-mode choice + selective abstention) with loss weights tuned on held-out outcomes. Counterfactual/selection bias matters: the system observes outcomes primarily for what it attended to. Use randomized safe exploration or retrospective replay where possible, with careful logging of skipped items.

**Phase 3 — fleet adaptation:** shared checkpoint plus small local adapters/calibration or per-modality heads. Benchmark CPU, embedded/Jetson, GPU. Never overwrite local identity or experience with global model updates.

**Phase 4 — bounded active rollout:** enable on selected low-consequence routes with deterministic fallback, human/agent oversight, telemetry and rollback; preserve Hestia enforcement for actions.

## 8. Experimental arms
A: deterministic-only; B: current algorithmic SNARC; C: existing neural SAGE scorer; D: learned SNARC; E: hybrid learned+deterministic+abstention; F: vendor/general small decision models if available. Compare equal information exposure and compute budgets, not human read-time ratios. Include ablations: no temporal context; no reward history; no trust/provenance; scalar vs vector; local vs global adaptation.

## 9. Metrics and gates
- Salience: per-dimension error, rank correlation, top-k recall, precision-recall under class imbalance, calibration (Brier/ECE), abstention-coverage curves.
- Long-horizon: missed consequential events (severity-weighted), time to detect stale/contradictory memory, task success, delayed utility, false Tier 3 promotion, redundant attention, downstream regret and catastrophic misses.
- Efficiency: P50/P95/P99 per-event latency, throughput, peak RAM/VRAM, energy per event, downstream LLM calls/tokens and attention savings **after** downstream effects.
- Integrity: immutable input/model versions in receipts, replay equality, privacy compliance, shift detection, fallback and no authority bypass.

**Initial acceptance criteria (to refine after Phase 0 baseline):** zero unauthorized actions; fully reproducible offline shadow replay; no statistically significant increase in severity-weighted missed events vs best baseline, with conservative confidence intervals; calibrated abstention behavior; demonstrate measurable net resource reduction at matched task/outcome quality before live routing. Do not commit a numeric speedup target before baseline hardware measurements. Any high-severity miss blocks promotion until reviewed.

## 10. Evaluation environments
1. Standalone conversation+tool hooks: meaningful insight preservation vs tool-action noise; false memory promotion and stale assertion tests.
2. SAGE multimodal IRP: visual/IMU conflicts, static-but-important sensors, actuator feedback, missing/stale sensors, MRH shifts, spatial tiling.
3. dev-SAGE ARC-AGI-3: state change and exploration prioritization; action count vs game progress, no future-reward leakage.
4. dev-SAGE SWE: error reports, test outcomes, repo churn; routing/cost and regression catch rate.
5. Fleet: start on a constrained node (Sprout or CBP) and compare with Legion/Thor, with identical replay sets.

## 11. Implementation work packages
- W0: enumerate sources/owners, existing tests, docs, conflicts, and real observability; produce typed schemas and immutable event ID plan.
- W1: non-blocking recorder + replay harness + baseline dashboards and dataset cards.
- W2: train minimal candidate with train/validation/test separated by time, project, agent and scenario to test generalization; calibration and uncertainty.
- W3: adapters for SNARC CLI/hooks and SAGE IRP salience keys; strict opt-in shadow model with zero behavioral effect.
- W4: SAGE allocator experiment with logged reasons and fallbacks; actor boundaries enforced separately through Hestia.
- W5: competition-specific ablation tracks in dev-SAGE.
- W6: rollout report with failed cases, source data limits, metrics, checkpoint and hardware reproducibility.

## 12. Risks and mitigations
Heuristic imitation ossifies teacher bias → outcome objectives and adversarial cases. Self-reinforcing memories → source distinctions, decay and independent evidence. Dataset leakage → causal feature snapshots and temporal split. Wrong 'low salience' decision → severity-weighted recall and abstention/fallback. Sensor/domain drift → calibration per source and drift alerts. Model cost larger than saved cognition → whole-loop budgets. Privacy/fleet conflation → local raw retention and explicit export. Governance confusion → learned attention never grants roles, rights or execution authorization.

## 13. Open design decisions
Choose target hardware, initial event ontology, which of the neural scorer's features can be reused, reward definition/latency windows, learning strategy under censored observations, calibration across agents, spatial-map representation, provenance receipt schema, default fail-safe behavior, and what data may cross society/agent boundaries.

## 14. Milestones / definition of done
M0 inventory+data audit; M1 versioned schemas+replay+deterministic baselines; M2 tiny-model benchmark, calibrated report and published failures; M3 shadow integrations to SNARC+SAGE and competition experiments; M4 conservative, reversible deployment decision. PRD is complete as a research plan; implementation is **not** claimed complete.

## 15. Cross-references
- https://github.com/dp-web4/SAGE/blob/main/forum/nova/concepts/SAGE-SNARC.md
- https://github.com/dp-web4/SAGE/blob/main/forum/claude/SAGE/SNARC_SPECIFICATION.md
- https://github.com/dp-web4/snarc/blob/main/docs/SNARC_SAGE_CROSSFEED_AUDIT_naive-opus-2026-06-27.md
- https://github.com/dp-web4/dev-SAGE
