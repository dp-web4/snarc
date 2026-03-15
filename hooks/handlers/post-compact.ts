#!/usr/bin/env node
/**
 * PostCompact hook — re-inject engram context after context compaction.
 *
 * When Claude Code compacts the conversation, the SessionStart briefing
 * is lost. This hook re-injects it immediately so Claude doesn't lose
 * awareness of past patterns and observations.
 *
 * Uses additionalContext JSON field (same as UserPromptSubmit).
 */

import { EngramMemory } from '../../src/memory.js';

async function main() {
  let input = '';
  for await (const chunk of process.stdin) {
    input += chunk;
  }

  try {
    const memory = new EngramMemory();
    const briefing = memory.getSessionBriefing();
    memory.close();

    if (briefing) {
      const output = JSON.stringify({
        additionalContext: `<engram-context>\n${briefing}\n</engram-context>`,
      });
      process.stdout.write(output);
    }
  } catch (e) {
    // Silent failure
  }
}

main();
