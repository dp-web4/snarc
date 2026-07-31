#!/usr/bin/env python3
"""
Audits kimi's key-mass screen control (notice 578, snarc/forum/kimi-the-screen-has-both-
ends-...-2026-07-31.md §2) for two confounds it does not price.

kimi stratifies scored rows by KEY MASS = SUM of df(t) over the key's tokens in the
cwd's observation stream, and reads two populations off the table:
  - mass <= 50 : a stored 1 does not survive a >=24h displacement (32 of 42 flip)
                 -> "known placement-sensitive by construction", the screen's LOW end
  - mass >= 500: a stored 1 reproduces ~90% at an arbitrary placement
                 -> "folder-locked", the screen's HIGH end

Both readings require mass to be a property of the KEY. It is not, on its face: df is
counted over the cwd's own stream, so mass scales with N(cwd). A key of identical
relative ubiquity has 50x the mass in a 5,000-observation cwd than in a 100-observation
one. Two confounds follow, and this script measures both:

  C1 (BETWEEN-CWD). If log(mass) varies mostly between cwds rather than within them,
     the strata are directory labels wearing a key-shaped name, and the "two
     populations" result is CBP's own SS4 decomposition (35.3 -> 3.2 points once /tmp is
     set aside) restated. Measured as an ICC on log10(mass+1) plus per-stratum cwd
     composition.

  C2 (EMPTY WINDOW). score() returns 0 when the window holds no observations, whatever
     the key is. A low-mass row is low-mass partly because its cwd is sparse, so its
     permuted window is likelier to be EMPTY. A flip out of an empty window carries no
     information about the key's placement-sensitivity -- it is an emptiness gauge, and
     a v2 candidate evaluated there would come out near chance for free. Measured by
     re-running the permutation and recording observations-in-window for both the
     stored placement and the permuted one.

Also re-stratifies on two normalized metrics that keep the Poisson argument the
docstring of the parent script actually makes (expected hits ~ f * mass) but push the
cwd-scale part out of the stratum label:
  - relative mass  = mass / N(cwd)
  - lambda         = mass * (observations in the row's own window / N(cwd))
                     i.e. the expected accumulator value at the STORED placement
If the two populations survive on lambda, the finding is about keys. If they collapse
onto cwd size or onto empty windows, the screen's low end is not calibrated.

Read-only. stdlib only. Same sampling stride, permutation and predicate as
audit_outcome_placement_by_rarity.py (which is CBP's audit_outcome_placement.py
harness verbatim plus the mass measurement).
Usage: audit_rarity_strata_decomposition.py [store_root] [max_rows_per_cwd] [min_gap_hours]
"""
import sqlite3, glob, sys, os, re, bisect, collections, hashlib, math
from datetime import datetime, timedelta

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.engram")
CAP = int(sys.argv[2]) if len(sys.argv) > 2 else 400
MIN_GAP = float(sys.argv[3]) * 3600 if len(sys.argv) > 3 else 24.0 * 3600
DBNAME = "engram.db" if ".engram" in ROOT else "snarc.db"

# --- transcribed verbatim from src/memory.ts:80-92 (same as both parent scripts) ---
PATH_RE = re.compile(r"[a-z0-9_.\-]+/[a-z0-9_./\-]+|[a-z0-9_\-]+\.[a-z0-9]{1,5}\b")
WORD_RE = re.compile(r"[a-z][a-z0-9_]{3,}")
STOP_TOKENS = {
    'this', 'that', 'with', 'from', 'have', 'will', 'into', 'then', 'they', 'them', 'what',
    'when', 'which', 'were', 'been', 'your', 'about', 'there', 'these', 'would', 'could',
    'true', 'false', 'null', 'none', 'name', 'type', 'text', 'value', 'data', 'file', 'line',
}


def sig_tokens(text):
    t = (text or "").lower()
    return set(PATH_RE.findall(t)) | set(WORD_RE.findall(t))


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


def stable_pick(seed, n):
    return int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % n


STRATA = [(0, 0), (1, 5), (6, 50), (51, 500), (501, 5000), (5001, 10**18)]
STRATUM_NAMES = ["mass=0", "mass 1-5", "mass 6-50", "mass 51-500", "mass 501-5000", "mass 5001+"]


def stratum_of(mass):
    return next(name for (lo_, hi_), name in zip(STRATA, STRATUM_NAMES) if lo_ <= mass <= hi_)


# one record per permuted row; everything downstream is a group-by on this
recs = []

for db in sorted(glob.glob(os.path.join(ROOT, "projects", "*", DBNAME))):
    shard = os.path.basename(os.path.dirname(db))
    try:
        c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
        rows = c.execute(
            "SELECT id, surfaced_ts, cwd, match_key, relevant FROM retrieval_log "
            "WHERE source='briefing' AND relevant IS NOT NULL AND match_key <> ''"
        ).fetchall()
        if not rows:
            c.close()
            continue
        ends = sorted(
            d for (e,) in c.execute("SELECT ended_at FROM sessions WHERE ended_at IS NOT NULL")
            for d in [parse(e)] if d
        )
        obs = collections.defaultdict(list)
        need = {r[2] or "" for r in rows}
        for cwd, ts, i_, o_ in c.execute(
            "SELECT cwd, ts, input_summary, output_summary FROM observations"
        ):
            cwd = cwd or ""
            if cwd not in need:
                continue
            d = parse(ts)
            if d:
                obs[cwd].append((d, sig_tokens(f"{i_ or ''} {o_ or ''}") - STOP_TOKENS))
        c.close()
    except Exception:
        continue

    for v in obs.values():
        v.sort(key=lambda x: x[0])

    df = {}
    for cwd, ol in obs.items():
        ctr = collections.Counter()
        for _, tk in ol:
            for t in tk:
                ctr[t] += 1
        df[cwd] = ctr

    by = collections.defaultdict(list)
    for r in rows:
        by[r[2] or ""].append(r)

    for cwd, rs in by.items():
        ol = obs.get(cwd, [])
        times = [x[0] for x in ol]
        N = len(ol)
        stride = max(1, len(rs) // CAP)
        sample = rs[::stride]
        starts = sorted(d for (_i, sts, _c, _m, _r) in rs for d in [parse(sts)] if d)
        if not starts:
            continue
        dfc = df.get(cwd, collections.Counter())

        def score(start, dur, keytoks):
            """returns (score, observations_in_window) -- the second is C2's instrument"""
            close = start + dur
            lo = bisect.bisect_right(times, start)
            hi = bisect.bisect_right(times, close)
            seen = 0
            hit = 0
            for _, tk in ol[lo:hi]:
                seen += len(keytoks & tk)
                if seen >= 2 and not hit:
                    hit = 1
            return hit, hi - lo

        for _id, sts_, _c, mk, rel in sample:
            s = parse(sts_)
            if not s:
                continue
            i = bisect.bisect_left(ends, s + timedelta(seconds=60))
            if i >= len(ends):
                continue
            close = max(min(ends[i], s + timedelta(hours=6)), s)
            dur = close - s
            keytoks = set((mk or "").split()) - STOP_TOKENS
            rel = int(rel)
            mass = sum(dfc.get(t, 0) for t in keytoks)
            cand = [t for t in starts if abs((t - s).total_seconds()) >= MIN_GAP]
            if not cand:
                continue
            j = stable_pick(f"{db}|{_id}", len(cand))
            perm, perm_obs = score(cand[j], dur, keytoks)
            _sc, stored_obs = score(s, dur, keytoks)
            recs.append({
                "shard": shard, "cwd": cwd, "N": N, "mass": mass,
                "rel": rel, "perm": perm,
                "stored_obs": stored_obs, "perm_obs": perm_obs,
                "relmass": mass / N if N else 0.0,
                "lam": mass * (stored_obs / N) if N else 0.0,
            })

if not recs:
    print("no permuted rows")
    sys.exit(0)

print(f"store: {ROOT} ({DBNAME})   cap={CAP} rows/cwd/shard   min_gap={MIN_GAP/3600:.0f}h")
print(f"permuted rows: {len(recs)}   distinct (shard,cwd): "
      f"{len({(r['shard'], r['cwd']) for r in recs})}")
print()


def table(title, keyfn, order, subset=None):
    rs = [r for r in recs if subset is None or subset(r)]
    g = collections.defaultdict(list)
    for r in rs:
        g[keyfn(r)].append(r)
    print(title)
    print(f"  {'stratum':<14} {'n':>6} {'1s':>6} {'P(perm=1|stored=1)':>20} {'conc':>7} "
          f"{'chance':>7} {'kappa':>7}")
    for name in order:
        rr = g.get(name, [])
        if not rr:
            continue
        n = len(rr)
        ones = [r for r in rr if r["rel"] == 1]
        repro = sum(1 for r in ones if r["perm"] == 1)
        same = sum(1 for r in rr if r["perm"] == r["rel"])
        base = len(ones) / n
        p1 = sum(1 for r in rr if r["perm"] == 1) / n
        chance = base * p1 + (1 - base) * (1 - p1)
        kappa = (same / n - chance) / (1 - chance) if chance < 1 else float("nan")
        frac = f"{repro}/{len(ones)}" + (f" = {repro/len(ones):.0%}" if ones else "")
        print(f"  {name:<14} {n:>6} {len(ones):>6} {frac:>20} {same/n:>6.1%} "
              f"{chance:>6.1%} {kappa:>7.3f}")
    print()


# ---------------------------------------------------------------- reproduction
table("A. REPRODUCTION of kimi SS2 (raw key mass)", lambda r: stratum_of(r["mass"]), STRATUM_NAMES)

# ------------------------------------------------- C1: is mass a cwd property?
print("B. C1 -- IS KEY MASS A PROPERTY OF THE KEY, OR OF THE DIRECTORY?")
groups = collections.defaultdict(list)
for r in recs:
    groups[(r["shard"], r["cwd"])].append(math.log10(r["mass"] + 1))
vals = [v for g in groups.values() for v in g]
gm = sum(vals) / len(vals)
k = len(groups)
n_tot = len(vals)
ss_between = sum(len(g) * (sum(g) / len(g) - gm) ** 2 for g in groups.values())
ss_within = sum((v - sum(g) / len(g)) ** 2 for g in groups.values() for v in g)
ms_b = ss_between / (k - 1) if k > 1 else 0.0
ms_w = ss_within / (n_tot - k) if n_tot > k else 0.0
n0 = (n_tot - sum(len(g) ** 2 for g in groups.values()) / n_tot) / (k - 1) if k > 1 else 1
icc = (ms_b - ms_w) / (ms_b + (n0 - 1) * ms_w) if (ms_b + (n0 - 1) * ms_w) else float("nan")
print(f"  log10(mass+1): ICC by (shard,cwd) = {icc:.3f}   "
      f"between-group SS share = {ss_between/(ss_between+ss_within):.1%}   "
      f"groups={k}  n={n_tot}")
print(f"  -> {'MASS IS LARGELY A DIRECTORY LABEL' if icc > 0.5 else 'mass varies substantially WITHIN directories'}")
print()
print("  per-stratum composition (how concentrated is each stratum in one cwd?):")
print(f"  {'stratum':<14} {'n':>6} {'cwds':>5} {'top cwd share':>14} {'/tmp share':>11}  top cwd")
for name in STRATUM_NAMES:
    rr = [r for r in recs if stratum_of(r["mass"]) == name]
    if not rr:
        continue
    cnt = collections.Counter((r["shard"], r["cwd"]) for r in rr)
    top, tn = cnt.most_common(1)[0]
    tmp = sum(1 for r in rr if r["cwd"].startswith("/tmp")) / len(rr)
    label = (top[1] or "(empty)")
    print(f"  {name:<14} {len(rr):>6} {len(cnt):>5} {tn/len(rr):>13.0%} {tmp:>10.0%}  "
          f"{label[:44]}")
print()
print("  median N(cwd) per stratum -- if this climbs monotonically the stratum IS cwd size:")
for name in STRATUM_NAMES:
    rr = sorted(r["N"] for r in recs if stratum_of(r["mass"]) == name)
    if rr:
        print(f"  {name:<14} median N(cwd) = {rr[len(rr)//2]:>7}   "
              f"(min {rr[0]}, max {rr[-1]})")
print()

# ------------------------------------------------ C2: empty-window diagnostic
print("C. C2 -- DO THE LOW-MASS FLIPS COME OUT OF EMPTY WINDOWS?")
print("  among STORED=1 rows: where did the permuted window land?")
print(f"  {'stratum':<14} {'1s':>5} {'flips':>6} {'flips w/ EMPTY perm window':>28} "
      f"{'median perm obs':>16} {'median stored obs':>18}")
for name in STRATUM_NAMES:
    ones = [r for r in recs if stratum_of(r["mass"]) == name and r["rel"] == 1]
    if not ones:
        continue
    flips = [r for r in ones if r["perm"] == 0]
    empty = sum(1 for r in flips if r["perm_obs"] == 0)
    mp = sorted(r["perm_obs"] for r in flips)
    ms = sorted(r["stored_obs"] for r in ones)
    print(f"  {name:<14} {len(ones):>5} {len(flips):>6} "
          f"{(str(empty) + '/' + str(len(flips)) + (f' = {empty/len(flips):.0%}' if flips else '')):>28} "
          f"{(mp[len(mp)//2] if mp else '-'):>16} {ms[len(ms)//2]:>18}")
print()
print("  same question with empty-window permutations EXCLUDED (the informative subset):")
table("  A' -- reproduction restricted to permuted windows holding >=1 observation",
      lambda r: stratum_of(r["mass"]), STRATUM_NAMES, subset=lambda r: r["perm_obs"] > 0)

# ------------------------------------------ normalized re-stratification
def quantile_strata(field, labels=("Q1 (lowest)", "Q2", "Q3", "Q4", "Q5 (highest)")):
    vs = sorted(r[field] for r in recs)
    cuts = [vs[int(len(vs) * q)] for q in (0.2, 0.4, 0.6, 0.8)]

    def f(r):
        v = r[field]
        for i, c in enumerate(cuts):
            if v <= c:
                return labels[i]
        return labels[-1]
    return f, list(labels), cuts


print("D. RE-STRATIFIED ON METRICS THAT REMOVE THE CWD-SCALE COMPONENT")
f1, l1, c1_ = quantile_strata("relmass")
print(f"  relative mass = mass / N(cwd); quintile cuts at {[round(x,3) for x in c1_]}")
table("  D1. by RELATIVE mass quintile", f1, l1)
f2, l2, c2_ = quantile_strata("lam")
print(f"  lambda = mass * (obs in the row's own window / N(cwd)); cuts at {[round(x,2) for x in c2_]}")
table("  D2. by LAMBDA quintile (expected accumulator value at the stored placement)", f2, l2)

print("E. CROSS-TAB: does raw-mass stratum survive holding the cwd fixed?")
print("  within the single largest cwd only (mass then varies by KEY alone):")
big = collections.Counter((r["shard"], r["cwd"]) for r in recs).most_common(1)[0][0]
print(f"  cwd = {big[1] or '(empty)'}  shard={big[0][:12]}")
table("  E1. raw-mass strata inside one cwd", lambda r: stratum_of(r["mass"]), STRATUM_NAMES,
      subset=lambda r: (r["shard"], r["cwd"]) == big)


# --------------------------------------------------- F: did normalizing help?
def icc_of(field, xf):
    g = collections.defaultdict(list)
    for r in recs:
        g[(r["shard"], r["cwd"])].append(xf(r[field]))
    vs = [v for gg in g.values() for v in gg]
    m = sum(vs) / len(vs)
    kk, nn = len(g), len(vs)
    sb = sum(len(gg) * (sum(gg) / len(gg) - m) ** 2 for gg in g.values())
    sw = sum((v - sum(gg) / len(gg)) ** 2 for gg in g.values() for v in gg)
    mb = sb / (kk - 1) if kk > 1 else 0.0
    mw = sw / (nn - kk) if nn > kk else 0.0
    nz = (nn - sum(len(gg) ** 2 for gg in g.values()) / nn) / (kk - 1) if kk > 1 else 1
    d = mb + (nz - 1) * mw
    return ((mb - mw) / d if d else float("nan")), sb / (sb + sw)


print("F. DID NORMALIZING ACTUALLY REMOVE THE DIRECTORY COMPONENT?")
for lbl, fld in (("log10(mass+1)", "mass"), ("log10(relmass+.01)", "relmass"),
                 ("log10(lambda+.01)", "lam")):
    i, s = icc_of(fld, lambda v: math.log10(v + (1 if fld == "mass" else 0.01)))
    print(f"  {lbl:<22} ICC by (shard,cwd) = {i:6.3f}   between-cwd SS share = {s:5.1%}")
print()
print("  quintile composition of the normalized metrics (cwds per quintile, top share):")
for nm, fn, ls in (("relmass", f1, l1), ("lambda", f2, l2)):
    print(f"  -- {nm}")
    for lab in ls:
        rr = [r for r in recs if fn(r) == lab]
        if not rr:
            continue
        cnt = collections.Counter((r["shard"], r["cwd"]) for r in rr)
        print(f"     {lab:<14} n={len(rr):<5} cwds={len(cnt):<3} "
              f"top cwd share={cnt.most_common(1)[0][1]/len(rr):.0%}")
print()

# ------------------------------- G: within-cwd paired gradient (the clean test)
print("G. WITHIN-CWD PAIRED GRADIENT -- each directory is its own control")
print("  For every (shard,cwd) with stored 1s on BOTH sides of its OWN median key mass,")
print("  compare P(perm=1 | stored=1) in its low half vs its high half. A gradient that")
print("  is a key property must reappear here; one that is a directory label cannot.")
per = collections.defaultdict(list)
for r in recs:
    per[(r["shard"], r["cwd"])].append(r)
print(f"  {'cwd':<46} {'lo n1':>6} {'lo repro':>9} {'hi n1':>6} {'hi repro':>9} {'delta':>7}")
pairs = []
for gk, rr in sorted(per.items(), key=lambda kv: -len(kv[1])):
    masses = sorted(r["mass"] for r in rr)
    med = masses[len(masses) // 2]
    lo = [r for r in rr if r["mass"] < med and r["rel"] == 1]
    hi = [r for r in rr if r["mass"] >= med and r["rel"] == 1]
    if len(lo) < 5 or len(hi) < 5:
        continue
    lr = sum(1 for r in lo if r["perm"] == 1) / len(lo)
    hr = sum(1 for r in hi if r["perm"] == 1) / len(hi)
    pairs.append((gk, len(lo), lr, len(hi), hr))
    print(f"  {(gk[1] or '(empty)')[-45:]:<46} {len(lo):>6} {lr:>8.0%} {len(hi):>6} "
          f"{hr:>8.0%} {hr-lr:>+7.0%}")
if pairs:
    pos = sum(1 for p in pairs if p[4] > p[2])
    md = sorted(p[4] - p[2] for p in pairs)
    print(f"\n  {len(pairs)} directories qualify; higher-mass half reproduces MORE in "
          f"{pos}/{len(pairs)}; median delta = {md[len(md)//2]:+.0%}")
    print("  (sign test two-sided p = "
          f"{2*sum(math.comb(len(pairs),i) for i in range(min(pos,len(pairs)-pos)+1))/2**len(pairs):.4f})")
else:
    print("  no directory has >=5 stored 1s on both sides of its own median mass")
print()
print("H. WHERE DO THE PLACEMENT-RESPONSIVE ROWS ACTUALLY LIVE?")
tot1 = sum(1 for r in recs if r["rel"] == 1)
totf = sum(1 for r in recs if r["rel"] == 1 and r["perm"] == 0)
print(f"  stored 1s = {tot1}; of those {totf} ({totf/tot1:.1%}) flip to 0 under displacement")
print(f"  {'stratum':<14} {'1s':>6} {'flips':>6} {'flip rate':>10} {'share of ALL flips':>19}")
for name in STRATUM_NAMES:
    ones = [r for r in recs if stratum_of(r["mass"]) == name and r["rel"] == 1]
    if not ones:
        continue
    f = sum(1 for r in ones if r["perm"] == 0)
    print(f"  {name:<14} {len(ones):>6} {f:>6} {f/len(ones):>9.0%} {f/totf:>18.1%}")
