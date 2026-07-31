#!/usr/bin/env python3
"""
Audit the retrieval outcome instrument (`retrieval_log.relevant`).

WHY THIS EXISTS
---------------
`retrieval_log` records, for every memory surfaced into a session, an `estimate`
(the salience/confidence it was surfaced with) and an outcome `relevant` (did the
session act on it). It is the only estimate-vs-outcome loop in the store, and
PRD §8's recall-utility primitive was about to be built on top of it.

The outcome side is a token-overlap proxy (`memory.ts:scoreRetrievals`): a surfaced
memory counts as relevant if >=2 of its significant tokens reappear in later work in
the same cwd within 6h. That proxy has never had a negative control.

This script supplies one. It re-runs the scorer three ways against the live db:

  REAL         the memory that was actually surfaced
  PLACEBO      a DIFFERENT memory of the same kind and the same token length,
               scored against the same session
  CROSS-CWD    a different memory from an UNRELATED project directory

If REAL does not beat PLACEBO, the column is not measuring the item. It is
measuring how many tokens the item was allowed to contribute and what genre they
came from -- both item properties uncorrelated with usefulness.

STATUS: as of 2026-07-31 this gate FAILS for outcome-v1, by design. It is a live
gauge, not a green check. Any replacement outcome definition must make it pass
before a number from this table is quoted as evidence about a memory tier.

Usage:
    python3 scripts/audit_outcome_instrument.py [--db PATH] [--sample N] [--seed N]

Exit 0 if every kind with enough data shows a significant, material lift over
placebo; exit 1 otherwise.
"""

import argparse
import math
import os
import random
import re
import sqlite3
import sys

# --- mirrored verbatim from src/memory.ts:48-60. If those change, change these. ---
STOP_TOKENS = {
    'this', 'that', 'with', 'from', 'have', 'will', 'into', 'then', 'they', 'them', 'what',
    'when', 'which', 'were', 'been', 'your', 'about', 'there', 'these', 'would', 'could',
    'true', 'false', 'null', 'none', 'name', 'type', 'text', 'value', 'data', 'file', 'line',
}
_PATHISH = re.compile(r'[a-z0-9_.\-]+/[a-z0-9_./\-]+|[a-z0-9_\-]+\.[a-z0-9]{1,5}\b')
_WORDISH = re.compile(r'[a-z][a-z0-9_]{3,}')

OVERLAP_THRESHOLD = 2   # memory.ts:369  `overlap >= 2 ? 1 : 0`
WINDOW = '+6 hours'     # db.ts:396      getObsAfter

# A lift must be both statistically distinguishable from placebo and big enough to
# carry a claim. 5pp is the floor at which "the pattern tier is 9% relevant" would
# have meant anything about patterns.
MIN_LIFT_PP = 5.0
MAX_P = 0.01
MIN_N = 200


def sig_tokens(text):
    t = (text or '').lower()
    out = set(_PATHISH.findall(t))
    out.update(_WORDISH.findall(t))
    return out


# Store roots, newest naming first. The 8aacf1a rename (2026-07-31) moved the live
# store ~/.engram/**/engram.db -> ~/.snarc/**/snarc.db and deliberately left the old
# 1.1GB store in place as an archive. Both are real; which one you read is an axis of
# any number this script prints, so it is always named in the output and never guessed
# silently. The 10,715 scored pairs this audit was written against live ONLY in the
# archive -- the live store started at zero.
STORE_ROOTS = [('~/.snarc/projects', 'snarc.db', 'live'),
               ('~/.engram/projects', 'engram.db', 'ARCHIVE (pre-8aacf1a rename)')]


def find_stores():
    """Every store found, largest first, each tagged with which root it came from."""
    found = []
    for root, fname, label in STORE_ROOTS:
        r = os.path.expanduser(root)
        if not os.path.isdir(r):
            continue
        for d in sorted(os.listdir(r)):
            p = os.path.join(r, d, fname)
            if os.path.exists(p):
                found.append((p, label, os.path.getsize(p)))
    return found


def default_db():
    """Pick the store that actually has scored pairs; prefer live over archive on ties.

    Picking by file size alone would silently select the archive forever, since it is
    100x larger and will stay that way for months.
    """
    stores = find_stores()
    scored = []
    for p, label, size in stores:
        try:
            c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
            n = c.execute("SELECT COUNT(*) FROM retrieval_log WHERE relevant IS NOT NULL").fetchone()[0]
            c.close()
        except sqlite3.Error:
            n = 0
        scored.append((p, label, n))
    with_rows = [s for s in scored if s[2] > 0]
    if not with_rows:
        return (None, scored)
    # live root is listed first in STORE_ROOTS, so a stable sort on row count keeps it ahead
    best = max(with_rows, key=lambda s: s[2])
    return (best, scored)


class SessionVocab:
    """Later-work token set per (cwd, surfaced_ts). Cached: many rows share a session."""

    def __init__(self, conn):
        self.conn = conn
        self.cache = {}

    def get(self, cwd, ts):
        key = (cwd, ts)
        if key not in self.cache:
            rows = self.conn.execute(
                "SELECT input_summary, output_summary FROM observations "
                "WHERE cwd = ? AND ts > ? AND ts <= datetime(?, ?)",
                (cwd or '', ts, ts, WINDOW),
            ).fetchall()
            vocab = set()
            for a, b in rows:
                vocab |= sig_tokens(f"{a or ''} {b or ''}") - STOP_TOKENS
            self.cache[key] = vocab
        return self.cache[key]


def scores(tokens, vocab):
    return 1 if sum(1 for t in set(tokens) if t in vocab) >= OVERLAP_THRESHOLD else 0


def mcnemar(b, c):
    """Paired test on discordant pairs. Returns (chi2, p) with continuity correction."""
    if b + c == 0:
        return 0.0, 1.0
    chi2 = (abs(b - c) - 1) ** 2 / (b + c)
    return chi2, math.erfc(math.sqrt(chi2 / 2))


def audit(conn, sample, seed):
    rng = random.Random(seed)
    vocab = SessionVocab(conn)
    kinds = [r[0] for r in conn.execute(
        "SELECT DISTINCT item_kind FROM retrieval_log WHERE relevant IS NOT NULL ORDER BY 1")]

    results = []
    for kind in kinds:
        rows = conn.execute(
            "SELECT cwd, surfaced_ts, match_key FROM retrieval_log "
            "WHERE relevant IS NOT NULL AND item_kind = ?", (kind,)).fetchall()
        if not rows:
            continue

        # Index by token length so the placebo is length-matched: an unmatched placebo
        # would just re-measure the length effect this control exists to remove.
        by_len = {}
        for r in rows:
            by_len.setdefault(len(r[2].split(' ')), []).append(r)

        pop = rows if sample <= 0 or sample >= len(rows) else rng.sample(rows, sample)
        n = real = plac = cross = b = c = 0
        n_cross = no_placebo = 0
        for cwd, ts, mk in pop:
            toks = [t for t in (mk or '').split(' ') if t]
            if not toks:
                continue

            # A store can hold only one distinct item of a kind (e.g. a single identity
            # statement re-surfaced every session). Then no length-matched ALTERNATIVE
            # exists and the row is uncontrollable. Scoring the missing placebo as 0
            # would manufacture a lift out of the absence of a comparison -- which is the
            # exact failure mode this script exists to catch. Exclude and report instead.
            pool = [x for L in range(len(toks) - 3, len(toks) + 4) for x in by_len.get(L, ())
                    if x[2] != mk]
            if not pool:
                no_placebo += 1
                continue

            v = vocab.get(cwd, ts)
            n += 1
            R = scores(toks, v)
            real += R
            P = scores(rng.choice(pool)[2].split(' '), v)
            plac += P
            if R and not P:
                b += 1
            elif P and not R:
                c += 1

            xpool = [x for x in pool if x[0] != cwd]
            if xpool:
                cross += scores(rng.choice(xpool)[2].split(' '), v)
                n_cross += 1

        if n == 0:
            continue
        chi2, p = mcnemar(b, c)
        results.append({
            'kind': kind, 'n': n,
            'real': real / n * 100, 'placebo': plac / n * 100,
            'cross': cross / n_cross * 100 if n_cross else float('nan'),
            'lift': (real - plac) / n * 100, 'b': b, 'c': c, 'chi2': chi2, 'p': p,
            'no_placebo': no_placebo,
            'avg_tokens': sum(len(r[2].split(' ')) for r in pop) / len(pop),
        })
    return results


def all_stores(args):
    """Replicate the placebo control independently in every store that has enough data.

    Each per-project store is an independent sample: different repo, different vocabulary,
    different sessions. If the outcome column were measuring the item, the lift would
    survive in most of them.
    """
    stores = [(p, lab, n) for p, lab, n in
              [(p, lab, count_scored(p)) for p, lab, _ in find_stores()]
              if n >= args.min_store_n]
    if not stores:
        print(f"no store has >= {args.min_store_n} scored rows", file=sys.stderr)
        return 2

    print(f"Replicating the placebo control across {len(stores)} independent stores "
          f"(>= {args.min_store_n} scored rows each)\n")
    hdr = f"{'store':<14} {'kind':<12} {'n':>6} {'REAL':>7} {'PLACEBO':>8} {'lift':>8} {'p':>7}"
    print(hdr)
    print('-' * len(hdr))

    pooled = {}
    n_lifts = {}
    for path, label, _ in stores:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        tag = os.path.basename(os.path.dirname(path))[:12]
        try:
            for r in audit(conn, args.sample, args.seed):
                if r['n'] < 30:
                    continue
                excl = f"  excl={r['no_placebo']}" if r['no_placebo'] else ''
                print(f"{tag:<14} {r['kind']:<12} {r['n']:>6} {r['real']:>6.1f}% "
                      f"{r['placebo']:>7.1f}% {r['lift']:>+7.1f}pp {r['p']:>7.3f}{excl}")
                agg = pooled.setdefault(r['kind'], {'n': 0, 'b': 0, 'c': 0, 'real': 0.0, 'plac': 0.0})
                agg['n'] += r['n']
                agg['b'] += r['b']
                agg['c'] += r['c']
                agg['real'] += r['real'] * r['n'] / 100
                agg['plac'] += r['placebo'] * r['n'] / 100
                n_lifts.setdefault(r['kind'], []).append(r['lift'])
        except sqlite3.Error as e:
            print(f"{tag:<14} (skipped: {e})")
        finally:
            conn.close()

    print(f"\n{'POOLED':<14} {'kind':<12} {'n':>6} {'REAL':>7} {'PLACEBO':>8} {'lift':>8} {'p':>7}   stores with lift>=5pp")
    print('-' * 95)
    failed = []
    for kind, a in sorted(pooled.items()):
        real, plac = a['real'] / a['n'] * 100, a['plac'] / a['n'] * 100
        _, p = mcnemar(a['b'], a['c'])
        lifts = n_lifts[kind]
        material = sum(1 for L in lifts if L >= MIN_LIFT_PP)
        ok = (real - plac) >= MIN_LIFT_PP and p <= MAX_P
        if not ok:
            failed.append(kind)
        print(f"{'':<14} {kind:<12} {a['n']:>6} {real:>6.1f}% {plac:>7.1f}% "
              f"{real - plac:>+7.1f}pp {p:>7.3f}   {material}/{len(lifts)}")

    print()
    if failed:
        print(f"FAIL (pooled): outcome is item-blind for {', '.join(failed)} across "
              f"{len(stores)} independent stores.")
        return 1
    print("PASS (pooled).")
    return 0


def count_scored(path):
    try:
        c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        n = c.execute("SELECT COUNT(*) FROM retrieval_log WHERE relevant IS NOT NULL").fetchone()[0]
        c.close()
        return n
    except sqlite3.Error:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=None)
    ap.add_argument('--sample', type=int, default=0, help='0 = full population')
    ap.add_argument('--seed', type=int, default=23)
    ap.add_argument('--all-stores', action='store_true',
                    help='replicate across every per-project store, then pool. Turns a '
                         'single-seat finding into N independent ones.')
    ap.add_argument('--min-store-n', type=int, default=100,
                    help='with --all-stores, skip stores with fewer scored rows')
    args = ap.parse_args()

    if args.all_stores:
        return all_stores(args)

    if args.db:
        db, label, inventory = args.db, 'explicit --db', []
    else:
        best, inventory = default_db()
        if best is None:
            print("no store with scored retrieval_log rows found. Stores seen:", file=sys.stderr)
            for p, lab, n in inventory:
                print(f"  {p}  [{lab}]  scored={n}", file=sys.stderr)
            return 2
        db, label, _ = best

    if not os.path.exists(db):
        print(f"no store at {db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    print(f"db: {db}   [{label}]")
    others = [(p, lab, n) for p, lab, n in inventory if p != db and n > 0]
    if others:
        tot = sum(n for _, _, n in others)
        print(f"    ({len(others)} other stores hold {tot:,} more scored pairs — "
              f"--all-stores to replicate across them)")
    if 'ARCHIVE' in label:
        print("    NOTE: reading the archive. The live store has no scored pairs yet — but\n"
              "    scoreRetrievals() is unchanged by the rename, so it will refill with the\n"
              "    same item-blind definition unless that is fixed first.")
    print(f"outcome instrument: >={OVERLAP_THRESHOLD} shared significant tokens with "
          f"later same-cwd work within {WINDOW.strip('+')}\n")

    results = audit(conn, args.sample, args.seed)
    if not results:
        print("retrieval_log has no scored rows — nothing to audit.")
        return 2

    hdr = (f"{'kind':<12} {'n':>6} {'avg tok':>8} {'REAL':>7} {'PLACEBO':>8} "
           f"{'CROSS-CWD':>10} {'lift':>8} {'p':>8}  verdict")
    print(hdr)
    print('-' * len(hdr))

    failed = []
    for r in results:
        ok = r['n'] >= MIN_N and r['lift'] >= MIN_LIFT_PP and r['p'] <= MAX_P
        if r['n'] < MIN_N:
            verdict = 'INSUFFICIENT DATA'
        elif ok:
            verdict = 'discriminates'
        else:
            verdict = 'ITEM-BLIND'
            failed.append(r['kind'])
        excl = f"  (+{r['no_placebo']} rows had no length-matched alternative — excluded)" if r['no_placebo'] else ''
        print(f"{r['kind']:<12} {r['n']:>6} {r['avg_tokens']:>8.1f} {r['real']:>6.1f}% "
              f"{r['placebo']:>7.1f}% {r['cross']:>9.1f}% {r['lift']:>+7.1f}pp "
              f"{r['p']:>8.3f}  {verdict}{excl}")

    print()
    if failed:
        print(f"FAIL: outcome is item-blind for {', '.join(failed)} — a length-matched "
              f"random OTHER memory scores the same.")
        print("      Numbers from retrieval_log for these kinds describe token budget and "
              "genre, not usefulness.")
        print(f"      Bar to clear: lift >= {MIN_LIFT_PP}pp at p <= {MAX_P}.")
        return 1

    print("PASS: outcome discriminates the surfaced item from a length-matched placebo.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
