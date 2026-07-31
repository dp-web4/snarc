#!/usr/bin/env python3
"""Audit the SELECTION side of the briefing, independently of the outcome column.

`audit_outcome_instrument.py` asks: can `retrieval_log.relevant` see the item that
was surfaced?  (It cannot.)  This script asks a question that needs no outcome
instrument at all:

    Is there a selection here to evaluate in the first place?

Both must pass before a per-tier rate ("patterns are relevant 9% of the time") means
anything.  They fail independently and for unrelated reasons, so repairing either one
alone leaves the other's number uninterpretable.  Three checks, all read-only:

  QUOTA       Every briefing takes slice(0,3) per kind (memory.ts:293,304,317).  A tier
              is surfaced whether or not it holds anything worth surfacing, so its
              surfacing count carries no information about its quality.

  HEADROOM    A deterministic top-3 over a small static pool is a CONSTANT function.
              If a tier surfaces the same k items in every briefing for weeks, its
              per-tier rate is the score of a fixed playlist, not of a ranker.

  RESOLUTION  The five SNARC dimensions the ranker reads.  A column that takes 5
              distinct values across 704k rows cannot order 704k rows.  This is the
              data-level form of the "never scored" claim: it needs no code read, so
              it can be seconded by a reviewer whose scope excludes src/.

Exits 1 while any check fails.  Like the outcome gate, it is a gauge, not a
regression test: it is supposed to fail today, so that a later PASS distinguishes a
repair from a dead instrument.

    python3 scripts/audit_selection_tier.py [--db PATH]
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter, defaultdict

# A tier whose surfaced set is this small is a playlist: a deterministic ranker over a
# static pool returns the same items forever, so the tier's rate is a constant.
MIN_DISTINCT_SURFACED = 10
# A dimension with fewer distinct values than this cannot express a per-item judgement.
MIN_DISTINCT_DIM = 50
# Fraction of briefings that must depart from the fixed quota for selection to be
# responding to content rather than filling slots.
MAX_QUOTA_SHARE = 0.90

DIMS = ('surprise', 'novelty', 'arousal', 'reward', 'conflict')


def resolve_db(explicit):
    """Name the store on every run.  Picking by size would silently select the
    ~/.engram archive over the live ~/.snarc store for the next several months --
    'defaults are unstated axes', and this thread has already been bitten by it."""
    if explicit:
        return explicit
    for p in ('~/.engram/projects/791cace57ce9/engram.db',
              '~/.snarc/projects/791cace57ce9/snarc.db'):
        f = os.path.expanduser(p)
        if os.path.exists(f):
            return f
    sys.exit('no store found; pass --db explicitly')


def check_quota(conn):
    rows = conn.execute(
        "SELECT surfaced_ts, cwd, item_kind FROM retrieval_log WHERE source='briefing'"
    ).fetchall()
    briefings = defaultdict(Counter)
    for ts, cwd, kind in rows:
        briefings[(ts, cwd)][kind] += 1
    n = len(briefings)
    if not n:
        return None, []
    comp = Counter((b['pattern'], b['identity'], b['observation']) for b in briefings.values())
    (top, top_n), = comp.most_common(1)
    per_kind = {k: sum(1 for b in briefings.values() if b[k] == 3) / n
                for k in ('pattern', 'identity', 'observation')}
    print(f"QUOTA      {n} briefings, {len(rows)} item surfacings")
    print(f"           modal composition (pat,id,obs) = {top} in {top_n} "
          f"({100 * top_n / n:.1f}%)")
    for k, share in sorted(per_kind.items()):
        print(f"           {k:<12} exactly 3 in {100 * share:5.1f}% of briefings")
    failed = [k for k, s in per_kind.items() if s > MAX_QUOTA_SHARE]
    return n, failed


def check_headroom(conn, n_briefings):
    print("\nHEADROOM   pool sizes: what exists, what is ever chosen")
    hdr = f"           {'kind':<12} {'in store':>9} {'distinct surfaced':>18} {'surfacings':>11} {'top-3 share':>12}"
    print(hdr)
    tables = {'pattern': 'patterns', 'identity': 'identity', 'observation': 'observations'}
    failed = []
    for kind, table in tables.items():
        stored = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        rows = conn.execute(
            "SELECT match_key, COUNT(*) FROM retrieval_log WHERE item_kind=? "
            "GROUP BY 1 ORDER BY 2 DESC", (kind,)).fetchall()
        if not rows:
            continue
        total = sum(r[1] for r in rows)
        top3 = 100 * sum(r[1] for r in rows[:3]) / total
        print(f"           {kind:<12} {stored:>9} {len(rows):>18} {total:>11} {top3:>11.1f}%")
        if len(rows) < MIN_DISTINCT_SURFACED:
            failed.append(kind)
    # A tier whose every item appears in every briefing has a constant selector.
    for kind in tables:
        rows = conn.execute(
            "SELECT match_key, COUNT(*) FROM retrieval_log WHERE item_kind=? GROUP BY 1",
            (kind,)).fetchall()
        if rows and n_briefings and all(c >= 0.99 * n_briefings for _, c in rows):
            print(f"           -> {kind}: every surfaced item appears in ~every briefing. "
                  f"The selector is a constant function.")
    return failed


def check_resolution(conn):
    total = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    print(f"\nRESOLUTION dimension cardinality over {total} observations")
    failed = []
    for col in DIMS:
        distinct = conn.execute(f"SELECT COUNT(DISTINCT {col}) FROM observations").fetchone()[0]
        top = conn.execute(
            f"SELECT {col}, COUNT(*) c FROM observations GROUP BY 1 ORDER BY c DESC LIMIT 2"
        ).fetchall()
        share = 100 * sum(c for _, c in top) / total
        modes = ", ".join(f"{v}={100 * c / total:.1f}%" for v, c in top)
        flag = "  <-- constant" if distinct < MIN_DISTINCT_DIM else ""
        print(f"           {col:<10} distinct={distinct:<5} top2 covers {share:5.1f}%  ({modes}){flag}")
        if distinct < MIN_DISTINCT_DIM:
            failed.append(col)
    triples = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT surprise, novelty, conflict FROM observations)"
    ).fetchone()[0]
    top3 = conn.execute(
        "SELECT COUNT(*) c FROM observations GROUP BY surprise, novelty, conflict "
        "ORDER BY c DESC LIMIT 3").fetchall()
    print(f"           {triples} distinct (surprise,novelty,conflict) triples; "
          f"top 3 cover {100 * sum(r[0] for r in top3) / total:.2f}% of rows")
    return failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db')
    args = ap.parse_args()
    db = resolve_db(args.db)
    print(f"store: {db}\n")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    n_briefings, quota_failed = check_quota(conn)
    headroom_failed = check_headroom(conn, n_briefings)
    dim_failed = check_resolution(conn)

    print()
    if quota_failed or headroom_failed or dim_failed:
        if quota_failed:
            print(f"FAIL quota:      {', '.join(quota_failed)} filled to the slice(0,3) cap in "
                  f">{100 * MAX_QUOTA_SHARE:.0f}% of briefings -- surfacing count is not evidence")
        if headroom_failed:
            print(f"FAIL headroom:   {', '.join(headroom_failed)} surface fewer than "
                  f"{MIN_DISTINCT_SURFACED} distinct items -- the rate scores a playlist")
        if dim_failed:
            print(f"FAIL resolution: {', '.join(dim_failed)} carry fewer than "
                  f"{MIN_DISTINCT_DIM} distinct values -- cannot order the corpus")
        print("\nNo per-tier relevance rate is interpretable while these fail, independently "
              "of whether the outcome column is repaired.")
        sys.exit(1)
    print("PASS: a selection exists that is worth measuring.")


if __name__ == '__main__':
    main()
