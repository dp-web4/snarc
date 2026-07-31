#!/usr/bin/env python3
"""
Is the outcome column's 0 a measurement, or is some of it structural?

scoreRetrievals() (src/memory.ts:454) is called ONLY from the SessionEnd hook
(hooks/handlers/session-end.ts:44). Its row selector (src/db.ts:752) takes every
row with `relevant IS NULL AND surfaced_ts <= now-60s` -- there is no wait for the
6h window in getObsAfter (src/db.ts:757) to elapse. The write at memory.ts:470 is
terminal: `WHERE relevant IS NULL` never revisits a scored row.

So the evidence window for a row is not 6h. It is

    min(6h, first_session_end_after(surfaced_ts + 60s) - surfaced_ts)

and a row whose window held no observations is scored 0 because nothing could have
matched -- indistinguishable, in the column, from a row the session saw and ignored.

This prints the blind fraction: of the rows scored 0, how many had ZERO eligible
observations in their reconstructed window.

Usage: audit_outcome_censoring.py [store_root]   (default: the archive)
"""
import sqlite3, glob, sys, os
from datetime import datetime, timedelta

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.engram")
DBNAME = "engram.db" if ".engram" in ROOT else "snarc.db"


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


BUCKETS = [(0, 1), (1, 5), (5, 15), (15, 60), (60, 360), (360, 10**9)]
LABEL = ["<1m", "1-5m", "5-15m", "15-60m", "1-6h", ">=6h (capped)"]

tot = blind = blind_zero = zero = one = 0
by_bucket = {i: [0, 0, 0] for i in range(len(BUCKETS))}  # n, relevant, zero-obs
no_window = 0

for db in sorted(glob.glob(os.path.join(ROOT, "projects", "*", DBNAME))):
    try:
        c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
        rows = c.execute(
            "SELECT surfaced_ts, cwd, relevant FROM retrieval_log "
            "WHERE source='briefing' AND relevant IS NOT NULL"
        ).fetchall()
        if not rows:
            continue
        # Session ends are collected SHARD-WIDE, not per cwd. getUnscoredRetrievals
        # (src/db.ts:752) has no cwd predicate, so any session ending in the shard scores
        # every unscored row in it -- the trigger is cwd-blind even though the evidence
        # (getObsAfter, db.ts:757) is cwd-scoped. A cwd-respecting join here is also empty
        # by construction: session-end.ts:26 calls initSession() with no cwd, and
        # INSERT OR REPLACE (db.ts:785, session_id is PK) wipes cwd before endSession sets
        # ended_at -- 0 of 21,078 rows corpus-wide carry both.
        ends, obs = [], {}
        for (e,) in c.execute(
            "SELECT ended_at FROM sessions WHERE ended_at IS NOT NULL"
        ):
            d = parse(e)
            if d:
                ends.append(d)
        for cwd, t in c.execute("SELECT cwd, ts FROM observations"):
            d = parse(t)
            if d:
                obs.setdefault(cwd or "", []).append(d)
        ends.sort()
        for v in obs.values():
            v.sort()
        c.close()
    except Exception:
        continue

    import bisect

    for sts, cwd, rel in rows:
        s = parse(sts)
        if not s:
            continue
        cwd = cwd or ""
        i = bisect.bisect_left(ends, s + timedelta(seconds=60))
        if i >= len(ends):
            no_window += 1
            continue
        close = min(ends[i], s + timedelta(hours=6))
        if close <= s:
            close = s
        mins = (close - s).total_seconds() / 60.0
        ol = obs.get(cwd, [])
        n_obs = bisect.bisect_right(ol, close) - bisect.bisect_right(ol, s)

        tot += 1
        if rel:
            one += 1
        else:
            zero += 1
        if n_obs == 0:
            blind += 1
            if not rel:
                blind_zero += 1
        for bi, (lo, hi) in enumerate(BUCKETS):
            if lo <= mins < hi:
                by_bucket[bi][0] += 1
                by_bucket[bi][1] += rel or 0
                if n_obs == 0:
                    by_bucket[bi][2] += 1
                break

print(f"store: {ROOT}  ({DBNAME})")
print(f"scored briefing rows with a reconstructable window : {tot}")
print(f"  no session end after surfaced_ts+60s (skipped)   : {no_window}")
if not tot:
    sys.exit(0)
print(f"  relevant=1 : {one} ({100*one/tot:.1f}%)")
print(f"  relevant=0 : {zero} ({100*zero/tot:.1f}%)")
print()
print(f"STRUCTURAL ZEROS: rows whose window held 0 observations")
print(f"  {blind} of {tot} rows ({100*blind/tot:.1f}%) could not match by construction")
if zero:
    print(f"  they are {blind_zero} of the {zero} zeros = {100*blind_zero/zero:.1f}% of the 0 class")
print()
print(f"{'window':<16}{'n':>7}{'relevant':>10}{'0-obs window':>14}")
for bi, lab in enumerate(LABEL):
    n, r, z = by_bucket[bi]
    if n:
        print(f"{lab:<16}{n:>7}{100*r/n:>9.1f}%{100*z/n:>13.1f}%")
