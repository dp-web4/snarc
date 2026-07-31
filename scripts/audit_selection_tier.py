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
              surfacing count carries no information about its quality.  Counted at
              DISTINCT-ITEM grain, not slot grain: the same item can occupy 2 or 3 of a
              tier's 3 slots, so a slot-grain count reports content that is not there.

  CHANNEL     Whether this store can carry the outcome a session-grain design needs at
              all: tool-event rows, closed sessions, a populated identity tier.  A
              design that "needs no outcome column" still needs a channel, and a store
              can be missing the channel while every other check reads normally.

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
import re
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
    """Name the store on every run, and refuse to pick one silently.

    The previous default preferred ~/.engram over ~/.snarc, which selected a store
    that stopped taking writes at 2026-07-31T04:20Z -- every number this script
    printed described a db no longer being written, while the PRD it fed prescribes
    for the live one.  'Defaults are unstated axes'; the store is the loudest axis
    here, so it is now an explicit argument."""
    if explicit:
        return explicit
    sys.exit('pass --db explicitly: the archive (~/.engram/...) and the live store\n'
             '(~/.snarc/projects/<hash>/snarc.db) give different answers, and the\n'
             'live store is sharded per project -- there is no single default.')


def print_ref(conn, db):
    """Pin the reference, not just the path.  'Byte-exact replication' between two
    seats means nothing unless both saw the same rows; a max(id) makes that checkable
    instead of assumed."""
    n, mx = conn.execute("SELECT COUNT(*), MAX(id) FROM retrieval_log").fetchone()
    lo, hi = conn.execute("SELECT MIN(surfaced_ts), MAX(surfaced_ts) FROM retrieval_log").fetchone()
    obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    print(f"store: {db}")
    print(f"ref:   retrieval_log rows={n} max(id)={mx} span=[{lo} .. {hi}] observations={obs}")
    print("       quote this ref beside any number below; two seats agree only if it matches.\n")


def briefing_key(rows, tol_secs=2):
    """Group surfacings into briefings by (cwd, contiguous timestamp run).

    Neither obvious key is correct.  Keying on surfaced_ts alone MERGES two briefings
    that ran in the same second under different cwds; keying on (surfaced_ts, cwd)
    SPLITS two briefings whose writes straddled a second boundary.  On this archive the
    two errors are equal and opposite (2 each), so both keys print a plausible number
    while misclassifying four briefings between them -- agreement is not correctness.
    """
    from datetime import datetime
    per_cwd = defaultdict(list)
    for ts, cwd, kind, mk in rows:
        per_cwd[cwd].append((ts, kind, mk))
    briefings = {}
    for cwd, items in per_cwd.items():
        items.sort()
        prev, key = None, None
        for ts, kind, mk in items:
            t = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
            if prev is None or (t - prev).total_seconds() > tol_secs:
                key = (cwd, ts)
                briefings[key] = defaultdict(list)
            briefings[key][kind].append(mk)
            prev = t
    return briefings


def check_quota(conn):
    rows = conn.execute(
        "SELECT surfaced_ts, cwd, item_kind, match_key FROM retrieval_log "
        "WHERE source='briefing'").fetchall()
    briefings = briefing_key(rows)
    n = len(briefings)
    if not n:
        print("QUOTA      no briefings in this store")
        return None, []
    kinds = ('pattern', 'identity', 'observation')
    slots = Counter(tuple(len(b[k]) for k in kinds) for b in briefings.values())
    distinct = Counter(tuple(len(set(b[k])) for k in kinds) for b in briefings.values())
    (s_top, s_n), = slots.most_common(1)
    (d_top, d_n), = distinct.most_common(1)
    dup_rows = sum(len(b[k]) - len(set(b[k])) for b in briefings.values() for k in kinds)

    print(f"QUOTA      {n} briefings, {len(rows)} item surfacings")
    print(f"           modal composition (pat,id,obs)")
    print(f"             slot grain     {s_top} in {100 * s_n / n:5.1f}%")
    print(f"             DISTINCT grain {d_top} in {100 * d_n / n:5.1f}%   <-- the honest one")
    if dup_rows:
        print(f"           {dup_rows} slots filled by an item already in the same briefing "
              f"({100 * dup_rows / len(rows):.1f}% of all surfacings)")
    failed = []
    for k in kinds:
        s = sum(1 for b in briefings.values() if len(b[k]) == 3) / n
        d = sum(1 for b in briefings.values() if len(set(b[k])) == 3) / n
        note = "  <-- padded with repeats" if s - d > 0.05 else ""
        print(f"           {k:<12} 3 slots {100 * s:5.1f}%   3 distinct {100 * d:5.1f}%{note}")
        if s > MAX_QUOTA_SHARE:
            failed.append(k)
    return n, failed


def check_channel(conn):
    """Can this store carry a session-grain outcome at all?

    The randomized-withhold design is identified without the item-blind `relevant`
    column -- but identification is not instrumentation.  Attempt efficiency is counted
    over tool events, attributed to sessions, and (for the identity arm) needs the
    identity tier to be populated.  Each of those is a separate channel that can be
    empty while the store looks alive."""
    print("\nCHANNEL    can a session-grain outcome be computed from this store?")
    failed = []
    TOOL = "tool_name NOT IN ('Conversation','user_prompt','structural')"
    total = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    tool, last = conn.execute(
        f"SELECT COUNT(*), MAX(ts) FROM observations WHERE {TOOL}").fetchone()
    print(f"           tool-event rows   {tool:>7} of {total} observations, last: {last}")
    # `output_summary` is the two-character LITERAL '""' on rows with no captured payload,
    # not NULL and not ''.  A trim()='' test reads those as populated and reports 0% empty
    # against a channel that is 99.5% dead -- name the encoding, not just the column.
    payload = conn.execute(
        f"SELECT COUNT(*) FROM observations WHERE {TOOL} AND LENGTH(output_summary) > 12"
    ).fetchone()[0]
    print(f"           with a payload    {payload:>7} "
          f"({100 * payload / tool if tool else 0:.1f}% of tool events carry an outcome)")
    if not tool or payload < 0.5 * tool:
        print("           -> attempt efficiency has no numerator: tool events are recorded "
              "without their outcome")
        failed.append('attempt-outcomes')
    closed, sess = conn.execute(
        "SELECT SUM(ended_at IS NOT NULL), COUNT(*) FROM sessions").fetchone()
    closed = closed or 0
    print(f"           closed sessions   {closed:>7} of {sess} "
          f"({100 * closed / sess if sess else 0:.1f}%)")
    if sess and closed / sess < 0.5:
        print("           -> the session unit does not close: session-grain aggregation is "
              "over an open interval")
        failed.append('session-boundaries')
    ident = conn.execute("SELECT COUNT(*) FROM identity").fetchone()[0]
    surf = conn.execute(
        "SELECT COUNT(*) FROM retrieval_log WHERE item_kind='identity'").fetchone()[0]
    lo, hi = conn.execute("SELECT MIN(ts), MAX(ts) FROM observations").fetchone()
    print(f"           identity tier     {ident:>7} stored, {surf} surfacings")
    # The identity tier is not populated by writing to it.  It is populated by
    # deep-consolidation proposing an `identity` pattern and that proposal being
    # RE-proposed until frequency >= REOCCUR_THRESHOLD (deep-consolidation.ts:158;
    # 3, or 1 when auto_promote_identity is on).  So "empty" and "young" are not the
    # only two states, and store age does not decide between them: read the pipeline.
    # HEAD's writers emit source in {deep-dream-immediate, reproduced-<N>x,
    # human-confirmed} (deep-consolidation.ts:177-178, memory.ts:441).  Anything else
    # in the column was written by code that is no longer in the tree.
    HEAD_SOURCES_RE = r'^(deep-dream-immediate|reproduced-[0-9]+x|human-confirmed)$'
    try:
        auto = conn.execute(
            "SELECT value FROM settings WHERE key='auto_promote_identity'").fetchone()
    except sqlite3.Error:
        auto = None
    threshold = 1 if (auto and str(auto[0]) not in ('0', '', 'false')) else 3
    props = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(frequency), 0) FROM patterns "
        "WHERE kind='proposed_identity'").fetchone()
    deep_runs = conn.execute(
        "SELECT COUNT(*) FROM patterns WHERE kind LIKE 'deep\\_%' ESCAPE '\\'").fetchone()[0]
    print(f"           promotion path    {props[0]} proposed_identity pattern(s), "
          f"max frequency {props[1]} of {threshold} needed; "
          f"{deep_runs} deep_* pattern(s) "
          f"({'deep-consolidation has run' if deep_runs else 'no evidence it has run'})")
    if ident:
        srcs = sum(
            1 for (s,) in conn.execute("SELECT source FROM identity")
            if not re.match(HEAD_SOURCES_RE, s or ''))
        newest = conn.execute("SELECT MAX(created_at) FROM identity").fetchone()[0]
        print(f"           identity writers  {srcs} of {ident} row(s) carry a source no "
              f"code path at HEAD can emit; newest identity write {newest}")
        if srcs == ident:
            print("           -> the tier is WRITE-FROZEN: every row was written by a "
                  "retired writer, and the current promotion path has produced none of "
                  "them. 'Fixed content for N weeks' is the tier not being written, not "
                  "the tier being stable.")
            failed.append('identity-writer')
    else:
        if deep_runs and props[0] == 0:
            print("           -> the identity tier is empty and deep-consolidation HAS run "
                  "here without proposing one: the promotion path fired and yielded "
                  "nothing. The withhold arm has no treatment, and waiting is not the "
                  "remedy — this is scored regardless of store age.")
            failed.append('identity-tier')
        elif props[0]:
            print(f"           -> empty, but {props[0]} proposal(s) are accumulating; the "
                  f"tier is {threshold - props[1]} re-proposal(s) from its first promotion. "
                  f"Reported, not scored.")
        else:
            print(f"           -> empty, and deep-consolidation has not run here "
                  f"(store spans {lo} .. {hi}): 'not built yet' and 'not built' are still "
                  f"indistinguishable. NOT scored.")
    tgt = conn.execute("SELECT COUNT(*) FROM target_outcomes").fetchone()[0]
    vals = conn.execute("SELECT COUNT(DISTINCT last_success) FROM target_outcomes").fetchone()[0]
    print(f"           target_outcomes   {tgt:>7} rows, {vals} distinct last_success")
    if tgt and vals < 2:
        print("           -> last_success is a constant: it records that a target was seen, "
              "not that it succeeded")
        failed.append('target-outcomes')
    return failed


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
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    print_ref(conn, db)

    n_briefings, quota_failed = check_quota(conn)
    headroom_failed = check_headroom(conn, n_briefings)
    dim_failed = check_resolution(conn)
    channel_failed = check_channel(conn)

    print()
    if channel_failed:
        print(f"FAIL channel:    {', '.join(channel_failed)} -- this store cannot carry a "
              f"session-grain outcome; a design that needs no outcome column still needs "
              f"a channel")
    if quota_failed or headroom_failed or dim_failed or channel_failed:
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
