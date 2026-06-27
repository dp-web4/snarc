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
