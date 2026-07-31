#!/usr/bin/env python3
"""Re-derive the PRD's two DENOMINATOR PENDING rates over distinct events, plus the
fleet duplication factor and the break-attempt tests on defect #11's distinct-event key.

Exists because CBP assigned the split (forum 2026-07-31): "I take the writer diff, you
take the re-derivation and the fleet duplication factor across the 195." Every number
the PRD carries over duplicated rows is re-stated here over distinct events.

Predicates, stated:
  distinct event  = distinct (tool_name, input_summary, output_summary)  [CBP's key;
                    survives the break-attempt below for Conversation, upper-bounds the
                    tool class within ~1pp]
  scorer-path row = tool_name NOT IN ('Conversation','user_prompt','decision',
                    'structural')  -- rows written via Memory.capture() (SNARC-scored).
                    The complement is the captureContext bypass. Reproduces the PRD's
                    691,760/704,042 = 98.3% to within 7 rows (store moved between reads).
  literal dims    = surprise=0.5 AND novelty=0.7  -- the captureContext nominal vector.
                    A turn is all-copies-literal iff EVERY copy carries it.
  burst minute    = minute with >100 inserted rows. Live capture does not write
                    100 rows/min; replay does.
  fleet event     = md5(tool, input, output) unioned across all 195 archive shards.
  access          : file:...?mode=ro (WAL read through).

Usage: distinct_denominators.py [--check]
"""
import glob
import hashlib
import os
import sqlite3
import sys
from collections import defaultdict

DB = os.path.expanduser("~/.engram/projects/791cace57ce9/engram.db")
SHARDS = os.path.expanduser("~/.engram/projects/*/engram.db")
CTX = ("Conversation", "user_prompt", "decision", "structural")

# Measured 2026-07-31 ~09:30Z, archive last write 2026-07-31 04:20:12.
EXPECT = {
    "scored_rows": 12282,
    "conv_literal_rows": 409258,
    "distinct_events": 31219,
    "scored_distinct": 12113,
    "distinct_turns": 17808,
    "literal_turns": 9490,
    "mixed_dim_turns": 3050,
    "burst_minutes": 444,
    "rows_in_burst": 686426,
    "truncated_tool_triples": 7561,
    "fleet_rows": 921478,
    "fleet_distinct": 61792,
    "events_multi_shard": 19778,
}


def one(c, s, a=()):
    return c.execute(s, a).fetchone()


def main():
    check = "--check" in sys.argv
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.execute("pragma temp_store=FILE")
    g = {}

    rows = one(c, "select count(*) from observations")[0]
    g["scored_rows"] = one(c, "select count(*) from observations where tool_name not in (?,?,?,?)", CTX)[0]
    conv_rows = one(c, "select count(*) from observations where tool_name='Conversation'")[0]
    g["conv_literal_rows"] = one(c, "select count(*) from observations where tool_name='Conversation' and surprise=0.5 and novelty=0.7")[0]
    g["distinct_events"] = one(c, "select count(*) from (select distinct tool_name,input_summary,output_summary from observations)")[0]
    g["scored_distinct"] = one(c, "select count(*) from (select distinct tool_name,input_summary,output_summary from observations where tool_name not in (?,?,?,?))", CTX)[0]
    g["distinct_turns"] = one(c, "select count(*) from (select distinct input_summary,output_summary from observations where tool_name='Conversation')")[0]
    g["literal_turns"] = one(c, """select count(*) from (select input_summary,output_summary from observations
        where tool_name='Conversation' group by 1,2
        having min(surprise)=0.5 and max(surprise)=0.5 and min(novelty)=0.7 and max(novelty)=0.7)""")[0]
    g["mixed_dim_turns"] = one(c, """select count(*) from (select input_summary,output_summary from observations
        where tool_name='Conversation' group by 1,2
        having min(surprise)<max(surprise) or min(novelty)<max(novelty))""")[0]

    vol = defaultdict(int)
    for (m,) in c.execute("select substr(ts,1,16) from observations"):
        vol[m] += 1
    g["burst_minutes"] = sum(1 for n in vol.values() if n > 100)
    g["rows_in_burst"] = sum(n for n in vol.values() if n > 100)
    g["truncated_tool_triples"] = one(c, """select count(*) from (select distinct tool_name,input_summary,output_summary
        from observations where tool_name not in (?,?,?,?) and (length(input_summary)>=295 or length(output_summary)>=295))""", CTX)[0]
    tool_triples = one(c, "select count(*) from (select distinct tool_name,input_summary,output_summary from observations where tool_name not in (?,?,?,?))", CTX)[0]

    print(f"db                    {DB}  (mode=ro)")
    print(f"never-scored          {rows - g['scored_rows']:,} / {rows:,} = {(rows-g['scored_rows'])/rows:.1%} of ROWS")
    print(f"  over distinct       {g['distinct_events'] - g['scored_distinct']:,} / {g['distinct_events']:,}"
          f" = {(g['distinct_events']-g['scored_distinct'])/g['distinct_events']:.1%} of distinct events")
    print(f"literal-dim Conv      {g['conv_literal_rows']:,} / {conv_rows:,} = {g['conv_literal_rows']/conv_rows:.1%} of ROWS")
    print(f"  over distinct       {g['literal_turns']:,} / {g['distinct_turns']:,} = {g['literal_turns']/g['distinct_turns']:.1%} of distinct turns"
          f"   (mixed-dim turns: {g['mixed_dim_turns']:,} — copies written by different writer generations)")
    print(f"burst structure       {g['rows_in_burst']:,} / {rows:,} rows in {g['burst_minutes']} burst minutes (>100 rows/min)"
          f" of {len(vol):,} — live capture does not write like this; replay does")
    print(f"key break-attempt     {g['truncated_tool_triples']:,} / {tool_triples:,} distinct tool triples at the 300-char summary cap"
          f" (collision-prone; direction: distinct is UNDERcounted, so the raw-key dup rate is an UPPER bound, ~1pp sensitivity)")

    # fleet duplication factor
    seen = defaultdict(set)
    g["fleet_rows"] = 0
    paths = sorted(glob.glob(SHARDS))
    for p in paths:
        h = os.path.basename(os.path.dirname(p))
        try:
            cc = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            for t, i, o in cc.execute("select tool_name, input_summary, output_summary from observations"):
                g["fleet_rows"] += 1
                seen[hashlib.md5((t or "").encode() + b"\x00" + (i or "").encode() + b"\x00" + (o or "").encode()).digest()].add(h)
            cc.close()
        except sqlite3.Error as e:
            print(f"  ERR {h}: {e}")
    g["fleet_distinct"] = len(seen)
    g["events_multi_shard"] = sum(1 for s in seen.values() if len(s) > 1)
    worst = max(len(s) for s in seen.values())
    mean_shards = sum(len(s) for s in seen.values()) / g["fleet_distinct"]
    print(f"fleet                 {g['fleet_rows']:,} rows -> {g['fleet_distinct']:,} distinct events"
          f" = {1 - g['fleet_distinct']/g['fleet_rows']:.1%} duplication, {g['fleet_rows']/g['fleet_distinct']:.1f} rows/event")
    print(f"  cross-shard         {g['events_multi_shard']:,} events in >1 shard ({g['events_multi_shard']/g['fleet_distinct']:.1%}),"
          f" mean {mean_shards:.2f} shards/event, worst {worst} shards")

    if not check:
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
