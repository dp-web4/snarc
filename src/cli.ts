#!/usr/bin/env node
/**
 * Engram CLI
 *
 * Usage:
 *   engram stats          — memory health dashboard
 *   engram search <query> — search across all tiers
 *   engram patterns       — list consolidated patterns
 *   engram export         — dump Tier 2+3 to markdown (stdout)
 *   engram dream          — trigger manual consolidation
 */

import { EngramMemory } from './memory.js';
import { exportMarkdown } from './export.js';
import { getDbPath } from './db.js';

const cmd = process.argv[2];
const args = process.argv.slice(3);

const memory = new EngramMemory();

try {
  switch (cmd) {
    case 'stats': {
      const stats = memory.getStats();
      const identity = memory.getIdentity();
      console.log('=== Engram Memory ===');
      console.log(`Project:               ${process.cwd()}`);
      console.log(`Database:              ${getDbPath()}`);
      console.log(`Observations (Tier 1): ${stats.observations}`);
      console.log(`Patterns (Tier 2):     ${stats.patterns}`);
      console.log(`Identity (Tier 3):     ${stats.identityFacts}`);
      console.log(`Buffer (Tier 0):       ${stats.bufferSize}/50`);
      console.log(`Seen tokens:           ${stats.seenTokens}`);
      console.log(`Sessions:              ${stats.sessions}`);
      console.log(`Avg salience:          ${stats.avgSalience?.toFixed(3) || 'n/a'}`);
      console.log(`Last observation:      ${stats.lastObservation || 'none'}`);
      if (identity.length > 0) {
        console.log('\n--- Identity ---');
        for (const i of identity) {
          console.log(`  ${i.key}: ${i.value} (${i.confidence.toFixed(2)})`);
        }
      }
      break;
    }

    case 'search': {
      const query = args.join(' ');
      if (!query) { console.error('Usage: engram search <query>'); process.exit(1); }
      const results = memory.search(query, 20);
      if (results.length === 0) { console.log('No memories found.'); break; }
      for (const r of results) {
        console.log(`[Tier ${r.tier}${r.kind ? ` ${r.kind}` : ''}] ${r.summary}${r.salience ? ` (${r.salience.toFixed(3)})` : ''}`);
      }
      break;
    }

    case 'patterns': {
      const kind = args[0];
      const patterns = memory.getPatterns(kind);
      if (patterns.length === 0) { console.log('No patterns yet. Patterns are extracted during dream cycles.'); break; }
      for (const p of patterns) {
        console.log(`[${p.kind}] ${p.summary} (freq: ${p.frequency}, conf: ${p.confidence.toFixed(2)})`);
      }
      break;
    }

    case 'export': {
      console.log(exportMarkdown(memory));
      break;
    }

    case 'dream': {
      const sessionId = args[0] || 'manual-dream';
      memory.initSession(sessionId);
      const result = memory.endSession();
      console.log(`Dream cycle complete: ${result.patternsCreated} patterns created`);
      break;
    }

    default:
      console.log(`engram — salience-gated memory for Claude Code

Usage:
  engram stats            Memory health dashboard
  engram search <query>   Search across all tiers
  engram patterns [kind]  List consolidated patterns
  engram export           Export Tier 2+3 to markdown
  engram dream            Trigger manual consolidation`);
  }
} finally {
  memory.close();
}
