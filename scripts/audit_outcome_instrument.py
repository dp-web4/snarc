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


def default_db():
    root = os.path.expanduser('~/.engram/projects')
    if not os.path.isdir(root):
        return None
    cands = [os.path.join(root, d, 'engram.db') for d in os.listdir(root)]
    cands = [c for c in cands if os.path.exists(c)]
    return max(cands, key=os.path.getsize) if cands else None


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
        stored_agree = 0
        for cwd, ts, mk in pop:
            toks = [t for t in (mk or '').split(' ') if t]
            if not toks:
                continue
            v = vocab.get(cwd, ts)
            n += 1

            R = scores(toks, v)
            real += R

            pool = [x for L in range(len(toks) - 3, len(toks) + 4) for x in by_len.get(L, ())
                    if x[2] != mk]
            P = scores(rng.choice(pool)[2].split(' '), v) if pool else 0
            plac += P
            if R and not P:
                b += 1
            elif P and not R:
                c += 1

            xpool = [x for x in pool if x[0] != cwd]
            if xpool:
                cross += scores(rng.choice(xpool)[2].split(' '), v)
                stored_agree += 1

        if n == 0:
            continue
        chi2, p = mcnemar(b, c)
        results.append({
            'kind': kind, 'n': n,
            'real': real / n * 100, 'placebo': plac / n * 100,
            'cross': cross / stored_agree * 100 if stored_agree else float('nan'),
            'lift': (real - plac) / n * 100, 'b': b, 'c': c, 'chi2': chi2, 'p': p,
            'avg_tokens': sum(len(r[2].split(' ')) for r in pop) / len(pop),
        })
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=None)
    ap.add_argument('--sample', type=int, default=0, help='0 = full population')
    ap.add_argument('--seed', type=int, default=23)
    args = ap.parse_args()

    db = args.db or default_db()
    if not db or not os.path.exists(db):
        print(f"no engram db found (looked at {db})", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    print(f"db: {db}")
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
        print(f"{r['kind']:<12} {r['n']:>6} {r['avg_tokens']:>8.1f} {r['real']:>6.1f}% "
              f"{r['placebo']:>7.1f}% {r['cross']:>9.1f}% {r['lift']:>+7.1f}pp "
              f"{r['p']:>8.3f}  {verdict}")

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
