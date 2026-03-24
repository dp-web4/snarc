# SNARC

Salience-gated memory for Claude Code.

Captures what matters, forgets what doesn't, consolidates patterns while sleeping.

> Formerly "engram" — renamed to SNARC to avoid collision with an existing project. SNARC is the mechanism itself: **S**urprise, **N**ovelty, **A**rousal, **R**eward, **C**onflict.

## What's new (v0.3.x)

**Conversation capture** — the biggest change. Previous versions only observed tool calls: edits, commands, searches. We discovered that after hundreds of sessions, SNARC's memory was "Bash → Bash → Bash (51×)" and "focused work on file.py" — mechanics without meaning. The actual value of a session — the insights, the reframes, the decisions, the "wait, damping should not be a thing" moments — lived in the conversation and vanished at compaction. The new `PreCompact` hook reads the full conversation transcript before it's compressed and stores semantically salient turns. The mind, not just the hands.

**Deep dream and auto-promote on by default.** Both were opt-in, both are now on. We're in R&D — the goal is observing what SNARC learns, not gatekeeping it. Deep dream runs at every session end. Identity facts auto-promote to Tier 3 so they influence future sessions immediately. Confidence decay corrects mistakes over time. Disable with `snarc config deep_dream 0` or `snarc config auto_promote_identity 0` if this is too aggressive for your use case.

**Per-project settings via DB, not env vars.** Removed `SNARC_DEEP_DREAM` and `SNARC_AUTO_PROMOTE` environment variables. All settings are now per-project via `snarc config`. Each launch directory is isolated — what you configure for one project doesn't leak to another.

**What we've learned so far**: Salience scoring on tool calls captures workflow mechanics but not intent. The heuristic extractors (tool sequences, concept clusters) are useful for accounting but shallow for memory. The real value is in conversation turns scored on semantic content — insight language, domain concepts, decisions, analogies. Deep dream operating on conversation observations produces qualitatively different patterns than deep dream on tool logs. This is the direction.

## What it does

SNARC captures two things: what you **do** (tool calls) and what you **discuss** (conversation). Tool-use hooks observe every edit, command, and search. Before context compaction, the PreCompact hook reads the full conversation transcript and extracts semantically salient turns — insights, decisions, reframes, connections. Both streams are scored on 5 salience dimensions and stored if above threshold.

At session end, a "dream cycle" extracts patterns from stored observations — either mechanically (heuristic) or semantically (LLM-powered deep dream). Over time, SNARC builds a structured memory of how you work and what you discuss — not just which tools you used, but why.

Context injection is automatic. SNARC injects relevant memories at session start, after each prompt (if related memories exist), and after context compaction. You don't need to query it — it surfaces what's relevant without being asked.

## How it's different from logging everything

Most memory systems capture everything and retrieve by search. SNARC captures selectively using [SNARC salience scoring](https://github.com/dp-web4/SAGE) — the same attention mechanism used by the SAGE cognition kernel:

| Dimension | What it measures | How |
|-----------|-----------------|-----|
| **S**urprise | How unexpected was this tool transition? | Tool transition frequency map |
| **N**ovelty | Are these files/symbols/concepts new? | Seen-before set (SQLite) |
| **A**rousal | Errors, warnings, state changes? | Keyword pattern matching |
| **R**eward | Did this advance the task? | Success/build/test signals |
| **C**onflict | Does this contradict recent observations? | Recent result comparison |

Observations scoring below the salience threshold stay in the circular buffer briefly and then evict. High-salience observations persist. This mirrors biological memory: you don't remember every step, but you remember the one where you tripped.

## Memory tiers

| Tier | Name | Contents | Retention | Storage |
|------|------|----------|-----------|---------|
| 0 | Buffer | Last 50 observations, raw | Session only (FIFO) | In-memory |
| 1 | Observations | Salience-gated experiences (observed) | Decays after 7 days | SQLite |
| 2 | Patterns | Consolidated workflows, error-fix chains (inferred) | Decays 0.05/day, pruned below 0.1 | SQLite |
| 3 | Identity | Persistent project facts (human-confirmed) | Permanent | SQLite |

Injection is epistemically labeled: Tier 1 = "observed (directly recorded)", Tier 2 = "inferred (heuristic — may not be accurate)", Tier 3 = "auto-extracted, verify if unsure". Injection is conservative — biased toward omission. Wrong memory is more damaging than missing memory.

## Dream cycles

Two modes of consolidation:

### Heuristic dream (always runs at session end, <100ms)

- **Tool sequences**: Recurring workflows (e.g., `Edit → Bash(test) → Edit` = TDD loop)
- **Error-fix chains**: Error followed by fix on the same file within 5 observations
- **Concept clusters**: Multiple observations grouped around the same files

### Deep dream (LLM-powered, on by default)

At session end, SNARC sends observations to Claude via `claude --print` for semantic pattern extraction:

- **Workflows**: Recurring approaches (not just tool sequences — understands intent)
- **Error-fix chains**: Problem → solution with semantic understanding
- **Insights**: Something learned about the codebase
- **Decisions**: Architectural choices made during the session
- **Identity facts**: Persistent project knowledge, auto-promoted to Tier 3

```bash
snarc dream --deep                # manual trigger
snarc config deep_dream 0         # disable automatic deep dream at session end
```

### Identity auto-promotion

**On by default.** Deep dream identity facts are automatically promoted to Tier 3 (persistent identity) without human review.

**Why this is on**: We're actively exploring what SNARC learns about projects through deep dream. Auto-promotion lets identity facts accumulate and influence future sessions immediately, so we can observe the feedback loop — what it gets right, what it gets wrong, and how the system self-corrects via confidence decay. This is R&D; the goal is learning, not safety theater.

**The risk**: Deep dream can produce convincing but wrong identity facts. A hallucinated "this project uses Jest" will be injected into every future session until it decays or is manually removed. If you're using SNARC in a context where wrong identity facts cause real problems, turn this off.

```bash
snarc config auto_promote_identity 0   # quarantine: proposals need human review
snarc review                           # see quarantined proposals
snarc promote 42 "test_framework" "Jest"  # human confirms → Tier 3
snarc reject 43                        # delete bad proposal
```

All settings are per-project (stored in the SQLite database for each launch directory).

### Confidence decay

Memories are not permanent. Patterns lose 0.05 confidence per day since last seen. Observations lose salience after 7 days. Patterns below 0.1 confidence are pruned. A memory system that only accumulates is a distortion engine — SNARC forgets.

## Context injection (automatic)

| Hook | When | What |
|------|------|------|
| **SessionStart** | Session begins | Inject briefing: recent patterns, high-salience observations (tool + conversation), identity facts |
| **UserPromptSubmit** | Every user message | Search for related memories, inject if found (most prompts pass silently) |
| **PreCompact** | Before context compaction | Extract semantically salient conversation turns from transcript before they're compressed |
| **PostCompact** | After context compaction | Mid-session dream (consolidate observations so far) + re-inject enriched briefing |

All injection is conservative: patterns need confidence >= 0.6, observations need salience >= 0.6, identity needs confidence >= 0.7. Quarantined proposals are never injected. Below those thresholds, SNARC stays silent.

## Retrieval (MCP tools)

For when you want to dig deeper than automatic injection:

| Tool | Purpose |
|------|---------|
| `snarc_search` | Query across all tiers, ranked by salience |
| `snarc_context` | Observations around a timestamp or session |
| `snarc_patterns` | Consolidated patterns from dream cycles |
| `snarc_stats` | Memory health: tier sizes, salience distribution |

## Fleet portability

Tier 2 and 3 export to markdown for git sync across machines:

```bash
snarc export > memory-export.md     # dump patterns + identity
snarc import memory-export.md       # load on another machine
```

Tier 0 and 1 stay local — they're raw and session-specific.

## Install

### Claude Code Plugin (recommended)

```bash
/plugin install snarc
```

This registers all 6 hooks, the MCP server, and the CLI automatically.

### From source

```bash
git clone https://github.com/dp-web4/snarc.git
cd snarc && bash install.sh
```

### npm

```bash
npm install -g snarc
```

## CLI

```bash
snarc stats              # Memory health dashboard
snarc search <query>     # Search across all tiers
snarc patterns [kind]    # List consolidated patterns
snarc export             # Export Tier 2+3 to markdown
snarc dream              # Heuristic consolidation
snarc dream --deep       # LLM-powered semantic consolidation
snarc review             # List quarantined identity proposals
snarc promote <id> k v   # Promote proposal to Tier 3 (human-confirmed)
snarc reject <id>        # Delete a quarantined proposal
snarc config [key] [val] # View/set persistent settings
```

## Architecture

```
SessionStart hook
  │
  └─→ Inject session briefing (conservative, epistemically labeled)
        ├─ Inferred patterns (Tier 2, confidence >= 0.6, excludes proposals)
        ├─ Recent observations (Tier 1, salience >= 0.6)
        └─ Project facts (Tier 3, confidence >= 0.7)

UserPromptSubmit hook (every user message)
  │
  ├─→ Extract keywords from prompt
  ├─→ FTS5 search across observations and patterns
  └─→ If matches found: inject via additionalContext
      (most prompts pass silently — no match = no injection)

PostToolUse hook (every tool invocation)
  │
  ├─→ Summarize input/output (truncate to 300 chars)
  ├─→ Push to Tier 0 circular buffer
  ├─→ SNARC heuristic score (<10ms, no LLM)
  │     S — tool transition frequency
  │     N — seen-set lookup
  │     A — error/warning keywords
  │     R — success signals
  │     C — result contradicts history
  ├─→ salience >= threshold? → INSERT Tier 1 (SQLite)
  └─→ Silent pass-through (never blocks Claude Code)

PreCompact hook (fires BEFORE context compression)
  │
  ├─→ Read transcript_path — full conversation JSONL
  ├─→ Extract user + assistant turns (skip short procedural messages)
  ├─→ Score on semantic salience:
  │     Insight language ("the key is", "realization", "reframe")
  │     Domain concepts (MRH, T3, trust, consciousness, etc.)
  │     Decision language ("let's", "the plan", "going forward")
  │     Analogies ("maps to", "like a", "same as")
  │     Identity observations ("what it means to", "affordance")
  ├─→ salience >= 0.3? → INSERT Tier 1 as tool_name='Conversation'
  └─→ The MIND, not just the hands

PostCompact hook (compaction = long session = lots of observations)
  │
  ├─→ Mid-session heuristic dream (<100ms) — consolidate so far
  └─→ Re-inject enriched briefing (now includes conversation observations)

Stop hook (dream cycle)
  │
  ├─→ Confidence decay (patterns -0.05/day, observations after 7 days)
  ├─→ Prune patterns below 0.1 confidence
  ├─→ Heuristic extraction → Tier 2
  └─→ Deep dream via claude --print → Tier 2
      └─→ Identity facts → Tier 3 (or quarantine if auto_promote_identity=0)
```

## Data

Each launch directory gets its own isolated database:
```
~/.engram/projects/<hash>/engram.db    # observations, patterns, identity, settings
~/.engram/projects/<hash>/meta.json    # maps hash → directory path
```

Same pattern as Claude Code's `-c` flag: project context is scoped to where you launched from. Working on project A won't surface project B's patterns.

Settings (like `auto_promote_identity`) persist per project in the same database.

No external API calls. No telemetry. All local.

## Origin

SNARC is a lightweight spinoff from [SAGE](https://github.com/dp-web4/SAGE) (Situation-Aware Governance Engine) — a cognition kernel for edge AI that runs a continuous consciousness loop with salience-gated memory, metabolic states, and trust dynamics. SAGE's SNARC attention system, multi-tier memory architecture, and sleep consolidation cycles are adapted here into a practical Claude Code plugin.

The SNARC salience scoring concept (Surprise, Novelty, Arousal, Reward, Conflict) originates from Richard Aragon's [Transformer Sidecar](https://github.com/RichardAragon/Transformer-Sidecar-Bolt-On-Persistent-State-Space-Memory) — a selective memory system that only writes when moments are novel, surprising, or rewarded. SAGE adapted this into a neural scorer; SNARC adapts it further into pure heuristic TypeScript.

The observation pipeline draws from [claude-mem](https://github.com/thedotmack/claude-mem)'s auto-capture hooks. The filtering and consolidation draw from SAGE. See [COMPARISON.md](COMPARISON.md) for a detailed side-by-side.

The key insight: capturing everything is noisy. Capturing nothing loses context. Salience scoring finds the middle — capture what your attention system flags as important, consolidate patterns during downtime, forget the rest.

## License

MIT
