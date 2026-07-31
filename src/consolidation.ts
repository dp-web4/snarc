/**
 * Consolidation — the "dream cycle."
 *
 * Runs at SessionEnd. Takes Tier 1 observations and extracts patterns:
 * - Tool sequence patterns (recurring workflows)
 * - Error-fix chains (error followed by fix on same target)
 * - Concept clusters (observations grouped by shared files/tokens)
 */

import type Database from 'better-sqlite3';
import type { Statements } from './db.js';

interface Observation {
  id: number;
  tool_name: string;
  input_summary: string;
  output_summary: string;
  salience: number;
  ts: string;
  tags: string;
}

/**
 * Upsert a pattern and set its frequency to the size of its DISTINCT evidence set.
 *
 * The old call was `upsertPattern(..., count, ...)` where count was a per-run tally
 * added to whatever was already there. Nothing recorded which observations the tally
 * covered, so a second consolidation over the same rows added them a second time —
 * the tier-2 form of the duplication a35e3a8 fixed at tier 1. See db.ts for the
 * measurement (25,188 = 2 x 12,594 on a store built this morning; 43,581,138 on the
 * archive) and for why an inflated frequency is decay immunity rather than a cosmetic.
 *
 * The frequency delta passed to upsertPattern is 0: the row's value is not incremented,
 * it is DERIVED from pattern_sources immediately after. That makes re-consolidation
 * idempotent and makes an already-inflated row self-correct on next touch.
 *
 * deep-consolidation.ts deliberately keeps the additive upsert — there frequency counts
 * distinct SESSIONS proposing an identity fact, which is a different quantity with its
 * own guard. It is not routed through here.
 */
function recordPattern(
  stmts: Statements,
  kind: string,
  summary: string,
  detail: string,
  ids: number[],
  confidence: number,
): void {
  const distinct = [...new Set(ids)];
  stmts.upsertPattern.run(kind, summary, detail, 0, JSON.stringify(distinct), confidence);
  const row = stmts.getPatternId.get(kind, summary) as { id: number } | undefined;
  if (!row) return;   // upsert cannot fail silently, but a missing id must not throw the dream cycle
  for (const obsId of distinct) stmts.claimPatternSource.run(row.id, obsId);
  stmts.syncPatternFrequency.run(row.id);
}

export function consolidate(
  db: Database.Database,
  stmts: Statements,
  sessionObs: Observation[],
  sessionId: string,
): { patternsCreated: number; patternsDecayed: number; patternsPruned: number; seenPruned: number } {
  // Always run decay/prune, even if no observations this session
  const decayResult = stmts.decayPatterns.run();
  const patternsDecayed = decayResult.changes;
  stmts.decayObservations.run();
  const pruneResult = stmts.prunePatterns.run();
  const patternsPruned = pruneResult.changes;
  // Recency-window the seen-set so novelty stays a live signal (unbounded growth → novelty → 0).
  const seenPruned = stmts.pruneSeen.run().changes;

  if (sessionObs.length < 3) return { patternsCreated: 0, patternsDecayed, patternsPruned, seenPruned };

  let created = 0;

  // 1. Tool sequence patterns
  created += extractToolSequences(stmts, sessionObs);

  // 2. Error-fix chains
  created += extractErrorFixChains(stmts, sessionObs);

  // 3. Concept clusters
  created += extractConceptClusters(stmts, sessionObs);

  return { patternsCreated: created, patternsDecayed, patternsPruned, seenPruned };
}

/**
 * Find repeated tool sequences (e.g., Edit → Bash → Edit = TDD loop)
 */
function extractToolSequences(stmts: Statements, obs: Observation[]): number {
  const windowSize = 3;
  const sequences = new Map<string, { count: number; ids: number[] }>();

  for (let i = 0; i <= obs.length - windowSize; i++) {
    const seq = obs.slice(i, i + windowSize).map(o => o.tool_name).join(' → ');
    const entry = sequences.get(seq) || { count: 0, ids: [] };
    entry.count++;
    entry.ids.push(...obs.slice(i, i + windowSize).map(o => o.id));
    sequences.set(seq, entry);
  }

  let created = 0;
  for (const [seq, entry] of sequences) {
    if (entry.count >= 2) {
      recordPattern(
        stmts,
        'tool_sequence',
        `Recurring workflow: ${seq}`,
        JSON.stringify({ sequence: seq.split(' → '), count: entry.count }),
        entry.ids,
        Math.min(0.5 + entry.count * 0.1, 0.9),
      );
      created++;
    }
  }
  return created;
}

/**
 * Find error → fix chains: high-arousal observation followed by success on same target
 */
function extractErrorFixChains(stmts: Statements, obs: Observation[]): number {
  let created = 0;

  for (let i = 0; i < obs.length - 1; i++) {
    const current = obs[i];
    if (!isError(current)) continue;

    // Look ahead up to 5 observations for a fix
    for (let j = i + 1; j < Math.min(i + 6, obs.length); j++) {
      const candidate = obs[j];
      if (isSuccess(candidate) && shareTarget(current, candidate)) {
        const errorSig = extractErrorSignature(current.output_summary);
        const fixApproach = candidate.input_summary.slice(0, 200);

        recordPattern(
          stmts,
          'error_fix',
          `Error: ${errorSig} → Fix: ${fixApproach}`,
          JSON.stringify({
            error: current.output_summary.slice(0, 300),
            fix: candidate.input_summary.slice(0, 300),
            tool: current.tool_name,
            steps: j - i,
          }),
          [current.id, candidate.id],
          0.6,
        );
        created++;
        break; // don't double-count
      }
    }
  }
  return created;
}

/**
 * Group observations by shared files/tokens into concept clusters
 */
function extractConceptClusters(stmts: Statements, obs: Observation[]): number {
  // Extract file paths from each observation
  const fileToObs = new Map<string, number[]>();

  for (const o of obs) {
    const files = extractFiles(o.input_summary);
    for (const f of files) {
      const list = fileToObs.get(f) || [];
      list.push(o.id);
      fileToObs.set(f, list);
    }
  }

  let created = 0;
  for (const [file, ids] of fileToObs) {
    if (ids.length >= 3) {
      recordPattern(
        stmts,
        'concept_cluster',
        `Focused work on ${file}`,
        JSON.stringify({ file, observation_count: ids.length }),
        ids,
        Math.min(0.4 + ids.length * 0.05, 0.8),
      );
      created++;
    }
  }
  return created;
}

function isError(obs: Observation): boolean {
  return /\b(error|Error|ERROR|FAIL|fail|exception|Exception)\b/.test(obs.output_summary);
}

function isSuccess(obs: Observation): boolean {
  return /\b(pass|Pass|success|Success|OK|ok|fixed|resolved)\b/.test(obs.output_summary) ||
    (obs.tool_name === 'Edit' && !isError(obs));
}

function shareTarget(a: Observation, b: Observation): boolean {
  const filesA = extractFiles(a.input_summary);
  const filesB = extractFiles(b.input_summary);
  return filesA.some(f => filesB.includes(f));
}

function extractErrorSignature(output: string): string {
  // Try to extract the first error line
  const match = output.match(/(?:error|Error|ERROR|FAIL)[:\s].{0,100}/);
  return match ? match[0].slice(0, 100) : output.slice(0, 80);
}

function extractFiles(input: string): string[] {
  const matches = input.match(/[\w./\-]+\.\w{1,8}/g);
  return matches ? [...new Set(matches)] : [];
}
