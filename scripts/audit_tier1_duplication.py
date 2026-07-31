#!/usr/bin/env python3
"""Tier 1 is a duplication pile: measure how much of `observations` is re-ingestion.

Found 2026-07-31 while committing the census script the fleet numbers should have
shipped with. Chain:

  1. `session_id` on observations is effectively a constant — one value covers
     697,888 / 704,049 rows (99.12%) spanning 2026-03-15 -> 2026-07-31, four and a
     half months. It is not a session.
  2. `content_hash` is NULL on 702,058 / 704,049 rows (99.72%).
  3. The only dedup guard is `existsContentHash` (db.ts:285):
        SELECT 1 FROM observations WHERE session_id = ? AND content_hash = ?
     `= NULL` is never true in SQL, so for 99.72% of rows the guard cannot match.
  4. Therefore every PreCompact / SessionEnd re-walks the transcript and re-inserts
     every turn it can still see. Observed: one Conversation turn present 414 times
     across 22 distinct days and many cwds.
  5. Consequence at the top of the stack: `tool_transitions` holds 1,576,273
     Conversation->Conversation edges from 689,549 Conversation rows, and the
     `patterns` upsert accumulates that across runs into frequency 43,581,138 for
     "Recurring workflow: Conversation -> Conversation -> Conversation", conf 0.90 —
     the single most-surfaced memory in the system (defect #7: #1 in 1,212 of 1,217
     briefings). The duplication bug is not only inflating denominators; it is
     authoring the top item the system shows itself.

Predicates, stated:
  distinct event = distinct (tool_name, input_summary, output_summary). This is a
  LOWER bound on uniqueness: two genuinely distinct events with identical summaries
  collapse. It is the right key for Conversation turns (identical human/assistant
  text pairs are the same turn re-read) and conservative for tool rows.
  Access: file:...?mode=ro so the -wal is read through (a flat copy reports 704,045).

Unaffected by this defect: `retrieval_log` (written once per surfacing, no
re-ingestion path). The thread's retrieval-tier findings stand.

Usage: audit_tier1_duplication.py [--db PATH] [--check]
"""
import argparse
import os
import sqlite3
import sys

DEFAULT_DB = "~/.engram/projects/791cace57ce9/engram.db"

# Measured 2026-07-31 ~07:30Z, archive last write 2026-07-31 04:20:12.
EXPECT = {
    "rows": 704049,
    "distinct_events": 31219,
    "conversation_rows": 689549,
    "distinct_conversation": 17808,
    "null_content_hash": 702058,
    "top_session_rows": 697888,
    "max_copies": 414,
    "transitions_conv_conv": 1576273,
    "top_pattern_frequency": 43581138,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    path = os.path.expanduser(args.db)
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.execute("pragma temp_store=FILE")
    c.execute("pragma cache_size=-200000")
    one = lambda s, a=(): c.execute(s, a).fetchone()

    g = {}
    g["rows"] = one("select count(*) from observations")[0]
    g["null_content_hash"] = one("select count(*) from observations where content_hash is null")[0]
    g["distinct_events"] = one(
        "select count(*) from (select distinct tool_name,input_summary,output_summary from observations)")[0]
    g["conversation_rows"] = one("select count(*) from observations where tool_name='Conversation'")[0]
    g["distinct_conversation"] = one(
        "select count(*) from (select distinct input_summary,output_summary from observations"
        " where tool_name='Conversation')")[0]
    sid, g["top_session_rows"] , smin, smax = one(
        "select session_id, count(*) n, min(ts), max(ts) from observations"
        " group by session_id order by n desc limit 1")
    n_sessions = one("select count(distinct session_id) from observations")[0]
    top_in, top_out, g["max_copies"] = one(
        "select input_summary, output_summary, count(*) n from observations"
        " where tool_name='Conversation' group by input_summary, output_summary order by n desc limit 1")
    days = one("select count(distinct substr(ts,1,10)) from observations where input_summary=? and output_summary=?",
               (top_in, top_out))[0]
    cwds = one("select count(distinct cwd) from observations where input_summary=? and output_summary=?",
               (top_in, top_out))[0]
    g["transitions_conv_conv"] = (one(
        "select count from tool_transitions where from_tool='Conversation' and to_tool='Conversation'") or [0])[0]
    g["top_pattern_frequency"] = one("select max(frequency) from patterns")[0]

    dup = 1 - g["distinct_events"] / g["rows"]
    cdup = 1 - g["distinct_conversation"] / g["conversation_rows"]
    print(f"db                      {path}  (mode=ro)")
    print(f"observations            {g['rows']:,}")
    print(f"distinct events         {g['distinct_events']:,}   -> {dup:.1%} of tier 1 is duplicate rows")
    print(f"  mean copies/event     {g['rows'] / g['distinct_events']:.1f}")
    print(f"Conversation rows       {g['conversation_rows']:,}")
    print(f"distinct turns          {g['distinct_conversation']:,}   -> {cdup:.1%} duplicates,"
          f" {g['conversation_rows'] / g['distinct_conversation']:.1f} copies each")
    print(f"  worst turn            {g['max_copies']} copies, {days} distinct days, {cwds} distinct cwds")
    print(f"content_hash NULL       {g['null_content_hash']:,} / {g['rows']:,}"
          f" = {g['null_content_hash'] / g['rows']:.2%}  -> dedup guard cannot match")
    print(f"session_id concentration {g['top_session_rows']:,} / {g['rows']:,}"
          f" = {g['top_session_rows'] / g['rows']:.2%} on ONE id")
    print(f"  {sid}  {smin} -> {smax}   ({n_sessions} distinct ids total)")
    print(f"tool_transitions Conv->Conv  {g['transitions_conv_conv']:,}"
          f"   ({g['transitions_conv_conv'] / g['conversation_rows']:.2f}x the rows it counts)")
    print(f"top pattern frequency   {g['top_pattern_frequency']:,}")

    # the outcome table cannot carry the session grain at all
    cols = [d[1] for d in c.execute("pragma table_info(retrieval_log)")]
    print(f"retrieval_log columns   {cols}")
    print(f"  session grain present: {'session_id' in cols}"
          "   <- the holdout's randomization unit has no column in the outcome table")

    if not args.check:
        return 0
    bad = [f"{k}: expected {v:,}, got {g[k]:,}" for k, v in EXPECT.items() if g[k] != v]
    print()
    if bad:
        print("DRIFT vs the 2026-07-31 reading:")
        for b in bad:
            print(f"  {b}")
        return 1
    print(f"OK — all {len(EXPECT)} numbers reproduce")
    return 0


if __name__ == "__main__":
    sys.exit(main())
