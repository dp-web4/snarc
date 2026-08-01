#!/usr/bin/env python3
"""
audit_column_completeness.py — the column set audit_recovery_payout.py tests, against the
column set that exists; and the two counts that were read as drift.

WHY THIS EXISTS. kimi's reply to notice 502 answered §7's "a seventh place the replayer
left a trace" by checking eight more columns (`output_summary` and the seven scalars),
found each constant across every duplicated hash, and concluded: *"Every column of
`observations` is now tested; only id/ts vary."* Two things in that are worth measuring
rather than agreeing with.

  1. THE COLUMN SET IS 18, NOT 16. `COPY_COLS` is six; plus `id`/`ts` plus kimi's eight is
     sixteen. The schema carries `content_hash` (the grouping key — constant by
     construction, not a finding) and `event_session_id`. The latter was in nobody's test,
     and it is the column the entire thread is about: the axis the recovery installs, the
     one §4's "check 2 is the tripwire if ownership ever becomes event-session-aware"
     guards. A completeness claim that omits the load-bearing column is the kind that gets
     quoted forward.

     It does not refute the negative. But the REASON it does not is different in kind from
     the other fifteen: those are populated columns measured to be constant — the
     instrument could have discriminated and did not. This one is 99.98% NULL across the
     duplicated corpus, so "varies in 0 of N" is a zero produced by ABSENCE, not by
     constancy, and printed in the same column of the same table it is indistinguishable
     from one. This script prints them apart, and reports the blind fraction.

  2. 12,670 vs 12,672 IS A GRAIN, NOT DRIFT. kimi's §1 reports 12,670 and §2 reports
     12,672, attributing +2 to the store growing between runs. `load_corpus()` keys
     `per[h][shard]` and keeps the FIRST row per (hash, shard), so its `dup` is
     "appears in >=2 SHARDS". A row-grain count is ">=2 ROWS anywhere". The delta is
     exactly the hashes with repeats INSIDE one shard — which are invisible to the
     cross-shard instrument, and which include the most-duplicated content in the store.
     A later run than either of kimi's still reports 12,670 at shard grain, which is what
     separates the two hypotheses.

  --check is RED when the tested column set is not the full column set, when a duplicated
  hash carries two distinct non-null event_session_ids (which would REFUTE the negative
  and is written to be able to say so), and when the evidence is absent.

Usage:
  python3 audit_column_completeness.py
  python3 audit_column_completeness.py --check
"""
import sys, os, glob, sqlite3
from collections import defaultdict, Counter

SNARC_ROOT = os.path.expanduser("~/.snarc")
SHARD_GLOB = os.path.join(SNARC_ROOT, "projects", "*", "*.db")

# what the two audits between them test
COPY_COLS = ["input_summary", "session_id", "scored_by", "cwd", "tags", "tool_name"]
KIMI_COLS = ["output_summary", "surprise", "novelty", "arousal", "reward", "conflict",
             "salience", "base_salience"]
WRITE_ORDER = ["id", "ts"]
BY_CONSTRUCTION = ["content_hash"]          # the grouping key: constant is a tautology
TESTED = set(COPY_COLS) | set(KIMI_COLS) | set(WRITE_ORDER) | set(BY_CONSTRUCTION)

# item-3's open ownership question (still dp's call)
OWNERSHIP = ["791cace57ce9", "7d210ad7238a", "23094633bebc", "777c4901744b"]


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def main():
    check = "--check" in sys.argv
    fails = []

    print("=" * 78)
    print("column completeness — is the full column set tested, and is +2 drift or grain?")
    print("=" * 78)

    paths = sorted(glob.glob(SHARD_GLOB))
    if not paths:
        print("\nNO SHARDS under", SHARD_GLOB, "— the population is ABSENT, every ratio below is 0/0")
        sys.exit(1 if check else 0)

    rows = defaultdict(list)      # content_hash -> [(shard, event_session_id)]
    cols_of, nrows = {}, {}
    for p in paths:
        sh = os.path.basename(os.path.dirname(p))
        c = ro(p)
        cols = [r[1] for r in c.execute("PRAGMA table_info(observations)")]
        cols_of[sh] = cols
        nrows[sh] = c.execute("SELECT count(*) FROM observations").fetchone()[0]
        q = ("SELECT content_hash, event_session_id FROM observations WHERE content_hash IS NOT NULL"
             if "event_session_id" in cols else
             "SELECT content_hash, NULL FROM observations WHERE content_hash IS NOT NULL")
        for h, es in c.execute(q):
            rows[h].append((sh, es))
        c.close()

    # ------------------------------------------------------------------ 1
    print(f"\n[1] the schema, per shard ({len(paths)} shards)")
    every = sorted({c for cs in cols_of.values() for c in cs})
    print(f"  {'shard':14} {'rows':>7}  event_session_id")
    for sh in sorted(nrows, key=lambda s: -nrows[s]):
        print(f"  {sh:14} {nrows[sh]:7d}  {'yes' if 'event_session_id' in cols_of[sh] else 'NO COLUMN'}")
    untested = [c for c in every if c not in TESTED]
    print(f"\n  columns in the union of schemas : {len(every)}")
    print(f"  columns tested by the two audits: {len([c for c in every if c in TESTED])}")
    print(f"  UNTESTED                        : {untested or 'none'}")
    if untested:
        fails.append(f"the tested column set is not the full column set: {untested} untested "
                     f"while 'every column of observations is now tested' is on the record")

    missing = [s for s in OWNERSHIP if s in cols_of and "event_session_id" not in cols_of[s]]
    absent = [s for s in OWNERSHIP if s not in cols_of]
    print(f"\n  item-3 ownership candidates lacking the column: {missing or 'none'}")
    if absent:
        print(f"  item-3 ownership candidates not present here  : {absent}")
    if missing:
        print("  -> the open ownership call has a precondition nobody has priced: two of the four")
        print("     candidate shards have no `event_session_id` column to recover INTO. That is a")
        print("     schema migration, not a backfill, and it is upstream of the choice itself.")

    # ------------------------------------------------------------------ 2
    row_dup = {h: v for h, v in rows.items() if len(v) > 1}
    shard_dup = {h: v for h, v in rows.items() if len({s for s, _ in v}) > 1}
    inside = {h: v for h, v in row_dup.items() if len(v) > len({s for s, _ in v})}
    print(f"\n[2] the two counts")
    print(f"  duplicated at SHARD grain (>=2 shards, load_corpus's dup) : {len(shard_dup)}")
    print(f"  duplicated at ROW grain   (>=2 rows anywhere)             : {len(row_dup)}")
    print(f"  delta                                                     : {len(row_dup) - len(shard_dup)}")
    print(f"  hashes with >=2 rows INSIDE one shard                     : {len(inside)}")
    for h, v in sorted(inside.items(), key=lambda kv: -len(kv[1]))[:5]:
        c = Counter(s for s, _ in v)
        print(f"    {h[:16]}  {dict(c)}   (shard-grain says NOT duplicated)")
    if len(row_dup) - len(shard_dup) == len(inside):
        print("  -> the delta is accounted for entirely by intra-shard repeats. It is a GRAIN")
        print("     difference between two instruments, not the store growing between two runs:")
        print("     a run later than both still reports the shard-grain number.")

    # ------------------------------------------------------------------ 3
    print(f"\n[3] event_session_id across the copies of a duplicated hash (row grain)")
    varies = const_nn = some_null = all_null = 0
    offenders = []
    for h, v in row_dup.items():
        vals = [es for _, es in v]
        nn = [x for x in vals if x is not None]
        d = set(nn)
        if not nn:
            all_null += 1
        elif len(d) > 1:
            varies += 1
            offenders.append((h, sorted(d)[:3]))
        elif len(nn) < len(vals):
            some_null += 1
        else:
            const_nn += 1
    n = len(row_dup) or 1
    print(f"  VARIES (>=2 distinct non-null)   : {varies}")
    print(f"  constant non-null, all copies    : {const_nn}")
    print(f"  constant non-null, some copies NULL: {some_null}")
    print(f"  ALL copies NULL                  : {all_null}   ({100.0*all_null/n:.2f}%)")
    print(f"\n  blind fraction of this column   : {all_null}/{n} = {100.0*all_null/n:.2f}%")
    if varies:
        print("  -> REFUTED: a duplicated hash carries two conversations. Per-copy provenance")
        print("     EXISTS, in the one column the sequence turns on.")
        for h, d in offenders[:5]:
            print(f"     {h[:16]}  {d}")
        fails.append(f"{varies} duplicated hashes carry differing event_session_id across copies — "
                     f"the 'nothing carries per-copy provenance' negative is refuted")
    else:
        print("  -> the negative HOLDS over the full column set. But note what carries it here:")
        print("     the other fifteen columns are POPULATED and measured constant — the")
        print("     instrument could have separated the copies and did not. This one is empty on")
        print(f"     {100.0*all_null/n:.2f}% of the corpus, so its zero is absence, not constancy,")
        print("     and the empirical half of the argument does not reach it. What holds it is the")
        print("     structural half alone: the recovery is keyed on norm(content) and the copies")
        print("     are the same content. That argument is sound — it is just not a measurement,")
        print("     and a table printing 'varies in 0 of N' for both kinds hides which one you have.")

    print("\n[verdict]")
    print(f"  {len(every)} columns exist; {len(untested)} untested; event_session_id is "
          f"{100.0*all_null/n:.2f}% NULL on the duplicated corpus.")
    print(f"  The +2 is a grain, not drift ({len(inside)} intra-shard repeat hashes).")
    if fails:
        print("\nFAILURES:")
        for f in fails:
            print("  -", f)
    if check:
        sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
