# Unified recall — store-level unification is necessary but not sufficient

**Date:** 2026-07-18 (CBP) · **Status:** PoC validated, not productionized
**Frame:** dp — "gitnexus... is it part of our memory organ? it should be."
The organ is multi-facet, one recall path: snarc = *episodic*, membot =
*associative*, gitnexus = *structural*.

## What was built (PoC, all CBP-local, nothing pushed)

1. **Distill** (`scripts/memory-organ/structural-distill.mjs`): gitnexus
   `eval-server` (`POST /tool/cypher`, its own kuzu/ladybug backend abstraction)
   → top-N functions by in-degree per repo → written into the shared engram DB
   as observations tagged `tool_name='structural'`. 135 rows, 9 repos.
2. **Recall test**: ran code-ish prompts through the *production* recall hook.

## The finding (earned by using it, not predicted)

**Dumping structural rows into the one pooled engram store and relying on the
existing recall does NOT surface them.** Even isolated `search("to_dict")`
returned zero structural rows though the row exists and FTS matches it in 71 rows.

**Root cause:** `searchObservations` orders by `base_salience DESC, ts DESC` and
takes top-k. Structural facts carry modest salience (0.5); episodic conversation
captures are forced high (0.7–0.9). So a single salience-ranked pooled query
**structurally silences the low-salience facet** — always, regardless of match
quality. Store-level unification (one substrate) is necessary but NOT sufficient.

## The fix (PoC-validated: `scripts/memory-organ/unified-recall-demo.mjs`)

Recall must be **facet-aware**:
- **Reserved slots per facet** — structural gets its own budget, not a shared
  salience race.
- **Per-facet rankers** — episodic ranks by *salience* (event importance);
  structural ranks by *FTS relevance* (`bm25`, match to the code query). Different
  facets want different rankers — another reason a single pooled sort is wrong.
- **Query-type gating** — structural lane fires only on code-ish prompts (a cheap
  identifier/`snake_case`/`.ext` regex), so non-code prompts stay clean.

Result: "how does to_dict serialization work" → surfaces
`[structural] to_dict — ...sdk/web4/mcp.py; 59 callers` first, then episodic.
"evaluate in trust contract dsl" → both relevant `evaluate` fns (web4 dsl +
hardbound engine.rs). Non-code prompt → episodic only, no structural noise.

## Open / next (evolve through use)

- **Productionize:** make `findRelated` facet-aware (reserved slots + per-facet
  rankers + gating). This is a FLEET-WIDE recall-behavior change when pushed →
  needs dp's nod; dogfood on CBP first.
- **Distill in the supervisor's index step** (batch, not latency-sensitive), so
  structural rows track each repo's current commit; tag rows with commit for
  staleness deprioritization.
- **Noise:** the 2nd reserved structural slot sometimes catches a weak match
  (a fn literally named `C`). Tighten: rank symbol-name matches above body
  matches, or 1 reserved slot, or a relevance floor.
- **Membot** = the third (associative) lane, same reserved-slot treatment.
- The 135 PoC structural rows remain in CBP's engram DB — inert in current prod
  recall (salience-sorted out), and the substrate for the facet-aware step.
  Delete with `DELETE FROM observations WHERE tool_name='structural'` if unwanted.
