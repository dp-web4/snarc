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
  captureContext(kind: string, text: string, cwd: string, salience?: number): boolean;
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

/**
 * A transcript-format recognizer: given one parsed JSONL entry, return a
 * {role, content} turn, or null if it doesn't recognize this line's shape.
 * Registering a recognizer is how a non-Claude harness plugs into snarc capture
 * without forking parseTranscript (Kimi's integration note #3, 2026-07-21: don't
 * make every non-Claude agent re-implement the parser).
 */
export type TurnRecognizer = (entry: any) => TranscriptTurn | null;

/** Claude Code transcripts: type:'user'/'assistant' (or role); content under .message/.content. */
export const claudeRecognizer: TurnRecognizer = (entry) => {
  if (entry.type === 'user' || entry.type === 'human' || entry.role === 'user') {
    const content = extractTextContent(entry);
    if (content && content.length > 20) return { role: 'user', content, ts: entry.timestamp || entry.ts };
  } else if (entry.type === 'assistant' || entry.role === 'assistant') {
    const content = extractTextContent(entry);
    if (content && content.length > 50) return { role: 'assistant', content, ts: entry.timestamp || entry.ts };
  }
  return null;
};

/**
 * Kimi Code wire.jsonl: assistant text lives in
 * type:'context.append_loop_event' → event.type:'content.part' → event.part{type:'text',text}.
 * Ported verbatim from Kimi's own verified reference (shared-context/kimi-memory/lib/
 * kimi-transcript.js) so the shape is confirmed, not guessed. User prompts (turn.prompt) are
 * not recognized here yet — a known gap, left rather than guessed at (guessing = silent no-match).
 */
export const kimiRecognizer: TurnRecognizer = (entry) => {
  if (entry.type === 'context.append_loop_event') {
    const ev = entry.event;
    if (ev?.type === 'content.part' && ev.part?.type === 'text' && ev.part.text) {
      const content = String(ev.part.text).trim();
      if (content && content.length > 50) return { role: 'assistant', content, ts: entry.timestamp || entry.ts };
    }
  }
  return null;
};

/** Recognizers tried in order, first match wins per line. Claude first (the common case). */
export const TURN_RECOGNIZERS: TurnRecognizer[] = [claudeRecognizer, kimiRecognizer];

export function parseTranscript(
  transcriptPath: string,
  recognizers: TurnRecognizer[] = TURN_RECOGNIZERS,
): TranscriptTurn[] {
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
        // Try each format recognizer; first match wins per line. Claude and Kimi
        // shapes are both handled without forking this loop (see TURN_RECOGNIZERS).
        for (const recognize of recognizers) {
          const turn = recognize(entry);
          if (turn) { turns.push(turn); break; }
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
    // The SEMANTIC score (insight/decision/analogy/identity/concept) is the right salience for
    // conversation — a prose synthesis is salient by its CONTENT, not by tool-telemetry. Compute
    // it once and USE it (it used to be computed only as a gate then discarded, letting the
    // tool-oriented SNARC scorer re-flatten every Claude turn to ~0.1 while user prompts kept
    // their 0.9 "salient by construction" floor — the role-asymmetry dp flagged 2026-07-18).
    const semantic = scoreConversationTurn(turn.content, turn.role);
    if (semantic < threshold) continue;
    // Store the turn labelled by SUBSTRATE ('Human'/'Claude'), not by the transcript's service-role
    // 'user'. The 'user'/'assistant' schema is a chat-API artifact that frames the human as a tool
    // operator; in a raising/collaboration frame that's reductive (the use is mutual). Name what is,
    // not the role the protocol assigns. See memory: not-a-user / entities-not-people.
    const roleLabel = turn.role === 'user' ? 'Human' : 'Claude';
    const taggedSummary = `[${roleLabel}] ${summarizeForStorage(turn.content)}`;
    if (existing.has(taggedSummary)) { skipped++; continue; }
    // captureContext = the "salient by construction" path (bypasses the tool-telemetry SNARC
    // scorer); store at the semantic salience so a genuine Claude synthesis reaches parity with
    // the prompt that triggered it, without inflating routine chatter (the gate already dropped it).
    memory.captureContext('Conversation', taggedSummary, cwd, semantic);
    membotStore(taggedSummary, 'conversation').catch(() => {});
    existing.add(taggedSummary);
    captured++;
  }
  return { captured, total: turns.length, skipped };
}
