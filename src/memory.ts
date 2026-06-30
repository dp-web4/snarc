/**
 * Memory Manager — orchestrates capture → score → store → consolidate.
 * Central coordinator wiring db, buffer, and snarc scorer.
 */

import Database from 'better-sqlite3';
import { openDatabase, prepareStatements, type Statements } from './db.js';
import { CircularBuffer, type RawObservation } from './buffer.js';
import { SNARCScorer, type SNARCScores } from './snarc.js';
import { consolidate } from './consolidation.js';

export interface CaptureResult {
  salience: number;
  stored: boolean; // true if promoted to Tier 1
  scores: SNARCScores;
}

export interface SearchResult {
  tier: number;
  id: number;
  summary: string;
  salience?: number;
  confidence?: number;
  ts?: string;
  kind?: string;
}

export interface MemoryStats {
  observations: number;
  patterns: number;
  identityFacts: number;
  seenTokens: number;
  sessions: number;
  avgSalience: number | null;
  lastObservation: string | null;
  bufferSize: number;
}

/**
 * Significant tokens for retrieval-relevance matching (Sprint 0.2 calibration loop):
 * file-path-like tokens + lowercase words >=4 chars. Used to decide whether a session acted on a
 * surfaced memory (token overlap between the memory and the session's later observations).
 */
export function sigTokens(text: string): Set<string> {
  const t = (text || '').toLowerCase();
  const out = new Set<string>();
  for (const m of t.matchAll(/[a-z0-9_.\-]+\/[a-z0-9_./\-]+|[a-z0-9_\-]+\.[a-z0-9]{1,5}\b/g)) out.add(m[0]);
  for (const m of t.matchAll(/[a-z][a-z0-9_]{3,}/g)) out.add(m[0]);
  return out;
}

const STOP_TOKENS = new Set([
  'this', 'that', 'with', 'from', 'have', 'will', 'into', 'then', 'they', 'them', 'what',
  'when', 'which', 'were', 'been', 'your', 'about', 'there', 'these', 'would', 'could',
  'true', 'false', 'null', 'none', 'name', 'type', 'text', 'value', 'data', 'file', 'line',
]);

export class EngramMemory {
  private db: Database.Database;
  private stmts: Statements;
  private buffer: CircularBuffer;
  private scorer: SNARCScorer;
  private sessionId: string = '';

  constructor(dbPath?: string) {
    this.db = openDatabase(dbPath);
    this.stmts = prepareStatements(this.db);
    this.buffer = new CircularBuffer(50);
    this.scorer = new SNARCScorer(this.stmts, this.buffer);
  }

  initSession(sessionId: string, cwd?: string): void {
    this.sessionId = sessionId;
    this.buffer = new CircularBuffer(50);
    this.scorer = new SNARCScorer(this.stmts, this.buffer);
    this.stmts.initSession.run(sessionId, cwd || '');
    this.rehydrateBuffer(sessionId);
  }

  /**
   * Rehydrate the in-memory buffer from this session's stored observations.
   * Each Claude Code hook runs as a FRESH process with an empty buffer, so without this SNARC
   * scoring is half-dead: surprise is hardwired to 0.5 (no `buffer.lastToolName`) and the
   * same-target conflict path never fires (empty `getLast`). Loading recent stored obs gives the
   * scorer real recent history, reviving 2 of 5 dimensions. (Sub-threshold tool calls aren't in
   * the DB, so the buffer reflects stored/salient history — an approximation, not full fidelity.)
   * See docs/SNARC_SAGE_CROSSFEED_AUDIT_naive-opus-2026-06-27.md.
   */
  private rehydrateBuffer(sessionId: string): void {
    try {
      const recent = this.stmts.getSessionObservations.all(sessionId) as any[];
      for (const r of recent.slice(-50)) {
        this.buffer.push({
          toolName: r.tool_name,
          inputSummary: r.input_summary || '',
          outputSummary: r.output_summary || '',
          cwd: r.cwd || '',
          ts: r.ts,
          exitCode: undefined,
        });
      }
    } catch {
      // fresh session / no prior observations — buffer stays empty, scoring degrades gracefully
    }
  }

  capture(toolName: string, input: string, output: string, cwd: string, exitCode?: number): CaptureResult {
    const inputSummary = summarize(input, 300);
    const outputSummary = summarize(output, 300);

    const obs: RawObservation = {
      toolName,
      inputSummary,
      outputSummary,
      cwd,
      ts: new Date().toISOString(),
      exitCode,
    };

    // Score with SNARC FIRST — the scorer must see the PREVIOUS observation as context
    // (surprise's `lastToolName`, conflict's `getLast`). Pushing before scoring made
    // `lastToolName` the CURRENT obs → a self-transition (e.g. Bash→Bash), defeating surprise.
    // Score against prior context, THEN record. (Pairs with rehydrateBuffer for cross-process.)
    const scores = this.scorer.score(obs);

    // Tier 0: now record it in the buffer
    this.buffer.push(obs);

    // Tier 1: promote if above salience threshold
    const stored = scores.salience >= this.scorer.threshold;
    if (stored) {
      const tags = extractTags(toolName, inputSummary, outputSummary);
      this.stmts.insertObservation.run(
        this.sessionId,
        toolName,
        inputSummary,
        outputSummary,
        scores.surprise,
        scores.novelty,
        scores.arousal,
        scores.reward,
        scores.conflict,
        scores.salience,
        scores.salience, // base_salience — immutable importance; `salience` (prev col) decays, this doesn't
        cwd,
        JSON.stringify(tags),
      );
    }

    return { salience: scores.salience, stored, scores };
  }

  endSession(): { patternsCreated: number; patternsDecayed: number; patternsPruned: number } {
    // Run consolidation on this session's observations
    const sessionObs = this.stmts.getSessionObservations.all(this.sessionId) as any[];
    const result = consolidate(this.db, this.stmts, sessionObs, this.sessionId);

    // Close session record
    this.stmts.endSession.run(this.sessionId, this.sessionId);

    return result;
  }

  search(query: string, limit = 10): SearchResult[] {
    const results: SearchResult[] = [];

    try {
      // Search Tier 1 (observations)
      const obsRows = this.stmts.searchObservations.all(query, limit) as any[];
      for (const row of obsRows) {
        results.push({
          tier: 1,
          id: row.id,
          summary: `[${row.tool_name}] ${row.input_summary}`,
          salience: row.base_salience ?? row.salience, // rank by importance, not decayed activation
          ts: row.ts,
        });
      }
    } catch { /* FTS query syntax error — skip */ }

    try {
      // Search Tier 2 (patterns)
      const patRows = this.stmts.searchPatterns.all(query, limit) as any[];
      for (const row of patRows) {
        results.push({
          tier: 2,
          id: row.id,
          summary: row.summary,
          kind: row.kind,
          confidence: row.confidence,
        });
      }
    } catch { /* FTS query syntax error — skip */ }

    // Sort: patterns first (higher value), then by salience
    results.sort((a, b) => {
      if (a.tier !== b.tier) return a.tier - b.tier; // lower tier = higher value
      return (b.salience || 0) - (a.salience || 0);
    });

    return results.slice(0, limit);
  }

  getContext(sessionId?: string, timestamp?: string, limit = 20): any[] {
    if (sessionId) {
      return this.stmts.getSessionObservations.all(sessionId);
    }
    if (timestamp) {
      return this.stmts.getObservationContext.all(timestamp, timestamp);
    }
    return this.stmts.getRecentObservations.all(limit);
  }

  getPatterns(kind?: string): any[] {
    if (kind) {
      return this.stmts.getPatternsByKind.all(kind);
    }
    return this.stmts.getAllPatterns.all();
  }

  getIdentity(): any[] {
    return this.stmts.getAllIdentity.all();
  }

  getStats(): MemoryStats {
    const row = this.stmts.getStats.get() as any;
    return {
      observations: row.obs_count,
      patterns: row.pattern_count,
      identityFacts: row.identity_count,
      seenTokens: row.seen_count,
      sessions: row.session_count,
      avgSalience: row.avg_salience,
      lastObservation: row.last_obs,
      bufferSize: this.buffer.size,
    };
  }

  /**
   * Get a session briefing — conservative, epistemically labeled.
   *
   * Observations are "observed" (raw tool results, attributed).
   * Patterns are "inferred" (heuristic extraction, may be wrong).
   * Identity facts carry confidence scores.
   *
   * Injection is biased toward omission: only high-confidence patterns
   * and high-salience observations are surfaced. Wrong memory is more
   * damaging than missing memory.
   */
  getSessionBriefing(cwd?: string, maxTokens = 500): string {
    const lines: string[] = [];

    // Tier 2 patterns — INFERRED, only high-confidence (>= 0.6)
    // Exclude proposed_identity — those need human review before injection
    const patterns = this.getPatterns()
      .filter((p: any) => p.confidence >= 0.6 && p.kind !== 'proposed_identity');
    if (patterns.length > 0) {
      lines.push('Inferred patterns (heuristic — may not be accurate):');
      for (const p of patterns.slice(0, 3)) {
        lines.push(`  - [${p.kind}] ${p.summary} (confidence: ${p.confidence.toFixed(2)})`);
        this.logRetrieval(cwd, 'briefing', 'pattern', p.confidence, `${p.summary} ${p.detail || ''}`);
      }
    }

    // Tier 1 observations — OBSERVED, above median salience (>= 0.35)
    const recent = this.stmts.getRecentObservations.all(20) as any[];
    const highSalience = recent.filter((o: any) => o.salience >= 0.35);
    if (highSalience.length > 0) {
      lines.push('Recent observations (directly recorded):');
      for (const o of highSalience.slice(0, 3)) {
        lines.push(`  - [${o.tool_name}] ${o.input_summary.slice(0, 100)} (${o.ts})`);
        // estimate = base_salience (immutable importance), not the decayed `salience`
        this.logRetrieval(cwd, 'briefing', 'observation', o.base_salience ?? o.salience,
          `${o.input_summary || ''} ${o.output_summary || ''}`);
      }
    }

    // Tier 3 identity — only high-confidence (>= 0.7)
    const identity = this.getIdentity()
      .filter((i: any) => i.confidence >= 0.7);
    if (identity.length > 0) {
      lines.push('Project facts (auto-extracted, verify if unsure):');
      for (const i of identity.slice(0, 3)) {
        lines.push(`  - ${i.key}: ${i.value}`);
        this.logRetrieval(cwd, 'briefing', 'identity', i.confidence, `${i.key} ${i.value}`);
      }
    }

    if (lines.length === 0) return '';

    const full = lines.join('\n');
    if (full.length > maxTokens * 4) {
      return full.slice(0, maxTokens * 4) + '\n  ...';
    }
    return full;
  }

  /**
   * Record that a memory was SURFACED into a session (the estimate side of the calibration loop).
   * Outcome (relevant) is filled later by scoreRetrievals(). Never throws — instrumentation must
   * not break briefing injection.
   */
  private logRetrieval(cwd: string | undefined, source: string, kind: string, estimate: number, content: string): void {
    try {
      const toks = [...sigTokens(content)].filter(t => !STOP_TOKENS.has(t)).slice(0, 40);
      if (toks.length === 0) return;
      const est = Math.max(0, Math.min(1, estimate || 0));
      this.stmts.insertRetrieval.run(cwd || '', source, kind, est, toks.join(' '));
    } catch { /* instrumentation must never break injection */ }
  }

  /**
   * Fill the outcome side: a surfaced memory is "relevant" (1) if the same cwd saw later work
   * (within 6h) that shares >=2 significant tokens with it, else 0. cwd+time-windowed so it works
   * regardless of session-id matching across the start/end hook processes. Returns rows scored.
   * (Outcome v1 — token-overlap is a coarse proxy for "the session acted on it"; the definition is
   *  itself a research question, see fractal-leverage-SPRINT-0-calibration.md falsifiable stop.)
   */
  scoreRetrievals(): number {
    let scored = 0;
    try {
      const rows = this.stmts.getUnscoredRetrievals.all() as any[];
      for (const r of rows) {
        const memToks = new Set<string>((r.match_key || '').split(' ').filter(Boolean));
        if (memToks.size === 0) { this.stmts.setRetrievalRelevant.run(0, r.id); scored++; continue; }
        const obs = this.stmts.getObsAfter.all(r.cwd || '', r.surfaced_ts, r.surfaced_ts) as any[];
        const sessionToks = new Set<string>();
        for (const o of obs) {
          for (const t of sigTokens(`${o.input_summary || ''} ${o.output_summary || ''}`)) {
            if (!STOP_TOKENS.has(t)) sessionToks.add(t);
          }
        }
        let overlap = 0;
        for (const t of memToks) if (sessionToks.has(t)) overlap++;
        this.stmts.setRetrievalRelevant.run(overlap >= 2 ? 1 : 0, r.id);
        scored++;
      }
    } catch { /* best-effort */ }
    return scored;
  }

  /** Calibration pairs {estimate, outcome} for the harness (calib.py). */
  getCalibrationPairs(): Array<{ estimate: number; outcome: number; source: string; item_kind: string; surfaced_ts: string }> {
    const rows = this.stmts.getCalibrationPairs.all() as any[];
    return rows.map(r => ({
      estimate: r.estimate, outcome: r.relevant, source: r.source,
      item_kind: r.item_kind, surfaced_ts: r.surfaced_ts,
    }));
  }

  /**
   * Find observations related to a query, for reactive injection.
   * v2: More permissive — surfaces results with salience >= 0.3 (Tier 1)
   * or confidence >= 0.5 (Tier 2). Labels provenance explicitly.
   */
  findRelated(query: string, limit = 3): string {
    const results = this.search(query, limit * 2) // overfetch, then filter
      .filter(r =>
        (r.tier === 1 && (r.salience || 0) >= 0.3) ||
        (r.tier === 2 && (r.confidence || 0) >= 0.5 && r.kind !== 'proposed_identity')
      )
      .slice(0, limit);
    if (results.length === 0) return '';

    const lines = ['Related SNARC memories (verify before relying on these):'];
    for (const r of results) {
      const provenance = r.tier === 1 ? 'observed' : 'inferred';
      lines.push(`  - [${provenance}${r.kind ? ` ${r.kind}` : ''}] ${r.summary}`);
    }
    return lines.join('\n');
  }

  getSetting(key: string): string | undefined {
    const row = this.stmts.getSetting.get(key) as { value: string } | undefined;
    return row?.value;
  }

  setSetting(key: string, value: string): void {
    this.stmts.setSetting.run(key, value);
  }

  /** List quarantined identity proposals from deep dream */
  getProposedIdentity(): any[] {
    return this.stmts.getProposedIdentity.all();
  }

  /** Promote a proposed identity to Tier 3 (human-confirmed) */
  promoteIdentity(patternId: number, key: string, value: string): void {
    this.stmts.upsertIdentity.run(key, value, 'human-confirmed', 0.9);
    this.stmts.deletePattern.run(patternId);
  }

  /** Reject a proposed identity (delete from quarantine) */
  rejectIdentity(patternId: number): void {
    this.stmts.deletePattern.run(patternId);
  }

  close(): void {
    this.db.close();
  }
}

/** Truncate and clean text for storage */
function summarize(text: string, maxLen: number): string {
  if (!text) return '';
  // For objects, stringify first
  if (typeof text === 'object') text = JSON.stringify(text);
  // Strip ANSI escape codes
  text = text.replace(/\x1b\[[0-9;]*m/g, '');
  // Collapse whitespace
  text = text.replace(/\s+/g, ' ').trim();
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen - 3) + '...';
}

/** Extract tags from tool usage for search */
function extractTags(toolName: string, input: string, output: string): string[] {
  const tags = [toolName.toLowerCase()];

  // File extensions
  const exts = input.match(/\.([a-z]{1,8})\b/gi);
  if (exts) tags.push(...exts.map(e => e.toLowerCase()));

  // Error tag
  if (/error|fail|exception/i.test(output)) tags.push('error');
  if (/pass|success|ok/i.test(output)) tags.push('success');

  // Git operations
  if (/git\s+(commit|push|pull|merge)/i.test(input)) tags.push('git');

  // Test operations
  if (/test|spec|jest|pytest|vitest/i.test(input)) tags.push('test');

  return [...new Set(tags)];
}
