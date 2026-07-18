#!/usr/bin/env node
/**
 * SessionEnd hook — run consolidation (dream cycle).
 * Gets 30 seconds — enough for heuristic pattern extraction.
 */

import { EngramMemory } from '../../src/memory.js';
import { getDbPath } from '../../src/db.js';
import { resolveProjectRoot } from '../lib/project-root.js';
import { deepConsolidate } from '../../src/deep-consolidation.js';
import { membotStore, membotSave } from '../../src/membot-bridge.js';
import { captureConversationTurns } from '../../src/conversation-capture.js';

async function main() {
  let input = '';
  for await (const chunk of process.stdin) {
    input += chunk;
  }

  try {
    const data = JSON.parse(input || '{}');
    const sessionId = data.session_id || process.env.SESSION_ID || 'unknown';
    const projectRoot = resolveProjectRoot(data.cwd || process.cwd());

    const memory = new EngramMemory(getDbPath(projectRoot));
    memory.initSession(sessionId);

    const parts = [];

    // Pre-exit conversation review — capture "what was said" for sessions that ended WITHOUT
    // compacting (PreCompact only fires at compaction). Runs before consolidation so the dream
    // cycle sees the conversation too. Dedup avoids double-storing turns PreCompact already took.
    let convCaptured = 0;
    if (data.transcript_path) {
      try {
        const cr = captureConversationTurns(memory, data.transcript_path, data.cwd || process.cwd(), sessionId);
        convCaptured = cr.captured;   // these dual-write to membot too — track so we persist below
        if (cr.captured > 0) parts.push(`${cr.captured} conversation`);
      } catch { /* never block exit */ }
    }

    // Calibration loop (Sprint 0.2): score now-settled surfaced memories against the work the
    // session actually did → (estimate, outcome) pairs for the fractal-leverage harness.
    const scored = memory.scoreRetrievals();
    if (scored > 0) parts.push(`${scored} retrieval-scored`);

    // Heuristic consolidation (always runs)
    const result = memory.endSession();

    if (result.patternsCreated > 0) parts.push(`${result.patternsCreated} created`);
    if (result.patternsDecayed > 0) parts.push(`${result.patternsDecayed} decayed`);
    if (result.patternsPruned > 0) parts.push(`${result.patternsPruned} pruned`);

    // Deep consolidation (LLM-powered, on by default — disable with `snarc config deep_dream 0`)
    if (memory.getSetting('deep_dream') !== '0') {
      const obs = memory.getContext(sessionId);
      if (obs.length >= 3) {
        // Default OFF → identity promotes by RE-OCCURRENCE across sessions (deep-consolidation),
        // not by a single dream. ON ('1') restores immediate promotion (legacy, aggressive).
        const autoPromote = memory.getSetting('auto_promote_identity') === '1';
        const stmts = (memory as any).stmts;
        const deep = await deepConsolidate(stmts, obs, autoPromote);
        if (deep.patternsCreated > 0) parts.push(`${deep.patternsCreated} deep patterns`);
        if (deep.proposedIdentity > 0) parts.push(`${deep.proposedIdentity} proposed identity (quarantined)`);
        if (deep.autoPromoted > 0) parts.push(`${deep.autoPromoted} identity auto-promoted`);
      }
    }

    // EXPERIMENT: dual-write deep dream patterns to membot
    // Store extracted patterns in embedding space for semantic retrieval
    const patterns = memory.getPatterns();
    let membotStored = 0;
    for (const p of patterns.slice(-10)) { // last 10 patterns from this session
      // Only deep-dream consolidations go to membot's semantic layer. Shallow
      // heuristic patterns (concept_cluster path-logging, generic tool_sequence)
      // are session-local noise that re-emit every session and bloat the cartridge.
      if (p.kind.startsWith('deep_')) {
        const stored = await membotStore(
          `[${p.kind}] ${p.summary}`,
          `pattern,${p.kind},conf:${p.confidence.toFixed(2)}`
        ).catch(() => false);
        if (stored) membotStored++;
      }
    }
    if (membotStored > 0) parts.push(`${membotStored} membot-stored`);
    // Persist the membot cartridge if ANY writes landed this session — conversation turns
    // (captured above, which dual-write to membot) OR deep-dream patterns. The save used to fire
    // ONLY on deep-dream patterns, so conversation writes were lost on a membot restart whenever
    // deep_dream was off or yielded nothing. membot stores are in-memory until an explicit save.
    if (convCaptured > 0 || membotStored > 0) {
      const saved = await membotSave().catch(() => false);
      if (saved) parts.push('membot-saved');
    }

    memory.close();

    if (parts.length > 0) {
      process.stderr.write(`[snarc] Dream cycle: ${parts.join(', ')}\n`);
    }
  } catch (e) {
    // Silent failure
  }

  process.stdout.write(JSON.stringify({ continue: true, suppressOutput: true }));
}

main();
