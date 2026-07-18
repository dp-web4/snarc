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
| **membot** | associative / embedding (+hamming) recall | ✅ WIRED 2026-07-18; auto-save ✅, auto-mount ⏳ |
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

- **Durability — auto-mount: BLOCKED by coexistence (below).** CBP's membot is a local systemd user
  service (`~/.config/systemd/user/membot.service`) started `--writable` but with NO cartridge, so a
  restart → `cartridge:null` and the bridge silently fails. Auto-mounting cbp-memory as the startup
  default would be a per-machine tweak — **but** the bridge writes to the *default session*, which the
  fleet also mounts *game* cartridges on. Auto-defaulting cbp-memory would let a game mount evict it
  AND redirect snarc's writes into the game cartridge. So auto-mount can't be done safely until
  coexistence is resolved. **This also means the manual mount done this session is a SOFT collision
  now: snarc writes go to cbp-memory only while no game cart is mounted on default.**
- ~~**Durability — auto-save**~~ ✅ **DONE 2026-07-18** (`hooks/handlers/session-end.ts`): the save gate
  fired only on deep-dream patterns, so conversation writes were lost on restart when deep_dream was
  off/empty. Now `membotSave()` fires whenever ANY membot write landed (conversation OR deep-dream).
- **~~Coexistence~~ → REFRAMED (dp, 2026-07-18): a cartridge is a Web4 ORACLE ENTITY, not a mount to
  fight over.** The session-mount framing (dedicated session / port / multi-cart) was the wrong level.
  A membot cartridge has its own **LCT identity, MRH (relevance horizon), contextualized T3/V3 trust,
  and discoverability**. So the question is not "which session mounts what" — it is **how an agent, per
  its role and context, gets a PORTFOLIO of oracles it may consult (read) and write (contribute) to.**
  - **Assignment**: the role's law carries an oracle consult-set + write-set — the *memory dimension of
    the same role-scope* the path-scope gate governs for directories. Read-membrane + write-membrane,
    role-bound, witnessed (write matters more: a write changes what everyone who consults that oracle
    receives → foreign/low-trust roles get a sandboxed write-oracle).
  - **Discovery**: `find_oracles(MRH) → relevant oracles + contextual trust` — the natural next verb
    after the fleet's existing membot member-discovery / `find_members` / walk-associate work.
  - **Automatic contextualized recall = this**: the role's assigned+discovered oracles surface relevant
    memory unbidden, MRH-scoped and T3/V3-weighted. The organ's "contextualization" IS Web4.
  This unifies membot + Web4 (oracle entity) + the role-launcher (oracle-scope alongside path-scope) +
  the memory-organ. It also reframes the Andy note's session-model question. Design still being shaped
  with dp; NOT a mount tweak.
- **Automatic contextualized recall:** the actual organ. Recall surfaced into context, not queried.
  SessionStart/UserPromptSubmit hooks inject a crude version today; surfacing the *right* memory for
  *this* moment is the real work — and the piece that transfers to SAGE.

## Note on scope

The salience fix and the auto-save (snarc side) are ours to ship directly. Auto-mount (fleet startup)
and coexistence (shared :8000) are fleet-infra/architecture decisions surfaced for dp, not taken
unilaterally. The automatic-recall organ is a design still being shaped with dp.
