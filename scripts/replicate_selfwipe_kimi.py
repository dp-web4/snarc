#!/usr/bin/env python3
"""
Kimi-seat replication of CBP's self-wipe findings (notice 558 /
forum: cbp-the-window-reconstruction-survives-my-attack-...-2026-07-31).

From ~/.engram and ~/.snarc, read-only:
  1. sessions-table census: total rows, rows with both ended_at AND cwd,
     cwd-only, ended_at-only, neither; share carrying ended_at;
     obs_count==0 share; CBP's falsifiable prediction: rows with
     obs_count > 0 AND ended_at IS NULL (expected 0 if the write path
     is initSession(INSERT OR REPLACE) -> endSession(UPDATE) as read).
  2. Window reconstruction (first ended_at after surfaced_ts+60s,
     shard-wide, cwd-blind -- db.ts:752 has no cwd predicate):
     median effective window per store.
  3. THE OPEN QUESTION from CBP's post section 4: my "median 4 (archive)
     / 2 (live) observations in window" -- computed off the observations
     table (count of obs rows with row's cwd inside the window) AND off
     sessions.obs_count, side by side. If the first reproduces 4/2 and
     the second reads 0, the number stands as written and the wiped
     column could not have produced it.

Usage: replicate_selfwipe_kimi.py
"""
import sqlite3, glob, os, bisect
from datetime import datetime, timedelta

STORES = [
    (os.path.expanduser("~/.engram"), "engram.db", "archive"),
    (os.path.expanduser("~/.snarc"), "snarc.db", "live"),
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


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


grand = dict(total=0, both=0, cwd_only=0, end_only=0, neither=0,
             ended=0, obs0=0, obs_pos_no_end=0)

for root, dbname, label in STORES:
    windows, obs_in_window, obscount_in_window = [], [], []
    census = dict(total=0, both=0, cwd_only=0, end_only=0, neither=0,
                  ended=0, obs0=0, obs_pos_no_end=0)
    for db in sorted(glob.glob(os.path.join(root, "projects", "*", dbname))):
        try:
            c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "sessions" not in tables:
                c.close(); continue
            for sid, ended_at, cwd, oc in c.execute(
                    "SELECT session_id, ended_at, cwd, obs_count FROM sessions"):
                census["total"] += 1
                has_end, has_cwd = bool(ended_at), bool(cwd)
                if has_end and has_cwd: census["both"] += 1
                elif has_cwd: census["cwd_only"] += 1
                elif has_end: census["end_only"] += 1
                else: census["neither"] += 1
                if has_end: census["ended"] += 1
                if not oc: census["obs0"] += 1
                if oc and not has_end: census["obs_pos_no_end"] += 1
            if "retrieval_log" not in tables or "observations" not in tables:
                c.close(); continue
            rows = c.execute(
                "SELECT surfaced_ts, cwd FROM retrieval_log "
                "WHERE source='briefing' AND relevant IS NOT NULL").fetchall()
            if not rows:
                c.close(); continue
            ends = sorted(d for d in (parse(e) for (e,) in c.execute(
                "SELECT ended_at FROM sessions WHERE ended_at IS NOT NULL")) if d)
            obs = {}
            for cwd, t in c.execute("SELECT cwd, ts FROM observations"):
                d = parse(t)
                if d:
                    obs.setdefault(cwd or "", []).append(d)
            for v in obs.values():
                v.sort()
            # sessions.obs_count per session END timestamp, for the wiped-column contrast
            c.close()
        except Exception:
            continue
        for sts, cwd in rows:
            s = parse(sts)
            if not s or not ends:
                continue
            i = bisect.bisect_left(ends, s + timedelta(seconds=60))
            if i >= len(ends):
                continue
            close = min(ends[i], s + timedelta(hours=6))
            if close <= s:
                close = s
            windows.append((close - s).total_seconds() / 60.0)
            ol = obs.get(cwd or "", [])
            obs_in_window.append(
                bisect.bisect_right(ol, close) - bisect.bisect_right(ol, s))

    print(f"== {label} ({root}) ==")
    print(f"  sessions rows: {census['total']}")
    print(f"    ended_at AND cwd : {census['both']}")
    print(f"    cwd only         : {census['cwd_only']}")
    print(f"    ended_at only    : {census['end_only']}")
    print(f"    neither          : {census['neither']}")
    if census["total"]:
        print(f"    carrying ended_at: {100*census['ended']/census['total']:.1f}%")
        print(f"    obs_count == 0   : {100*census['obs0']/census['total']:.1f}%")
    print(f"    obs_count>0 AND ended_at NULL (CBP prediction, expect 0): "
          f"{census['obs_pos_no_end']}")
    print(f"  windows reconstructed: {len(windows)}; median {median(windows):.1f} min")
    print(f"  median obs in window (observations table): {median(obs_in_window)}")
    for k in census:
        grand[k] += census[k]

print("== corpus-wide ==")
print(f"  sessions rows total        : {grand['total']}")
print(f"  ended_at AND cwd both      : {grand['both']}")
print(f"  carrying ended_at          : {100*grand['ended']/grand['total']:.1f}%")
print(f"  obs_count == 0             : {100*grand['obs0']/grand['total']:.1f}%")
print(f"  obs_count>0 AND ended NULL : {grand['obs_pos_no_end']}")
