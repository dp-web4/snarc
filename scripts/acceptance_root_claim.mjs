#!/usr/bin/env node
/**
 * acceptance_root_claim.mjs — the cross-shard claim authority (seen.db) and the
 * captureContext ts thread-through.
 *
 * Written to be RED on the pre-fix tree (e9d5210). Each check is crash-isolated so a
 * throw is a red rather than an early exit that reports fewer reds than exist (same
 * harness shape as acceptance_dedup_scope.mjs / acceptance_pattern_accumulator.mjs).
 *
 * The mechanism under test was measured live 2026-07-31: one transcript's 12,606
 * Conversation events first-written into TWO shards eight minutes apart (791ca at
 * 04:22, 7d210 at 04:30), each shard internally perfect, 0.1% in the archive — a
 * duplicate no per-shard authority can see by construction
 * (forum/kimi-the-leak-is-two-first-writes-…-2026-07-31.md §3).
 *
 * Prediction written BEFORE the pre-fix run: checks 1-5 RED (1: no seen.db; 2: the
 * second shard stores — the leak itself; 3+4: no claim_conflict table; 5: captureContext
 * has no ts parameter, so the row takes write time), checks 6-7 green both.
 *
 * MEASURED, and the prediction was wrong by one: PRE-fix (e9d5210, detached worktree,
 * script carried over, dist built from that tree) 6 red / 1 green, 7 attempted;
 * POST-fix 7 green / 0 red, 7 attempted. Check 7 also reds pre-fix — not behaviourally
 * (pre-fix has no root, so its store step simply works) but because its setup asserts
 * seen.db exists: with no claim authority there is no crash window to heal, so the
 * scenario cannot even be constructed. Checks 1, 3, 4 red on the same missing file;
 * checks 2 and 5 red on behaviour — 2 is the 12,606 leak running live in miniature,
 * 5 is write time wearing provenance's clothes. The wrong prediction stays in the git
 * history of this header rather than being quietly corrected, because a red COUNT
 * identifies no configuration on its own.
 *
 *   1. RED pre-fix (no seen.db)     — first write claims the hash at the root.
 *   2. RED pre-fix (shard B stores) — the same event into a second shard is DENIED:
 *                                     returns false, B holds no row, root still names A.
 *   3. RED pre-fix (no table)       — the denial is RECORDED: claim_conflict carries the
 *                                     denied shard and its session (CBP's amendment).
 *   4. RED pre-fix (no table)       — re-capture in the OWNING shard no-ops WITHOUT a new
 *                                     conflict row (a35e3a8 composes; a within-shard replay
 *                                     is not a cross-shard denial).
 *   5. RED pre-fix (ts = write time)— a supplied event ts lands on the stored row,
 *                                     normalized to the column's format.
 *   6. green both                   — a store opened outside projects/<hash>/ (standalone:
 *                                     tests, explicit paths) still captures: no root, no
 *                                     regression. Exists so 1-5 cannot pass by captureContext
 *                                     becoming a no-op.
 *   7. green both                   — crash-window heal: the root names THIS shard as owner
 *                                     but the shard holds no row (claim succeeded, process
 *                                     died before the store) — a retry must store, not
 *                                     no-op into a permanently lost event.
 *
 * Run:  node scripts/acceptance_root_claim.mjs
 */
import { mkdtempSync, rmSync, mkdirSync } from 'node:fs';
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

const root = mkdtempSync(join(tmpdir(), 'snarc-root-claim-'));
const shardPath = (id) => {
  const dir = join(root, 'projects', id);
  mkdirSync(dir, { recursive: true });
  return join(dir, 'snarc.db');
};
const A = 'aaaaaaaaaaaa';
const B = 'bbbbbbbbbbbb';

const { SNARCMemory } = await import('../dist/src/memory.js');
const Database = (await import('better-sqlite3')).default;

const memA = new SNARCMemory(shardPath(A));
memA.initSession('session-A', '/proj/a');
const memB = new SNARCMemory(shardPath(B));
memB.initSession('session-B', '/proj/b');

const TEXT = 'the authority belongs at the root because the leak is two first-writes of one event';
const EVENT_TS = '2026-03-15T10:00:00.000Z';
const seenDb = () => new Database(join(root, 'seen.db'), { readonly: true });
const rows = (mem, session) => mem.getContext(session).filter((r) => r.tool_name === 'user_prompt');

check('1. first write stores in shard A and claims the hash at the root', () => {
  const stored = memA.captureContext('user_prompt', TEXT, '/proj/a', 0.9, EVENT_TS);
  if (!stored) throw new Error('first-ever capture returned false');
  if (rows(memA, 'session-A').length !== 1) throw new Error('shard A holds no row');
  const seen = seenDb().prepare('SELECT first_shard, first_ts FROM seen').all();
  if (seen.length !== 1) throw new Error(`seen holds ${seen.length} rows, want 1 (is there a root claim at all?)`);
  if (seen[0].first_shard !== A) throw new Error(`root names ${seen[0].first_shard}, want ${A}`);
  if (seen[0].first_ts !== '2026-03-15 10:00:00') {
    throw new Error(`first_ts is ${seen[0].first_ts}, want the event's own ts`);
  }
  return `seen -> ${A} @ ${seen[0].first_ts}`;
});

check('2. the same event into shard B is denied (the 12,606 mechanism, stopped)', () => {
  const stored = memB.captureContext('user_prompt', TEXT, '/proj/b', 0.9, EVENT_TS);
  if (stored) throw new Error('shard B STORED the duplicate — the cross-shard leak is open');
  if (rows(memB, 'session-B').length !== 0) throw new Error('shard B holds a row it was denied');
  const owner = seenDb().prepare('SELECT first_shard FROM seen').get();
  if (owner.first_shard !== A) throw new Error(`ownership moved to ${owner.first_shard}`);
  return 'returns false, B holds 0 rows, root still names A';
});

check('3. the denial is recorded with the denied shard and its session', () => {
  const conflicts = seenDb().prepare('SELECT * FROM claim_conflict').all();
  if (conflicts.length !== 1) throw new Error(`claim_conflict holds ${conflicts.length} rows, want 1`);
  const c = conflicts[0];
  if (c.shard !== B) throw new Error(`conflict names shard ${c.shard}, want ${B} (the DENIED shard)`);
  if (c.session_id !== 'session-B') throw new Error(`conflict session is ${c.session_id}, want session-B`);
  if (c.ts !== '2026-03-15 10:00:00') throw new Error(`conflict ts is ${c.ts}, want the denied event's ts`);
  return `${B} / session-B / ${c.ts}`;
});

check('4. re-capture in the OWNING shard no-ops and records no new conflict', () => {
  const again = memA.captureContext('user_prompt', TEXT, '/proj/a', 0.9, EVENT_TS);
  if (again) throw new Error('owning shard re-stored its own event (a35e3a8 regression)');
  if (rows(memA, 'session-A').length !== 1) throw new Error('owning shard duplicated its row');
  const n = seenDb().prepare('SELECT COUNT(*) AS n FROM claim_conflict').get().n;
  if (n !== 1) throw new Error(`a within-shard replay was recorded as a cross-shard denial (${n} rows)`);
  return 'no-op, still 1 row in A, still 1 conflict row';
});

check('5. the stored row carries the event ts, not the write time', () => {
  const r = rows(memA, 'session-A')[0];
  if (r.ts !== '2026-03-15 10:00:00') {
    throw new Error(`stored ts is ${r.ts} — write time wearing provenance's clothes`);
  }
  return `ts = ${r.ts}`;
});

check('6. a standalone store (no projects/<hash>/ path) still captures', () => {
  const solo = new SNARCMemory(join(root, 'standalone.db'));
  solo.initSession('session-solo', '/tmp');
  const stored = solo.captureContext('user_prompt', 'standalone capture still works without a root', '/tmp');
  solo.close();
  if (!stored) throw new Error('capture failed on a store with no root authority');
  return 'stored, no root consulted';
});

check('7. crash window heals: root names this shard, shard lacks the row -> retry stores', () => {
  // Simulate: claim succeeded, process died before the shard stored. Shard C's seen row
  // exists (written via C's own first capture of a DIFFERENT event is not enough — we
  // need owner==C with no C row), so claim directly through a fresh memory on C whose
  // first capture we then delete from the shard, leaving the root row behind.
  const C = 'cccccccccccc';
  const memC = new SNARCMemory(shardPath(C));
  memC.initSession('session-C', '/proj/c');
  const text = 'an event whose shard row was lost between claim and store';
  if (!memC.captureContext('user_prompt', text, '/proj/c')) throw new Error('setup: first capture failed');
  const shardDb = new Database(shardPath(C));
  shardDb.prepare("DELETE FROM observations WHERE tool_name = 'user_prompt'").run();
  shardDb.close();
  const healed = memC.captureContext('user_prompt', text, '/proj/c');
  if (!healed) throw new Error('retry no-oped: the event is permanently lost (root claimed, shard empty)');
  if (memC.getContext('session-C').filter((r) => r.tool_name === 'user_prompt').length !== 1) {
    throw new Error('heal stored the wrong number of rows');
  }
  const n = seenDb().prepare('SELECT COUNT(*) AS n FROM claim_conflict').get().n;
  if (n !== 1) throw new Error(`self-owned re-claim recorded a conflict (${n} rows)`);
  memC.close();
  return 'retry stored 1 row, no conflict recorded against self';
});

memA.close();
memB.close();

let red = 0;
console.log('\n  acceptance: root claim authority + ts thread-through\n');
for (const r of results) {
  if (!r.ok) red++;
  console.log(`  ${r.ok ? 'green' : 'RED  '}  ${r.name}\n         ${r.msg}`);
}
console.log(`\n  attempted ${results.length}, ${results.length - red} green, ${red} red\n`);

rmSync(root, { recursive: true, force: true });
process.exit(red === 0 ? 0 : 1);
