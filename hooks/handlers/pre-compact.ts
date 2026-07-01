#!/usr/bin/env node
/**
 * PreCompact hook — capture the conversation (the "mind") BEFORE context compaction.
 *
 * Tool-use hooks capture the hands (what was typed/edited/committed). This captures the mind
 * (what was discussed, realized, decided). Transcript scoring + storage lives in the shared
 * `conversation-capture` lib so this hook and SessionEnd use one source of truth.
 *
 * Must be fast + silent-fail — never block compaction.
 */

import { EngramMemory } from '../../src/memory.js';
import { getDbPath } from '../../src/db.js';
import { resolveProjectRoot } from '../lib/project-root.js';
import { captureConversationTurns } from '../../src/conversation-capture.js';
import { deepConsolidate } from '../../src/deep-consolidation.js';

/** The DREAM cycle: distill everything accumulated SINCE THE LAST DREAM (user_prompt/decision/failure +
 *  conversation) into durable patterns/identity, then advance the watermark. Shared by PreCompact + Stop
 *  via the `last_dream_obs_id` setting, so "since the last one" holds across both boundaries. (v2, dp
 *  2026-07-01.) Best-effort + LLM-gated; never blocks. */
export async function dreamSinceLast(memory: any, sessionId: string): Promise<void> {
  if (memory.getSetting('deep_dream') === '0') return;
  const all = memory.getContext(sessionId) as any[];
  if (!all.length) return;
  const watermark = parseInt(memory.getSetting('last_dream_obs_id') || '0', 10);
  const fresh = all.filter((o) => o.id > watermark);
  if (fresh.length < 3) return;
  const autoPromote = memory.getSetting('auto_promote_identity') === '1';
  try { await deepConsolidate((memory as any).stmts, fresh, autoPromote); } catch { /* dream best-effort */ }
  memory.setSetting('last_dream_obs_id', String(Math.max(...all.map((o) => o.id))));
}

async function main() {
  let input = '';
  for await (const chunk of process.stdin) {
    input += chunk;
  }

  try {
    const data = JSON.parse(input || '{}');
    const transcriptPath = data.transcript_path;
    const sessionId = data.session_id || process.env.SESSION_ID || 'compact';
    const cwd = data.cwd || process.cwd();

    if (transcriptPath) {
      const memory = new EngramMemory(getDbPath(resolveProjectRoot(cwd)));
      memory.initSession(sessionId);
      const r = captureConversationTurns(memory, transcriptPath, cwd, sessionId);
      await dreamSinceLast(memory, sessionId);   // DREAM before context is dropped
      memory.close();
      if (r.captured > 0) {
        process.stderr.write(
          `[snarc] Pre-compact: captured ${r.captured}/${r.total} conversation turns` +
          (r.skipped ? ` (${r.skipped} dup-skipped)` : '') + '\n',
        );
      }
    }
  } catch (e) {
    // Silent failure — never block compaction
  }

  process.stdout.write(JSON.stringify({ continue: true }));
}

main();
