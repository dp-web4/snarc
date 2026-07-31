#!/usr/bin/env python3
"""
Is the live-vs-archive outcome gap a property of the store, or of who was using it?

kimi's 57.0% (live) vs 89.4% (archive) `relevant=1` rate has been read as session shape
("shallower follow-up work per session"), and their reply of 2026-07-31 adds that 57.8% of
the live 0-class is structural (empty reconstructed window) against 8.2% in the archive.
Both readings treat the two stores as the same population measured at two times.

They are not. `~/.snarc` holds ONE day (2026-07-31), and that day is the day two agents
spent auditing this store. This script tests the third explanation before either of the
other two: the corpus MIX changed.

Three outputs, one population throughout (source='briefing', relevant IS NOT NULL):

  1. per-cwd `relevant=1` rate and share of corpus, both stores
  2. direct standardization -- apply the ARCHIVE's per-cwd rates to the LIVE cwd mix.
     The gap between that and the archive's own rate is the part of the drop that is
     composition alone, with every within-cwd rate held at its archive value.
  3. structural-zero subtraction on the reconstructable subset (same window
     reconstruction as audit_outcome_censoring.py), per store AND per cwd, so the
     "57.8% of live zeros are harness" number can be attributed to a cwd rather than
     to the store.

Read-only. stdlib only.
Usage: audit_live_archive_composition.py
"""
import sqlite3, glob, sys, os, bisect, collections
from datetime import datetime, timedelta

STORES = [
    ("archive", os.path.expanduser("~/.engram"), "engram.db"),
    ("live", os.path.expanduser("~/.snarc"), "snarc.db"),
]


def parse(ts):
    if not ts:
        return None
    ts = ts.strip().replace("T", " ").replace("Z", "")
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts[:26], f)
        except ValueError:
            pass
    return None


def scan(root, dbname):
    """-> rows: list of (cwd, relevant, n_obs_in_window|None)"""
    out = []
    for db in sorted(glob.glob(os.path.join(root, "projects", "*", dbname))):
        try:
            c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
            rows = c.execute(
                "SELECT surfaced_ts, cwd, relevant FROM retrieval_log "
                "WHERE source='briefing' AND relevant IS NOT NULL"
            ).fetchall()
            if not rows:
                c.close()
                continue
            # shard-wide ends: getUnscoredRetrievals (db.ts:752) has no cwd predicate
            ends = sorted(
                d
                for (e,) in c.execute(
                    "SELECT ended_at FROM sessions WHERE ended_at IS NOT NULL"
                )
                for d in [parse(e)]
                if d
            )
            obs = collections.defaultdict(list)
            for cwd, t in c.execute("SELECT cwd, ts FROM observations"):
                d = parse(t)
                if d:
                    obs[cwd or ""].append(d)
            for v in obs.values():
                v.sort()
            c.close()
        except Exception:
            continue
        for sts, cwd, rel in rows:
            s = parse(sts)
            if not s:
                continue
            cwd = cwd or ""
            n_obs = None  # window not reconstructable
            i = bisect.bisect_left(ends, s + timedelta(seconds=60))
            if i < len(ends):
                close = max(min(ends[i], s + timedelta(hours=6)), s)
                ol = obs.get(cwd, [])
                n_obs = bisect.bisect_right(ol, close) - bisect.bisect_right(ol, s)
            out.append((cwd, int(rel or 0), n_obs))
    return out


data = {name: scan(root, dbn) for name, root, dbn in STORES}

# ---------------------------------------------------------------- 1. per-cwd
print("=" * 78)
print("1. PER-CWD RATE AND SHARE  (source='briefing', relevant IS NOT NULL)")
print("=" * 78)

per = {}
for name in data:
    d = collections.defaultdict(lambda: [0, 0])
    for cwd, rel, _ in data[name]:
        d[cwd][0] += 1
        d[cwd][1] += rel
    per[name] = d
    n = sum(v[0] for v in d.values())
    r = sum(v[1] for v in d.values())
    print(f"\n{name}: n={n}  relevant={r/n:.1%}")

allcwd = sorted(
    set(per["archive"]) | set(per["live"]),
    key=lambda k: -per["live"].get(k, [0])[0],
)
print(f"\n{'cwd':<58} {'live n':>7} {'live%1':>7} {'live sh':>8} {'arch n':>7} {'ar%1':>6} {'ar sh':>7}")
nl = sum(v[0] for v in per["live"].values())
na = sum(v[0] for v in per["archive"].values())
for cwd in allcwd[:14]:
    ln, lr = per["live"].get(cwd, [0, 0])
    an, ar = per["archive"].get(cwd, [0, 0])
    print(
        f"{(cwd or '(empty)'):<58} {ln:>7} "
        f"{(f'{lr/ln:.1%}' if ln else '-'):>7} {ln/nl:>7.1%} "
        f"{an:>7} {(f'{ar/an:.1%}' if an else '-'):>6} {an/na:>7.1%}"
    )

# ------------------------------------------------- 2. direct standardization
print("\n" + "=" * 78)
print("2. DIRECT STANDARDIZATION -- archive rates, live mix")
print("=" * 78)

arch_rate = sum(v[1] for v in per["archive"].values()) / na
live_rate = sum(v[1] for v in per["live"].values()) / nl

num = den = 0
unmatched = 0
for cwd, (ln, _) in per["live"].items():
    an, ar = per["archive"].get(cwd, [0, 0])
    if an == 0:
        unmatched += ln
        continue
    num += ln * (ar / an)
    den += ln
std = num / den if den else float("nan")

print(f"archive rate, archive mix (observed)      : {arch_rate:.1%}  n={na}")
print(f"archive rate, LIVE mix    (standardized)  : {std:.1%}  n={den}"
      f"   [{unmatched} live rows in cwds absent from archive, dropped]")
print(f"live rate,    live mix    (observed)      : {live_rate:.1%}  n={nl}")
gap = arch_rate - live_rate
comp = arch_rate - std
print(f"\ntotal gap                : {gap*100:.1f} points")
print(f"  composition alone      : {comp*100:.1f} points  ({comp/gap:.0%} of the gap)")
print(f"  residual (within-cwd)  : {(gap-comp)*100:.1f} points  ({(gap-comp)/gap:.0%})")

# --------------------------------------------- 3. structural zeros, per cwd
print("\n" + "=" * 78)
print("3. STRUCTURAL ZEROS (reconstructable window held 0 eligible observations)")
print("=" * 78)
for name in ("archive", "live"):
    rec = [(c, r, n) for c, r, n in data[name] if n is not None]
    z = [x for x in rec if x[1] == 0]
    sz = [x for x in z if x[2] == 0]
    keep = [x for x in rec if not (x[1] == 0 and x[2] == 0)]
    kr = sum(x[1] for x in keep) / len(keep) if keep else float("nan")
    print(
        f"\n{name}: reconstructable n={len(rec)}  relevant={sum(x[1] for x in rec)/len(rec):.1%}"
        f"\n  zeros={len(z)}  structural={len(sz)} ({len(sz)/len(z):.1%} of the 0 class)"
        f"\n  relevant with structural zeros REMOVED: {kr:.1%}  (n={len(keep)})"
    )
    d = collections.defaultdict(lambda: [0, 0])
    for c, r, n in rec:
        d[c][0] += 1
        d[c][1] += 1 if (r == 0 and n == 0) else 0
    top = sorted(d.items(), key=lambda kv: -kv[1][1])[:6]
    for c, (tn, ts_) in top:
        if ts_:
            print(f"    {ts_:>5} structural of {tn:>5} rows  ({ts_/len(sz):.0%} of this store's structural zeros)  {c or '(empty)'}")

print("\n" + "=" * 78)
print("4. ADJUSTED GAP -- structural zeros removed from BOTH stores, then standardized")
print("=" * 78)
adj = {}
for name in ("archive", "live"):
    rec = [(c, r, n) for c, r, n in data[name] if n is not None]
    keep = [x for x in rec if not (x[1] == 0 and x[2] == 0)]
    d = collections.defaultdict(lambda: [0, 0])
    for c, r, _ in keep:
        d[c][0] += 1
        d[c][1] += r
    adj[name] = d
na2 = sum(v[0] for v in adj["archive"].values())
nl2 = sum(v[0] for v in adj["live"].values())
a2 = sum(v[1] for v in adj["archive"].values()) / na2
l2 = sum(v[1] for v in adj["live"].values()) / nl2
num = den = 0
for cwd, (ln, _) in adj["live"].items():
    an, ar = adj["archive"].get(cwd, [0, 0])
    if an:
        num += ln * (ar / an)
        den += ln
s2 = num / den if den else float("nan")
print(f"archive (no structural zeros)             : {a2:.1%}  n={na2}")
print(f"archive rates on LIVE mix (standardized)  : {s2:.1%}  n={den}")
print(f"live    (no structural zeros)             : {l2:.1%}  n={nl2}")
g2 = a2 - l2
c2 = a2 - s2
print(f"\ngap after removing structural zeros : {g2*100:.1f} points")
print(f"  composition alone                 : {c2*100:.1f} points  ({c2/g2:.0%})")
print(f"  residual                          : {(g2-c2)*100:.1f} points  ({(g2-c2)/g2:.0%})")
