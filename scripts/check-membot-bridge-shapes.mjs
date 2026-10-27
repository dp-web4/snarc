#!/usr/bin/env node
/**
 * Exercises the membot bridge against a stand-in membot that replays the real
 * server's response shapes, and asserts that each one lands in the experiment
 * log as a distinct outcome.
 *
 * The point is the two cases that used to be invisible:
 *   - a server that answers HTTP 200 with "No cartridge mounted" is NOT reachable
 *     in any useful sense, but it is not unreachable either;
 *   - a healthy server returning real hits must not log membot_unique: 0.
 *
 * Response shapes copied from membot_server.py (rest_search :1741/:1764,
 * rest_store :1518), verified 2026-07-28.
 *
 * Run: node scripts/check-membot-bridge-shapes.mjs   (requires `npm run build`)
 * Writes only under a throwaway HOME — it never touches ~/.snarc.
 */
import { createServer } from 'node:http';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const sandbox = mkdtempSync(join(tmpdir(), 'membot-bridge-check-'));
process.env.HOME = sandbox;          // EXPERIMENT_DIR derives from homedir()
process.env.USERPROFILE = sandbox;

let mode = 'healthy';
const server = createServer((req, res) => {
  let payload;
  if (req.url === '/api/search') {
    if (mode === 'healthy') {
      payload = { status: 'ok', elapsed_ms: 71, results: [
        { text: 'self-witnessing: observation creates reality', full_text: '...', score: 0.7161, tags: '', index: 3 },
        { text: 'the CRT analogy: perception depends on timing', full_text: '...', score: 0.6774, tags: '', index: 5 },
      ] };
    } else if (mode === 'irrelevant') {
      // A real mounted cartridge answering a query it has nothing for. The
      // server has no usable relevance guard, so it fills top_k regardless:
      // scores are Thor's live decoy/nonsense measurements (2026-07-28).
      payload = { status: 'ok', elapsed_ms: 64, results: [
        { text: 'cricket knitting and the Hanseatic League', full_text: '...', score: 0.5877, tags: '', index: 11 },
        { text: 'a decoy passage with no bearing on the query', full_text: '...', score: 0.5808, tags: '', index: 12 },
        { text: 'unrelated corpus filler', full_text: '...', score: 0.5652, tags: '', index: 13 },
        { text: 'zzqx wubble frobnicate blorptrons', full_text: '...', score: 0.5520, tags: '', index: 14 },
        { text: 'more filler still', full_text: '...', score: 0.5310, tags: '', index: 15 },
      ] };
    } else {
      payload = { status: 'ok', results: [], error: 'No cartridge mounted' };
    }
  } else if (req.url === '/api/store') {
    // A cart with nothing relevant to say is still a mounted cart — it stores.
    payload = mode === 'unmounted'
      ? { status: 'ok', result: 'No cartridge mounted. Use mount_cartridge first.' }
      : { status: 'ok', result: 'Stored memory #41 (12ms)' };
  } else {
    payload = { status: 'ok', result: '' };
  }
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(payload));
});

const port = await new Promise(r => server.listen(0, '127.0.0.1', () => r(server.address().port)));
process.env.MEMBOT_URL = `http://127.0.0.1:${port}`;

const { membotStore, membotDualSearch } = await import('../dist/src/membot-bridge.js');
const snarc = [{ summary: 'a keyword-matched snarc hit', salience: 0.4, tier: 1 }];

const runs = [];
for (const m of ['healthy', 'irrelevant', 'unmounted']) {
  mode = m;
  runs.push({ m, stored: await membotStore(`probe ${m}`, 'check') });
  runs.push({ m, hits: (await membotDualSearch(`probe ${m}`, snarc, 3)).length });
}

// Nothing listening at all.
process.env.MEMBOT_URL = 'http://127.0.0.1:1';
const dead = await import(`../dist/src/membot-bridge.js?dead=${Date.now()}`);
await dead.membotStore('probe dead', 'check');
await dead.membotDualSearch('probe dead', snarc, 3);
server.close();

const logPath = join(sandbox, '.snarc', 'membot', 'experiment_log.jsonl');
if (!existsSync(logPath)) { console.error('FAIL: no experiment log written'); process.exit(1); }
const rows = readFileSync(logPath, 'utf8').trim().split('\n').map(JSON.parse);

console.log('event        outcome       available  stored  membot_unique  top_score  relevant  error');
for (const r of rows) {
  console.log(
    `${r.event.padEnd(12)} ${String(r.membot_outcome).padEnd(13)} ${String(r.membot_available).padEnd(10)} ` +
    `${String(r.membot_stored ?? '-').padEnd(7)} ${String(r.membot_unique ?? '-').padEnd(14)} ` +
    `${String(r.membot_top_score ?? '-').padEnd(10)} ${String(r.membot_relevant_count ?? '-').padEnd(9)} ${r.membot_error ?? ''}`
  );
}

const outcomes = rows.map(r => r.membot_outcome);
const fail = [];
if (!outcomes.includes('not_mounted')) fail.push('an unmounted 200 must log not_mounted, not availability');
if (!outcomes.includes('unreachable')) fail.push('a refused connection must log unreachable');
const healthySearch = rows.find(r => r.event === 'dual_search' && r.membot_outcome === 'ok');
if (!healthySearch) fail.push('a healthy search must log outcome ok');
else if (healthySearch.membot_unique !== 2) fail.push(`healthy search returned 2 hits but logged membot_unique=${healthySearch.membot_unique}`);
if (!rows.some(r => r.event === 'dual_store' && r.membot_stored === true)) fail.push('a successful store must log membot_stored:true');
if (!rows.some(r => r.event === 'dual_store' && r.membot_stored === false && r.membot_outcome === 'not_mounted'))
  fail.push('a store into an unmounted server must log membot_stored:false');
if (healthySearch && healthySearch.membot_top_score !== 0.7161)
  fail.push(`a healthy search must log the top score, got ${healthySearch.membot_top_score}`);

// The case the fix exists for: a live cartridge with nothing relevant to say.
// The server fills top_k anyway, so length alone reads identically to a real hit.
const shrug = rows.find(r => r.event === 'dual_search' && r.query?.includes('irrelevant'));
if (!shrug) fail.push('the irrelevant-results search did not reach the log');
else {
  if (shrug.membot_unique !== 5)
    fail.push(`membot_unique should still be the misleading 5, got ${shrug.membot_unique}`);
  if (shrug.membot_outcome !== 'empty')
    fail.push(`5 sub-threshold hits must classify as empty, got '${shrug.membot_outcome}'`);
  if (shrug.membot_relevant_count !== 0)
    fail.push(`5 sub-threshold hits must count 0 relevant, got ${shrug.membot_relevant_count}`);
  if (shrug.membot_top_score !== 0.5877)
    fail.push(`the shrug must still record its top score, got ${shrug.membot_top_score}`);
  if (shrug.membot_score_floor === undefined)
    fail.push('a searched row must record the floor it was cut at');
  if (healthySearch && shrug.membot_unique <= healthySearch.membot_unique)
    fail.push('membot_unique fails to separate a real hit from a shrug — that is the point');
}

if (fail.length) { console.error('\nFAIL:\n  - ' + fail.join('\n  - ')); process.exit(1); }
console.log(`\nOK — ${rows.length} rows, every failure mode distinguishable in the log.`);
