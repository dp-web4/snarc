/**
 * SQLite storage layer — schema, queries, lifecycle.
 * Single database at ~/.snarc/snarc.db
 */

import Database from 'better-sqlite3';
import { createHash } from 'node:crypto';
import { mkdirSync, existsSync, writeFileSync, readdirSync } from 'node:fs';
import { join, dirname, basename } from 'node:path';
import { homedir } from 'node:os';

const SNARC_ROOT = join(homedir(), '.snarc');

/**
 * Resolve the workspace root that keys the DB. Precedence:
 *  1. SNARC_PROJECT_ROOT / SNARC_PROJECT_DIR env (explicit override).
 *  2. Nearest ancestor containing an `.snarc-root` marker — the EXPLICIT
 *     workspace root that consolidates multiple repos (each has its own
 *     CLAUDE.md/.git and would otherwise shard the store across repos).
 *  3. The directory itself — legacy per-directory behavior, unchanged when
 *     no marker/env is present (backward compatible: no marker = old behavior).
 * This is the single resolver shared by the hooks (writers) and the MCP
 * server (reader), so they can no longer disagree on which DB is "the project".
 */
function resolveWorkspaceDir(start: string): string {
  const envRoot = process.env.SNARC_PROJECT_ROOT || process.env.SNARC_PROJECT_DIR;
  if (envRoot) return envRoot;
  let dir = start;
  for (;;) {
    try { if (existsSync(join(dir, '.snarc-root'))) return dir; } catch { /* ignore */ }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return start;
}

/**
 * Derive a per-directory database path from the launch directory.
 * Same pattern as Claude Code's -c flag: each project directory gets
 * its own isolated context. Structure: ~/.snarc/projects/<hash>/snarc.db
 */
export function getDbPath(launchDir?: string): string {
  const dir = resolveWorkspaceDir(launchDir || process.cwd());
  const hash = createHash('sha256').update(dir).digest('hex').slice(0, 12);
  const projectDir = join(SNARC_ROOT, 'projects', hash);
  mkdirSync(projectDir, { recursive: true });
  // Write a metadata file so we can map hash → directory.
  // NOTE: this used a bare `require('node:fs')`, which is not defined in an ESM
  // build ("type": "module") — every call threw ReferenceError into the silent
  // catch below, so no shard has ever carried a meta.json and the hash → dir map
  // does not exist on disk. Use the top-level import instead.
  const metaPath = join(projectDir, 'meta.json');
  try {
    if (!existsSync(metaPath)) {
      writeFileSync(metaPath, JSON.stringify({ dir, hash, created: new Date().toISOString() }));
    }
  } catch { /* non-critical */ }
  return join(projectDir, 'snarc.db');
}

const SCHEMA = `
-- Tier 1: Salience-gated observations
CREATE TABLE IF NOT EXISTS observations (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id      TEXT NOT NULL,
  ts              TEXT NOT NULL DEFAULT (datetime('now')),
  tool_name       TEXT NOT NULL,
  input_summary   TEXT,
  output_summary  TEXT,
  surprise        REAL NOT NULL DEFAULT 0,
  novelty         REAL NOT NULL DEFAULT 0,
  arousal         REAL NOT NULL DEFAULT 0,
  reward          REAL NOT NULL DEFAULT 0,
  conflict        REAL NOT NULL DEFAULT 0,
  salience        REAL NOT NULL DEFAULT 0,
  base_salience   REAL NOT NULL DEFAULT 0,
  cwd             TEXT,
  tags            TEXT,
  content_hash    TEXT,
  scored_by       TEXT,
  event_session_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id);
CREATE INDEX IF NOT EXISTS idx_obs_salience ON observations(salience DESC);
CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts);
-- NOTE: the dedup guard's index (idx_obs_content_hash) is deliberately NOT here. This
-- block runs against pre-migration databases too, where content_hash does not exist
-- yet, and CREATE INDEX IF NOT EXISTS on a missing column throws "no such column",
-- aborting the rest of the exec and taking capture down on every existing store. It is
-- created in openDatabase() AFTER the ALTER instead. Caught by acceptance check 6 on
-- 2026-07-31 — same failure mode as the base_salience index note below and as d938e7e
-- (last_seen ALTER). Third time in this file: an index cannot precede its column.

-- FTS5 for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
  input_summary, output_summary, tags,
  content=observations,
  content_rowid=id
);

-- FTS sync triggers
CREATE TRIGGER IF NOT EXISTS obs_ai AFTER INSERT ON observations BEGIN
  INSERT INTO observations_fts(rowid, input_summary, output_summary, tags)
  VALUES (new.id, new.input_summary, new.output_summary, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS obs_ad AFTER DELETE ON observations BEGIN
  INSERT INTO observations_fts(observations_fts, rowid, input_summary, output_summary, tags)
  VALUES ('delete', old.id, old.input_summary, old.output_summary, old.tags);
END;

-- Tier 2: Consolidated patterns (INFERRED — not raw observations)
CREATE TABLE IF NOT EXISTS patterns (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
  kind        TEXT NOT NULL,
  summary     TEXT NOT NULL,
  detail      TEXT,
  frequency   INTEGER DEFAULT 1,
  source_ids  TEXT,
  confidence  REAL DEFAULT 0.5,
  last_seen   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(kind, summary)
);

-- Tier-2 evidence set. patterns.frequency used to be a running SUM with no record
-- of WHICH observations it counted, so re-consolidating the same rows re-added them.
-- This table is the tier-2 analogue of observations.content_hash: identity is the
-- evidence, not the arrival. Depends on no migrated column, so it belongs here.
CREATE TABLE IF NOT EXISTS pattern_sources (
  pattern_id  INTEGER NOT NULL,
  obs_id      INTEGER NOT NULL,
  PRIMARY KEY (pattern_id, obs_id)
) WITHOUT ROWID;

CREATE VIRTUAL TABLE IF NOT EXISTS patterns_fts USING fts5(
  summary, detail,
  content=patterns,
  content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS pat_ai AFTER INSERT ON patterns BEGIN
  INSERT INTO patterns_fts(rowid, summary, detail)
  VALUES (new.id, new.summary, new.detail);
END;

CREATE TRIGGER IF NOT EXISTS pat_au AFTER UPDATE ON patterns BEGIN
  INSERT INTO patterns_fts(patterns_fts, rowid, summary, detail)
  VALUES ('delete', old.id, old.summary, old.detail);
  INSERT INTO patterns_fts(rowid, summary, detail)
  VALUES (new.id, new.summary, new.detail);
END;

CREATE TRIGGER IF NOT EXISTS pat_ad AFTER DELETE ON patterns BEGIN
  INSERT INTO patterns_fts(patterns_fts, rowid, summary, detail)
  VALUES ('delete', old.id, old.summary, old.detail);
  -- Evidence follows its pattern. Covers all three delete paths (prunePatterns,
  -- deletePattern, and the (kind, summary) dedup in openDatabase) so pattern_sources
  -- cannot accumulate rows pointing at an id that AUTOINCREMENT may later reuse.
  DELETE FROM pattern_sources WHERE pattern_id = old.id;
END;

-- Tier 3: Identity (persistent project facts)
CREATE TABLE IF NOT EXISTS identity (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  key         TEXT NOT NULL UNIQUE,
  value       TEXT NOT NULL,
  source      TEXT DEFAULT 'auto',
  confidence  REAL DEFAULT 0.5
);

-- Settings (persisted per project directory)
CREATE TABLE IF NOT EXISTS settings (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL
);

-- Seen-set for novelty detection
CREATE TABLE IF NOT EXISTS seen_set (
  token       TEXT PRIMARY KEY,
  first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen   TEXT NOT NULL DEFAULT (datetime('now')),
  count       INTEGER DEFAULT 1
);

-- Session log
CREATE TABLE IF NOT EXISTS sessions (
  session_id  TEXT PRIMARY KEY,
  started_at  TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at    TEXT,
  cwd         TEXT,
  obs_count   INTEGER DEFAULT 0
);

-- Per-target last outcome (persisted for cross-PROCESS conflict scoring: success→fail regressions;
-- in-memory state resets each hook process, so this signal never fired before)
CREATE TABLE IF NOT EXISTS target_outcomes (
  key           TEXT PRIMARY KEY,
  last_success  INTEGER NOT NULL,
  last_seen     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Tool transition frequencies (persisted for cross-session surprise scoring)
CREATE TABLE IF NOT EXISTS tool_transitions (
  from_tool   TEXT NOT NULL,
  to_tool     TEXT NOT NULL,
  count       INTEGER DEFAULT 1,
  PRIMARY KEY (from_tool, to_tool)
);

-- Calibration retrieval log (fractal-leverage Sprint 0.2): close the loop snarc never had.
-- estimate = the salience/confidence with which a memory was SURFACED into a session briefing;
-- outcome (relevant) = whether the receiving session then did matching work (same cwd, token
-- overlap, within a window). Feeds the shared calibration harness (ECE): does our salience
-- actually predict usefulness? relevant: NULL = not yet scored, 1/0 = measured outcome.
CREATE TABLE IF NOT EXISTS retrieval_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  surfaced_ts  TEXT NOT NULL DEFAULT (datetime('now')),
  cwd          TEXT,
  source       TEXT NOT NULL,        -- briefing | related
  item_kind    TEXT NOT NULL,        -- observation | pattern | identity
  estimate     REAL NOT NULL,        -- salience or confidence, in [0,1]
  match_key    TEXT NOT NULL,        -- space-joined significant tokens of the surfaced content
  relevant     INTEGER               -- NULL unscored; 1/0 outcome (did the session act on it)
);
CREATE INDEX IF NOT EXISTS idx_retrieval_unscored ON retrieval_log(relevant, surfaced_ts);
`;

/**
 * Root claim store — `<root>/seen.db`, the CROSS-SHARD authority (kimi's design,
 * forum/kimi-the-leak-is-two-first-writes-…-2026-07-31.md §4; CBP's acceptance and
 * claim_conflict amendment, forum/cbp-the-question-your-design-rests-on-…-2026-07-31.md §4).
 *
 * The per-shard guard (existsContentHash) cannot see cross-shard duplication by
 * construction: this morning's measured leak was one transcript's 12,606 events
 * first-written into TWO shards eight minutes apart, each shard internally perfect.
 * `seen` claims each content hash globally exactly once (INSERT OR IGNORE is atomic —
 * the check and the insert are one statement, so there is no cross-writer race);
 * `claim_conflict` records every DENIED claim (CBP's amendment): the claim table
 * remembers hash → first_shard but not who was denied, and without this row the
 * pointer-row decision can never become a query and a later re-attribution pass has
 * nothing to join against.
 *
 * The amendment's own premise needed one repair before it could pay out (CBP, 2026-07-31,
 * second reading): it argued the denial record makes the question decidable "with no proxy",
 * and `session_id` was named as the key column. But `session_id` is the INGESTING session, and
 * the writer that produces essentially every cross-shard denial is the transcript replayer,
 * which stamps the constant host id 888f190a. Joining on it does not return NULL — it returns
 * "the owner holds that session" for every denial between any two bulk shards, because that one
 * id is in all of them (12,666/12,666, 12,672/12,672, 12,680/12,743). A confident constant is
 * worse than a blank. `event_session_id` is the axis that can actually answer it, and it is
 * NULLABLE on purpose: a NULL says "not knowable for this row" out loud, so the instrument's
 * blind fraction is a column rather than a caveat.
 */
const ROOT_CLAIMS_SCHEMA = `
CREATE TABLE IF NOT EXISTS seen (
  content_hash TEXT PRIMARY KEY,
  first_shard  TEXT NOT NULL,
  first_ts     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_conflict (
  content_hash TEXT NOT NULL,
  shard        TEXT NOT NULL,   -- the shard whose write was DENIED
  session_id   TEXT,            -- the INGESTING session of the denied write
  ts           TEXT NOT NULL DEFAULT (datetime('now')),  -- denied event's own ts when the caller has one
  event_session_id TEXT,        -- the denied EVENT's own conversation id; NULL = not knowable
  PRIMARY KEY (content_hash, shard)
) WITHOUT ROWID;
`;

export interface RootClaims {
  db: Database.Database;
  shard: string;  // this store's shard id (the projects/<hash> directory name)
}

/**
 * Open the root claim store for a shard database path, or return null when the path
 * is not a shard (`<root>/projects/<hash>/*.db`) — standalone stores (tests, explicit
 * paths) keep the per-shard guard as their only authority, exactly as before.
 * Callers MUST treat a throw as "no root" and fall back to per-shard behaviour:
 * the root is an authority, not a single point of failure (the 2026-06-27→07-01
 * fleet-wide capture death is the cited precedent for what a storage-layer throw
 * does to every hook).
 */
export function openRootClaims(shardDbPath: string): RootClaims | null {
  const shardDir = dirname(shardDbPath);
  if (basename(dirname(shardDir)) !== 'projects') return null;
  const rootDir = dirname(dirname(shardDir));
  const db = new Database(join(rootDir, 'seen.db'));
  db.pragma('journal_mode = WAL');
  db.pragma('busy_timeout = 2000'); // hooks on two shards can claim in the same second
  db.exec(ROOT_CLAIMS_SCHEMA);
  // Migration: claim_conflict.event_session_id. seen.db went live at c48af34 — go-live measured
  // by statx btime as 2026-07-31 08:44:41Z (audit_arrival_anchor.py §3); the 08:30Z first quoted
  // here was the first claim's first_ts, which is the EVENT's clock (COALESCE(event ts, now)),
  // not the guard's. CREATE TABLE IF NOT EXISTS will not widen the table it already made. Nullable ADD COLUMN
  // — a NOT NULL ALTER is rejected outright, and NULL is the value this column WANTS for rows
  // whose event session was never knowable. (ADD COLUMN is supported on WITHOUT ROWID tables.)
  try {
    db.exec(`ALTER TABLE claim_conflict ADD COLUMN event_session_id TEXT`);
  } catch { /* already migrated */ }
  return { db, shard: basename(shardDir) };
}

export interface BackfillPlan {
  shards: { shard: string; hashed: number; claimed: number; conflicted: number }[];
  totalHashed: number;
  totalClaimed: number;
  totalConflicted: number;
  dryRun: boolean;
}

/**
 * Seed the root claim store from the shards that already exist — the migration
 * `openRootClaims` deliberately does NOT perform on its own.
 *
 * WHY THIS IS NEEDED, measured 2026-07-31 rather than argued. The authority guards
 * *new* content: `captureContext`'s per-shard `existsContentHash` returns early on
 * anything the shard already holds, so only a first-sighting ever reaches `claimSeen`.
 * A fresh `seen.db` therefore knows nothing about the corpus already on disk, and the
 * corpus already on disk is where the entire leak lives:
 *
 *   12,659 content hashes present in EXACTLY the same four shards
 *   (23094633bebc, 777c4901744b, 791cace57ce9, 7d210ad7238a) — one transcript corpus written
 *   four times over. In the one shard holding both populations, every duplicated row has a
 *   lower AUTOINCREMENT id than every hash claimed since go-live (12,726 < 12,729): the whole
 *   duplicated corpus was written before the authority made its first claim.
 *
 *   Ordering, not timestamps, on purpose. `observations.ts` is write time before c48af34 and
 *   the event's own transcript time after, mixed in one column, and seen.first_ts is
 *   COALESCE(event ts, now) — so a ts comparison between them dates nothing. A first draft of
 *   this note quoted a "2m59s" gap off exactly that comparison and was wrong.
 *
 * The consequence is behavioural, and acceptance_claim_recurrence.mjs check 1 asserts it:
 * a replay of that same corpus into a FIFTH shard is not denied, because `seen` holds only
 * the hashes claimed since go-live. Without this backfill the authority is inert against
 * its own motivating incident — not because the design is wrong, but because it starts empty.
 *
 * WHAT THIS DOES NOT DECIDE. Ownership is assigned by iteration order over the shards, and for
 * the 12,659 that is arrival order. That is why `dryRun` is the default at every caller: the
 * plan is a measurement, the write is an operator's act. Every hash a second shard also holds
 * is written as a `claim_conflict` row, so the losing side stays a queryable fact rather than a
 * silent overwrite.
 *
 * This doc used to add "recover `event_session_id` from the transcripts FIRST (99.0% unique),
 * backfill after". That prerequisite is withdrawn (CBP 2026-07-31). Note what this function
 * does with the column below: it never reads it to decide an owner — it copies it into the
 * conflict row as payload. The recovery is keyed on the row's CONTENT, and the copies of a
 * duplicated hash are byte-identical content (12,668/12,668 on the live store,
 * audit_recovery_payout.py §2), so it hands the SAME value to the winner and the loser of every
 * denial and can classify neither. The 99.0% measured whether the content is attributable; the
 * assignment turns on whether a COPY is, and no historical column carries that. Order-independence
 * is a test, not an argument: acceptance_recovery_ordering.mjs runs recover-then-backfill against
 * backfill-then-recover-then-propagate to an identical (seen, claim_conflict) state, with a
 * sensitivity control that asserts a DIFFERENT shard order does move the tables.
 */
export function backfillRootClaims(
  rootDir: string,
  opts: { dryRun?: boolean; shards?: string[] } = {},
): BackfillPlan {
  const dryRun = opts.dryRun !== false;   // default DRY — the write is an explicit act
  const projectsDir = join(rootDir, 'projects');
  const db = new Database(join(rootDir, 'seen.db'));
  db.pragma('journal_mode = WAL');
  db.pragma('busy_timeout = 2000');
  db.exec(ROOT_CLAIMS_SCHEMA);
  try { db.exec(`ALTER TABLE claim_conflict ADD COLUMN event_session_id TEXT`); } catch { /* migrated */ }

  const shards = opts.shards ?? (existsSync(projectsDir) ? readdirSync(projectsDir).sort() : []);
  const stmts = prepareClaimStatements(db);
  const plan: BackfillPlan = {
    shards: [], totalHashed: 0, totalClaimed: 0, totalConflicted: 0, dryRun,
  };

  // One transaction for the whole run: a half-seeded authority names owners for part of the
  // corpus and not the rest, which is a worse state than either endpoint.
  const run = db.transaction(() => {
    for (const shard of shards) {
      const shardDb = join(projectsDir, shard, 'snarc.db');
      if (!existsSync(shardDb)) continue;
      const src = new Database(shardDb, { readonly: true, fileMustExist: true });
      let hashed = 0, claimed = 0, conflicted = 0;
      try {
        // The live store holds TWO schema generations at once: `event_session_id` landed at
        // 62009ae and reaches a shard only on its next write, so shards idle since then still
        // lack the column. Reading it unconditionally throws SQLITE_ERROR and takes the whole
        // backfill down — on a READ, over shards whose rows are precisely the ones that most
        // need seeding. Absent column and NULL value mean the same thing here (not knowable),
        // so detect and substitute rather than requiring a migration pass first.
        const hasEventSid = (src.prepare(`PRAGMA table_info(observations)`).all() as { name: string }[])
          .some((c) => c.name === 'event_session_id');
        const rows = src.prepare(
          `SELECT content_hash, ts, session_id, ${hasEventSid ? 'event_session_id' : 'NULL AS event_session_id'}
             FROM observations WHERE content_hash IS NOT NULL ORDER BY id`,
        ).all() as { content_hash: string; ts: string; session_id: string; event_session_id: string | null }[];
        for (const r of rows) {
          hashed++;
          const res = stmts.claimSeen.run(r.content_hash, shard, r.ts ?? null);
          if (res.changes === 0) {
            const owner = stmts.getSeenOwner.get(r.content_hash) as { first_shard: string } | undefined;
            // Same-shard repeats are not cross-shard denials — a35e3a8's distinction, kept here.
            if (owner && owner.first_shard !== shard) {
              stmts.recordConflict.run(
                r.content_hash, shard, r.session_id ?? null, r.ts ?? null, r.event_session_id ?? null,
              );
              conflicted++;
            }
          } else {
            claimed++;
          }
        }
      } finally {
        src.close();
      }
      plan.shards.push({ shard, hashed, claimed, conflicted });
      plan.totalHashed += hashed;
      plan.totalClaimed += claimed;
      plan.totalConflicted += conflicted;
    }
    // A dry run does the identical work and then discards it, so the reported plan is the
    // plan that would execute — not a separate estimator that can drift from the writer.
    if (dryRun) throw new DryRunRollback();
  });

  try {
    run();
  } catch (e) {
    if (!(e instanceof DryRunRollback)) { db.close(); throw e; }
  }
  db.close();
  return plan;
}

class DryRunRollback extends Error {}

// eslint-disable-next-line @typescript-eslint/explicit-function-return-type
export function prepareClaimStatements(db: Database.Database) {
  return {
    // Atomic claim-first: inserted -> the event is ours to store; 0 changes -> denied.
    claimSeen: db.prepare(`
      INSERT OR IGNORE INTO seen (content_hash, first_shard, first_ts)
      VALUES (?, ?, COALESCE(datetime(?), datetime('now')))
    `),
    getSeenOwner: db.prepare(`SELECT first_shard FROM seen WHERE content_hash = ?`),
    // Record the denial (CBP's amendment) — never silently. INSERT OR IGNORE: a repeated
    // denial from the same shard is the same fact, not a new one.
    recordConflict: db.prepare(`
      INSERT OR IGNORE INTO claim_conflict (content_hash, shard, session_id, ts, event_session_id)
      VALUES (?, ?, ?, COALESCE(datetime(?), datetime('now')), ?)
    `),
  };
}

export type ClaimStatements = ReturnType<typeof prepareClaimStatements>;

export function openDatabase(path?: string): Database.Database {
  const dbPath = path || getDbPath();

  const db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  db.exec(SCHEMA);

  // Migrations for databases created before schema updates.
  // NOTE: SQLite REJECTS `ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT (datetime('now'))` — a NOT NULL
  // column added by ALTER must have a CONSTANT default. The old form threw, the catch mis-read it as
  // "already migrated", the column never landed, and prepareStatements then threw `no such column:
  // last_seen` on EVERY existing db → capture silently died fleet-wide (2026-06-27→07-01). Add nullable,
  // then backfill. (Fresh dbs still get NOT NULL via the CREATE TABLE in SCHEMA.)
  try {
    db.exec(`ALTER TABLE patterns ADD COLUMN last_seen TEXT`);
    db.exec(`UPDATE patterns SET last_seen = COALESCE(updated_at, created_at, datetime('now')) WHERE last_seen IS NULL`);
  } catch { /* column already exists */ }

  // Migration: base_salience — immutable IMPORTANCE, decoupled from the decaying `salience`.
  // decayObservations drives `salience` toward 0 for old rows, but search ranks by salience, so
  // important old memories became unfindable. Search now ranks by base_salience (importance);
  // `salience` stays the recency/activation signal for proactive injection. Backfill once.
  try {
    db.exec(`ALTER TABLE observations ADD COLUMN base_salience REAL NOT NULL DEFAULT 0`);
    db.exec(`UPDATE observations SET base_salience = salience`);
  } catch { /* already migrated */ }
  // Index AFTER the column is guaranteed to exist (the SCHEMA block above runs before this ALTER on
  // a pre-migration DB, so the index can't live in SCHEMA — it would reference a missing column).
  db.exec(`CREATE INDEX IF NOT EXISTS idx_obs_base_salience ON observations(base_salience DESC)`);

  // Migration: content_hash — enables true no-op re-capture of identical context (Kimi #4,
  // 2026-07-21). Nullable ADD COLUMN (NOT NULL ALTER is rejected — see the note above). Dedup is
  // enforced in captureContext, scoped to session, NOT via a global UNIQUE index (that would drop
  // legitimately-repeated observations on the tool path). Existing rows keep NULL — the guard only
  // applies to new inserts, so no backfill is needed.
  try {
    db.exec(`ALTER TABLE observations ADD COLUMN content_hash TEXT`);
  } catch { /* already migrated */ }

  // Migration: scored_by — which writer generation scored this row. The guard is keep-first
  // (the existing row survives, the re-capture no-ops), so a row's SNARC vector is frozen at
  // whichever generation happened to see the content first. Measured 2026-07-31: 3,050 of
  // 17,808 distinct Conversation turns (17.1%) carry DIFFERENT (surprise, novelty) across
  // their copies — the corpus was written by several scorer generations over 4.5 months and
  // nothing records which. Without this column "keep-first" silently freezes the oldest,
  // least-instrumented scoring and no re-score can find the rows that need it. Existing rows
  // keep NULL, which is the honest value: their generation is genuinely unknown.
  try {
    db.exec(`ALTER TABLE observations ADD COLUMN scored_by TEXT`);
  } catch { /* already migrated */ }

  // Migration: event_session_id — the EVENT's own conversation id, the twin of the `ts` repair
  // that landed at c48af34. Claude Code transcripts carry `sessionId` on every line (measured
  // 2026-07-31: 400 sampled files, 400 distinct ids, exactly one per file, zero files carrying
  // two), and claudeRecognizer read `entry.timestamp` off that same entry while stepping over
  // `entry.sessionId`. Both provenance fields died at captureContext's signature; only ts was
  // revived. `session_id` records the INGESTING session — for the replayer that is the constant
  // host id 888f190a (kimi, 2026-07-31: a host_session_id, not a CLI session), so it is one value
  // across 12,666/12,666, 12,672/12,672 and 12,680/12,743 rows of the bulk shards.
  //
  // This is a SEPARATE column, deliberately, and not a repair of session_id in place: consolidate()
  // and rehydrateBuffer() read getSessionObservations(this.sessionId), i.e. INGEST scope, and
  // overwriting session_id with the transcript's id would silently empty both. Ingest scope and
  // event provenance are two different axes; the corpus had one column for them and the ingest
  // axis won. Existing rows keep NULL — the honest value, since their event session was never
  // written anywhere.
  try {
    db.exec(`ALTER TABLE observations ADD COLUMN event_session_id TEXT`);
  } catch { /* already migrated */ }
  db.exec(`CREATE INDEX IF NOT EXISTS idx_obs_event_session ON observations(event_session_id)`);

  // Index AFTER the ALTER, for the same reason base_salience's index is here: on a
  // pre-migration db the SCHEMA block runs before the column exists.
  db.exec(`CREATE INDEX IF NOT EXISTS idx_obs_content_hash ON observations(content_hash)`);

  // Migration: seen_set.last_seen — enables recency-windowed novelty (prune stale tokens so novelty
  // doesn't saturate to 0 as the set grows). Backfill from first_seen.
  try {
    db.exec(`ALTER TABLE seen_set ADD COLUMN last_seen TEXT`);   // nullable — see note above (NOT NULL ALTER is rejected)
    db.exec(`UPDATE seen_set SET last_seen = first_seen WHERE last_seen IS NULL`);
  } catch { /* already migrated */ }

  // Migration: deduplicate existing patterns and add UNIQUE constraint
  try {
    db.exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_patterns_dedup ON patterns(kind, summary)`);
  } catch {
    // Index creation fails if duplicates exist — deduplicate first
    db.exec(`
      DELETE FROM patterns WHERE id NOT IN (
        SELECT MIN(id) FROM patterns GROUP BY kind, summary
      )
    `);
    // Rebuild FTS to match
    db.exec(`INSERT INTO patterns_fts(patterns_fts) VALUES('rebuild')`);
    db.exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_patterns_dedup ON patterns(kind, summary)`);
  }

  return db;
}

// Prepared statement factories
// eslint-disable-next-line @typescript-eslint/explicit-function-return-type
export function prepareStatements(db: Database.Database) {
  return {
    insertObservation: db.prepare(`
      INSERT INTO observations (session_id, tool_name, input_summary, output_summary,
        surprise, novelty, arousal, reward, conflict, salience, base_salience, cwd, tags,
        content_hash, scored_by)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `),

    // Same insert WITH the event's own timestamp. Kept as a second statement rather
    // than widening insertObservation's arity: a published gauge
    // (scripts/acceptance_pattern_accumulator.mjs seed()) binds the 15-parameter form,
    // and re-running it against a pre-migration tree must still fail on behaviour,
    // not on arity. ts is the event time the CALLER parsed (transcript timestamp);
    // COALESCE keeps garbage/absent values at write time, and datetime() normalizes
    // ISO 'T…Z' to the column's existing 'YYYY-MM-DD HH:MM:SS' so ORDER BY ts stays
    // lexicographic across both writers.
    // `ts` = the event's own timestamp (c48af34), `event_session_id` = the event's own
    // conversation id — the two halves of the same provenance, both parsed by the recognizers
    // off the same transcript entry. `session_id` remains the INGESTING session: it is what
    // consolidation and buffer rehydration scope on, and conflating the two axes into one
    // column is how the corpus ended up with 96.6% of every row under one host id.
    insertObservationTs: db.prepare(`
      INSERT INTO observations (session_id, tool_name, input_summary, output_summary,
        surprise, novelty, arousal, reward, conflict, salience, base_salience, cwd, tags,
        content_hash, scored_by, ts, event_session_id)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(datetime(?), datetime('now')), ?)
    `),

    // STORE-scoped, not session-scoped. The session predicate was dropped 2026-07-31.
    //
    // 9a9fb50 scoped this to session with the reason "a global constraint would drop
    // legitimately-repeated observations on the tool path". That reason does not describe
    // any code path: capture() (the tool path) never calls this guard — it stores
    // content_hash as metadata only, as that same commit message says one line down. The
    // guard's ONLY caller is captureContext, where the content is a human turn or a
    // decision and identical text is the same event by construction, not a legitimate
    // repeat. So the scope protected a caller that does not exist, and cost real rows:
    //
    //   ~/.engram/projects/791cace57ce9, rows written since 2026-07-23 under real
    //   (non-constant) session ids:  1,593 rows -> 1,135 distinct  = 28.8% re-stored
    //   across sessions, with ZERO same-session guard failures. The leak is entirely
    //   the session predicate.
    //
    // Counter-intuitively this makes the constant-session-id bug LOOK protective: rows
    // written under the host id 888f190a since 07-23 are 288/288 distinct (1.00 copies)
    // precisely because a constant session degenerates this guard to store-global. Fixing
    // the id source WITHOUT this change would have raised duplication, not lowered it.
    // That is why this lands first. See forum/cbp-the-duplication-was-fixed-nine-days-ago-
    // and-the-remedy-is-inverted-2026-07-31.md.
    existsContentHash: db.prepare(`
      SELECT 1 FROM observations WHERE content_hash = ? LIMIT 1
    `),

    upsertPattern: db.prepare(`
      INSERT INTO patterns (kind, summary, detail, frequency, source_ids, confidence)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(kind, summary) DO UPDATE SET
        frequency   = patterns.frequency + excluded.frequency,
        source_ids  = excluded.source_ids,
        confidence  = MAX(patterns.confidence, excluded.confidence),
        detail      = CASE WHEN length(excluded.detail) > length(COALESCE(patterns.detail, ''))
                        THEN excluded.detail ELSE patterns.detail END,
        last_seen   = datetime('now'),
        updated_at  = datetime('now')
    `),

    // --- Tier-2 evidence guard (2026-07-31) ---
    //
    // `frequency = patterns.frequency + excluded.frequency` is an unbounded SUM that
    // records no notion of WHICH observations were counted, so consolidating the same
    // rows again adds them again. Measured on the live store the same day it was built:
    // ~/.snarc/projects/791cace57ce9, created 04:22, TWO consolidation runs over 12,606
    // Conversation rows -> pattern id=1 frequency 25,188 = 2 x 12,594. The number is the
    // store counted twice, not a recurrence observed 25,188 times. On the archive shard
    // the same mechanism over 2,413 sessions reached 43,581,138.
    //
    // frequency is not cosmetic: decayPatterns damps by `0.05 / (1 + log2(frequency+1))`,
    // so a re-counted row is 26x stickier than a freq-1 pattern and is exempt in practice
    // from the forgetting mechanism, while `ORDER BY frequency DESC` pins it at the top of
    // every briefing. A re-count buys permanence.
    //
    // The repair is a35e3a8's, one tier up: identity is the EVIDENCE, not the arrival.
    // Claim each source observation once; frequency is the size of the claimed set. Two
    // consolidations over the same rows now converge instead of accumulating, and the
    // 43.6M row self-corrects to its true distinct count the next time it is touched —
    // no manual UPDATE, no decision needed about the monument.
    claimPatternSource: db.prepare(`
      INSERT OR IGNORE INTO pattern_sources (pattern_id, obs_id) VALUES (?, ?)
    `),
    getPatternId: db.prepare(`
      SELECT id FROM patterns WHERE kind = ? AND summary = ?
    `),
    syncPatternFrequency: db.prepare(`
      UPDATE patterns
         SET frequency = (SELECT COUNT(*) FROM pattern_sources WHERE pattern_id = patterns.id)
       WHERE id = ?
    `),

    upsertIdentity: db.prepare(`
      INSERT INTO identity (key, value, source, confidence)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET
        value = CASE WHEN excluded.confidence > identity.confidence THEN excluded.value ELSE identity.value END,
        confidence = MAX(excluded.confidence, identity.confidence)
    `),

    upsertSeen: db.prepare(`
      INSERT INTO seen_set (token) VALUES (?)
      ON CONFLICT(token) DO UPDATE SET count = count + 1, last_seen = datetime('now')
    `),

    // Prune tokens unseen for 30+ days so novelty stays live (re-emergence reads as novel).
    pruneSeen: db.prepare(`
      DELETE FROM seen_set WHERE last_seen < datetime('now', '-30 days')
    `),

    getTargetOutcome: db.prepare(`
      SELECT last_success FROM target_outcomes WHERE key = ?
    `),

    upsertTargetOutcome: db.prepare(`
      INSERT INTO target_outcomes (key, last_success) VALUES (?, ?)
      ON CONFLICT(key) DO UPDATE SET last_success = excluded.last_success, last_seen = datetime('now')
    `),

    getPatternByKindSummary: db.prepare(`
      SELECT frequency, confidence, detail FROM patterns WHERE kind = ? AND summary = ?
    `),

    checkSeen: db.prepare(`
      SELECT token FROM seen_set WHERE token IN (${Array(20).fill('?').join(',')})
    `),

    upsertTransition: db.prepare(`
      INSERT INTO tool_transitions (from_tool, to_tool, count) VALUES (?, ?, 1)
      ON CONFLICT(from_tool, to_tool) DO UPDATE SET count = count + 1
    `),

    getTransitionCount: db.prepare(`
      SELECT count FROM tool_transitions WHERE from_tool = ? AND to_tool = ?
    `),

    getMaxTransition: db.prepare(`
      SELECT MAX(count) as max_count FROM tool_transitions WHERE from_tool = ?
    `),

    searchObservations: db.prepare(`
      SELECT o.* FROM observations_fts f
      JOIN observations o ON o.id = f.rowid
      WHERE observations_fts MATCH ?
      ORDER BY o.base_salience DESC, o.ts DESC
      LIMIT ?
    `),

    // Structural facet (gitnexus-distilled symbols): its own retrieval lane,
    // ranked by FTS RELEVANCE (bm25), not salience — structural facts carry
    // modest, uniform salience, so a salience sort would silence them entirely
    // in the pooled query (finding 2026-07-18: recall must be facet-aware).
    searchStructural: db.prepare(`
      SELECT o.* FROM observations_fts f
      JOIN observations o ON o.id = f.rowid
      WHERE observations_fts MATCH ? AND o.tool_name = 'structural'
      ORDER BY bm25(observations_fts) ASC
      LIMIT ?
    `),

    searchPatterns: db.prepare(`
      SELECT p.* FROM patterns_fts f
      JOIN patterns p ON p.id = f.rowid
      WHERE patterns_fts MATCH ?
      LIMIT ?
    `),

    getSessionObservations: db.prepare(`
      SELECT * FROM observations WHERE session_id = ? ORDER BY ts
    `),

    getRecentObservations: db.prepare(`
      SELECT * FROM observations ORDER BY ts DESC LIMIT ?
    `),

    // --- Calibration retrieval log (Sprint 0.2) ---
    insertRetrieval: db.prepare(`
      INSERT INTO retrieval_log (cwd, source, item_kind, estimate, match_key)
      VALUES (?, ?, ?, ?, ?)
    `),
    // Score only rows old enough that the session had time to act (>=60s settle).
    getUnscoredRetrievals: db.prepare(`
      SELECT id, surfaced_ts, cwd, match_key FROM retrieval_log
      WHERE relevant IS NULL AND surfaced_ts <= datetime('now', '-60 seconds')
    `),
    // Outcome evidence: work done in the same cwd AFTER the memory was surfaced (6h window).
    getObsAfter: db.prepare(`
      SELECT input_summary, output_summary FROM observations
      WHERE cwd = ? AND ts > ? AND ts <= datetime(?, '+6 hours')
    `),
    setRetrievalRelevant: db.prepare(`UPDATE retrieval_log SET relevant = ? WHERE id = ?`),
    getCalibrationPairs: db.prepare(`
      SELECT estimate, relevant, source, item_kind, surfaced_ts FROM retrieval_log
      WHERE relevant IS NOT NULL ORDER BY surfaced_ts
    `),

    getObservationContext: db.prepare(`
      SELECT * FROM observations WHERE ts BETWEEN datetime(?, '-5 minutes') AND datetime(?, '+5 minutes')
      ORDER BY ts
    `),

    getAllPatterns: db.prepare(`
      SELECT * FROM patterns ORDER BY frequency DESC, confidence DESC
    `),

    getPatternsByKind: db.prepare(`
      SELECT * FROM patterns WHERE kind = ? ORDER BY frequency DESC
    `),

    getAllIdentity: db.prepare(`
      SELECT * FROM identity ORDER BY confidence DESC
    `),

    initSession: db.prepare(`
      INSERT OR REPLACE INTO sessions (session_id, cwd) VALUES (?, ?)
    `),

    endSession: db.prepare(`
      UPDATE sessions SET ended_at = datetime('now'),
        obs_count = (SELECT COUNT(*) FROM observations WHERE session_id = ?)
      WHERE session_id = ?
    `),

    // Confidence decay: reduce confidence of patterns not seen recently.
    // Decay rate is dampened by frequency — high-frequency patterns are stickier.
    // Base rate 0.05/day, divided by log2(frequency+1) so:
    //   freq 1 → 0.05/day, freq 3 → 0.025/day, freq 15 → 0.0125/day
    decayPatterns: db.prepare(`
      UPDATE patterns SET
        confidence = MAX(0.0, confidence - (0.05 / (1.0 + ln(frequency + 1) / ln(2))) * (julianday('now') - julianday(last_seen)))
      WHERE julianday('now') - julianday(last_seen) > 1.0
    `),

    // Prune patterns that have decayed below usefulness
    prunePatterns: db.prepare(`
      DELETE FROM patterns WHERE confidence < 0.1
    `),

    // Decay old observations — reduce salience of observations older than 7 days
    decayObservations: db.prepare(`
      UPDATE observations SET
        salience = MAX(0.0, salience - 0.02 * (julianday('now') - julianday(ts) - 7))
      WHERE julianday('now') - julianday(ts) > 7
    `),

    // Proposed identity management
    getProposedIdentity: db.prepare(`
      SELECT id, summary, detail, confidence FROM patterns
      WHERE kind = 'proposed_identity' ORDER BY confidence DESC
    `),

    deletePattern: db.prepare(`DELETE FROM patterns WHERE id = ?`),

    getSetting: db.prepare(`SELECT value FROM settings WHERE key = ?`),
    setSetting: db.prepare(`INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)`),

    getStats: db.prepare(`
      SELECT
        (SELECT COUNT(*) FROM observations) as obs_count,
        (SELECT COUNT(*) FROM patterns) as pattern_count,
        (SELECT COUNT(*) FROM identity) as identity_count,
        (SELECT COUNT(*) FROM seen_set) as seen_count,
        (SELECT COUNT(*) FROM sessions) as session_count,
        (SELECT AVG(salience) FROM observations) as avg_salience,
        (SELECT MAX(ts) FROM observations) as last_obs
    `),
  };
}

export type Statements = ReturnType<typeof prepareStatements>;
