#!/usr/bin/env node
/**
 * acceptance_recovery_ordering.mjs — the sequencing constraint this thread has been holding
 * the backfill for is not a constraint.
 *
 * WHAT THE THREAD DECIDED, and has now repeated across five posts as settled:
 *
 *     1. recover event_session_id from the transcripts   (99.0% unique)
 *     2. backfill seen
 *     3. every loser is a claim_conflict row
 *   "Running step 2 first is not wrong so much as irreversible on the axis step 1 repairs."
 *   (backfill_seen.mjs header; db.ts backfillRootClaims doc; carried in kimi 495/499 and
 *    CBP 492/496/498 as "sequence unchanged".)
 *
 * The claim inside it is that running the backfill first LOSES something the recovery could
 * otherwise have supplied. Three readings of the code say otherwise, and each is a check here:
 *
 *   a. `backfillRootClaims` never reads `event_session_id` when deciding ownership. It claims
 *      in shard-iteration order and copies the column into the conflict row as payload. So the
 *      value being present or NULL cannot move a single assignment.
 *   b. `claim_conflict`'s primary key is (content_hash, shard) — exactly the pair a later
 *      recovery joins on. Backfilling first leaves the column NULL on rows that are still
 *      addressable, so the recovery is a propagating UPDATE, not a lost opportunity.
 *   c. The recovery's key is the CONTENT. On the live store every one of the 12,668 duplicated
 *      hashes has byte-identical `input_summary` in every shard holding it (measured
 *      2026-07-31, all four bulk shards, full population — not a sample). A content-keyed
 *      recovery is therefore a constant function across the copies of a hash: winner and loser
 *      receive the same event session, always, by construction.
 *
 * PREDICTION, written before the first run:
 *
 *   check 1  GREEN — the sensitivity control. Two DIFFERENT shard orders must produce
 *                    DIFFERENT tables. If this is red the comparator is dead and checks 2-4
 *                    are measuring nothing; every "identical" below would be vacuous.
 *   check 2  GREEN — ownership is byte-identical with the column populated and with it NULL.
 *   check 3  GREEN — recover-then-backfill and backfill-then-recover-then-propagate reach the
 *                    identical (seen, claim_conflict) state, event_session_id included.
 *   check 4  GREEN — every duplicated hash hands the same recovered value to winner and loser,
 *                    so the column cannot discriminate between them.
 *
 * Check 1 is the one that makes the other three mean anything, and it is written to be the
 * first thing that breaks: it asserts a DIFFERENCE, on the same machinery the others assert
 * sameness on. A run where 2-4 are green and 1 is red is a dead gauge, not a finding.
 *
 * Run:  node scripts/acceptance_recovery_ordering.mjs
 */
import { mkdtempSync, rmSync, mkdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const results = [];
function check(name, fn) {
  try {
    results.push({ name, ok: true, msg: fn() || '' });
  } catch (e) {
    results.push({ name, ok: false, msg: e && e.message ? e.message : String(e) });
  }
}

const { SNARCMemory } = await import('../dist/src/memory.js');
const dbMod = await import('../dist/src/db.js');
const Database = (await import('better-sqlite3')).default;

const N = 20;
/**
 * The corpus. `sid` is the EVENT's conversation — the ground truth the recovery is trying to
 * reach. It is deliberately NOT written into the shards below: the live 791ca/7d210/230946
 * shards do not even carry the column, and 777c carries it non-null on 7 of 12,804 rows.
 * The fixture models the store as it is, not as the schema allows.
 */
const EVENTS = Array.from({ length: N }, (_, i) => ({
  text: `replayed conversation turn ${i}: one transcript corpus written into two shards`,
  ts: `2026-03-15T10:${String(i).padStart(2, '0')}:00.000Z`,
  sid: `event-session-${i % 4}`,
}));

/**
 * The recovery, modelled exactly as audit_claim_conflict_decidability.py performs it: a lookup
 * keyed on the row's CONTENT. Note what it does not take: the shard, the row id, the write
 * time. That is the whole finding of check 4, expressed as a function signature.
 */
const RECOVER = new Map(EVENTS.map((e) => [e.text, e.sid]));
function recoveredFor(inputSummary) {
  for (const [text, sid] of RECOVER) if ((inputSummary || '').includes(text)) return sid;
  return null;
}

function newRoot(tag) { return mkdtempSync(join(tmpdir(), `snarc-ordering-${tag}-`)); }
function shardPath(root, id) {
  const dir = join(root, 'projects', id);
  mkdirSync(dir, { recursive: true });
  return join(dir, 'snarc.db');
}
function dropAuthority(root) {
  for (const suffix of ['', '-wal', '-shm']) {
    const p = join(root, `seen.db${suffix}`);
    if (existsSync(p)) rmSync(p);
  }
}
/** Fill a shard with the whole corpus, leaving event_session_id NULL — the live condition. */
function fill(root, shard) {
  const mem = new SNARCMemory(shardPath(root, shard));
  mem.initSession(`ingest-${shard}`, `/proj/${shard}`);
  let stored = 0;
  for (const e of EVENTS) {
    // no sid argument: the replayer that produced the live corpus had no event session either
    if (mem.captureContext('user_prompt', e.text, `/proj/${shard}`, 0.9, e.ts)) stored++;
  }
  mem.close();
  return stored;
}
/** The step-1 recovery: fill observations.event_session_id from the content-keyed lookup. */
function recoverShards(root, shards) {
  let updated = 0;
  for (const shard of shards) {
    const db = new Database(shardPath(root, shard));
    try { db.exec('ALTER TABLE observations ADD COLUMN event_session_id TEXT'); } catch { /* migrated */ }
    const rows = db.prepare('SELECT id, input_summary FROM observations').all();
    const set = db.prepare('UPDATE observations SET event_session_id = ? WHERE id = ?');
    for (const r of rows) {
      const sid = recoveredFor(r.input_summary);
      if (sid) { set.run(sid, r.id); updated++; }
    }
    db.close();
  }
  return updated;
}
/**
 * The propagating UPDATE that reading (b) says is always available afterwards. Joins on
 * claim_conflict's own primary key. If this needs anything the backfill destroyed, it fails
 * here rather than in an argument.
 */
function propagateIntoConflicts(root, shards) {
  const seen = new Database(join(root, 'seen.db'));
  const rows = seen.prepare('SELECT content_hash, shard FROM claim_conflict').all();
  const set = seen.prepare('UPDATE claim_conflict SET event_session_id = ? WHERE content_hash = ? AND shard = ?');
  const byShard = new Map();
  for (const shard of shards) {
    const db = new Database(shardPath(root, shard), { readonly: true });
    const m = new Map();
    for (const r of db.prepare('SELECT content_hash, event_session_id FROM observations').all()) {
      m.set(r.content_hash, r.event_session_id);
    }
    db.close();
    byShard.set(shard, m);
  }
  let updated = 0;
  for (const r of rows) {
    const sid = byShard.get(r.shard)?.get(r.content_hash) ?? null;
    if (sid !== null) { set.run(sid, r.content_hash, r.shard); updated++; }
  }
  seen.close();
  return updated;
}

/** The two authority tables, as comparable text. `ts` is excluded: it is the event's clock. */
function snapshot(root) {
  const db = new Database(join(root, 'seen.db'), { readonly: true });
  const seen = db.prepare('SELECT content_hash, first_shard FROM seen ORDER BY content_hash').all();
  const conf = db.prepare(
    'SELECT content_hash, shard, session_id, event_session_id FROM claim_conflict ORDER BY content_hash, shard',
  ).all();
  db.close();
  return { seen, conf, seenText: JSON.stringify(seen), confText: JSON.stringify(conf) };
}

/** Two shards holding the identical corpus, authority dropped: the live pre-go-live state. */
function twoShardStore(tag) {
  const root = newRoot(tag);
  const a = fill(root, 'aaaaaaaaaaaa');
  dropAuthority(root);
  const b = fill(root, 'bbbbbbbbbbbb');
  dropAuthority(root);
  if (a !== N || b !== N) throw new Error(`fixture stored ${a}/${b} of ${N} — harness broken, not a finding`);
  return root;
}

// ---------------------------------------------------------------------------
// 1. SENSITIVITY CONTROL. Everything below asserts "identical"; this asserts "different".
// ---------------------------------------------------------------------------
check('1. CONTROL — a different shard ORDER does produce different tables', () => {
  const fwd = twoShardStore('ctl-fwd');
  const rev = twoShardStore('ctl-rev');
  const pf = dbMod.backfillRootClaims(fwd, { dryRun: false, shards: ['aaaaaaaaaaaa', 'bbbbbbbbbbbb'] });
  const pr = dbMod.backfillRootClaims(rev, { dryRun: false, shards: ['bbbbbbbbbbbb', 'aaaaaaaaaaaa'] });
  if (pf.totalConflicted === 0 || pr.totalConflicted === 0) {
    throw new Error(
      `the fixture produced ${pf.totalConflicted}/${pr.totalConflicted} conflicts — with no denials ` +
      `there is nothing for checks 2-4 to compare and their green would be evidence of nothing`,
    );
  }
  const sf = snapshot(fwd), sr = snapshot(rev);
  if (sf.seenText === sr.seenText) {
    throw new Error('reversing the shard order left `seen` byte-identical — the comparator cannot see a real difference, so no "identical" below means anything');
  }
  if (sf.confText === sr.confText) {
    throw new Error('reversing the shard order left `claim_conflict` byte-identical — comparator dead');
  }
  const ownersF = new Set(sf.seen.map((r) => r.first_shard));
  const ownersR = new Set(sr.seen.map((r) => r.first_shard));
  return `order a,b -> owner ${[...ownersF].join('/')}; order b,a -> owner ${[...ownersR].join('/')}; ` +
    `${pf.totalConflicted} denials each. The comparator is live.`;
});

// ---------------------------------------------------------------------------
// 2. Reading (a): the ownership decision never consults the column.
// ---------------------------------------------------------------------------
check('2. ownership is byte-identical whether event_session_id is populated or NULL', () => {
  const bare = twoShardStore('own-bare');
  const rich = twoShardStore('own-rich');
  const updated = recoverShards(rich, ['aaaaaaaaaaaa', 'bbbbbbbbbbbb']);
  if (updated !== 2 * N) throw new Error(`recovery filled ${updated} rows, want ${2 * N} — the arm is not actually enriched`);

  const order = ['aaaaaaaaaaaa', 'bbbbbbbbbbbb'];
  dbMod.backfillRootClaims(bare, { dryRun: false, shards: order });
  dbMod.backfillRootClaims(rich, { dryRun: false, shards: order });
  const sb = snapshot(bare), sr = snapshot(rich);
  if (sb.seenText !== sr.seenText) {
    throw new Error('`seen` differs between the populated and NULL arms — the column DOES move ownership, and the sequence is load-bearing after all');
  }
  const bareSids = new Set(sb.conf.map((r) => r.event_session_id));
  const richSids = new Set(sr.conf.map((r) => r.event_session_id));
  if (!(bareSids.size === 1 && bareSids.has(null))) throw new Error(`the NULL arm carried ${[...bareSids]} — fixture did not model the live shards`);
  if (richSids.has(null) || richSids.size !== 4) throw new Error(`the populated arm carried ${richSids.size} distinct event sessions, want 4 non-null`);
  return `identical \`seen\` (${sb.seen.length} rows) from both arms; conflict payload differs (${richSids.size} event sessions vs all-NULL) and changes no assignment`;
});

// ---------------------------------------------------------------------------
// 3. Reading (b): the order is reversible, and the reversal is one UPDATE on the PK.
// ---------------------------------------------------------------------------
check('3. recover-then-backfill and backfill-then-recover reach the identical state', () => {
  const order = ['aaaaaaaaaaaa', 'bbbbbbbbbbbb'];

  const first = twoShardStore('seq-recover-first');
  recoverShards(first, order);
  dbMod.backfillRootClaims(first, { dryRun: false, shards: order });

  const later = twoShardStore('seq-backfill-first');
  const plan = dbMod.backfillRootClaims(later, { dryRun: false, shards: order });
  const before = snapshot(later);
  if (!before.conf.every((r) => r.event_session_id === null)) {
    throw new Error('the backfill-first arm already carried event sessions — the arms are not distinguishable');
  }
  recoverShards(later, order);
  const propagated = propagateIntoConflicts(later, order);
  if (propagated !== plan.totalConflicted) {
    throw new Error(`propagation reached ${propagated} of ${plan.totalConflicted} conflict rows — some denial is NOT addressable after the fact, which is what "irreversible" would mean`);
  }

  const a = snapshot(first), b = snapshot(later);
  if (a.seenText !== b.seenText) throw new Error('`seen` differs between the two sequences');
  if (a.confText !== b.confText) {
    throw new Error(`claim_conflict differs between the two sequences:\n  recover-first: ${a.confText.slice(0, 200)}\n  backfill-first: ${b.confText.slice(0, 200)}`);
  }
  return `both sequences -> identical seen (${a.seen.length}) and claim_conflict (${a.conf.length}, event sessions included); the catch-up was ${propagated} UPDATEs on (content_hash, shard)`;
});

// ---------------------------------------------------------------------------
// 4. Reading (c): a content-keyed recovery is constant across the copies of a hash.
// ---------------------------------------------------------------------------
check('4. the recovered value is the SAME for winner and loser of every denial', () => {
  const order = ['aaaaaaaaaaaa', 'bbbbbbbbbbbb'];
  const root = twoShardStore('const-fn');
  recoverShards(root, order);
  dbMod.backfillRootClaims(root, { dryRun: false, shards: order });

  const byShard = new Map();
  for (const shard of order) {
    const db = new Database(shardPath(root, shard), { readonly: true });
    const m = new Map();
    for (const r of db.prepare('SELECT content_hash, input_summary, event_session_id FROM observations').all()) {
      m.set(r.content_hash, r);
    }
    db.close();
    byShard.set(shard, m);
  }
  const db = new Database(join(root, 'seen.db'), { readonly: true });
  const denials = db.prepare('SELECT cc.content_hash, cc.shard AS loser, s.first_shard AS winner FROM claim_conflict cc JOIN seen s ON s.content_hash = cc.content_hash').all();
  db.close();
  if (denials.length === 0) throw new Error('no denials to compare — evidence absent, not a green');

  let sameContent = 0, sameSid = 0, disagree = 0;
  for (const d of denials) {
    const w = byShard.get(d.winner).get(d.content_hash);
    const l = byShard.get(d.loser).get(d.content_hash);
    if (w.input_summary === l.input_summary) sameContent++;
    if (w.event_session_id === l.event_session_id) sameSid++; else disagree++;
  }
  if (sameContent !== denials.length) throw new Error(`${denials.length - sameContent} denials had differing content between copies — the live store has 0 of 12,668`);
  if (disagree !== 0) {
    throw new Error(`${disagree} denials received DIFFERENT event sessions for winner and loser — the column discriminates after all, and the recovery can pay out`);
  }
  return `${denials.length}/${denials.length} denials: identical content, identical recovered event session. ` +
    `The column separates conversations across the corpus and NOT the copies of one hash — which is the axis the attribution decision is on.`;
});

// ---------------------------------------------------------------------------
let red = 0;
for (const r of results) {
  if (!r.ok) red++;
  console.log(`${r.ok ? 'GREEN' : 'RED  '}  ${r.name}${r.msg ? `\n         ${r.msg}` : ''}`);
}
console.log(`\nattempted: ${results.length}   green: ${results.length - red}   red: ${red}`);
if (results.length !== 4) console.log(`WARNING: ${results.length} of 4 checks ran — this run is truncated, do not read the count above as a verdict`);
process.exit(red === 0 ? 0 : 1);
