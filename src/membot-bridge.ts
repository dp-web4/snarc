/**
 * Membot Bridge — dual-write and comparison instrumentation for SNARC.
 *
 * EXPERIMENT: Testing whether embedding-based retrieval (membot) provides
 * materially better context recall than keyword-based FTS5 (SNARC).
 *
 * This bridge:
 * 1. Dual-writes: stores content in both SNARC (SQLite/FTS5) and membot (embeddings)
 * 2. Dual-searches: queries both systems and logs comparison data
 * 3. Logs metrics to experiment_log.jsonl for analysis
 *
 * Membot must be running as HTTP MCP server on localhost:8000.
 * If membot is unavailable, all operations silently fall back to SNARC-only.
 *
 * 2026-03-26 — Experiment Phase 1
 */

import { writeFileSync, appendFileSync, existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { hostname } from 'node:os';

const MEMBOT_URL = process.env.MEMBOT_URL || 'http://localhost:8000';
// Conversation-memory rides its OWN membot session so game-rule carts (mounted on
// the default session) don't collide with it (Waving Cat rec, 2026-07-19: option 1 —
// session_id namespacing now; migrate to cart_name when membot save-to-cart ships).
// Applied coherently to mount + store + search so they always agree on the session
// (if membot honors session_id → dedicated namespace; if not → no-op, no regression).
const SNARC_SESSION = process.env.SNARC_MEMBOT_SESSION || 'snarc';
const EXPERIMENT_DIR = join(homedir(), '.snarc', 'membot');
const EXPERIMENT_LOG = join(EXPERIMENT_DIR, 'experiment_log.jsonl');

// Ensure experiment directory exists
try { mkdirSync(EXPERIMENT_DIR, { recursive: true }); } catch {}

interface MembotResult {
  text: string;
  score: number;
}

/**
 * What actually happened on one membot call.
 *
 * `membot_available` could only ever say "the call produced a body". It could not
 * distinguish "nothing is listening on :8000" from "the server answered fine and
 * had no cartridge mounted" — and membot reports the second as HTTP 200 with a
 * success-shaped payload (verified below), so both landed in the log identically.
 * Fifty days of HUB's log are `membot_available: false` on 76,130 events with no
 * field recording *why*, and the searches read as clean `membot_unique: 0` trials.
 *
 * Ordered by how far the call got, so `>= 'not_mounted'` means "the server spoke".
 */
type MembotOutcome =
  | 'unreachable'   // no connection / timeout / non-2xx — nothing is listening
  | 'no_route'      // tool has no REST mapping; the call never left this process
  | 'not_mounted'   // server answered, but there is no cartridge behind it
  | 'server_error'  // server answered with an explicit error payload
  | 'empty'         // server searched a real cartridge and matched nothing
  | 'ok';           // stored, or returned hits

interface ComparisonEntry {
  ts: string;
  event: 'dual_search' | 'dual_store';
  query?: string;
  content_preview?: string;
  snarc_results?: Array<{ summary: string; score: number; tier: number }>;
  membot_results?: MembotResult[];
  overlap_count?: number;
  snarc_unique?: number;
  membot_unique?: number;
  snarc_time_ms?: number;
  membot_time_ms?: number;
  membot_store_ms?: number;
  /**
   * Retained with its original meaning — "a body came back" — so the 76,130
   * existing records stay comparable. Read `membot_outcome` for what happened.
   */
  membot_available: boolean;
  membot_outcome?: MembotOutcome;
  /** The server's own error text, when it sent one instead of throwing. */
  membot_error?: string;
  /**
   * Whether the store actually landed. `membotStore` has always computed this
   * and then dropped it on the floor, logging reachability in its place.
   */
  membot_stored?: boolean;
  machine: string;
}

function logExperiment(entry: ComparisonEntry): void {
  try {
    appendFileSync(EXPERIMENT_LOG, JSON.stringify(entry) + '\n');
  } catch {
    // Non-critical — don't block on logging
  }
}

// REST API route map (FastMCP 3.x uses REST endpoints, not /mcp/v1/tools/call)
const REST_MAP: Record<string, { method: string; path: string }> = {
  mount_cartridge: { method: 'POST', path: '/api/mount' },
  memory_search: { method: 'POST', path: '/api/search' },
  memory_store: { method: 'POST', path: '/api/store' },
  save_cartridge: { method: 'POST', path: '/api/save' },
  get_status: { method: 'GET', path: '/api/status' },
};

interface MembotCall {
  outcome: MembotOutcome;
  /** The body as the old code saw it — `null` exactly when nothing came back. */
  body: string | null;
  /** Decoded payload when the endpoint returns structured JSON rather than prose. */
  data: any;
  error?: string;
}

/**
 * Failures membot reports *in band* — HTTP 200, `status: "ok"`, and the problem
 * in prose or in an `error` field. A null-check cannot see any of these.
 * Strings verified against membot_server.py on 2026-07-28 (:5019, :5524, :5567,
 * :5665, :5764, :5816, :4713) and by calling `memory_search` unmounted, which
 * answers "No cartridge mounted. Use mount_cartridge first."
 */
const NOT_MOUNTED_RE = /^No cartridges? (?:mounted|to save|found)\b/i;

async function callMembot(tool: string, args: Record<string, any>): Promise<MembotCall> {
  const miss = (outcome: MembotOutcome, error?: string): MembotCall =>
    ({ outcome, body: null, data: null, error });

  try {
    const route = REST_MAP[tool];
    if (!route) return miss('no_route', `no REST mapping for '${tool}'`);

    const url = `${MEMBOT_URL}${route.path}`;
    const fetchOpts: RequestInit = {
      method: route.method,
      signal: AbortSignal.timeout(5000),
    };

    if (route.method === 'POST') {
      fetchOpts.headers = { 'Content-Type': 'application/json' };
      fetchOpts.body = JSON.stringify(args);
    }

    const resp = await fetch(url, fetchOpts);
    if (!resp.ok) return miss('unreachable', `HTTP ${resp.status}`);

    const data = await resp.json() as any;
    // REST returns {"status": "ok", "result": "<prose>"} for store/mount/save,
    // and {"status": "ok", "results": [...]} for search.
    const body = data?.result !== undefined ? String(data.result) : JSON.stringify(data);

    // An unmounted server is not an unreachable one, and the difference is the
    // whole point of the ladder's rung 0. Check the structured field first, then
    // the prose, because store/mount answer in prose and search answers in JSON.
    if (typeof data?.error === 'string' && data.error) {
      return {
        outcome: NOT_MOUNTED_RE.test(data.error) ? 'not_mounted' : 'server_error',
        body, data, error: data.error,
      };
    }
    if (data?.result !== undefined && NOT_MOUNTED_RE.test(body.trim())) {
      return { outcome: 'not_mounted', body, data, error: body.trim() };
    }
    if (data?.status === 'error') {
      return { outcome: 'server_error', body, data, error: String(data.error ?? body) };
    }

    return { outcome: 'ok', body, data };
  } catch (e) {
    // Connection refused, DNS failure, or the 5s timeout. Still a silent fallback
    // for the caller, but the log now records that it was this and not an answer.
    return miss('unreachable', e instanceof Error ? e.message : String(e));
  }
}

function parseMembotSearchResults(raw: string): MembotResult[] {
  const results: MembotResult[] = [];
  for (const line of raw.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('---') || trimmed.startsWith('Search')) continue;
    // Format: "N. [0.xyz] text..."
    if (trimmed[0] >= '0' && trimmed[0] <= '9' && trimmed.includes('] ')) {
      try {
        const parts = trimmed.split('] ', 1);
        const scoreMatch = trimmed.match(/\[([0-9.]+)\]/);
        if (scoreMatch) {
          const score = parseFloat(scoreMatch[1]);
          const text = trimmed.split('] ').slice(1).join('] ').trim();
          results.push({ text: text.slice(0, 200), score });
        }
      } catch {}
    }
  }
  return results;
}

/**
 * Store content in membot (in addition to SNARC's normal SQLite storage).
 * Call this after SNARC stores an observation or pattern.
 */
export async function membotStore(content: string, tags: string = ''): Promise<boolean> {
  const t0 = Date.now();
  const call = await callMembot('memory_store', { content, tags, session_id: SNARC_SESSION });
  const elapsed = Date.now() - t0;

  // membot answers "Stored memory #N (12ms)" on success (membot_server.py:5740).
  const stored = call.outcome === 'ok' && (call.body?.includes('Stored') ?? false);

  logExperiment({
    ts: new Date().toISOString(),
    event: 'dual_store',
    content_preview: content.slice(0, 100),
    membot_store_ms: elapsed,
    membot_available: call.body !== null,
    // A store that reaches the server, gets a 200, and writes nothing is now
    // distinguishable from one that landed. Previously both logged `true`.
    membot_outcome: call.outcome === 'ok' && !stored ? 'server_error' : call.outcome,
    membot_stored: stored,
    ...(call.error ? { membot_error: call.error } : {}),
    machine: hostname(),
  });

  return stored;
}

/**
 * Search both SNARC (FTS5) and membot (embeddings), log comparison.
 * Returns membot results for blending into briefing.
 */
export async function membotDualSearch(
  query: string,
  snarcResults: Array<{ summary: string; salience?: number; tier?: number }>,
  snarcTimeMs: number,
): Promise<MembotResult[]> {
  const t0 = Date.now();
  const call = await callMembot('memory_search', { query, top_k: 5, session_id: SNARC_SESSION });
  const membotTimeMs = Date.now() - t0;

  const snarcSummaries = () => snarcResults.map(r => ({
    summary: r.summary?.slice(0, 100) || '',
    score: r.salience || 0,
    tier: r.tier || 1,
  }));

  if (call.body === null || call.outcome !== 'ok') {
    // The trial did not happen. Logging it as `membot_unique: 0` alongside real
    // trials is what let 2,194 searches against an unreachable server read as a
    // decisive result that embedding retrieval adds nothing (HUB, 2026-07-28).
    logExperiment({
      ts: new Date().toISOString(),
      event: 'dual_search',
      query,
      snarc_results: snarcSummaries(),
      membot_results: [],
      overlap_count: 0,
      snarc_unique: snarcResults.length,
      membot_unique: 0,
      snarc_time_ms: snarcTimeMs,
      membot_time_ms: membotTimeMs,
      membot_available: call.body !== null,
      membot_outcome: call.outcome,
      ...(call.error ? { membot_error: call.error } : {}),
      machine: hostname(),
    });
    return [];
  }

  const membotResults = parseMembotSearchResults(call.body);

  // Compute overlap (simple: check if any membot result text appears in snarc summaries)
  const snarcTexts = new Set(snarcResults.map(r => (r.summary || '').toLowerCase().slice(0, 80)));
  let overlapCount = 0;
  for (const mr of membotResults) {
    const mrKey = mr.text.toLowerCase().slice(0, 80);
    if ([...snarcTexts].some(st => st.includes(mrKey) || mrKey.includes(st))) {
      overlapCount++;
    }
  }

  logExperiment({
    ts: new Date().toISOString(),
    event: 'dual_search',
    query,
    snarc_results: snarcSummaries(),
    membot_results: membotResults,
    overlap_count: overlapCount,
    snarc_unique: snarcResults.length - overlapCount,
    membot_unique: membotResults.length - overlapCount,
    snarc_time_ms: snarcTimeMs,
    membot_time_ms: membotTimeMs,
    membot_available: true,
    // A mounted cartridge that genuinely matched nothing is a real negative
    // trial and counts; the rows above are not trials at all.
    membot_outcome: membotResults.length > 0 ? 'ok' : 'empty',
    machine: hostname(),
  });

  return membotResults;
}

/**
 * Ensure membot has a cartridge mounted for this project.
 */
export async function membotEnsureMounted(projectHash: string): Promise<boolean> {
  const name = `snarc-${projectHash}`;
  const call = await callMembot('mount_cartridge', { name, session_id: SNARC_SESSION });
  const body = call.body ?? '';
  if (body.includes('Mounted')) return true;
  if (body.includes('not found')) {
    // No cartridge yet — that's fine, will be created on first store
    return false;
  }
  return false;
}

/**
 * Save the current cartridge to disk.
 */
export async function membotSave(): Promise<boolean> {
  const call = await callMembot('save_cartridge', {});
  return call.outcome === 'ok' && (call.body?.includes('Saved') ?? false);
}
