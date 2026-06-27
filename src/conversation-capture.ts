/**
 * Conversation capture — the "mind" half of snarc memory.
 *
 * Reads a Claude Code transcript JSONL, scores user/assistant turns on SEMANTIC salience
 * (insight / decision / analogy / identity language), and stores the high-value ones as
 * Tier-1 observations tagged `Conversation`. Shared by the PreCompact hook (fires before
 * compaction) and the SessionEnd hook (fires at exit) so both "look at what was said and
 * decide what to carry forward" from one source of truth.
 *
 * Pure + fast (regex scoring, no LLM). The LLM-judgment upgrade layers on top of this later.
 */

import { readFileSync } from 'node:fs';
import { membotStore } from './membot-bridge.js';

// Patterns indicating semantic content worth preserving
const INSIGHT_PATTERNS = /\b(principle|insight|reali[zs]e|discover|the key|fundamental|axiom|breakthrough|novel|reframe|connection between|maps to|implies|therefore|this means|the real)\b/i;
const CONCEPT_PATTERNS = /\b(reification|synthon|attractor|MRH|T3|V3|LCT|ATP|trust tensor|consciousness|coherence|emergence|federation|governance|salience|witness|posture|metabolic|fractal)\b/;
const DECISION_PATTERNS = /\b(let's|we should|the fix|the approach|going forward|the plan|decided|choosing|commit to|priority)\b/i;
const QUESTION_PATTERNS = /\b(why does|how do we|what if|what makes|the question is|worth exploring|open question)\b/i;
const ANALOGY_PATTERNS = /\b(like a|analogous to|same as|maps to|equivalent of|think of it as|just as|the way)\b/i;
const IDENTITY_PATTERNS = /\b(you are|i am|we are|this is who|the nature of|what it means to|affordance|cognitive autonomy|self-actuali[zs])\b/i;

// Patterns indicating low-value content (procedural, not semantic)
const PROCEDURAL_PATTERNS = /^(ok|done|yes|no|good|thanks|cool|got it|sounds good|let's do it|perfect|nice|awesome)\s*[.!]?\s*$/i;
const TOOL_OUTPUT_PATTERNS = /^\s*\[?(Bash|Edit|Write|Read|Grep|Glob|Agent)\]?\s/;

export interface TranscriptTurn {
  role: 'user' | 'assistant';
  content: string;
  ts?: string;
}

/** Minimal surface of EngramMemory this module needs — avoids a circular import. */
export interface MemoryLike {
  capture(toolName: string, input: string, output: string, cwd: string, exitCode?: number): unknown;
  getContext(sessionId?: string, timestamp?: string, limit?: number): any[];
}

export function extractTextContent(entry: any): string {
  if (typeof entry.content === 'string') return entry.content;
  if (Array.isArray(entry.content)) {
    return entry.content
      .filter((block: any) => block.type === 'text')
      .map((block: any) => block.text || '')
      .join('\n');
  }
  if (entry.message?.content) return extractTextContent(entry.message);
  if (entry.text) return entry.text;
  return '';
}

export function parseTranscript(transcriptPath: string): TranscriptTurn[] {
  const turns: TranscriptTurn[] = [];
  let buf: Buffer;
  try {
    // Read as a Buffer, NOT a utf-8 string. A utf-8 string caps at ~512MB and THROWS on long
    // sessions — exactly the ones that compact and most need capture. Buffers go to ~2GB; we
    // slice line-by-line so each toString stays small.
    buf = readFileSync(transcriptPath);
  } catch {
    return turns; // transcript not readable
  }
  const NL = 0x0a;
  let start = 0;
  while (start < buf.length) {
    let nl = buf.indexOf(NL, start);
    if (nl === -1) nl = buf.length;
    if (nl > start) {
      try {
        const entry = JSON.parse(buf.toString('utf-8', start, nl));
        // Claude Code transcripts use type:'user' (top-level role is null; content under .message).
        // The original 'human' check matched nothing real, silently dropping ALL user turns.
        if (entry.type === 'user' || entry.type === 'human' || entry.role === 'user') {
          const content = extractTextContent(entry);
          if (content && content.length > 20) {
            turns.push({ role: 'user', content, ts: entry.timestamp || entry.ts });
          }
        } else if (entry.type === 'assistant' || entry.role === 'assistant') {
          const content = extractTextContent(entry);
          if (content && content.length > 50) {
            turns.push({ role: 'assistant', content, ts: entry.timestamp || entry.ts });
          }
        }
      } catch {
        // skip malformed / over-long line
      }
    }
    start = nl + 1;
  }
  return turns;
}

export function scoreConversationTurn(content: string, role: 'user' | 'assistant'): number {
  if (content.length < 30) return 0;
  if (PROCEDURAL_PATTERNS.test(content)) return 0;
  if (TOOL_OUTPUT_PATTERNS.test(content)) return 0;

  let score = 0;
  score += Math.min(content.length / 500, 0.3); // length, diminishing

  const insightMatches = content.match(INSIGHT_PATTERNS);
  if (insightMatches) score += Math.min(insightMatches.length * 0.15, 0.4);
  const conceptMatches = content.match(CONCEPT_PATTERNS);
  if (conceptMatches) score += Math.min(conceptMatches.length * 0.1, 0.3);
  if (DECISION_PATTERNS.test(content)) score += 0.2;
  if (QUESTION_PATTERNS.test(content)) score += 0.15;
  if (ANALOGY_PATTERNS.test(content)) score += 0.2;
  if (IDENTITY_PATTERNS.test(content)) score += 0.25;

  // dp's reframes are often one short directive sentence that changes everything
  if (role === 'user' && content.length < 200 && (INSIGHT_PATTERNS.test(content) || DECISION_PATTERNS.test(content))) {
    score += 0.2;
  }
  if (role === 'assistant' && /\*\*.*\*\*/.test(content)) score += 0.1;

  return Math.min(score, 1.0);
}

export function summarizeForStorage(content: string, maxLen = 500): string {
  if (content.length <= maxLen) return content;
  const truncated = content.slice(0, maxLen);
  const lastSentence = truncated.lastIndexOf('. ');
  if (lastSentence > maxLen * 0.5) return truncated.slice(0, lastSentence + 1);
  return truncated + '...';
}

export interface CaptureResult {
  captured: number;
  total: number;
  skipped: number;
}

/**
 * Score a transcript and store the high-value conversation turns as Tier-1 observations.
 * Deduplicates against turns already captured for this session (so the SessionEnd pass does
 * not re-store what PreCompact already grabbed mid-session, and re-runs are idempotent).
 */
export function captureConversationTurns(
  memory: MemoryLike,
  transcriptPath: string,
  cwd: string,
  sessionId: string,
  threshold = 0.3,
): CaptureResult {
  const turns = parseTranscript(transcriptPath);
  if (turns.length === 0) return { captured: 0, total: 0, skipped: 0 };

  const existing = new Set<string>();
  try {
    for (const r of memory.getContext(sessionId)) {
      if (r.tool_name === 'Conversation' && r.input_summary) existing.add(r.input_summary);
    }
  } catch {
    // no prior obs — fine
  }

  let captured = 0;
  let skipped = 0;
  for (const turn of turns) {
    if (scoreConversationTurn(turn.content, turn.role) < threshold) continue;
    // Store the turn labelled by SUBSTRATE ('Human'/'Claude'), not by the transcript's service-role
    // 'user'. The 'user'/'assistant' schema is a chat-API artifact that frames the human as a tool
    // operator; in a raising/collaboration frame that's reductive (the use is mutual). Name what is,
    // not the role the protocol assigns. See memory: not-a-user / entities-not-people.
    const roleLabel = turn.role === 'user' ? 'Human' : 'Claude';
    const taggedSummary = `[${roleLabel}] ${summarizeForStorage(turn.content)}`;
    if (existing.has(taggedSummary)) { skipped++; continue; }
    memory.capture('Conversation', taggedSummary, '', cwd);
    membotStore(taggedSummary, 'conversation').catch(() => {});
    existing.add(taggedSummary);
    captured++;
  }
  return { captured, total: turns.length, skipped };
}
