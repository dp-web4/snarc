#!/usr/bin/env node
/**
 * acceptance_session_provenance.mjs — the OTHER half of the signature repair.
 *
 * `ts` and `sessionId` sit on the same Claude Code transcript entry. c48af34 revived the
 * first and stepped over the second, so every replayed turn still reached the store labelled
 * with the INGESTING session — for the replayer, the constant host id 888f190a (kimi,
 * 2026-07-31: a host_session_id, not a CLI session).
 *
 * That is not a cosmetic gap, and it lands on CBP's own claim_conflict amendment
 * (forum/cbp-the-question-your-design-rests-on-…-2026-07-31.md §4a), which argued the denial
 * record makes the re-attribution question answerable "directly, with no proxy" and named
 * session_id as the key column. Measured on the live shards the same day:
 *
 *     23094633bebc   12,666 / 12,666 rows on 888f190a
 *     791cace57ce9   12,672 / 12,672
 *     7d210ad7238a   12,680 / 12,743
 *     777c4901744b   12,675 / 12,738
 *
 * One id, present in every bulk shard. So the join "does the owner hold this session" does not
 * return NULL for a replayer denial — it returns TRUE, for every pair of shards, by
 * construction. The instrument answers "re-attribution, nothing lost" with total confidence and
 * no information. A blind spot that returns a constant is worse than one that returns a blank,
 * because only the blank is visible. Check 4 is that fact, executable.
 *
 * The discriminator exists and was being discarded: `entry.sessionId` is on every transcript
 * line (400 files sampled 2026-07-31 — 400 distinct ids, exactly one per file, zero files
 * carrying two). It goes in its OWN column, not over session_id: consolidate() and
 * rehydrateBuffer() scope on getSessionObservations(this.sessionId), so overwriting it would
 * silently empty both. Ingest scope and event provenance are two axes; the corpus had one
 * column and the ingest axis won.
 *
 * Crash-isolated per check (a throw is a red, not an early exit that under-reports reds) —
 * same harness as acceptance_root_claim.mjs / acceptance_pattern_accumulator.mjs.
 *
 * PREDICTION, written before the pre-fix run (tree c48af34): checks 1, 2, 3, 5, 7 RED;
 * check 4 RED (it reads the conflict row check 3 could not write); check 6 green both.
 * So 6 red / 1 green, 7 attempted. Recorded before measuring and left in git history if
 * wrong — a red COUNT identifies no configuration on its own, so the reds table below is
 * the claim, not the total.
 *
 *   1. the Claude recognizer carries the turn's own sessionId off the entry it already
 *      reads `timestamp` from.
 *   2. a replayed turn stores BOTH axes and keeps them apart: session_id = the ingesting
 *      session, event_session_id = who actually said it.
 *   3. a cross-shard denial records the denied EVENT's session, not only the ingest session.
 *   4. THE FINDING: two shards replaying different conversations under ONE ingest session.
 *      On event_session_id the denial is decidable (owner never saw conv-beta). On
 *      session_id it is not merely blind — it returns "the owner holds that session",
 *      which is a confident wrong answer. Both halves asserted.
 *   5. a live-hook capture (no transcript, no sid) leaves event_session_id NULL rather than
 *      copying the ingest id — the blind fraction stays countable as a column.
 *   6. green both: a standalone store still captures. Exists so 1-5 cannot be satisfied by
 *      captureContext becoming a no-op.
 *   7. end-to-end: a Claude transcript replay lands the conversation id on the row.
 *   8. the kimi wire format derives its sid from the PATH (`session_<uuid>/agents/<agent>/
 *      wire.jsonl` — measured 175/175 on this host, 0 misses, kimi 2026-07-31), and a
 *      non-matching path yields no sid rather than a guessed one.
 *
 * Run:  node scripts/acceptance_session_provenance.mjs
 */
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from 'node:fs';
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

const root = mkdtempSync(join(tmpdir(), 'snarc-session-prov-'));
const shardPath = (id) => {
  const dir = join(root, 'projects', id);
  mkdirSync(dir, { recursive: true });
  return join(dir, 'snarc.db');
};
const A = 'aaaaaaaaaaaa';
const B = 'bbbbbbbbbbbb';

// The replayer's reality: ONE long-lived host id doing the ingesting, in both shards.
const HOST = '888f190a-f01d-4efe-a5a0-5320307d31ab';
const CONV_A = 'conv-alpha-11111111';
const CONV_B = 'conv-beta-22222222';

const { SNARCMemory } = await import('../dist/src/memory.js');
const { claudeRecognizer, kimiRecognizer, parseTranscript } = await import('../dist/src/conversation-capture.js');
const Database = (await import('better-sqlite3')).default;

const seenDb = () => new Database(join(root, 'seen.db'), { readonly: true });
const obs = (path) => new Database(path, { readonly: true })
  .prepare("SELECT * FROM observations WHERE tool_name IN ('user_prompt','Conversation')").all();

const pathA = shardPath(A);
const pathB = shardPath(B);
const memA = new SNARCMemory(pathA);
memA.initSession(HOST, '/proj/a');
const memB = new SNARCMemory(pathB);
memB.initSession(HOST, '/proj/b');

const TEXT = 'the ts and the session id died at the same signature and only one of them was revived';
const EVENT_TS = '2026-03-15T10:00:00.000Z';

check('1. the recognizer carries the turn\'s own sessionId', () => {
  const turn = claudeRecognizer({
    type: 'user',
    sessionId: CONV_A,
    timestamp: EVENT_TS,
    message: { content: 'a user turn long enough to clear the twenty character floor' },
  });
  if (!turn) throw new Error('recognizer did not recognize a well-formed user entry');
  if (turn.ts !== EVENT_TS) throw new Error(`ts regressed: ${turn.ts}`);
  if (turn.sid !== CONV_A) {
    throw new Error(`turn.sid is ${turn.sid} — sessionId is on the entry and is being stepped over`);
  }
  // And it must not invent one when the format genuinely lacks it (kimi wire.jsonl).
  const noSid = claudeRecognizer({
    type: 'user', timestamp: EVENT_TS,
    message: { content: 'a user turn long enough to clear the twenty character floor' },
  });
  if (noSid.sid !== undefined) throw new Error(`invented a sid (${noSid.sid}) for an entry that has none`);
  return `sid = ${turn.sid}, absent stays undefined`;
});

check('2. a stored row keeps the two axes apart', () => {
  const stored = memA.captureContext('user_prompt', TEXT, '/proj/a', 0.9, EVENT_TS, CONV_A);
  if (!stored) throw new Error('first-ever capture returned false');
  const r = obs(pathA)[0];
  if (!r) throw new Error('shard A holds no row');
  if (r.session_id !== HOST) throw new Error(`session_id is ${r.session_id} — the ingest axis moved`);
  if (r.event_session_id !== CONV_A) {
    throw new Error(`event_session_id is ${r.event_session_id}, want ${CONV_A} (who actually said it)`);
  }
  if (r.ts !== '2026-03-15 10:00:00') throw new Error(`ts regressed to ${r.ts}`);
  return `session_id=${HOST.slice(0, 8)}… event_session_id=${CONV_A}`;
});

check('3. the denial records the denied EVENT\'s session', () => {
  const stored = memB.captureContext('user_prompt', TEXT, '/proj/b', 0.9, EVENT_TS, CONV_B);
  if (stored) throw new Error('shard B stored the duplicate — the cross-shard leak is open');
  const c = seenDb().prepare('SELECT * FROM claim_conflict').get();
  if (!c) throw new Error('no conflict row recorded');
  if (c.shard !== B) throw new Error(`conflict names ${c.shard}, want the denied shard ${B}`);
  if (c.session_id !== HOST) throw new Error(`ingest axis is ${c.session_id}, want the host id`);
  if (c.event_session_id !== CONV_B) {
    throw new Error(`event_session_id is ${c.event_session_id}, want ${CONV_B} — the denial is unattributable`);
  }
  return `denied ${B} / ingest=host / event=${CONV_B}`;
});

check('4. the finding: on the ingest axis the denial answers confidently and WRONGLY', () => {
  const c = seenDb().prepare('SELECT * FROM claim_conflict').get();
  if (!c) throw new Error('no conflict row to classify');
  const ownerRows = obs(pathA);

  // The question CBP's §4a promised the denial record would answer:
  // "was the denied write from a session the owner never saw?"
  const ownerHasEventSession = ownerRows.some((r) => r.event_session_id === c.event_session_id);
  const ownerHasIngestSession = ownerRows.some((r) => r.session_id === c.session_id);

  if (ownerHasEventSession) {
    throw new Error('owner appears to hold conv-beta — the event axis does not discriminate either');
  }
  // The other half, and the reason this check exists: the ORIGINAL key column does not merely
  // fail to answer. It answers "the owner already has that session", i.e. "re-attribution,
  // nothing was lost" — for a denial that genuinely deleted a second conversation's row.
  if (!ownerHasIngestSession) {
    throw new Error(
      'setup is not the measured regime: the two shards must share ONE ingest session '
      + '(the replayer\'s host id) for this to reproduce the corpus',
    );
  }
  return 'event axis: owner never saw conv-beta (re-say, decidable). '
    + 'ingest axis: "owner holds it" — confident, and wrong';
});

check('5. a live-hook capture leaves the event axis NULL, not a copy of the ingest id', () => {
  const stored = memA.captureContext('user_prompt', 'a hook firing on the event it records', '/proj/a');
  if (!stored) throw new Error('live-hook capture returned false');
  const r = obs(pathA).find((x) => x.input_summary.startsWith('a hook firing'));
  if (!r) throw new Error('the hook row was not stored');
  if (r.event_session_id !== null) {
    throw new Error(
      `event_session_id is ${r.event_session_id}, want NULL — a copied ingest id makes an `
      + 'unknowable row indistinguishable from a known one, which is the defect being fixed',
    );
  }
  return 'NULL — the blind fraction stays countable';
});

check('6. a standalone store still captures (no-op guard)', () => {
  const solo = new SNARCMemory(join(root, 'standalone.db'));
  solo.initSession('session-solo', '/tmp');
  const stored = solo.captureContext('user_prompt', 'standalone capture still works without a root', '/tmp');
  solo.close();
  if (!stored) throw new Error('capture failed on a store with no root authority');
  return 'stored, no root consulted';
});

// End-to-end, outside the numbered checks: the real replay path must carry sid through
// parseTranscript -> captureContext. Reported as check 7 only if the file parses at all.
check('7. end-to-end: a transcript replay lands the conversation id on the row', () => {
  const tpath = join(root, 'transcript.jsonl');
  writeFileSync(tpath, [
    JSON.stringify({ type: 'user', sessionId: CONV_B, timestamp: EVENT_TS,
      message: { content: 'a replayed human turn well past the twenty character floor for capture' } }),
  ].join('\n') + '\n');
  const turns = parseTranscript(tpath);
  if (turns.length !== 1) throw new Error(`parsed ${turns.length} turns, want 1`);
  if (turns[0].sid !== CONV_B) throw new Error(`parseTranscript dropped sid (${turns[0].sid})`);
  return `parsed sid = ${turns[0].sid}`;
});

// The kimi half of the corpus: no wire entry carries the session id — it lives in the PATH
// (`…/session_<uuid>/agents/<agent>/wire.jsonl`). Measured 2026-07-31 on this host: 175/175
// wire.jsonl match, 0 misses; subagent wires (agent-2, …) share the parent session uuid, which
// is correct — they are events OF that conversation.
const KIMI_UUID = '2b4e21e7-5101-4f25-a3d8-c6a33fafafce';
const KIMI_WIRE_ENTRY = {
  type: 'context.append_loop_event',
  timestamp: EVENT_TS,
  event: { type: 'content.part', part: { type: 'text', text: TEXT + ' — long enough to clear the kimi floor' } },
};

check('8. the kimi recognizer derives sid from the transcript PATH, and guesses nothing', () => {
  const wirePath = join(root, 'sessions', 'wd_test_abc123', `session_${KIMI_UUID}`, 'agents', 'main', 'wire.jsonl');
  const turn = kimiRecognizer(KIMI_WIRE_ENTRY, { transcriptPath: wirePath });
  if (!turn) throw new Error('kimiRecognizer did not recognize a well-formed wire entry');
  if (turn.sid !== KIMI_UUID) {
    throw new Error(`turn.sid is ${turn.sid}, want ${KIMI_UUID} — the path is not being read`);
  }
  // The honest-blind half: a path that does not match the kimi shape yields NO sid — never a
  // guessed one. And with no context at all (the pre-fix call shape), the same holds.
  const oddPath = kimiRecognizer(KIMI_WIRE_ENTRY, { transcriptPath: join(root, 'transcripts', 'wire.jsonl') });
  if (oddPath.sid !== undefined) throw new Error(`invented a sid (${oddPath.sid}) from a non-kimi path`);
  const noCtx = kimiRecognizer(KIMI_WIRE_ENTRY);
  if (noCtx.sid !== undefined) throw new Error(`invented a sid (${noCtx.sid}) with no context`);
  // End-to-end through the real replay path, subagent wire included.
  const subPath = join(root, 'sessions', 'wd_test_abc123', `session_${KIMI_UUID}`, 'agents', 'agent-2', 'wire.jsonl');
  mkdirSync(join(root, 'sessions', 'wd_test_abc123', `session_${KIMI_UUID}`, 'agents', 'agent-2'), { recursive: true });
  writeFileSync(subPath, JSON.stringify(KIMI_WIRE_ENTRY) + '\n');
  const parsed = parseTranscript(subPath);
  if (parsed.length !== 1) throw new Error(`parsed ${parsed.length} turns, want 1`);
  if (parsed[0].sid !== KIMI_UUID) throw new Error(`parseTranscript dropped the path-derived sid (${parsed[0].sid})`);
  return `sid = ${KIMI_UUID.slice(0, 8)}… off the path (main AND subagent wire); non-matching path stays undefined`;
});

memA.close();
memB.close();

let red = 0;
console.log('\n  acceptance: event-session provenance (the other half of the signature)\n');
for (const r of results) {
  if (!r.ok) red++;
  console.log(`  ${r.ok ? 'green' : 'RED  '}  ${r.name}\n         ${r.msg}`);
}
console.log(`\n  attempted ${results.length}, ${results.length - red} green, ${red} red\n`);

rmSync(root, { recursive: true, force: true });
process.exit(red === 0 ? 0 : 1);
