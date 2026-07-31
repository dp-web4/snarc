#!/usr/bin/env node
/**
 * acceptance_claim_recurrence.mjs — the root claim authority starts EMPTY, and the corpus
 * it was built to protect is already on disk.
 *
 * THE MEASUREMENT THAT PRODUCED THIS SCRIPT (live stores, 2026-07-31, read-only):
 *
 *   copies-per-hash over the live shards   1 -> 203   2 -> 4   3 -> 5   4 -> 12,659
 *   the 12,659 sit in EXACTLY one shard set: 23094633bebc, 777c4901744b, 791cace57ce9,
 *   7d210ad7238a — one transcript corpus replayed FOUR times (the thread had recorded two).
 *
 *   In 777c4901744b — the only shard holding both populations — every duplicated row has a
 *   lower AUTOINCREMENT id than every hash claimed since go-live (12,726 < 12,729).
 *
 * So the duplicated corpus was written before the authority made its first claim, and the
 * authority did not inherit it. That is an ORDER, not a clock: `observations.ts` is write time
 * before c48af34 and event time after, mixed in one column, so the obvious timestamp version of
 * this sentence dates nothing — the first draft of this header quoted a "2m59s gap" off exactly
 * that comparison, and it was an artifact. The thread has been reading
 * `claim_conflict rows: 0` as "installed and unfired". That reading needs a denominator, and
 * the denominator is measured, not caveated: of the 44 hashes claimed since go-live, the
 * number whose content also exists in another live shard — i.e. that a seeded authority could
 * have denied — is 0/44. The zero is not weak evidence of rarity. It is no evidence, because
 * the population that can produce a denial had stopped writing three minutes earlier.
 *
 * That much is arithmetic over the live store. The claim this script exists to TEST is
 * behavioural, and asserting it would have been the same mistake in new clothes:
 *
 *   a replay of the already-duplicated corpus into a FIFTH shard is NOT denied,
 *   because `captureContext`'s per-shard guard returns early on anything the shard already
 *   holds, so only first-sightings reach `claimSeen` — and a fresh `seen.db` has never seen
 *   the corpus that is already on disk.
 *
 * PREDICTION, written before the first run (pre-fix tree = f149deb, which has the authority
 * but no `backfillRootClaims`):
 *
 *   check 1  GREEN pre AND post — it characterizes the live gap and depends on no new code.
 *                                 It is the one check here that is green BY the defect: it
 *                                 goes red the day the backfill becomes automatic, which is
 *                                 the correct signal, not a regression.
 *   checks 2-6  RED pre-fix (no such export), GREEN post-fix.
 *
 * A red count alone identifies no configuration, so the shape is named: pre-fix reds must be
 * `backfillRootClaims is not a function`, and check 1 must be green in BOTH runs. A pre-fix
 * run where check 1 is red means the harness broke, not that the finding replicated.
 *
 * Run:  node scripts/acceptance_claim_recurrence.mjs
 */
import { mkdtempSync, rmSync, mkdirSync, existsSync } from 'node:fs';
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

const { SNARCMemory } = await import('../dist/src/memory.js');
const dbMod = await import('../dist/src/db.js');
const Database = (await import('better-sqlite3')).default;

/** A corpus small enough to read and big enough that a miscount is visible. */
const N = 20;
const EVENTS = Array.from({ length: N }, (_, i) => ({
  text: `replayed conversation turn ${i}: the leak is one transcript written into two shards`,
  ts: `2026-03-15T10:${String(i).padStart(2, '0')}:00.000Z`,
  sid: `event-session-${i % 4}`,          // 4 distinct event sessions, to prove the column survives
}));

function newRoot(tag) {
  return mkdtempSync(join(tmpdir(), `snarc-recurrence-${tag}-`));
}
function shardPath(root, id) {
  const dir = join(root, 'projects', id);
  mkdirSync(dir, { recursive: true });
  return join(dir, 'snarc.db');
}
function seenDb(root) {
  return new Database(join(root, 'seen.db'), { readonly: true });
}
/** Delete the authority, leaving the shard corpus behind: the live pre-go-live condition. */
function dropAuthority(root) {
  for (const suffix of ['', '-wal', '-shm']) {
    const p = join(root, `seen.db${suffix}`);
    if (existsSync(p)) rmSync(p);
  }
}
/** Fill a shard with the whole corpus. Returns how many were actually stored. */
function fill(root, shard, session) {
  const mem = new SNARCMemory(shardPath(root, shard));
  mem.initSession(session, `/proj/${shard}`);
  let stored = 0;
  for (const e of EVENTS) {
    if (mem.captureContext('user_prompt', e.text, `/proj/${shard}`, 0.9, e.ts, e.sid)) stored++;
  }
  const held = mem.getContext(session).filter((r) => r.tool_name === 'user_prompt').length;
  mem.close();
  return { stored, held };
}

// ---------------------------------------------------------------------------
// 1. The gap itself, on the current tree. Green BY the defect.
// ---------------------------------------------------------------------------
check('1. a replay into a NEW shard is not denied when the corpus predates the authority', () => {
  const root = newRoot('gap');
  const a = fill(root, 'aaaaaaaaaaaa', 'session-A');
  if (a.stored !== N) throw new Error(`shard A stored ${a.stored}/${N} — harness broken, not a finding`);
  dropAuthority(root);   // the live condition: four full copies on disk, seen.db born empty

  const b = fill(root, 'bbbbbbbbbbbb', 'session-B');
  const conflicts = seenDb(root).prepare('SELECT COUNT(*) AS n FROM claim_conflict').get().n;
  if (b.stored !== N || b.held !== N) {
    throw new Error(
      `the fifth-copy replay was denied (${b.stored} stored / ${b.held} held of ${N}) — ` +
      `the gap this script characterizes is CLOSED; if a backfill became automatic, this red is the signal`,
    );
  }
  if (conflicts !== 0) throw new Error(`claim_conflict recorded ${conflicts} denials, want 0`);
  return `shard B stored all ${N} again; claim_conflict = 0 — the authority is inert against its own incident`;
});

// ---------------------------------------------------------------------------
// 2-6. The backfill.
// ---------------------------------------------------------------------------
check('2. the backfill seeds the authority and the next replay IS denied', () => {
  const root = newRoot('fix');
  fill(root, 'aaaaaaaaaaaa', 'session-A');
  dropAuthority(root);

  const plan = dbMod.backfillRootClaims(root, { dryRun: false });
  if (plan.totalClaimed !== N) throw new Error(`backfill claimed ${plan.totalClaimed}/${N}`);
  if (plan.totalConflicted !== 0) throw new Error(`one shard produced ${plan.totalConflicted} conflicts`);

  const b = fill(root, 'bbbbbbbbbbbb', 'session-B');
  if (b.stored !== 0 || b.held !== 0) {
    throw new Error(`shard B stored ${b.stored} / holds ${b.held} of ${N} after a seeded authority`);
  }
  const conflicts = seenDb(root).prepare(
    'SELECT COUNT(*) AS n FROM claim_conflict WHERE shard = ?',
  ).get('bbbbbbbbbbbb').n;
  if (conflicts !== N) throw new Error(`claim_conflict recorded ${conflicts} denials for B, want ${N}`);
  return `seeded ${plan.totalClaimed}; replay into B: 0 stored, ${conflicts} denials recorded`;
});

check('3. a dry run reports the identical plan and writes nothing', () => {
  const root = newRoot('dry');
  fill(root, 'aaaaaaaaaaaa', 'session-A');
  dropAuthority(root);

  const dry = dbMod.backfillRootClaims(root);                       // default IS dry
  if (!dry.dryRun) throw new Error('the default is not a dry run — the write must be an explicit act');
  const after = seenDb(root).prepare('SELECT COUNT(*) AS n FROM seen').get().n;
  if (after !== 0) throw new Error(`a dry run left ${after} rows in seen`);

  const wet = dbMod.backfillRootClaims(root, { dryRun: false });
  if (wet.totalClaimed !== dry.totalClaimed || wet.totalConflicted !== dry.totalConflicted) {
    throw new Error(
      `the dry plan (${dry.totalClaimed}/${dry.totalConflicted}) is not the plan that ran ` +
      `(${wet.totalClaimed}/${wet.totalConflicted}) — the preview is a separate estimator`,
    );
  }
  return `dry: ${dry.totalClaimed} claimed, 0 written; wet matched exactly`;
});

check('4. two shards holding the same corpus: one owner, and the loser is recorded', () => {
  const root = newRoot('two');
  fill(root, 'aaaaaaaaaaaa', 'session-A');
  dropAuthority(root);
  fill(root, 'bbbbbbbbbbbb', 'session-B');   // the leak, already on disk in both shards
  dropAuthority(root);

  const plan = dbMod.backfillRootClaims(root, { dryRun: false });
  const db = seenDb(root);
  const seen = db.prepare('SELECT COUNT(*) AS n FROM seen').get().n;
  const owners = db.prepare('SELECT first_shard, COUNT(*) AS n FROM seen GROUP BY 1').all();
  const conflicts = db.prepare('SELECT shard, COUNT(*) AS n FROM claim_conflict GROUP BY 1').all();
  if (seen !== N) throw new Error(`seen holds ${seen} rows, want ${N} — the same event was claimed twice`);
  if (owners.length !== 1 || owners[0].first_shard !== 'aaaaaaaaaaaa' || owners[0].n !== N) {
    throw new Error(`ownership is ${JSON.stringify(owners)}, want all ${N} to the first shard in order`);
  }
  if (conflicts.length !== 1 || conflicts[0].shard !== 'bbbbbbbbbbbb' || conflicts[0].n !== N) {
    throw new Error(`the losing side is ${JSON.stringify(conflicts)}, want ${N} rows naming bbbbbbbbbbbb`);
  }
  if (plan.totalConflicted !== N) throw new Error(`plan reported ${plan.totalConflicted} conflicts, want ${N}`);
  return `seen ${seen} (all -> A), claim_conflict ${N} (all -> B) — the assignment is a queryable fact`;
});

check('5. the conflict row carries the EVENT session, not just the ingesting one', () => {
  const root = newRoot('sid');
  fill(root, 'aaaaaaaaaaaa', 'session-A');
  dropAuthority(root);
  fill(root, 'bbbbbbbbbbbb', 'session-B');
  dropAuthority(root);
  dbMod.backfillRootClaims(root, { dryRun: false });

  const db = seenDb(root);
  const rows = db.prepare('SELECT session_id, event_session_id FROM claim_conflict').all();
  const ingest = new Set(rows.map((r) => r.session_id));
  const events = new Set(rows.map((r) => r.event_session_id));
  if (ingest.size !== 1 || !ingest.has('session-B')) {
    throw new Error(`ingest axis is ${JSON.stringify([...ingest])}, want the single denied session`);
  }
  // 62009ae's whole point: the event axis must VARY where the ingest axis is constant.
  if (events.size !== 4) {
    throw new Error(
      `event_session_id takes ${events.size} distinct values, want 4 — ` +
      `if it collapsed to 1 the backfill re-created the constant this fix removed`,
    );
  }
  return `ingest constant (1 value), event axis varies (${events.size} values) — both columns survive the backfill`;
});

check('6. the backfill is idempotent', () => {
  const root = newRoot('idem');
  fill(root, 'aaaaaaaaaaaa', 'session-A');
  dropAuthority(root);
  fill(root, 'bbbbbbbbbbbb', 'session-B');
  dropAuthority(root);

  const first = dbMod.backfillRootClaims(root, { dryRun: false });
  const second = dbMod.backfillRootClaims(root, { dryRun: false });
  const db = seenDb(root);
  const seen = db.prepare('SELECT COUNT(*) AS n FROM seen').get().n;
  const conflicts = db.prepare('SELECT COUNT(*) AS n FROM claim_conflict').get().n;
  if (seen !== N) throw new Error(`seen holds ${seen} after two runs, want ${N}`);
  if (conflicts !== N) throw new Error(`claim_conflict holds ${conflicts} after two runs, want ${N}`);
  if (second.totalClaimed !== 0) throw new Error(`the second run claimed ${second.totalClaimed} new hashes`);
  if (second.totalConflicted !== first.totalConflicted) {
    throw new Error(`conflict count moved between identical runs (${first.totalConflicted} -> ${second.totalConflicted})`);
  }
  return `run 2 claimed 0 new; seen ${seen}, claim_conflict ${conflicts} unchanged`;
});

// ---------------------------------------------------------------------------
let red = 0;
for (const r of results) {
  if (!r.ok) red++;
  console.log(`${r.ok ? 'GREEN' : 'RED  '}  ${r.name}${r.msg ? `\n         ${r.msg}` : ''}`);
}
console.log(`\nattempted: ${results.length}   green: ${results.length - red}   red: ${red}`);
process.exit(red === 0 ? 0 : 1);
