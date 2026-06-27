/**
 * Deep Consolidation — LLM-powered dream cycle.
 *
 * Sends session observations to Claude (via `claude --print`) for
 * semantic pattern extraction. Produces higher-quality Tier 2 patterns
 * than the heuristic extractors.
 *
 * IMPORTANT: Deep dream output is INFERRED, not observed. Results go to
 * Tier 2 as "deep_*" patterns. Identity facts are auto-promoted to Tier 3
 * by default (configurable via `snarc config auto_promote_identity 0`).
 *
 * Usage:
 *   snarc dream --deep            # CLI
 *   Runs automatically at session end (disable: `snarc config deep_dream 0`)
 */

import { execSync } from 'node:child_process';
import { writeFileSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import type { Statements } from './db.js';

interface Observation {
  id: number;
  tool_name: string;
  input_summary: string;
  output_summary: string;
  salience: number;
  ts: string;
}

interface DeepPattern {
  kind: 'workflow' | 'error_fix' | 'insight' | 'decision' | 'identity';
  summary: string;
  detail: string;
  confidence: number;
  source_ids: number[];
}

const VALID_KINDS = new Set(['workflow', 'error_fix', 'insight', 'decision', 'identity']);

const PROMPT_TEMPLATE = `You are a memory consolidation agent. You are reviewing observations from a coding session and extracting durable patterns.

Below are the session's observations — tool uses that scored above salience threshold. Each has a tool name, input summary, output summary, salience score, and timestamp.

Your job: extract patterns that would be useful in FUTURE sessions. Not a session log — durable knowledge.

Respond with a JSON array of patterns. Each pattern has:
- kind: "workflow" (recurring sequence), "error_fix" (problem→solution), "insight" (something learned), "decision" (architectural choice made), or "identity" (persistent project fact)
- summary: one-line description (what someone needs to know)
- detail: supporting context (2-3 sentences max)
- confidence: 0.0-1.0 (how confident are you this pattern is real and reusable?)
- source_ids: array of observation IDs that support this pattern

Rules:
- Only extract patterns you're confident about (>= 0.5)
- Prefer fewer high-quality patterns over many weak ones
- "identity" patterns should be very conservative (>= 0.8 confidence) — they will be quarantined for review, not auto-applied
- Do NOT include session-specific details (timestamps, exact commands) — extract the reusable knowledge
- Do NOT hallucinate patterns not supported by the observations
- source_ids MUST reference actual observation IDs from the list below
- If the observations don't contain meaningful patterns, return an empty array []

OBSERVATIONS:
`;

export async function deepConsolidate(
  stmts: Statements,
  observations: Observation[],
  autoPromote = false,
): Promise<{ patternsCreated: number; proposedIdentity: number; autoPromoted: number }> {
  if (observations.length < 3) {
    return { patternsCreated: 0, proposedIdentity: 0, autoPromoted: 0 };
  }

  // Build valid observation ID set for verification
  const validIds = new Set(observations.map(o => o.id));

  // Format observations for the prompt
  const obsText = observations.map(o =>
    `[#${o.id}] ${o.ts} | ${o.tool_name} | salience: ${o.salience.toFixed(2)}\n  input: ${o.input_summary.slice(0, 200)}\n  output: ${o.output_summary.slice(0, 200)}`
  ).join('\n\n');

  const prompt = PROMPT_TEMPLATE + obsText + '\n\nRespond with ONLY a JSON array. No markdown, no explanation.';

  // Write prompt to temp file and pass via stdin — avoids shell escaping issues
  const tmpFile = join(tmpdir(), `engram-dream-${Date.now()}.txt`);
  let response: string;
  try {
    writeFileSync(tmpFile, prompt);
    response = execSync(
      `cat "${tmpFile}" | claude --print -`,
      {
        timeout: 60_000,
        encoding: 'utf-8',
        stdio: ['pipe', 'pipe', 'pipe'],
      },
    ).trim();
  } catch (e: any) {
    console.error(`[snarc] Deep consolidation failed: ${e.message?.slice(0, 100)}`);
    return { patternsCreated: 0, proposedIdentity: 0, autoPromoted: 0 };
  } finally {
    try { unlinkSync(tmpFile); } catch { /* cleanup */ }
  }

  // Parse response — extract JSON array
  let patterns: DeepPattern[];
  try {
    const jsonMatch = response.match(/\[[\s\S]*\]/);
    if (!jsonMatch) {
      console.error('[snarc] Deep consolidation: no JSON array in response');
      return { patternsCreated: 0, proposedIdentity: 0, autoPromoted: 0 };
    }
    patterns = JSON.parse(jsonMatch[0]);
    if (!Array.isArray(patterns)) throw new Error('not an array');
  } catch (e) {
    console.error(`[snarc] Deep consolidation: failed to parse response`);
    return { patternsCreated: 0, proposedIdentity: 0, autoPromoted: 0 };
  }

  let patternsCreated = 0;
  let proposedIdentity = 0;
  let autoPromoted = 0;

  for (const p of patterns) {
    // Validate required fields
    if (!p.kind || !p.summary || p.confidence === undefined) continue;
    if (p.confidence < 0.5) continue;

    // Validate kind is one of the expected values
    if (!VALID_KINDS.has(p.kind)) continue;

    // Validate source_ids reference real observations
    const validSourceIds = (p.source_ids || []).filter(id => validIds.has(id));
    if (validSourceIds.length === 0 && observations.length > 0) {
      // LLM fabricated source IDs — still store pattern but lower confidence
      p.confidence = Math.max(0.5, p.confidence - 0.2);
    }

    if (p.kind === 'identity') {
      // Re-occurrence-gated promotion: an identity fact earns Tier 3 by being independently
      // re-proposed across multiple sessions (reproduced = a stable self, not a one-shot dream) —
      // NOT by human selection and NOT by a single confident guess. deep-dream runs once per
      // session, so the proposed_identity pattern's frequency ≈ the number of distinct sessions
      // that re-proposed this key. (auto_promote_identity=ON lowers the bar to 1 = immediate, the
      // legacy "dangerous" path.) Identity earned by being-the-same, not asserted or dictated.
      const REOCCUR_THRESHOLD = autoPromote ? 1 : 3;
      const key = p.summary.split(':')[0]?.trim() || p.summary.slice(0, 50);
      const value = p.detail || p.summary;
      // Accumulate under a stable per-KEY summary so re-proposals increment frequency even as the
      // LLM's wording of the value drifts between sessions.
      const propSummary = `[proposed-identity] ${key}`;
      stmts.upsertPattern.run(
        'proposed_identity',
        propSummary,
        value,
        1,
        JSON.stringify(validSourceIds),
        p.confidence,
      );
      const row = stmts.getPatternByKindSummary.get('proposed_identity', propSummary) as
        { frequency: number; confidence: number; detail: string } | undefined;
      const freq = row?.frequency ?? 1;
      if (freq >= REOCCUR_THRESHOLD) {
        // Earned by reproduction → promote to Tier 3 (idempotent if already promoted).
        const src = autoPromote ? 'deep-dream-immediate' : `reproduced-${freq}x`;
        stmts.upsertIdentity.run(key, row?.detail || value, src, row?.confidence ?? p.confidence);
        autoPromoted++;
      } else {
        proposedIdentity++;
      }
    } else {
      stmts.upsertPattern.run(
        `deep_${p.kind}`,
        p.summary,
        p.detail || '',
        1,
        JSON.stringify(validSourceIds),
        p.confidence,
      );
      patternsCreated++;
    }
  }

  return { patternsCreated, proposedIdentity, autoPromoted };
}
