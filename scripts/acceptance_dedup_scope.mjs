#!/usr/bin/env node
/**
 * Acceptance test for the defect-#11 writer diff (cross-session dedup leak).
 *
 * WRITTEN RED, ON PURPOSE. Run it against the pre-fix tree first: checks 1, 2 and 5 MUST
 * fail. A criterion that already passes cannot tell a repair from a dead gauge
 * (feedback: acceptance-test-must-currently-fail). The pre-fix reds are recorded in
 * forum/cbp-the-duplication-was-fixed-nine-days-ago-and-the-remedy-is-inverted-2026-07-31.md §5.
 *
 * Every check runs inside its own try/catch and a thrown error is a RED, not a crash.
 * On the pre-fix tree the missing `scored_by` column throws; an uncaught throw would end
 * the run early and report fewer reds than exist — a harness crash that reads like a
 * pass (feedback: assert-the-row-not-the-report).
 *
 * What it pins, and why each check exists:
 *
 *   1. CROSS-SESSION LEAK (the defect). Identical context captured under two different
 *      session ids must produce ONE row. Pre-fix the guard is
 *      `WHERE session_id = ? AND content_hash = ?`, so it produces two.
 *      Measured cost of the leak on real data: 28.8% of all rows written under real
 *      (non-constant) session ids in ~/.engram/projects/791cace57ce9 since 2026-07-23
 *      (1,593 rows -> 1,135 distinct).
 *
 *   2. THE INDEX. Going store-global turns the guard into a full table scan without an
 *      index on content_hash (704k rows on the big shard). Asserts the index exists.
 *
 *   3. THE TOOL PATH IS UNTOUCHED (the regression guard). The stated reason for scoping
 *      the guard to session was "a global constraint would drop legitimately-repeated
 *      observations on the tool path" (9a9fb50). capture() never consults the guard —
 *      it stores the hash as metadata only — so widening the scope cannot affect it.
 *      This check is what makes that claim falsifiable rather than asserted: two
 *      identical tool captures must still produce two rows.
 *
 *   4. SAME-SESSION DEDUP still holds (don't regress the 07-22 fix).
 *
 *   5. SCORER PROVENANCE. Keep-first freezes the first writer generation's scoring
 *      (kimi's question, 2026-07-31: 3,050 distinct turns carry mixed (surprise,
 *      novelty) across their copies). The surviving row must say which scorer wrote it.
 *
 *   6. OLD-DB MIGRATION. A db created before the column/index exists must migrate
 *      without error and keep its rows. This is the load-bearing check for a live store.
 *
 * Usage: node scripts/acceptance_dedup_scope.mjs      (after a build)
 * Exit 0 = all green. Exit 1 = at least one red, with the reds listed.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import * as mem from '../dist/src/memory.js';

const { SNARCMemory } = mem;
// Imported defensively: on the PRE-FIX tree this export does not exist. A bare named
// import would throw at module load and the run would report "0 red" — see the header.
const SCORER_VERSION = mem.SCORER_VERSION;

const reds = [];
const greens = [];

/** Run a check body; a throw is a RED with the message, never an early exit. */
function check(name, body) {
  let ok = false, detail = '';
  try {
    const r = body();
    ok = r.ok;
    detail = r.detail;
  } catch (e) {
    ok = false;
    detail = `threw: ${e.message}`;
  }
  (ok ? greens : reds).push(`${name} — ${detail}`);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  [${detail}]`);
}

const dir = mkdtempSync(join(tmpdir(), 'snarc-acceptance-'));
const TEXT = 'The dedup guard was scoped to session to protect a path that never calls it.';

try {
  // ---- checks 1, 4, 5 share one store ----
  const dbPath = join(dir, 'snarc.db');
  let first, sameSession, otherSession;
  {
    const m = new SNARCMemory(dbPath);
    m.initSession('session-A', '/tmp/proj');
    first = m.captureContext('user_prompt', TEXT, '/tmp/proj');
    sameSession = m.captureContext('user_prompt', TEXT, '/tmp/proj');
    m.initSession('session-B', '/tmp/proj');
    otherSession = m.captureContext('user_prompt', TEXT, '/tmp/proj');
  }

  check('1. cross-session: identical context under 2 session ids stores ONCE', () => {
    const db = new Database(dbPath, { readonly: true });
    const rows = db.prepare(
      `SELECT count(*) n FROM observations WHERE tool_name = 'user_prompt'`).get().n;
    db.close();
    return {
      ok: rows === 1 && otherSession === false,
      detail: `rows=${rows} (want 1), 2nd-session captureContext=${otherSession} (want false)`,
    };
  });

  check('4. same-session dedup still holds (07-22 fix not regressed)', () => ({
    ok: first === true && sameSession === false,
    detail: `first=${first} second=${sameSession}`,
  }));

  check('5. surviving row carries scorer provenance (keep-first is identifiable)', () => {
    const db = new Database(dbPath, { readonly: true });
    try {
      const row = db.prepare(
        `SELECT scored_by FROM observations WHERE tool_name = 'user_prompt' LIMIT 1`).get();
      return {
        ok: !!row?.scored_by && row.scored_by === SCORER_VERSION,
        detail: `scored_by=${JSON.stringify(row?.scored_by)} SCORER_VERSION=${JSON.stringify(SCORER_VERSION)}`,
      };
    } finally { db.close(); }
  });

  check('2. index on observations(content_hash) exists (global guard is not a table scan)', () => {
    const db = new Database(dbPath, { readonly: true });
    const idx = db.prepare(
      `SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='observations'
         AND sql LIKE '%content_hash%'`).all();
    db.close();
    return { ok: idx.length > 0, detail: `matching indexes=${JSON.stringify(idx.map(i => i.name))}` };
  });

  check('3. tool path unaffected by the guard (identical repeats still stored)', () => {
    const toolDb = join(dir, 'tool.db');
    const m = new SNARCMemory(toolDb);
    m.initSession('session-A', '/tmp/proj');
    // capture() applies its own salience gate, so assert on what it DECIDED: whatever the
    // scorer returns, every call it marks `stored` must land as a row. A guard leaking
    // onto this path would mark the second stored and then silently drop it.
    const a = m.capture('Bash', 'echo identical', 'identical', '/tmp/proj', 0);
    const b = m.capture('Bash', 'echo identical', 'identical', '/tmp/proj', 0);
    const db = new Database(toolDb, { readonly: true });
    const n = db.prepare(`SELECT count(*) n FROM observations WHERE tool_name = 'Bash'`).get().n;
    db.close();
    const expected = (a.stored ? 1 : 0) + (b.stored ? 1 : 0);
    return {
      ok: n === expected && expected === 2,
      detail: `stored flags=[${a.stored},${b.stored}] rows=${n} expected=${expected}`,
    };
  });

  check('6. pre-migration db migrates, keeps its rows, and accepts the next write', () => {
    const oldPath = join(dir, 'old.db');
    const old = new Database(oldPath);
    // A pre-9a9fb50 shape: no content_hash, no scored_by, no index.
    old.exec(`CREATE TABLE observations (
      id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
      ts TEXT NOT NULL DEFAULT (datetime('now')), tool_name TEXT NOT NULL,
      input_summary TEXT, output_summary TEXT,
      surprise REAL NOT NULL DEFAULT 0, novelty REAL NOT NULL DEFAULT 0,
      arousal REAL NOT NULL DEFAULT 0, reward REAL NOT NULL DEFAULT 0,
      conflict REAL NOT NULL DEFAULT 0, salience REAL NOT NULL DEFAULT 0,
      cwd TEXT, tags TEXT)`);
    old.exec(`INSERT INTO observations (session_id, tool_name, input_summary)
              VALUES ('legacy', 'Conversation', 'a row from before the migration')`);
    old.close();

    const m = new SNARCMemory(oldPath);
    m.initSession('session-C', '/tmp/proj');
    const captured = m.captureContext('user_prompt', 'post-migration write', '/tmp/proj');
    const db = new Database(oldPath, { readonly: true });
    const kept = db.prepare(
      `SELECT count(*) n FROM observations WHERE input_summary = 'a row from before the migration'`,
    ).get().n;
    db.close();
    return {
      ok: kept === 1 && captured === true,
      detail: `legacy_rows_kept=${kept} (want 1) captured=${captured} (want true)`,
    };
  });
} finally {
  rmSync(dir, { recursive: true, force: true });
}

console.log(`\n${greens.length} green, ${reds.length} red   (6 checks attempted)`);
if (greens.length + reds.length !== 6) {
  console.log('WARNING: not all 6 checks ran — the run did not reach its own end.');
  process.exit(1);
}
if (reds.length) {
  console.log('\nRED:');
  for (const r of reds) console.log(`  - ${r}`);
  process.exit(1);
}
process.exit(0);
