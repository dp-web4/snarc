#!/usr/bin/env node
/**
 * backfill_seen.mjs — seed the root claim authority from the shards already on disk.
 *
 * WHY. `seen.db` was created empty on 2026-07-31 and guards only first-sightings, because
 * captureContext's per-shard guard returns early on anything the shard already holds. The
 * corpus it was built to protect predates it: 12,668 duplicated content hashes, 12,659 of them
 * in exactly the same four shards, and the authority holds 0.0% of them
 * (scripts/audit_claim_exposure.py §5). A replay into a new shard is therefore claimed, not
 * denied — demonstrated, not inferred, by acceptance_claim_recurrence.mjs check 1.
 *
 * WHAT IT DOES NOT DECIDE, and why the default is dry. Ownership is assigned by iteration order
 * over the shard directories. For the 12,659 that is arrival order over rows whose
 * `event_session_id` is empty — the attribution decision itself wearing a migration's coat.
 * The recommended sequence is unchanged and is dp's call, not this script's:
 *
 *     1. recover event_session_id from the transcripts   (99.0% unique, controls in
 *                                                         audit_claim_conflict_decidability.py)
 *     2. backfill seen                                   (this script, --execute)
 *     3. every loser is a claim_conflict row, queryable, not a silent overwrite
 *
 * Running step 2 first is not wrong so much as irreversible on the axis step 1 repairs.
 *
 * Usage:
 *   node scripts/backfill_seen.mjs                # DRY: report the plan, write nothing
 *   node scripts/backfill_seen.mjs --execute      # perform it
 *   node scripts/backfill_seen.mjs --root /path   # default: $SNARC_ROOT or ~/.snarc
 *   node scripts/backfill_seen.mjs --shards a,b,c   # explicit ownership order (default: dir name)
 */
import { homedir } from 'node:os';
import { join } from 'node:path';

const argv = process.argv.slice(2);
const execute = argv.includes('--execute');
const rootIdx = argv.indexOf('--root');
const root = rootIdx >= 0 ? argv[rootIdx + 1]
  : (process.env.SNARC_ROOT || join(homedir(), '.snarc'));
const shardsIdx = argv.indexOf('--shards');
const shards = shardsIdx >= 0 ? argv[shardsIdx + 1].split(',').map((s) => s.trim()) : undefined;

const { backfillRootClaims } = await import('../dist/src/db.js');

console.log(`root: ${root}`);
console.log(`mode: ${execute ? 'EXECUTE — this writes to seen.db' : 'DRY RUN — nothing is written'}\n`);

const plan = backfillRootClaims(root, { dryRun: !execute, shards });

console.log('shard          hashed   claimed  conflicted');
for (const s of plan.shards) {
  console.log(`${s.shard}  ${String(s.hashed).padStart(7)}  ${String(s.claimed).padStart(8)}  ${String(s.conflicted).padStart(10)}`);
}
console.log(`${'TOTAL'.padEnd(12)}  ${String(plan.totalHashed).padStart(7)}  ${String(plan.totalClaimed).padStart(8)}  ${String(plan.totalConflicted).padStart(10)}`);
console.log();
console.log(`  ${plan.totalClaimed} hashes would be owned by their first-seen shard`);
console.log(`  ${plan.totalConflicted} denials would be recorded — one per shard that also holds the event`);

// A default is an unstated axis. Ownership here is decided by DIRECTORY-NAME order, which is
// not arrival order and not the order anyone would choose on purpose — so it gets printed
// rather than assumed. Note what is NOT claimed: that the winning shard arrived first or last.
// `observations.ts` is write time before c48af34 and the event's own transcript time after,
// mixed in one column with no era marker, so the era cannot be ASSUMED. It can be TESTED, per
// population — and for the duplicated corpus it tests write time on two independent instruments
// (kimi 2026-07-31, anchored CBP same day): the same content_hash carries a DIFFERENT ts in
// every shard (identity 0.0% over ~12.6k hashes, and the median cross-shard |dt| equals the
// gap between the two arrivals), which only a per-copy write clock produces; and statx btime on
// three of the four shard files precedes that shard's first duplicated row by 6-7s — a replay's
// startup latency, from a clock outside the store entirely. So arrival order IS readable here.
// The earlier text said it was not. Within-shard `id` order remains exact and era-free.
const winner = plan.shards.reduce((a, b) => (b.claimed > (a?.claimed ?? -1) ? b : a), null);
if (winner && plan.totalConflicted > 0) {
  console.log();
  console.log(`  ORDERING: ownership is assigned in directory-name order, NOT arrival order.`);
  console.log(`  As listed, ${winner.shard} would win ${winner.claimed} hashes by sorting first.`);
  console.log(`  Arrival order IS measurable — run scripts/audit_replay_arrival.py for it and`);
  console.log(`  scripts/audit_arrival_anchor.py for the two controls that anchor it, then pass`);
  console.log(`  --shards in that order. On this store the default awards the corpus to arrival`);
  console.log(`  #3 of 4, which is a choice no one made rather than a tiebreak anyone intended.`);
}
if (!execute) {
  console.log('\nnothing was written. Re-run with --execute to perform it.');
  console.log('Consider recovering event_session_id from the transcripts FIRST — the conflict');
  console.log('rows written here carry whatever that column holds at the time, and it is the');
  console.log('only axis on which the attribution this freezes can later be reviewed.');
}
