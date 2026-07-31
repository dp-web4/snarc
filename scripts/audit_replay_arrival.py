#!/usr/bin/env python3
"""
audit_replay_arrival.py — is replay arrival order readable from the store?

backfill_seen.mjs assigns ownership in directory-name order and reports, correctly, that
"nothing in the store records which replay ran first" (cb800f0 forum post §4). That
sentence is falsifiable for THIS population, and the test is the one the same thread's §5
lesson demands: `observations.ts` is write time before c48af34 and event time after, with
no era marker on the column. So the era cannot be ASSUMED — but it can be TESTED, per
population:

    era discriminator: the fraction of id-adjacent rows sharing the same second.
    A replay writes at machine pace (tens of rows/second sustained); the original corpus
    accumulated at human pace. ~99.9% same-second adjacency over 12,600 rows is write time.

If a shard's duplicated population tests write-time, then its min(ts) over that population
IS the wall-clock moment the copy started arriving, and ordering shards by it is arrival
order — measured, with the discriminator printed next to every reading.

Per shard this reports: same-second fraction, peak rows/sec (era evidence), min(ts) of the
duplicated population (replay start, IF the era test passes) — then the arrival ordering,
and which shard the backfill's dirname-order default would award ownership to instead.

Read-only. Usage: python3 scripts/audit_replay_arrival.py
"""
import os, glob, sqlite3, datetime
from collections import defaultdict

ROOT = os.path.expanduser(os.environ.get('SNARC_ROOT', '~/.snarc'))
ERA_THRESHOLD = 0.95  # same-second fraction above which the population reads as write time


def ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def sec(t):
    return datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S").timestamp()


def main():
    owner = defaultdict(set)
    shards = {}
    for db in sorted(glob.glob(os.path.join(ROOT, 'projects', '*', 'snarc.db'))):
        shard = os.path.basename(os.path.dirname(db))
        shards[shard] = db
        c = ro(db)
        for (h,) in c.execute("SELECT content_hash FROM observations WHERE content_hash IS NOT NULL"):
            owner[h].add(shard)
        c.close()
    multi = {h for h, v in owner.items() if len(v) > 1}
    print(f"duplicated population: {len(multi)} hashes\n")

    print(f"{'shard':<14}{'dup-rows':>9}{'same-sec':>10}{'peak r/s':>9}   era / first duplicated ts")
    arrivals = []
    for shard in sorted(shards):
        c = ro(shards[shard])
        rows = [(i, t) for (i, h, t) in c.execute(
            "SELECT id, content_hash, ts FROM observations "
            "WHERE content_hash IS NOT NULL ORDER BY id") if h in multi]
        c.close()
        if not rows:
            continue
        ts = [t for _, t in rows]
        same = sum(1 for a, b in zip(ts, ts[1:]) if a == b) / max(len(ts) - 1, 1)
        peak = 0.0
        for k in range(0, max(len(ts) - 1000, 1), 100):
            span = sec(ts[min(k + 1000, len(ts) - 1)]) - sec(ts[k])
            if span > 0:
                peak = max(peak, 1000 / span)
        write_time = same > ERA_THRESHOLD
        era = "WRITE-TIME" if write_time else "EVENT/MIXED — min(ts) is not arrival"
        print(f"{shard:<14}{len(rows):>9}{same:>10.3f}{peak:>9.0f}   {era} / {ts[0]}")
        if write_time:
            arrivals.append((ts[0], shard))

    if len(arrivals) >= 2:
        arrivals.sort()
        print("\narrival order over the duplicated population (era-verified, not assumed):")
        for t, s in arrivals:
            print(f"  {t}  {s}")
        default_winner = sorted(s for _, s in arrivals)[0]
        arrival_first = arrivals[0][1]
        print(f"\ndirname-order backfill default awards ownership to: {default_winner}")
        print(f"arrival order awards it to:                         {arrival_first}")
        if default_winner != arrival_first:
            rank = [s for _, s in arrivals].index(default_winner) + 1
            print(f"-> the default awards ownership to arrival #{rank} of {len(arrivals)},")
            print(f"   not the first. --shards {','.join(s for _, s in arrivals)}")
            print("   implements arrival-order ownership.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
