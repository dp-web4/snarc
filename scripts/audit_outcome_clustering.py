#!/usr/bin/env python3
"""
How many INDEPENDENT observations does `retrieval_log` actually contain?

Every power calculation either of us has quoted -- kimi's 3.4 days for the suppression
arm, my briefings-per-session pricing, every n= in every table -- treats a briefing row
as an independent draw. It is not. `relevant` is assigned by token overlap between a
match_key and the observations in one cwd, so two rows carrying the SAME key in the SAME
cwd are near-copies of each other, and the corpus is mostly repeats: the same memory is
re-surfaced into the same project over and over.

This prints the one-way random-effects ICC over (cwd, match_key) groups and the resulting
design effect, deff = 1 + (mbar - 1) * ICC. Effective n is n / deff.

It also prints the count of groups that are ENTIRELY 1 and ENTIRELY 0 -- the deterministic
tails. Note these are descriptive: selecting groups by their rate and then removing them
would be selecting on the outcome. The outcome-independent version of the same question is
audit_outcome_placement.py, which permutes the timing instead of removing rows.

Read-only. stdlib only.
Usage: audit_outcome_clustering.py
"""
import sqlite3, glob, os, collections

STORES = [
    ("archive", os.path.expanduser("~/.engram"), "engram.db"),
    ("live", os.path.expanduser("~/.snarc"), "snarc.db"),
]


def load(root, dbn):
    g = collections.defaultdict(lambda: [0, 0])
    for db in sorted(glob.glob(os.path.join(root, "projects", "*", dbn))):
        try:
            c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
            for cwd, mk, n, s in c.execute(
                "SELECT cwd, match_key, COUNT(*), SUM(relevant) FROM retrieval_log "
                "WHERE source='briefing' AND relevant IS NOT NULL GROUP BY cwd, match_key"
            ):
                k = (cwd or "", mk or "")
                g[k][0] += n
                g[k][1] += s or 0
            c.close()
        except Exception:
            continue
    return g


for name, root, dbn in STORES:
    g = load(root, dbn)
    if not g:
        print(f"{name}: empty")
        continue
    tot = sum(v[0] for v in g.values())
    ones = sum(v[1] for v in g.values())
    p = ones / tot
    multi = {k: v for k, v in g.items() if v[0] >= 2}
    mn = sum(v[0] for v in multi.values())
    print(f"\n=== {name}   n={tot}   relevant={p:.1%}")
    print(f"  distinct (cwd, match_key) groups : {len(g)}")
    print(f"  rows in groups of >=2            : {mn} ({mn/tot:.1%})")

    h = collections.Counter()
    for k, v in g.items():
        if v[0] >= 10:
            r = v[1] / v[0]
            h["all 1" if r == 1 else "all 0" if r == 0 else "mixed"] += 1
    print(f"  groups with n>=10, by rate       : {dict(h)}")

    ks = [v[0] for v in multi.values()]
    if len(ks) < 2:
        print("  too few multi-row groups for ICC")
        continue
    N = sum(ks)
    k = len(ks)
    n0 = (N - sum(x * x for x in ks) / N) / (k - 1)
    msb = sum(v[0] * ((v[1] / v[0]) - p) ** 2 for v in multi.values()) / (k - 1)
    msw = sum(
        v[1] * (1 - v[1] / v[0]) ** 2 + (v[0] - v[1]) * (v[1] / v[0]) ** 2
        for v in multi.values()
    ) / (N - k)
    icc = (msb - msw) / (msb + (n0 - 1) * msw)
    mbar = N / k
    deff = 1 + (mbar - 1) * icc
    print(f"  multi-row groups k={k}  mean size={mbar:.1f}")
    print(f"  ICC over (cwd, match_key)        : {icc:.3f}")
    print(f"  design effect                    : {deff:.1f}")
    print(f"  EFFECTIVE n                      : {tot}/{deff:.1f} = {tot/deff:.0f}")
    print(f"  CI width inflation vs naive      : {deff**0.5:.1f}x")
