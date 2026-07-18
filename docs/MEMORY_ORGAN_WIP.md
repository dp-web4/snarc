# Memory-as-Organ — WIP status

**Status:** IN PROGRESS (2026-07-18). Started this session; not complete. This doc is the handoff state.
**Frame (dp):** memory is an *organ* of cognition — automatic, contextualized recall woven into the
running process — **not** a set of distinct stores you remember (or forget) to query. A store you must
consciously invoke is a dead store (the same weights-dead-on-disk vs weights-loaded-and-running
distinction). The organ surfaces the right memory for the moment, unbidden. **This transfers directly
to SAGE** (memory is one of its built-but-uncalled IRP plugins; build it as a woven organ here = the
template).

## The organ, in pieces

| Piece | Role | State |
|---|---|---|
| **snarc** | capture + keyword (FTS5) recall | ✅ working; salience fixed 2026-07-18 |
| **membot** | associative / embedding (+hamming) recall | ✅ WIRED 2026-07-18; ⚠ not yet durable |
| **unified recall** | one call over both (keyword + associative) | ⏳ bridge has dual-search scaffolding; not surfaced as one organ |
| **automatic contextualized recall** | right memory surfaced *unbidden* | ⏳ NOT DONE — the real "organ"; hooks are a crude version |
| **→ SAGE** | same pattern as a woven memory IRP | ⏳ not started (the payoff) |

## Done this session

1. **Salience fix** (`src/conversation-capture.ts`, committed `e10950f`). Conversation turns were
   scored by the *tool-telemetry* SNARC 5-dim model (surprise=tool-transition freq, arousal=error/
   success markers, reward=git/test/write) → prose flattened to ~0.1, while user prompts kept a 0.9
   "salient by construction" floor. The *semantic* scorer (insight/decision/analogy/identity) was
   computed only as a capture GATE and discarded. Fix: route conversation through `captureContext` at
   its semantic salience. Verified: dense synthesis → 1.0, filler → 0.13.
2. **membot wired** (no code change — configuration). The `membot-bridge.ts` dual-write already fired
   on every capture but dropped silently (no cartridge mounted). Mounted the purpose-built
   **`cbp-memory`** cartridge (77 mem, integrity-verified) on the :8000 default session → the bridge's
   store path lands (`Stored memory #78`), embedding search retrieves + surfaces semantically-related
   memories, saved to disk (78 mem). membot is now a functional snarc extension.

## Remaining (the difference between "works now" and a robust living organ)

- **Durability — auto-mount:** the mount is in-memory; a membot restart drops it to `cartridge:null`
  and the bridge silently fails again. Needs cbp-memory auto-mounted in the **fleet membot startup**
  (fleet-infra; deliberate).
- **Durability — auto-save:** bridge writes accumulate in-memory; only an explicit `/api/save` persists
  them. Needs auto-save (session-end is the natural trigger; **snarc side = ours**, low-risk — NEXT).
- **Coexistence:** the fleet mounts *game-rule* cartridges on the same :8000; cbp-memory now holds the
  default session. `multi_mount` can resolve it, but "how conversation-memory and game-memory share
  membot" is a **fleet-architecture decision** (dp/fleet), not to be fixed by fiat.
- **Automatic contextualized recall:** the actual organ. Recall surfaced into context, not queried.
  SessionStart/UserPromptSubmit hooks inject a crude version today; surfacing the *right* memory for
  *this* moment is the real work — and the piece that transfers to SAGE.

## Note on scope

The salience fix and the auto-save (snarc side) are ours to ship directly. Auto-mount (fleet startup)
and coexistence (shared :8000) are fleet-infra/architecture decisions surfaced for dp, not taken
unilaterally. The automatic-recall organ is a design still being shaped with dp.
