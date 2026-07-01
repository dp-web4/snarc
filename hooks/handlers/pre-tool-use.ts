#!/usr/bin/env node
/**
 * PreToolUse hook — capture the DECISION/reasoning that led to this action.
 *
 * v2 capture model (dp 2026-07-01): snarc records WHY (the reasoning I articulated in the assistant text
 * since the last tool call), not tool telemetry (hestia owns that). Deduped via a per-session hash so a
 * chain of tools sharing one reasoning block captures ONCE. Fast: a transcript TAIL read + text extract,
 * no LLM. Must complete in <5s.
 */
import { openSync, readSync, closeSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { EngramMemory } from '../../src/memory.js';
import { getDbPath } from '../../src/db.js';
import { resolveProjectRoot } from '../lib/project-root.js';

/** My reasoning SINCE THE LAST ACTION = the assistant text blocks after the most recent tool_use (and
 *  within the current turn). The transcript stores each block on its OWN line (thinking / text / tool_use
 *  are separate lines), so we walk backward collecting assistant text and STOP at the previous tool_use
 *  (the last action) or a real user prompt (turn start). Reads only the file tail (transcripts get huge). */
function lastAssistantText(transcriptPath: string): string {
  try {
    const size = statSync(transcriptPath).size;
    const readBytes = Math.min(size, 262144);   // last 256KB is ample for one turn's reasoning
    const fd = openSync(transcriptPath, 'r');
    const buf = Buffer.alloc(readBytes);
    readSync(fd, buf, 0, readBytes, size - readBytes);
    closeSync(fd);
    const lines = buf.toString('utf8').split('\n').filter(Boolean);
    const texts: string[] = [];
    for (let i = lines.length - 1; i >= 0; i--) {
      let e: any;
      try { e = JSON.parse(lines[i]); } catch { continue; }
      const msg = e.message ?? e;
      const role = e.type ?? msg.role;
      const c = msg.content;
      const blocks = Array.isArray(c) ? c : (typeof c === 'string' ? [{ type: 'text', text: c }] : []);
      if (blocks.some((b: any) => b?.type === 'tool_use')) break;   // previous action → stop
      if (role === 'user') {
        if (blocks.some((b: any) => b?.type === 'text')) break;     // real user prompt = turn start → stop
        continue;                                                    // tool_result → skip
      }
      if (role === 'assistant') {
        for (const b of blocks) if (b?.type === 'text' && b.text?.trim()) texts.unshift(b.text.trim());
      }
    }
    return texts.join('\n').trim();
  } catch { /* fall through */ }
  return '';
}

async function main() {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  try {
    const data = JSON.parse(input);
    const cwd = data.cwd || process.cwd();
    const sessionId = data.session_id || process.env.SESSION_ID || 'unknown';
    if (!data.transcript_path) { process.exit(0); return; }

    const reasoning = lastAssistantText(data.transcript_path);
    if (reasoning.length < 40) { process.exit(0); return; }   // trivial / no real decision text

    const projectRoot = resolveProjectRoot(cwd);
    const memory = new EngramMemory(getDbPath(projectRoot));
    memory.initSession(sessionId, projectRoot);
    // DEDUP: a chain of tools shares one reasoning block → capture once (hash in the settings table).
    const hash = createHash('sha256').update(reasoning).digest('hex').slice(0, 16);
    if (memory.getSetting('last_decision_hash') !== hash) {
      memory.captureContext('decision', reasoning, cwd, 0.7);
      memory.setSetting('last_decision_hash', hash);
    }
    memory.close();
  } catch (e) {
    process.stderr.write(`[snarc] pre-tool-use decision skipped: ${(e as any)?.message ?? e}\n`);
  }
  process.exit(0);
}

main();
