#!/usr/bin/env python3
"""
Does `retrieval_log.relevant` carry information about WHEN the memory was surfaced,
or only about WHICH cwd it was surfaced into?

Two prior results bracket the column: rows whose reconstructed window held zero
observations are scored 0 by construction (structural zeros, 8.2% of the archive's
0 class), and 28 (cwd, match_key) groups of >=20 rows score 1 on EVERY row
(4,364 rows, 39.1% of the archive's 1 class). Both say the value may be a property
of the group rather than of the session. Removing the all-ones groups would be
selecting on the outcome, so this script does not remove anything. It runs the
scorer itself, twice.

  A. REPRODUCE. Re-run scoreRetrievals' exact predicate -- sigTokens (memory.ts:80,
     transcribed, not paraphrased), STOP_TOKENS (memory.ts:88), overlap >= 2
     (memory.ts:468) -- against the reconstructed window, and compare to the stored
     `relevant`. This validates the window reconstruction the last three posts rest on:
     if the reproduction agrees, the reconstruction is pinned; where it disagrees,
     the disagreement is the reconstruction's error bar.

  B. PERMUTE THE PLACEMENT. Score the SAME match_key against a window of the SAME
     duration placed at a different scored row's surfaced_ts in the SAME cwd. The
     key, the cwd and the window length are held fixed; only the timing moves. If the
     permuted score matches the real score, the column's value did not depend on when
     the memory was surfaced -- the session had no say in it.

     concordance = P(permuted == observed). A column that measures the session should
     sit near chance; a column determined by (cwd, key) sits near 1.

Read-only. stdlib only. Sampled -- pass a row cap as argv[2].
Usage: audit_outcome_placement.py [store_root] [max_rows_per_cwd] [min_displacement_hours]
       min_displacement_hours guards the obvious attack on B: with no constraint the
       permuted window can overlap the real one. Archive result moves 81.7% -> 80.4%
       when the permuted start is forced at least 24h away.
"""
import sqlite3, glob, sys, os, re, bisect, collections, hashlib
from datetime import datetime, timedelta

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.engram")
CAP = int(sys.argv[2]) if len(sys.argv) > 2 else 400
MIN_GAP = float(sys.argv[3]) * 3600 if len(sys.argv) > 3 else 0.0  # hours between real and permuted start
DBNAME = "engram.db" if ".engram" in ROOT else "snarc.db"

# --- transcribed verbatim from src/memory.ts:80-92 -------------------------
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
    """Deterministic index in [0,n) -- Math.random() is not available and a seed we
    can print is better than one we can't."""
    return int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % n


agree = disagree = 0
conf = collections.Counter()          # (observed, reproduced)
perm_conf = collections.Counter()     # (observed, permuted)
perm_n = perm_same = 0
by_cwd = collections.defaultdict(lambda: [0, 0, 0])  # n, reproduced_agree, perm_same
skipped_no_window = 0

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
        # tokenise only the cwds this shard's scored rows actually reference
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

    # cap per cwd, deterministic stride
    by = collections.defaultdict(list)
    for r in rows:
        by[r[2] or ""].append(r)

    for cwd, rs in by.items():
        ol = obs.get(cwd, [])
        times = [x[0] for x in ol]
        stride = max(1, len(rs) // CAP)
        sample = rs[::stride]
        starts = []
        for _id, sts, _c, _mk, _rel in rs:
            d = parse(sts)
            if d:
                starts.append(d)
        if not starts:
            continue
        starts.sort()

        def score(start, dur, keytoks):
            close = start + dur
            lo = bisect.bisect_right(times, start)
            hi = bisect.bisect_right(times, close)
            seen = 0
            for _, tk in ol[lo:hi]:
                seen += len(keytoks & tk)
                if seen >= 2:
                    return 1
            return 1 if seen >= 2 else 0

        for _id, sts, _c, mk, rel in sample:
            s = parse(sts)
            if not s:
                continue
            i = bisect.bisect_left(ends, s + timedelta(seconds=60))
            if i >= len(ends):
                skipped_no_window += 1
                continue
            close = max(min(ends[i], s + timedelta(hours=6)), s)
            dur = close - s
            keytoks = set((mk or "").split()) - STOP_TOKENS
            rep = score(s, dur, keytoks)
            rel = int(rel)
            conf[(rel, rep)] += 1
            by_cwd[cwd][0] += 1
            if rep == rel:
                agree += 1
                by_cwd[cwd][1] += 1
            else:
                disagree += 1
            # --- permutation: same key, same duration, different placement
            # MIN_GAP guards the obvious attack on this test: if the permuted start is
            # minutes away from the real one the two windows can overlap, and concordance
            # would be high for a trivial reason. Re-run with a large MIN_GAP to check.
            cand = [t for t in starts if abs((t - s).total_seconds()) >= MIN_GAP] if MIN_GAP else \
                   [t for t in starts if t != s]
            if cand:
                j = stable_pick(f"{db}|{_id}", len(cand))
                perm = score(cand[j], dur, keytoks)
                perm_conf[(rel, perm)] += 1
                perm_n += 1
                if perm == rel:
                    perm_same += 1
                    by_cwd[cwd][2] += 1

print(f"store: {ROOT} ({DBNAME})   cap={CAP} rows/cwd/shard   min_gap={MIN_GAP/3600:.0f}h")
print(f"rows scored by the transcribed predicate : {agree + disagree}")
print(f"  skipped, no session end after t+60s    : {skipped_no_window}")
print()
print("A. REPRODUCTION (stored `relevant` vs re-run predicate on reconstructed window)")
n = agree + disagree
print(f"   agreement : {agree}/{n} = {agree/n:.1%}" if n else "   no rows")
for k in ((0, 0), (0, 1), (1, 0), (1, 1)):
    print(f"     stored={k[0]} reproduced={k[1]} : {conf[k]}")
print()
print("B. PLACEMENT PERMUTATION (same key, same cwd, same duration, moved in time)")
if perm_n:
    base = sum(v for (o, _), v in perm_conf.items() if o == 1) / perm_n
    # chance concordance if the permuted score were independent with the same marginal
    p_perm1 = sum(v for (_, pm), v in perm_conf.items() if pm == 1) / perm_n
    chance = base * p_perm1 + (1 - base) * (1 - p_perm1)
    print(f"   concordance (permuted == observed) : {perm_same}/{perm_n} = {perm_same/perm_n:.1%}")
    print(f"   independent-marginals chance level : {chance:.1%}")
    print(f"   kappa                              : {(perm_same/perm_n - chance)/(1-chance):.3f}")
    for k in ((0, 0), (0, 1), (1, 0), (1, 1)):
        print(f"     observed={k[0]} permuted={k[1]} : {perm_conf[k]}")
print()
print("per-cwd (n, reproduction agreement, placement concordance), top 12 by n")
for cwd, (cn, ca, cp) in sorted(by_cwd.items(), key=lambda kv: -kv[1][0])[:12]:
    print(f"  {cn:>6}  repro {ca/cn:>6.1%}  perm {cp/cn:>6.1%}  {cwd or '(empty)'}")
