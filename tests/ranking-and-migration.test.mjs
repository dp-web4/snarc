// Regression pins for #5 (GPT seat's HOLD, 2026-09-25): the three memory behaviours this PR
// changes are load-bearing, so each is pinned against a THROWAWAY database in a temp dir.
//
//   1. migration of a pre-existing DB (no shape_hash column) -- and the ordering rule that
//      the index on the new column is created only AFTER the guarded ALTER
//   2. repeated-shape discount, without changing the first occurrence
//   3. a matched Tier-2 pattern outranks Tier-1 observations in search
//   4. confidence-vs-salience secondary ordering among patterns
//   5. conversational-sequence filtering in the briefing (and wake-prompt dedupe)
//
// SNARC_DIST selects the build under test (default: this checkout's dist/). Pointing it at a
// build of the pre-PR code is how these were proven red-before.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import Database from 'better-sqlite3';

const DIST = resolve(process.env.SNARC_DIST || new URL('../dist', import.meta.url).pathname);
const { SNARCMemory } = await import(pathToFileURL(join(DIST, 'src/memory.js')).href);

// A standalone store: not under `<root>/projects/<hash>/`, so the cross-shard root claim store
// is never opened (db.ts openRootClaims returns null) and nothing outside the temp dir is touched.
function freshPath() {
  return join(mkdtempSync(join(tmpdir(), 'snarc-test-')), 'snarc.db');
}
function memoryAt(path) {
  const m = new SNARCMemory(path);
  m.initSession('test-session', '/tmp');
  return m;
}
const stmts = (m) => m.stmts; // plain JS: the private field is reachable, and names the real statement
function addPattern(m, kind, summary, frequency, confidence, detail = '') {
  stmts(m).upsertPattern.run(kind, summary, detail, frequency, '[]', confidence);
}
function columns(path, table) {
  const db = new Database(path, { readonly: true });
  try { return db.prepare(`PRAGMA table_info(${table})`).all().map((c) => c.name); }
  finally { db.close(); }
}
function indexes(path, table) {
  const db = new Database(path, { readonly: true });
  try { return db.prepare(`PRAGMA index_list(${table})`).all().map((i) => i.name); }
  finally { db.close(); }
}

// ---------------------------------------------------------------------------------------------
// 1. Migration of a pre-existing database.
// ---------------------------------------------------------------------------------------------
test('1. a pre-shape_hash database opens, gains the column and its index, and keeps its rows', () => {
  const path = freshPath();
  // Build a store, then turn it into a PRE-PR store: drop the index and the column. What is left
  // is the observations table as every existing snarc.db has it.
  let m = memoryAt(path);
  m.capture('Bash', 'git status in the repo', 'clean', '/tmp');
  m.close();
  {
    const db = new Database(path);
    db.exec('DROP INDEX IF EXISTS idx_obs_shape');
    db.exec('ALTER TABLE observations DROP COLUMN shape_hash');
    db.close();
  }
  assert.ok(!columns(path, 'observations').includes('shape_hash'), 'fixture must lack the column');

  // Reopen with the code under test. If the index on shape_hash were created in SCHEMA (before
  // the guarded ALTER), this open would throw "no such column: shape_hash" -- the 2026-06/07
  // four-day capture outage shape.
  m = memoryAt(path);
  try {
    assert.ok(columns(path, 'observations').includes('shape_hash'), 'migration added shape_hash');
    assert.ok(indexes(path, 'observations').includes('idx_obs_shape'), 'index created after the ALTER');
    const rows = stmts(m).getRecentObservations.all(10);
    assert.equal(rows.length, 1, 'the pre-existing row survived');
    assert.equal(rows[0].shape_hash, null, 'no backfill: an old row honestly has no shape');
    // Capture must still work: insertObservation names shape_hash, so a missing column would
    // throw here and capture would die silently in the hook.
    const r = m.capture('Bash', 'git log --oneline', 'a1b2c3d commit', '/tmp');
    assert.equal(r.stored, true);
    assert.equal(stmts(m).getRecentObservations.all(10).length, 2);
  } finally { m.close(); }
});

// ---------------------------------------------------------------------------------------------
// 2. Repeated-shape discount, first occurrence unchanged.
// ---------------------------------------------------------------------------------------------
test('2. a repeated SHAPE is discounted; the first occurrence keeps its full salience', () => {
  const m = memoryAt(freshPath());
  try {
    // Same boilerplate, different volatile parts (id, sha, timestamp, count) -> distinct
    // content_hash, one shape. This is the hestia wake prompt pattern.
    const wake = (id, sha, ts, n) =>
      `You are Claude, woken by the mesh. notice id=${id} sha ${sha} queued_at=${ts} pending ${n}`;
    m.capture('user_prompt', wake(13020, 'ffb18188781595dc', '2026-09-25T10:00:00Z', 3), '', '/tmp');
    const first = stmts(m).getRecentObservations.all(1)[0];
    m.capture('user_prompt', wake(13021, '0a1b2c3d4e5f6789', '2026-09-25T11:30:00Z', 7), '', '/tmp');
    const rows = stmts(m).getRecentObservations.all(10);
    const second = rows.find((r) => r.id !== first.id);
    const firstAfter = rows.find((r) => r.id === first.id);

    assert.equal(first.base_salience, first.salience, 'first occurrence is not discounted');
    assert.equal(firstAfter.base_salience, first.base_salience, 'a later copy does not rewrite the first');
    assert.ok(second.base_salience < second.salience,
      `second copy discounted: base ${second.base_salience} vs salience ${second.salience}`);
    assert.ok(second.base_salience >= 0.05, 'never discounted to zero -- the event happened');

    // A DIFFERENT shape is not discounted.
    m.capture('Bash', 'npm run build', 'ok', '/tmp');
    const other = stmts(m).getRecentObservations.all(1)[0];
    assert.equal(other.base_salience, other.salience, 'a distinct shape keeps full salience');
  } finally { m.close(); }
});

// ---------------------------------------------------------------------------------------------
// 3. A matched Tier-2 pattern outranks Tier-1 observations.
// ---------------------------------------------------------------------------------------------
test('3. search: a matched pattern is returned ahead of observations that fill the limit', () => {
  const m = memoryAt(freshPath());
  try {
    // More matching observations than the limit, as in the live store (1,861 obs vs 86 patterns),
    // each at salience 0.9 -- HIGHER than the pattern's 0.7 confidence, so the pattern must win
    // on tier, not on strength. captureContext bypasses the scorer, which (correctly) drops
    // repeated same-tool captures below its threshold after the first two.
    const topics = ['auth', 'billing', 'cache', 'deploy', 'export', 'fonts', 'gateway', 'hooks'];
    for (const t of topics) {
      m.captureContext('decision', `looked for prior art on the ${t} refactor before starting`, '/tmp', 0.9);
    }
    addPattern(m, 'deep_workflow',
      'Before fixing a bug, check for prior art: look for open PRs that touch it', 1, 0.7);
    const res = m.search('prior', 5);
    assert.equal(res.length, 5);
    assert.equal(res[0].tier, 2, `first result is the pattern, got ${JSON.stringify(res.map((r) => r.tier))}`);
    assert.ok(res.some((r) => r.tier === 2), 'the pattern survives the limit slice');
  } finally { m.close(); }
});

// ---------------------------------------------------------------------------------------------
// 4. Secondary ordering compares like with like: patterns by confidence.
// ---------------------------------------------------------------------------------------------
test('4. search: patterns are ordered by confidence (they carry no salience)', () => {
  const m = memoryAt(freshPath());
  try {
    // Inserted LOW first, so any ordering that ignores confidence (reading `salience`, which
    // patterns do not have) leaves insertion/FTS order and puts the weak one first.
    addPattern(m, 'deep_insight', 'widget calibration drifts under load (weak signal)', 1, 0.61);
    addPattern(m, 'deep_insight', 'widget calibration must be re-run after a driver update', 1, 0.95);
    const res = m.search('widget', 10).filter((r) => r.tier === 2);
    assert.equal(res.length, 2);
    assert.deepEqual(res.map((r) => r.confidence), [0.95, 0.61]);
  } finally { m.close(); }
});

// ---------------------------------------------------------------------------------------------
// 5. Briefing: conversational tool_sequences are filtered; lessons are surfaced.
// ---------------------------------------------------------------------------------------------
test('5. briefing: talk-follows-talk sequences are dropped and a lesson takes the slot', () => {
  const m = memoryAt(freshPath());
  try {
    // The live shape: conversational scaffolding at huge frequency, every lesson at frequency 1.
    addPattern(m, 'tool_sequence', 'Recurring workflow: Conversation → user_prompt → Conversation', 434, 0.9);
    addPattern(m, 'tool_sequence', 'Recurring workflow: user_prompt → Conversation → user_prompt', 300, 0.9);
    addPattern(m, 'tool_sequence', 'Recurring workflow: Conversation → Conversation → Conversation', 200, 0.9);
    addPattern(m, 'deep_workflow', 'Take a letter\'s date from the file mtime, not the wake stamp', 1, 0.75);
    const b = m.getSessionBriefing('/tmp');
    assert.match(b, /file mtime/, 'the lesson is surfaced');
    assert.doesNotMatch(b, /Conversation → user_prompt/, 'a talk-only sequence is not surfaced');
    assert.doesNotMatch(b, /Conversation → Conversation/);
  } finally { m.close(); }
});

test('5b. briefing: a real tool sequence is kept; only all-conversational ones are dropped', () => {
  const m = memoryAt(freshPath());
  try {
    addPattern(m, 'tool_sequence', 'Recurring workflow: Read → Edit → Bash', 12, 0.9);
    addPattern(m, 'tool_sequence', 'Recurring workflow: Conversation → user_prompt → Conversation', 434, 0.9);
    const b = m.getSessionBriefing('/tmp');
    assert.match(b, /Read → Edit → Bash/);
    assert.doesNotMatch(b, /Conversation → user_prompt/);
  } finally { m.close(); }
});

test('5c. briefing: the same wake text recorded twice (role-tagged and not) fills one slot', () => {
  const m = memoryAt(freshPath());
  try {
    const wake = 'You are Claude (claude-code) on CBP, woken by the hestia member mesh. Pending notices';
    m.captureContext('user_prompt', wake, '/tmp', 1.0);
    m.captureContext('Conversation', `[Human] ${wake}`, '/tmp', 1.0);
    m.captureContext('decision', 'chose to pin the migration order in a test', '/tmp', 0.9);
    const b = m.getSessionBriefing('/tmp');
    const copies = b.split('\n').filter((l) => l.includes('woken by the hestia member mesh')).length;
    assert.equal(copies, 1, `wake text appears ${copies}x:\n${b}`);
    assert.match(b, /pin the migration order/, 'the next distinct thing gets the slot');
  } finally { m.close(); }
});
