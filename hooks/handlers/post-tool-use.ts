#!/usr/bin/env node
/**
 * PostToolUse hook — capture tool observation, score with SNARC, store if salient.
 * Must complete in <5 seconds. No LLM calls.
 */

import { EngramMemory } from '../../src/memory.js';
import { getDbPath } from '../../src/db.js';
import { resolveProjectRoot } from '../lib/project-root.js';

async function main() {
  // Read hook input from stdin
  let input = '';
  for await (const chunk of process.stdin) {
    input += chunk;
  }

  try {
    const data = JSON.parse(input);
    const toolName = data.tool_name || data.toolName || 'unknown';
    const toolInput = typeof data.tool_input === 'string'
      ? data.tool_input
      : JSON.stringify(data.tool_input || '');
    // Claude Code PostToolUse sends `tool_response` (not `tool_result`) — reading the wrong field
    // left output_summary empty for every observation, starving reward/conflict/error_fix/arousal,
    // which all scan output text. Prefer tool_response; keep tool_result as a back-compat fallback.
    const toolResponse = data.tool_response ?? data.tool_result;
    const toolOutput = typeof toolResponse === 'string'
      ? toolResponse
      : JSON.stringify(toolResponse ?? '');
    // Exit code if the tool reports one (Bash etc.); fall back to error/interrupt flags.
    let exitCode: number | undefined;
    if (toolResponse && typeof toolResponse === 'object') {
      const r = toolResponse as any;
      if (typeof r.exitCode === 'number') exitCode = r.exitCode;
      else if (typeof r.exit_code === 'number') exitCode = r.exit_code;
      else if (typeof r.returncode === 'number') exitCode = r.returncode;
      else if (r.interrupted === true || r.is_error === true) exitCode = 1;
    }
    if (exitCode === undefined && data.is_error === true) exitCode = 1;
    const cwd = data.cwd || process.cwd();
    const sessionId = data.session_id || process.env.SESSION_ID || 'unknown';

    // v2 capture model (dp 2026-07-01): snarc NO LONGER logs every tool — hestia owns the tool-use log,
    // and 98% of the old stream was mechanical noise. PostToolUse now records ONLY FAILURES: where things
    // didn't go according to plan. That's the learning signal (prediction error / the SNARC conflict dim).
    const failed = exitCode !== undefined && exitCode !== 0;
    if (failed) {
      const projectRoot = resolveProjectRoot(cwd);
      const memory = new EngramMemory(getDbPath(projectRoot));
      memory.initSession(sessionId, projectRoot);
      memory.captureContext('failure',
        `${toolName} FAILED (exit ${exitCode}): ${toolInput.slice(0, 200)} → ${toolOutput.slice(0, 400)}`,
        cwd, 0.85);
      memory.close();
    }
  } catch (e) {
    // Never BLOCK Claude Code — but do NOT swallow silently. This exact silent catch hid a fleet-wide
    // capture death for 4 days (a bad `last_seen` migration threw on every existing db). A stderr line
    // is visible in hook debug logs without blocking; that's the difference between a 4-day and a
    // 4-minute diagnosis.
    process.stderr.write(`[snarc] post-tool-use capture skipped: ${(e as any)?.message ?? e}\n`);
  }

  process.stdout.write(JSON.stringify({ continue: true, suppressOutput: true }));
}

main();
