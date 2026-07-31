#!/usr/bin/env node
/**
 * acceptance_pattern_accumulator.mjs — the tier-2 evidence guard.
 *
 * Written to be RED on the pre-fix tree. Each check is crash-isolated so a throw is a
 * red rather than an early exit that reports fewer reds than exist (same harness shape
 * as acceptance_dedup_scope.mjs — a red COUNT that stops early identifies nothing).
 *
 * Measured, not predicted: PRE-fix (bd496e2, detached worktree) 4 red / 1 green;
 * POST-fix 5 green / 0 red; 5 attempted in both runs. I had written "3 red" here before
 * running it — check 5 also reds pre-fix, for a schema reason (no such table) rather
 * than a behavioural one. The prediction is left visible in the git history rather than
 * quietly corrected, because a red COUNT identifies no configuration on its own.
 *
 *   1. RED pre-fix (10 -> 20)  — consolidating the SAME observations twice must not
 *                                change frequency.
 *   2. RED pre-fix (43,581,148 survives) — an already-inflated frequency must
 *                                self-correct on next touch.
 *   3. RED pre-fix (no table)  — pattern_sources must exist and hold the evidence.
 *   4. green both              — genuinely NEW evidence must still raise frequency.
 *                                Pre-fix it "passes" by growing 43,581,148 -> 43,581,155,
 *                                which is exactly the defect: a guard that lets everything
 *                                through is green here too. Check 4 alone proves nothing;
 *                                it exists so checks 1-3 cannot be satisfied by a no-op.
 *   5. RED pre-fix (no table)  — deleting a pattern must not orphan its evidence rows.
 *
 * NOTE the unit of `frequency` changes here, deliberately. Pre-fix it counted sliding
 * WINDOWS (12 observations -> 10); post-fix it counts DISTINCT SOURCE OBSERVATIONS
 * (-> 12). "How many observations support this pattern" is the quantity the decay damper
 * (db.ts: 0.05 / (1 + log2(frequency+1))) and `ORDER BY frequency DESC` were already
 * being read as, and the only one that can be recomputed from evidence.
 *
 * Run:  node scripts/acceptance_pattern_accumulator.mjs
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const results = [];
function check(name, fn) {
  try {
    const msg = fn();
    results.push({ name, ok: true, msg: msg || '' });
  } catch (e) {
    results.push({ name, ok: false, msg: e && e.message ? e.message : String(e) });
  }
}

const dir = mkdtempSync(join(tmpdir(), 'snarc-acc-'));
const dbPath = join(dir, 'snarc.db');

const { openDatabase, prepareStatements } = await import('../dist/src/db.js');
const { consolidate } = await import('../dist/src/consolidation.js');

const db = openDatabase(dbPath);
const stmts = prepareStatements(db);

/** N observations that form one repeating 3-window: the degenerate live case. */
function seed(n, session, tool = 'Conversation') {
  const ids = [];
  for (let i = 0; i < n; i++) {
    const info = stmts.insertObservation.run(
      session, tool, `input ${session} ${i}`, '', 0.5, 0.7, 0.8, 0.8, 0.1, 0.8, 0.8,
      '/tmp', '[]', `hash-${session}-${i}`, 'test',
    );
    ids.push(Number(info.lastInsertRowid));
  }
  return stmts.getSessionObservations.all(session);
}

const KEY = ['tool_sequence', 'Recurring workflow: Conversation → Conversation → Conversation'];
const freq = () => {
  const r = db.prepare('SELECT frequency FROM patterns WHERE kind = ? AND summary = ?').get(...KEY);
  return r ? r.frequency : null;
};

const obs = seed(12, 's1');

check('1. re-consolidating the same observations does not change frequency', () => {
  consolidate(db, stmts, obs, 's1');
  const first = freq();
  if (first === null) throw new Error('no tool_sequence pattern was created at all');
  consolidate(db, stmts, obs, 's1');
  const second = freq();
  if (second !== first) {
    throw new Error(`frequency moved on identical evidence: ${first} -> ${second} `
      + `(the accumulator is counting arrivals, not evidence)`);
  }
  return `stable at ${first} across two runs over the same 12 rows`;
});

check('2. an inflated frequency self-corrects on next touch', () => {
  db.prepare('UPDATE patterns SET frequency = 43581138 WHERE kind = ? AND summary = ?').run(...KEY);
  consolidate(db, stmts, obs, 's1');
  const f = freq();
  if (f === null) throw new Error('pattern vanished');
  if (f > 1000) {
    throw new Error(`monument survived consolidation: frequency still ${f} `
      + `(nothing derives frequency from the evidence, so it can only ever grow)`);
  }
  return `43,581,138 -> ${f} without a manual UPDATE`;
});

check('3. pattern_sources holds the evidence set', () => {
  const row = db.prepare(
    'SELECT COUNT(*) AS n FROM pattern_sources ps JOIN patterns p ON p.id = ps.pattern_id '
    + 'WHERE p.kind = ? AND p.summary = ?').get(...KEY);
  if (!row || row.n === 0) throw new Error('no evidence rows recorded for the pattern');
  if (row.n !== freq()) throw new Error(`frequency ${freq()} != evidence rows ${row.n}`);
  return `${row.n} evidence rows, equal to frequency`;
});

check('4. genuinely new evidence still raises frequency', () => {
  const before = freq();
  const obs2 = seed(9, 's2');
  consolidate(db, stmts, obs2, 's2');
  const after = freq();
  if (!(after > before)) {
    throw new Error(`new observations did not raise frequency: ${before} -> ${after} `
      + `(a guard that never lets anything through is not a guard)`);
  }
  return `${before} -> ${after} on 9 new rows`;
});

check('5. deleting a pattern does not orphan its evidence', () => {
  const p = db.prepare('SELECT id FROM patterns WHERE kind = ? AND summary = ?').get(...KEY);
  const n = db.prepare('SELECT COUNT(*) AS n FROM pattern_sources WHERE pattern_id = ?').get(p.id).n;
  db.prepare('DELETE FROM patterns WHERE id = ?').run(p.id);
  const left = db.prepare('SELECT COUNT(*) AS n FROM pattern_sources WHERE pattern_id = ?').get(p.id).n;
  if (left !== 0) throw new Error(`${left} of ${n} evidence rows survived the pattern`);
  return `${n} evidence rows removed with the pattern`;
});

db.close();
rmSync(dir, { recursive: true, force: true });

let red = 0;
console.log('\n  acceptance: tier-2 evidence guard\n');
for (const r of results) {
  if (!r.ok) red++;
  console.log(`  ${r.ok ? 'green' : 'RED  '}  ${r.name}\n         ${r.msg}`);
}
console.log(`\n  attempted ${results.length}, ${results.length - red} green, ${red} red\n`);
process.exit(red === 0 ? 0 : 1);
