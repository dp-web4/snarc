#!/usr/bin/env node
/**
 * UserPromptSubmit hook — check if the user's prompt relates to past memories.
 *
 * Extracts keywords from the prompt, searches engram, and injects
 * related observations via the `additionalContext` JSON field.
 *
 * Only fires when there's a relevant match — most prompts pass through silently.
 * Keeps injection under ~200 tokens to avoid context bloat.
 */

import { EngramMemory } from '../../src/memory.js';
import { getDbPath } from '../../src/db.js';
import { resolveProjectRoot } from '../lib/project-root.js';

async function main() {
  let input = '';
  for await (const chunk of process.stdin) {
    input += chunk;
  }

  try {
    const data = JSON.parse(input);
    const prompt = data.prompt || data.message || '';
    const cwd = data.cwd || process.cwd();
    const sessionId = data.session_id || process.env.SESSION_ID || 'unknown';
    const projectRoot = resolveProjectRoot(cwd);
    const memory = new EngramMemory(getDbPath(projectRoot));
    memory.initSession(sessionId, projectRoot);

    // CAPTURE the user's instruction — the highest-value context and the primary drift-guard: decisions
    // live in the user's prompts, and snarc never recorded them before (this is what let "RTX is
    // canonical" fail to stop a drift). Salient by construction. (v2 capture model, dp 2026-07-01.)
    if (prompt && prompt.trim()) memory.captureContext('user_prompt', prompt, cwd, 0.9);

    // Reactive injection — only for substantive prompts (short confirmations don't need a search).
    const searchQuery = prompt.length >= 10 ? extractSearchTerms(prompt) : '';
    const related = searchQuery ? memory.findRelated(searchQuery, 3) : '';
    memory.close();

    if (related) {
      // Inject via additionalContext — Claude sees this as part of the conversation
      process.stdout.write(JSON.stringify({ additionalContext: related }));
    }
  } catch (e) {
    process.stderr.write(`[snarc] user-prompt capture skipped: ${(e as any)?.message ?? e}\n`);
  }

  process.exit(0);
}

/**
 * Extract meaningful search terms from a user prompt.
 * Skip common words, keep file paths, technical terms, error messages.
 */
function extractSearchTerms(prompt: string): string {
  const STOP_WORDS = new Set([
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'to', 'of', 'in', 'for',
    'on', 'with', 'at', 'by', 'from', 'it', 'this', 'that', 'these',
    'those', 'i', 'you', 'we', 'they', 'he', 'she', 'me', 'my', 'your',
    'and', 'or', 'but', 'not', 'no', 'yes', 'if', 'then', 'else', 'when',
    'what', 'how', 'why', 'where', 'which', 'who', 'so', 'just', 'also',
    'please', 'thanks', 'let', 'lets', "let's", 'make', 'get', 'use',
    'now', 'here', 'there', 'some', 'all', 'any', 'each', 'every',
  ]);

  const words = prompt
    .toLowerCase()
    .replace(/[^\w\s./\-]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length > 2 && !STOP_WORDS.has(w));

  // Keep at most 5 terms for the FTS query
  const terms = words.slice(0, 5);
  if (terms.length === 0) return '';

  // FTS5 OR query
  return terms.join(' OR ');
}

main();
