#!/usr/bin/env python3
"""
Calibrates the LOW end of the placement screen (cbp, notice 571, §5).

CBP's placement permutation puts v1 at 80.7% concordance (kappa 0.591) on the
archive: four times in five the outcome column would have said the same thing had
the memory been surfaced at an unrelated moment in the same cwd. That is one end
of the scale. The screen CBP proposes -- run candidate v2 outcome definitions
through the same permutation and keep the ones that come out near chance -- needs
the OTHER end: a measurement on this store, with this harness, that is known to
be session-sensitive and must come out near chance. Without it a low concordance
cannot be distinguished from a noisy candidate.

This script builds that control out of v1 itself, by stratification rather than
by a new definition. scoreRetrievals' overlap>=2 predicate can only respond to
timing if the key's tokens are RARE in the cwd's observation stream: a key whose
most discriminative token appears in 3 observations can score 1 only when the
window covers those observations; a key whose tokens appear in every observation
scores 1 regardless of placement. So:

  - compute, per (shard, cwd), the document frequency df(t) of every token over
    that cwd's observations (same observation load as audit_outcome_placement.py)
  - per scored row, rarity = KEY MASS = SUM of df over the key's tokens. The
    first version of this script used MIN df, and it does not separate (df=0
    stratum: 78.3% concordance, kappa 0.562) -- because scoreRetrievals needs
    overlap >= 2, not all-tokens, and accumulates overlap ACROSS observations in
    the window, so the match is carried by the key's most COMMON tokens and the
    rarest token is inert. Key mass approximates the Poisson rate of that
    accumulator: a window of duration d covers ~f of the stream and expects
    ~f * (key mass) hits; low-mass keys can only score 1 in specific placements.
  - run CBP's exact permutation (same key, same cwd, same window duration,
    placement moved to another scored row's surfaced_ts, >=24h displacement) and
    report concordance / chance / kappa PER RARITY STRATUM

Predictions that would calibrate the screen:
  - high-df stratum: concordance near 1 (folder-determined; this is most of the
    corpus and drives the 80.7% headline)
  - low-df stratum: concordance near chance for stored=1 rows (the tokens
    physically cannot be matched outside the sessions that produced them) --
    the screen's low end, built from data the store already has
If even the low-df stratum sits near 1, token-overlap outcomes are dead at any
definition and the screen question is settled before it starts.

Not claimed: df is computed on the reconstructed observation stream, which the
self-wipe (INSERT OR REPLACE on session end) has already thinned -- df is a lower
bound on the true ubiquity of a token, so strata are shifted toward "rarer than
truth". The gradient direction is what matters, and a token rare in the thinned
stream was rare in the real one.

Read-only. stdlib only. Sampled, deterministic stride, same as the parent script.
Usage: audit_outcome_placement_by_rarity.py [store_root] [max_rows_per_cwd] [min_gap_hours]
"""
import sqlite3, glob, sys, os, re, bisect, collections, hashlib
from datetime import datetime, timedelta

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.engram")
CAP = int(sys.argv[2]) if len(sys.argv) > 2 else 400
MIN_GAP = float(sys.argv[3]) * 3600 if len(sys.argv) > 3 else 24.0 * 3600
DBNAME = "engram.db" if ".engram" in ROOT else "snarc.db"

# --- transcribed verbatim from src/memory.ts:80-92 (same as parent script) ---
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

# per stratum: n, perm_n, perm_same, perm_conf, stored-1 rows with perm=0 etc.
st = {name: {"n": 0, "perm_n": 0, "perm_same": 0,
             "conf": collections.Counter()} for name in STRATUM_NAMES}
overall = {"perm_n": 0, "perm_same": 0, "conf": collections.Counter()}

for db in sorted(glob.glob(os.path.join(ROOT, "projects", "*", DBNAME))):
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

    # token document frequency per cwd, over the same observation stream the
    # scorer sees (minus STOP_TOKENS, matching both the scorer and the parent)
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
        stride = max(1, len(rs) // CAP)
        sample = rs[::stride]
        starts = sorted(d for (_i, sts, _c, _m, _r) in rs for d in [parse(sts)] if d)
        if not starts:
            continue
        dfc = df.get(cwd, collections.Counter())

        def score(start, dur, keytoks):
            close = start + dur
            lo = bisect.bisect_right(times, start)
            hi = bisect.bisect_right(times, close)
            seen = 0
            for _, tk in ol[lo:hi]:
                seen += len(keytoks & tk)
                if seen >= 2:
                    return 1
            return 0

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
            rarity = sum(dfc.get(t, 0) for t in keytoks)
            sname = next(name for (lo_, hi_), name in zip(STRATA, STRATUM_NAMES)
                         if lo_ <= rarity <= hi_)
            st[sname]["n"] += 1
            cand = [t for t in starts if abs((t - s).total_seconds()) >= MIN_GAP]
            if not cand:
                continue
            j = stable_pick(f"{db}|{_id}", len(cand))
            perm = score(cand[j], dur, keytoks)
            st[sname]["perm_n"] += 1
            st[sname]["conf"][(rel, perm)] += 1
            overall["perm_n"] += 1
            overall["conf"][(rel, perm)] += 1
            if perm == rel:
                st[sname]["perm_same"] += 1
                overall["perm_same"] += 1


def report(name, d):
    n, pn, ps, conf = d["perm_n"] and d["perm_n"] or 0, d["perm_n"], d["perm_same"], d["conf"]
    if not d["perm_n"]:
        print(f"  {name:<10} n={d['n']:<6} no permutation candidates")
        return
    base = sum(v for (o, _), v in conf.items() if o == 1) / d["perm_n"]
    p1 = sum(v for (_, p), v in conf.items() if p == 1) / d["perm_n"]
    chance = base * p1 + (1 - base) * (1 - p1)
    kappa = (ps / d["perm_n"] - chance) / (1 - chance) if chance < 1 else float("nan")
    print(f"  {name:<10} n={d['n']:<6} permuted={d['perm_n']:<6} "
          f"concordance={ps/d['perm_n']:6.1%}  chance={chance:5.1%}  kappa={kappa:6.3f}  "
          f"| obs1->perm0 {conf[(1,0)]:<5} obs1->perm1 {conf[(1,1)]:<5} "
          f"obs0->perm0 {conf[(0,0)]:<5} obs0->perm1 {conf[(0,1)]}")


print(f"store: {ROOT} ({DBNAME})   cap={CAP} rows/cwd/shard   min_gap={MIN_GAP/3600:.0f}h")
print("key mass = sum of document frequencies of the key's tokens in the cwd's observation stream")
print()
print("PLACEMENT CONCORDANCE BY KEY-RARITY STRATUM (low df = timing-sensitive by construction)")
for name in STRATUM_NAMES:
    report(name, st[name])
print()
print("OVERALL (should reproduce the parent script's headline at the same cap/gap)")
report("overall", {"n": overall["perm_n"], "perm_n": overall["perm_n"],
                   "perm_same": overall["perm_same"], "conf": overall["conf"]})
