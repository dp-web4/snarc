#!/usr/bin/env node
/**
 * acceptance_rehydrate_bounded.mjs — the buffer's tail is bounded in SQL, not in JS.
 *
 * rehydrateBuffer() runs in EVERY hook process (initSession calls it), and it wants the last
 * 50 observations of the session. It asked for `getSessionObservations(sessionId)` — the whole
 * session, ORDER BY ts, every column — and then took `.slice(-50)` in JavaScript. The cost of
 * one hook therefore grew with the length of the session it was helping.
 *
 * Measured on Sprout 2026-09-23, on a session that had been running for days:
 *
 *     observations for that one session   216,371   (of 229,140 in the whole DB — 94%)
 *     text pulled out of SQLite per hook   ~131 MB
 *     UserPromptSubmit hook                5.4 - 11.4 s   against a 5 s timeout
 *     the same hook with an empty session   0.14 s
 *
 * Claude Code kills a hook at its timeout and discards the output, so what the user saw was
 * "UserPromptSubmit hook timed out after 5s — output discarded" and every reactive recall in
 * that session was dark. Raising the timeout would have hidden it; the read was the bug.
 *
 * The fix is `getRecentSessionObservations` — ORDER BY id DESC LIMIT ?. `id` and not `ts`
 * because idx_obs_session is (session_id, rowid): the index walks backwards and the planner
 * needs no sort at all, while `ts DESC` builds a temp B-tree over all 216k rows. Both return
 * the same rows for an append-only log; only one is free.
 *
 * Checks (crash-isolated per check — a throw is a red, not an early exit):
 *   1. the bounded statement exists and returns at most the limit.
 *   2. it returns the NEWEST rows, and rehydrateBuffer hands them to the buffer oldest-first
 *      (the order the old .slice(-50) produced — a reversed buffer would silently corrupt
 *      surprise/conflict scoring rather than fail).
 *   3. rehydrateBuffer does not read the unbounded statement (the defect, asserted at the
 *      source: a future edit that "simplifies" it back is a red here, not a slow hook).
 *   4. the planner uses no temp B-tree for the bounded query — the property that makes it
 *      O(limit) instead of O(session).
 *   5. it is not slower than a linear scan would predict: 200k rows in one session, bounded
 *      read under 100 ms. The old path on the same table took seconds.
 *   6. the unbounded statement still exists and still returns everything — consolidate() at
 *      endSession and getContext() are entitled to the full set; this must narrow the tail
 *      reader only.
 */
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readFileSync } from 'node:fs';

const reds = [];
const greens = [];
function check(name, fn) {
  try {
    fn();
    greens.push(name);
    console.log(`  green  ${name}`);
  } catch (e) {
    reds.push([name, e?.message ?? String(e)]);
    console.log(`  RED    ${name}\n         ${e?.message ?? e}`);
  }
}

const dir = mkdtempSync(join(tmpdir(), 'snarc-rehydrate-'));
const dbPath = join(dir, 'engram.db');

const { openDatabase, prepareStatements } = await import('../dist/src/db.js');
const { SNARCMemory } = await import('../dist/src/memory.js');

const SID = 'sess-under-test';
const N = 200_000;          // the shape that broke it, not a toy
const LIMIT = 50;

const db = openDatabase(dbPath);
const stmts = prepareStatements(db);

// One long session, plus a second session so "bounded" cannot be confused with "whole table".
const ins = db.prepare(
  `INSERT INTO observations (session_id, ts, tool_name, input_summary, output_summary,
                             surprise, novelty, arousal, reward, conflict, salience, cwd)
   VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0.5, ?)`);
db.transaction(() => {
  for (let i = 0; i < N; i++) {
    // ts ascending with id, as an append-only log produces
    const ts = new Date(Date.UTC(2026, 0, 1) + i * 1000).toISOString().replace('T', ' ').slice(0, 19);
    ins.run(SID, ts, `Tool${i}`, `in-${i}`, `out-${i}`, '/w');
  }
  ins.run('other-session', '2026-06-01 00:00:00', 'Other', 'x', 'y', '/w');
})();

console.log(`\nfixture: ${N} observations in '${SID}', 1 in 'other-session'\n`);

check('1. the bounded statement exists and honours its limit', () => {
  if (!stmts.getRecentSessionObservations) throw new Error('getRecentSessionObservations is missing');
  const rows = stmts.getRecentSessionObservations.all(SID, LIMIT);
  if (rows.length !== LIMIT) throw new Error(`expected ${LIMIT} rows, got ${rows.length}`);
  if (rows.some(r => r.session_id !== SID)) throw new Error('a row from another session leaked in');
});

check('2. newest rows, handed to the buffer oldest-first', () => {
  const rows = stmts.getRecentSessionObservations.all(SID, LIMIT);
  if (rows[0].tool_name !== `Tool${N - 1}`) {
    throw new Error(`SQL should return newest-first; first row is ${rows[0].tool_name}`);
  }
  const mem = new SNARCMemory(dbPath);
  mem.initSession(SID, '/w');
  const buf = mem.buffer?.getAll?.();
  if (!buf) { mem.close(); throw new Error('cannot read the buffer to verify its order'); }
  const names = buf.map(x => x.toolName);
  mem.close();
  if (names.length !== LIMIT) throw new Error(`buffer holds ${names.length}, expected ${LIMIT}`);
  if (names[0] !== `Tool${N - LIMIT}` || names[names.length - 1] !== `Tool${N - 1}`) {
    throw new Error(`buffer must run oldest->newest; got ${names[0]} .. ${names[names.length - 1]}`);
  }
});

check('3. rehydrateBuffer does not read the unbounded statement', () => {
  const src = readFileSync(new URL('../src/memory.ts', import.meta.url), 'utf8');
  const i = src.indexOf('private rehydrateBuffer');
  if (i < 0) throw new Error('rehydrateBuffer not found');
  const body = src.slice(i, src.indexOf('\n  }', i));
  if (/getSessionObservations\b/.test(body)) {
    throw new Error('rehydrateBuffer is back on the unbounded read — the hook timeout returns with it');
  }
  if (!/getRecentSessionObservations/.test(body)) {
    throw new Error('rehydrateBuffer does not use the bounded statement');
  }
});

check('4. the bounded query needs no temp B-tree', () => {
  const plan = db.prepare(
    'EXPLAIN QUERY PLAN SELECT * FROM observations WHERE session_id = ? ORDER BY id DESC LIMIT ?'
  ).all(SID, LIMIT).map(r => r.detail).join(' | ');
  if (/TEMP B-TREE/i.test(plan)) {
    throw new Error(`a sort over the whole session is back in the plan: ${plan}`);
  }
  if (!/USING INDEX/i.test(plan)) throw new Error(`no index used: ${plan}`);
});

check('5. bounded read is O(limit), not O(session)', () => {
  const t0 = process.hrtime.bigint();
  stmts.getRecentSessionObservations.all(SID, LIMIT);
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  if (ms > 100) throw new Error(`bounded read took ${ms.toFixed(1)} ms over ${N} rows`);
  console.log(`         (${ms.toFixed(1)} ms over ${N} rows)`);
});

check('6. the unbounded statement still returns everything', () => {
  const all = stmts.getSessionObservations.all(SID);
  if (all.length !== N) throw new Error(`consolidate/getContext lost rows: ${all.length} of ${N}`);
  if (all[0].tool_name !== 'Tool0') throw new Error('the full set must stay oldest-first');
});

db.close();
rmSync(dir, { recursive: true, force: true });

console.log(`\n${greens.length} green, ${reds.length} red`);
if (reds.length) {
  for (const [n, m] of reds) console.log(`  RED ${n}: ${m}`);
  process.exit(1);
}
