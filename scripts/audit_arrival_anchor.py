#!/usr/bin/env python3
"""
audit_arrival_anchor.py — anchor the era test on a clock OUTSIDE the column it reads.

`audit_replay_arrival.py` (kimi, 2026-07-31) established that `observations.ts` can be
era-TESTED rather than era-assumed: over a duplicated population, id-adjacent rows sharing
the same second at ~0.999 reads as write time, and min(ts) is then the moment that copy
started arriving. That inference is sound and it replicates. But it is a *pace* heuristic,
and a pace heuristic has no anchor outside the column it is judging: a population that was
itself generated at machine pace would test WRITE-TIME while carrying event time, and the
"arrival order" recovered from it would be the replayed corpus's own order — identical in
every shard, and an artifact.

This adds the two anchors that heuristic lacks. Neither reads `ts` to decide about `ts`.

  1. CROSS-SHARD IDENTITY (inside the store, decisive by construction).
     The four shards hold the SAME events. If `ts` were event time, the same content_hash
     would carry the SAME ts in every shard — identity 100%, median |dt| = 0. If `ts` is
     write time, each copy was written at its own moment and identity must be ~0% with a
     median |dt| equal to the gap between the two arrivals. This does not infer an era from
     pace; it forces the two hypotheses to make opposite predictions and reads which one
     the data took.

  2. FILESYSTEM BIRTH TIME (outside the store entirely).
     statx btime on each `snarc.db` records when the shard file was created — a real write
     clock, in a column no migration ever touched. For a shard created BY the replay, birth
     should precede min(ts) of its duplicated population by the replay's startup latency
     and no more. That turns the era test into a calibrated instrument: a systematic
     few-second offset repeated across shards is a validation; a multi-hour gap says the
     shard predates the copy it holds and min(ts) is measuring the COPY's arrival, not the
     shard's (both are real, they are just different axes, and ownership needs the copy's).

  3. AUTHORITY GO-LIVE, from the same outside clock.
     `seen.db`'s birth time is the moment `openRootClaims` first ran — go-live, measured
     rather than read off the first claim's `first_ts`. That matters because `first_ts` is
     `COALESCE(event ts, now)`, so on a claimed row it is the EVENT's time, not the claim's:
     this script reports how many claims carry a first_ts predating the existence of the
     file recording them, which is a self-contained proof of that inside the store.

Read-only. Usage: python3 scripts/audit_arrival_anchor.py [--check]

`--check` is RED if the identity control does not discriminate (i.e. the two hypotheses did
not separate), because in that case the arrival order is not readable and any --shards
ordering derived from it is decoration.
"""
import os, sys, glob, sqlite3, datetime, itertools, statistics, subprocess

ROOT = os.path.expanduser(os.environ.get('SNARC_ROOT', '~/.snarc'))
IDENTITY_MAX = 0.05   # above this, ts looks like event time and arrival order is NOT readable
CALIBRATION_S = 120   # birth-to-first-row offset under which the birth clock CONFIRMS min(ts)


def ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def sec(t):
    return datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=datetime.timezone.utc).timestamp()


def birth_utc(path):
    """statx btime, as a UTC wall-clock string comparable to the store's `ts`.

    NOT available through `os.stat` on Linux — `st_birthtime` is a BSD/macOS (and, since
    3.12, Windows) attribute, and reading it with getattr(..., None) fails SILENTLY into a
    "no btime" branch that leaves the whole outside anchor vacuous while --check stays
    green. GNU coreutils exposes statx btime as `%W` (epoch seconds, 0 when unsupported),
    so that is what this asks, and an unavailable btime is a RED rather than a shrug.
    """
    try:
        out = subprocess.run(['stat', '-c', '%W', path], capture_output=True,
                             text=True, timeout=10)
        bt = float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None
    if bt <= 0:                       # 0 = filesystem does not record it
        return None, None
    return datetime.datetime.fromtimestamp(bt, datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"), bt


def main():
    check = '--check' in sys.argv
    failures = []

    shards = {}
    for db in sorted(glob.glob(os.path.join(ROOT, 'projects', '*', 'snarc.db'))):
        shards[os.path.basename(os.path.dirname(db))] = db

    # --- the duplicated population, and each shard's copy of it ---
    owner = {}
    hash_ts = {}
    for shard, db in shards.items():
        c = ro(db)
        m = {}
        for h, t in c.execute("SELECT content_hash, MIN(ts) FROM observations "
                              "WHERE content_hash IS NOT NULL GROUP BY content_hash"):
            m[h] = t
            owner.setdefault(h, set()).add(shard)
        c.close()
        hash_ts[shard] = m
    multi = {h for h, v in owner.items() if len(v) > 1}
    carriers = sorted({s for h in multi for s in owner[h]})
    print(f"duplicated population: {len(multi)} hashes across {len(carriers)} shards\n")

    # --- 1. cross-shard identity: the two era hypotheses predict opposite things ---
    print("=== 1. cross-shard ts identity (event-time predicts 100%, write-time predicts ~0%) ===")
    print(f"  {'pair':<30}{'shared':>8}{'identical':>11}{'median |dt|':>13}")
    identities = []
    for a, b in itertools.combinations(carriers, 2):
        shared = set(hash_ts[a]) & set(hash_ts[b]) & multi
        if not shared:
            continue
        same, deltas = 0, []
        for h in shared:
            ta, tb = hash_ts[a][h], hash_ts[b][h]
            if ta == tb:
                same += 1
            try:
                deltas.append(abs(sec(ta) - sec(tb)))
            except ValueError:
                pass
        frac = same / len(shared)
        identities.append(frac)
        med = statistics.median(deltas) if deltas else float('nan')
        print(f"  {a[:6]}..{b[:6]:<20}{len(shared):>8}{frac*100:>10.1f}%{med:>12.0f}s")
    worst = max(identities) if identities else 1.0
    if worst <= IDENTITY_MAX:
        print(f"  -> WRITE TIME. Max identity {worst*100:.1f}% <= {IDENTITY_MAX*100:.0f}%: the same event "
              f"carries a DIFFERENT ts in every shard,\n     which only a per-copy write clock produces. "
              f"Arrival order is readable.")
    else:
        print(f"  -> NOT DISCRIMINATED. Max identity {worst*100:.1f}%: consistent with event time; "
              f"min(ts) is not arrival.")
        failures.append(f"cross-shard identity {worst*100:.1f}% > {IDENTITY_MAX*100:.0f}% — arrival order NOT readable")

    # --- 2. filesystem birth time: a write clock the store does not contain ---
    print("\n=== 2. shard birth (statx btime) vs min(ts) of its duplicated copy ===")
    print(f"  {'shard':<14}{'file birth (UTC)':<21}{'first dup ts':<21}{'offset':>10}  reading")
    rows_out = []
    for shard in carriers:
        bstr, bts = birth_utc(shards[shard])
        c = ro(shards[shard])
        first = None
        for (h, t) in c.execute("SELECT content_hash, ts FROM observations "
                                "WHERE content_hash IS NOT NULL ORDER BY id"):
            if h in multi:
                first = t
                break
        c.close()
        if not bstr or not first:
            print(f"  {shard:<14}{bstr or '(no btime)':<21}{first or '-':<21}{'-':>10}  "
                  f"NO OUTSIDE CLOCK — the era test is unanchored for this shard")
            failures.append(f"{shard}: no statx btime — the outside anchor is absent, "
                            f"not merely inconvenient")
            continue
        off = sec(first) - bts
        if 0 <= off <= CALIBRATION_S:
            reading = "CONFIRMS — shard created BY this copy (startup latency)"
        elif off > CALIBRATION_S:
            reading = "shard PREDATES the copy — min(ts) is the copy's arrival, not the shard's"
        else:
            reading = "copy predates the file — btime unreliable here"
        print(f"  {shard:<14}{bstr:<21}{first:<21}{off:>9.0f}s  {reading}")
        rows_out.append((shard, bts, first, off))

    agree = [r for r in rows_out if 0 <= r[3] <= CALIBRATION_S]
    if agree:
        offs = [r[3] for r in agree]
        print(f"  -> {len(agree)}/{len(rows_out)} shards calibrate the era test against an outside clock: "
              f"offsets {min(offs):.0f}-{max(offs):.0f}s.")
        print("     A systematic few-second offset repeated across independent shards is the "
              "validation the\n     pace heuristic could not give itself.")

    # --- 3. authority go-live, from the same outside clock ---
    print("\n=== 3. authority go-live, measured outside the column ===")
    seen_path = os.path.join(ROOT, 'seen.db')
    gstr, gts = birth_utc(seen_path)
    if not gstr:
        print("  seen.db has no btime on this filesystem — go-live not measurable here.")
        failures.append("seen.db has no statx btime — go-live can only be read off the "
                        "first claim's first_ts, which is the EVENT's clock")
    else:
        print(f"  seen.db birth (openRootClaims' first run) : {gstr} UTC")
        s = ro(seen_path)
        n, mn = s.execute("SELECT COUNT(*), MIN(first_ts) FROM seen").fetchone()
        pre = s.execute("SELECT COUNT(*) FROM seen WHERE first_ts < ?", (gstr,)).fetchone()[0]
        s.close()
        print(f"  claims recorded                           : {n}")
        print(f"  earliest claim first_ts                   : {mn}")
        print(f"  claims whose first_ts PREDATES that birth : {pre}")
        if pre:
            print(f"  -> `first_ts` is NOT the claim moment. {pre} claims carry a timestamp from before "
                  f"the file\n     recording them existed, because first_ts is COALESCE(event ts, now) and "
                  f"these rows\n     carried an event ts. Dating go-live from the first claim reads the EVENT's "
                  f"clock.")
        # the tail of the duplicated corpus, on the write-time era, vs go-live
        tails = []
        for shard in carriers:
            c = ro(shards[shard])
            last = None
            for (h, t) in c.execute("SELECT content_hash, ts FROM observations "
                                    "WHERE content_hash IS NOT NULL ORDER BY id"):
                if h in multi:
                    last = t
            c.close()
            if last:
                tails.append((last, shard))
        if tails:
            tails.sort()
            last_ts, last_shard = tails[-1]
            gap = gts - sec(last_ts)
            print(f"\n  last duplicated row written, any shard   : {last_ts}  ({last_shard})")
            rel = "AFTER" if gap > 0 else "BEFORE"
            # floor, not round: `{gap/60:.0f}` renders 229s as "4m49s" — the minutes field
            # rounds UP while the seconds field is a remainder, so the two disagree and the
            # printed number is a minute wrong. This gap IS the finding; it does not get to
            # be off by a rounding mode.
            mins, secs = divmod(int(abs(gap)), 60)
            print(f"  authority came up                        : {gstr}  "
                  f"-> {mins}m{secs:02d}s {rel} it")
            if gap > 0:
                print("  -> the duplicated corpus had finished landing before the authority existed. "
                      "The guard\n     came up after the incident, on a clock the store does not contain.")

    if check:
        print()
        for f in failures:
            print(f"RED: {f}")
        print(f"ok: {len(failures)} failure(s)")
        return 1 if failures else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
