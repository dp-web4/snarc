#!/usr/bin/env python3
"""The 95.6% duplication rate spans two writers. Find the boundary, and re-run the
constant-vs-real-session-id comparison INSIDE the regime where the guard can fire.

Written 2026-07-31 while building the defect-#11 writer diff, because the first thing a
diff needs is when the writer last changed. Two things fell out, both of which correct
the thread's own conclusions:

  1. THE RATE IS HISTORICAL. `9a9fb50` (2026-07-22) added content_hash + the store-level
     dedup guard. Copies-per-event falls 23.0 -> 1.32 at that boundary. The lifetime
     95.6% averages across a fix that landed nine days before it was published.

     The control that makes this a fix and not an activity drop is NEW-CONTENT RATE:
     distinct events whose FIRST appearance anywhere in the store is that day. Rows/day
     would go down for either reason. New-events/day went UP (25-383 before, 81-242
     after) while rows-per-new-event fell ~400x. A denominator the fix cannot move.

  2. THE REMEDY WAS INVERTED. The corpus-level comparison says the constant session id
     888f190a is catastrophic (26.07 copies vs 1.38 under real ids) — but BOTH arms of
     that comparison are pre-07-22, i.e. both are measured where the guard cannot fire
     at all, and the constant-id writer is merely the high-volume one. Inside the
     post-fix regime the sign FLIPS: constant id 1.00 copies, real ids 1.40. A constant
     session degenerates a session-scoped guard to store-global, which is the correct
     guard. Repairing the id source first would have RAISED duplication.

     The actual defect was the session predicate in the guard, which `a35e3a8` removes.
     After that commit this script's `leak_real_ids` number is what the fix closes; it
     will NOT change for these historical rows (nothing rewrites them), which is why the
     expectations below stay pinned to the pre-fix reading.

Predicates, stated:
  distinct event = distinct (tool_name, input_summary, output_summary), same key as
    audit_tier1_duplication.py. Lower bound on uniqueness (identical summaries collapse).
  regime boundary — THREE windows, not two, and they do not tile with one date. The
    migration landed 2026-07-22 00:32 PDT = 07:32Z, so 07-22 is a MIXED day (18,085
    rows written under both writers) and belongs to neither regime:
        pre    ts <  '2026-07-22'
        mixed  ts >= '2026-07-22' AND ts < '2026-07-23'   <- reported, never merged
        post   ts >= '2026-07-23'
    The first version of this script used a single boundary and silently folded the
    mixed day into `pre`, which moved 702,168/29,835 into a slot whose published number
    was 684,083/29,744. --check caught it. Any two-window split of a one-day cutover is
    wrong by exactly the cutover day; report the day rather than choosing a side.
  new-content date = date(min(ts)) over the distinct-event key — first appearance
    anywhere in this store, not first appearance that day.
  guard failure = a (session_id, content_hash) pair with more than one row. Zero of
    these in either arm is what proves the leak is the session predicate and not a
    broken guard.
  Access: file:...?mode=ro so the -wal is read through (a flat copy reports 704,045).

Scope: ONE shard (the archive, which holds 74% of fleet rows). Cross-shard duplication
is a different mechanism and a per-shard guard cannot address it — see
scripts/distinct_denominators.py for the fleet factor, which this fix does not move.

Usage: audit_dedup_regime.py [--db PATH] [--check]
"""
import argparse
import os
import sqlite3
import sys

DEFAULT_DB = "~/.engram/projects/791cace57ce9/engram.db"
MIGRATION_DAY = "2026-07-22"   # 9a9fb50, 00:32 PDT / 07:32Z — a mixed day, its own window
BOUNDARY = "2026-07-23"        # first fully-post-fix day
CONST_SESSION = "888f190a-f01d-4efe-a5a0-5320307d31ab"
KEY = "tool_name || char(0) || input_summary || char(0) || output_summary"

# Measured 2026-07-31 ~08:00Z against the archive (last write 2026-07-31 04:20:12).
# The archive is retired — the write path moved to ~/.snarc at 04:22Z — so these are
# expected to be stable. If they drift, something is writing to the archive again.
EXPECT = {
    "pre_rows": 684083,
    "pre_distinct": 29744,
    "mixed_rows": 18085,
    # 2,227 = distinct events AMONG THE ROWS WRITTEN that day. Not 91, which is the
    # series table's "NEW events/day" — distinct events whose FIRST appearance anywhere
    # is that day. Two different denominators with the same one-word name; I set this
    # expectation to 91 on the first pass and --check caught it.
    "mixed_distinct": 2227,
    "post_rows": 1881,
    "post_distinct": 1423,
    "post_const_rows": 288,
    "post_const_distinct": 288,
    "post_real_rows": 1593,
    "post_real_distinct": 1135,
    "post_guard_failures": 0,
    "post_cross_session_hashes": 157,
    "life_const_rows": 697888,
    "life_const_distinct": 26765,
    "life_real_rows": 6161,
    "life_real_distinct": 4461,
}


def gather(c: sqlite3.Connection) -> dict:
    g = {}

    def pair(prefix, where):
        n, d = c.execute(
            f"SELECT count(*), count(DISTINCT {KEY}) FROM observations WHERE {where}"
        ).fetchone()
        g[prefix + "_rows"], g[prefix + "_distinct"] = n, d

    pair("pre", f"ts < '{MIGRATION_DAY}'")
    pair("mixed", f"ts >= '{MIGRATION_DAY}' AND ts < '{BOUNDARY}'")
    pair("post", f"ts >= '{BOUNDARY}'")
    pair("post_const", f"ts >= '{BOUNDARY}' AND session_id = '{CONST_SESSION}'")
    pair("post_real", f"ts >= '{BOUNDARY}' AND session_id != '{CONST_SESSION}'")
    pair("life_const", f"session_id = '{CONST_SESSION}'")
    pair("life_real", f"session_id != '{CONST_SESSION}'")

    g["post_guard_failures"] = c.execute(
        f"""SELECT count(*) FROM (
              SELECT session_id, content_hash FROM observations
              WHERE ts >= '{BOUNDARY}' AND content_hash IS NOT NULL
              GROUP BY 1, 2 HAVING count(*) > 1)"""
    ).fetchone()[0]
    g["post_cross_session_hashes"] = c.execute(
        f"""SELECT count(*) FROM (
              SELECT content_hash FROM observations
              WHERE ts >= '{BOUNDARY}' AND content_hash IS NOT NULL
              GROUP BY 1 HAVING count(DISTINCT session_id) > 1)"""
    ).fetchone()[0]
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--since", default="2026-07-15", help="first day of the printed series")
    args = ap.parse_args()

    path = os.path.expanduser(args.db)
    if not os.path.exists(path):
        print(f"no such db: {path}", file=sys.stderr)
        return 2
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    # --- the series, with the control denominator -------------------------------------
    c.execute(f"CREATE TEMP TABLE fs AS SELECT {KEY} AS k, min(ts) AS first_ts "
              f"FROM observations GROUP BY 1")
    print(f"Per-day series (control: NEW distinct events/day — first appearance anywhere).")
    print(f"Rows/day alone cannot distinguish 'the fix worked' from 'less happened'.\n")
    print(f"{'date':<12}{'rows/day':>11}{'NEW events/day':>16}{'rows per new event':>21}")
    rows = dict(c.execute(
        f"SELECT date(ts), count(*) FROM observations WHERE ts >= '{args.since}' GROUP BY 1"))
    new = dict(c.execute(
        f"SELECT date(first_ts), count(*) FROM fs WHERE first_ts >= '{args.since}' GROUP BY 1"))
    for d in sorted(set(rows) | set(new)):
        r, n = rows.get(d, 0), new.get(d, 0)
        ratio = f"{r / n:.1f}" if n else "-"
        mark = "   <- 9a9fb50 (content_hash + guard)" if d == "2026-07-22" else ""
        print(f"{d:<12}{r:>11,}{n:>16,}{ratio:>21}{mark}")

    g = gather(c)

    # --- the regime split ---------------------------------------------------------------
    print(f"\nRegime split — THREE windows. {MIGRATION_DAY} is a cutover day (9a9fb50 landed")
    print(f"07:32Z) and belongs to neither regime, so it is reported, never merged:")
    for label, k in (("pre    (< 07-22)", "pre"),
                     ("mixed  (07-22)", "mixed"),
                     ("post   (>= 07-23)", "post")):
        n, d = g[k + "_rows"], g[k + "_distinct"]
        print(f"  {label:<22} {n:>9,} rows / {d:>7,} distinct = {n / d:>6.2f} copies/event")

    # --- the inversion ------------------------------------------------------------------
    print("\nConstant host id 888f190a vs real session ids — the SAME comparison, two windows:")
    print("  lifetime (both arms pre-fix, so the guard fires in NEITHER — confounded):")
    for label, k in (("constant id", "life_const"), ("real session ids", "life_real")):
        n, d = g[k + "_rows"], g[k + "_distinct"]
        print(f"    {label:<20} {n:>9,} rows / {d:>7,} distinct = {n / d:>5.2f} copies/event")
    print(f"  since {BOUNDARY} (the guard CAN fire — the sign flips):")
    for label, k in (("constant id", "post_const"), ("real session ids", "post_real")):
        n, d = g[k + "_rows"], g[k + "_distinct"]
        print(f"    {label:<20} {n:>9,} rows / {d:>7,} distinct = {n / d:>5.2f} copies/event")
    leak = 1 - g["post_real_distinct"] / g["post_real_rows"]
    print(f"\n  cross-session leak under real ids   {leak * 100:.1f}%"
          f"  ({g['post_real_rows'] - g['post_real_distinct']:,} rows re-stored)")
    print(f"  same-session guard failures         {g['post_guard_failures']}"
          "   <- the guard is sound; the SCOPE was the defect")
    print(f"  hashes appearing under >1 session   {g['post_cross_session_hashes']:,}")
    print("\n  A constant session degenerates a session-scoped guard to store-global, which")
    print("  is the correct guard. Fixing the id source WITHOUT widening the scope would")
    print("  have raised duplication from 1.00 to ~1.40 copies/event. a35e3a8 drops the")
    print("  session predicate; the id repair is safe only after it.")

    if not args.check:
        return 0
    bad = [f"{k}: expected {v:,}, got {g[k]:,}" for k, v in EXPECT.items() if g[k] != v]
    print()
    if bad:
        print("DRIFT vs the 2026-07-31 reading (the archive is retired — is something writing to it?):")
        for b in bad:
            print(f"  {b}")
        return 1
    print(f"OK — all {len(EXPECT)} numbers reproduce")
    return 0


if __name__ == "__main__":
    sys.exit(main())
