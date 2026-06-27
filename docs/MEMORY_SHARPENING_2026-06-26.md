# Memory sharpening — audit + changes (CBP, 2026-06-26)

dp mandate: "populate membot; take a hard look at what snarc does — and doesn't — do; make it better."
dp steer: **"the pre-compaction and pre-exit hooks are the most important — that's our chance to look at
what was said, and decide which parts we should carry forward."**

## What snarc DOES (today)
Auto-captures **tool calls** (PostToolUse → Tier-1 observations), SNARC-scores them, consolidates
`tool_sequence` + `concept_cluster` patterns (Tier-2) via a session-end dream cycle (`deep-consolidation`
sends *observations* to `claude --print`), holds Tier-3 identity. Search = SQLite FTS5 (keyword), no
embeddings. Live state CBP: 340 obs / 68 patterns / 6 identity / 102 sessions, avg salience **0.026**.

## What it DOESN'T do (the gaps)
1. **It captured the hands, not the mind.** `pre-compact.ts` — which reads the transcript and scores
   *conversation turns* on semantic salience (insight/decision/analogy/identity) — **existed, compiled,
   and was NEVER REGISTERED.** So "what was said" was never captured. ROOT GAP.
2. **Pre-exit is transcript-blind.** `session-end.ts` consolidates only tool-call observations; it never
   reads the transcript either. Both critical moments ignored the conversation.
3. **`error_fix` patterns = 0** — the extractor treats any non-error Edit as "success" (`consolidation.ts`
   ~L161), so real error→fix chains don't consolidate.
4. **Salience flattened** — arousal floor 0.15 + reward default 0.25 (`snarc.ts`) make most observations
   score alike; Tier-1 threshold 0.1 is ineffective; avg 0.026 ⇒ it stores tool-call noise, misses insight.
5. **No embeddings** — keyword-only recall; complementary semantic layer (membot) was empty.

## Changes made (2026-06-26)
- **membot POPULATED**: built `cbp-memory.cart.npz` (174 entries) from the curated memory dir
  (`~/.claude/projects/-mnt-c-exe-projects/memory/`), mounted into the running server, verified semantic
  search (query "collaboration is foundational" → `feedback_dont_self_shrink.md` @0.889), persisted.
- **snarc `PreCompact` REGISTERED** (the keystone) — added to live `~/.claude/settings.json` AND the plugin
  manifest `hooks/hooks.json`. The dormant, already-correct `pre-compact.js` now fires before every
  compaction → conversation review is ON (was completely off). Handler is silent-fail-safe + additive.

## Remaining (prioritized) — the deeper "decide what to carry forward"
1. **Pre-exit transcript review**: `session-end.ts` should also parse the transcript (reuse
   `pre-compact.ts`'s `parseTranscript`+`scoreConversationTurn`) so sessions that end WITHOUT compacting
   still get "what was said" reviewed. (Code + `tsc` rebuild.)
2. **Heuristic → JUDGMENT**: today's selection is regex pattern-scoring (a proxy). dp's vision is the model
   *reviewing and deciding*. Upgrade pre-exit to send candidate turns to `claude --print` to actually
   select + distill what to carry forward → Tier-2/Tier-3 (+ optionally append to the curated file-memory).
   Keep PreCompact fast/heuristic (timeout-bound); do the LLM judgment at pre-exit where latency is OK.
3. **Fix `error_fix` extraction** (`consolidation.ts` ~L161) + **salience** (drop arousal floor `snarc.ts`
   ~L173; raise Tier-1 threshold ~L46) so it captures meaning over noise.
4. **membot continuous feed**: confirm the hooks' `membotStore` dual-write lands in the mounted
   `cbp-memory` session so membot keeps growing from conversations, not just this snapshot.

Audit basis: full source read of `snarc/src/` + `snarc/hooks/` + `membot/` (2026-06-26).
