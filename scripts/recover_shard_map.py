#!/usr/bin/env python3
"""
recover_shard_map.py — the hash->dir map was never missing; it was in the `cwd` column.

BACKGROUND (the premise this script refutes)
--------------------------------------------
The store shards by working directory: `~/.snarc/projects/<hash>/`, where
`<hash> = sha256(abspath)[:12]`. The dir itself is NOT recorded in the path, and the
standing finding held that `meta.json` -- "the only hash->dir map" -- was never written
until the 2026-07-31 fix, leaving the archive (`~/.engram/projects`, 195 shards, where
all the historical data actually lives) permanently unattributable. Every per-repo
question about the corpus was therefore answered "not computable".

Two things are wrong with that.

1. `meta.json` was written once, on 2026-07-09, for shard 791cace57ce9. Its mtime
   (2026-07-08 20:46 PDT) matches its own `created` field, so it is NOT a product of the
   2026-07-31 fix. The writer predates the fix and fired exactly once in the archive.
   "Never written" was wrong; "written for 1 of 195" is the fact.

2. It did not matter, because `observations.cwd` carries the directory on the rows
   themselves. For 142 of 195 archive shards, some distinct `cwd` value in the shard's own
   observations hashes to the shard's own name. The map was inside the store the whole time.

TWO INSTRUMENTS, AND WHY BOTH
------------------------------
  A. `cwd` self-map  -- for each shard, group observations by cwd; keep any value v with
     sha256(v)[:12] == shard. Blind where a shard has no rows or no cwd.
  B. filesystem sweep -- enumerate real directories under the known roots, hash each,
     look up the shard. Blind where the directory has since been DELETED.

They are blind in unrelated places, which is the point: A resolves shards whose directory
no longer exists, B resolves shards that hold no cwd. Neither is a check on the other by
construction, so their agreement is evidence rather than a tautology.

ANCHOR (outside both instruments' samples)
-------------------------------------------
`meta.json` was written by the store, not derived by either instrument. It is the only
ground truth available and it is what keeps this from being an unanchored agreement:
  - live store ~/.snarc/projects: 8 shards, all 8 carry meta.json.
  - archive:                      1 shard carries meta.json (791cace57ce9).
Every resolution the instruments produce for an anchored shard must equal the anchor, or
this script is red.

8 anchors exist but only 6 SCORE against the archive, because an anchor is only usable
where its hash is also an archive shard. The two that do not score are 6b72c60c647f
(/tmp/snarc-meta-probe) and 89a267249e9c (a hub-mesh thread dir) -- both born in the live
store today and never present in the archive. Measured 2026-07-31 on the archive:
6 scored, 6 matched, 0 wrong. Quote 6, not 8: the count of anchors held is not the count
of anchors exercised.

RESULT (archive, 2026-07-31, CBP)
----------------------------------
  instrument A (cwd)          142/195
  instrument B (fs sweep)     162/195
  overlap                     118 shards, CONFLICTS 0
  union                       186/195 = 95.4% of shards
  the 9 unresolved            hold 0 rows each -- every one is an empty shard dir
  rows attributed             921,478 / 921,478 = 100.0%

So the archive is fully attributable by rows. "Never sum shards" still holds for the
overlap reason (a fresh shard replays the whole transcript), but "which repo is this
shard" is no longer an open question for any shard that contains data.

THREE PROPERTIES OF `cwd` THAT A FUTURE READER WILL TRIP ON
------------------------------------------------------------
  - 23 shards carry MORE THAN ONE distinct cwd. The shard is fixed at session start; the
    observations' cwd wanders as the session moves. Do not read `cwd` as the shard key --
    read it as a candidate set and let the hash select.
  - 2 shards have cwd values where NONE hashes to the shard name (2f33aaf82b99,
    c0743dedf746). Both recorded a DESCENDANT of the shard directory, never the shard
    directory itself. Those are instrument-A misses, not corrupt shards; B resolves one.
  - 51 shards have no usable cwd at all. That is instrument A's blind fraction: 26.2%,
    and it is countable, which is the property that matters.

Run:
  python3 scripts/recover_shard_map.py            # human table
  python3 scripts/recover_shard_map.py --check    # anchored acceptance; exit 1 on red
  python3 scripts/recover_shard_map.py --json OUT # write the recovered map
"""
import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import sys

ARCHIVE = pathlib.Path.home() / ".engram" / "projects"
LIVE = pathlib.Path.home() / ".snarc" / "projects"

# Roots swept by instrument B. Depth caps keep an NTFS walk of /mnt/c from running for
# minutes; a bare `/mnt/c` root timed out at 120s on CBP, which is why the roots are named
# individually rather than as one top-level walk.
SWEEP_ROOTS = [("/mnt/c/exe/projects", 5), (str(pathlib.Path.home()), 6), ("/tmp", 3)]
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "target",
    "build", "dist", ".cache", ".next", "site-packages",
}


def shard_hash(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:12]


def shard_db(shard_dir: pathlib.Path):
    dbs = sorted(shard_dir.glob("*.db"))
    return dbs[0] if dbs else None


def read_shard(shard_dir: pathlib.Path):
    """-> (row_count, [(cwd, n), ...]) ; (None, []) if the shard has no readable db."""
    db = shard_db(shard_dir)
    if db is None:
        return None, []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        n = con.execute("select count(*) from observations").fetchone()[0]
        cwds = con.execute(
            "select cwd, count(*) from observations "
            "where cwd is not null and cwd != '' group by cwd order by 2 desc"
        ).fetchall()
        con.close()
        return n, cwds
    except sqlite3.Error:
        return None, []


def instrument_cwd(store: pathlib.Path):
    """Instrument A. -> (resolved, stats)"""
    resolved, rows, no_cwd, descendant_only, multi = {}, {}, [], [], 0
    for d in sorted(p for p in store.iterdir() if p.is_dir()):
        n, cwds = read_shard(d)
        rows[d.name] = n or 0
        if not cwds:
            no_cwd.append(d.name)
            continue
        if len(cwds) > 1:
            multi += 1
        hit = [c for c, _ in cwds if shard_hash(c) == d.name]
        if hit:
            resolved[d.name] = hit[0]
        else:
            descendant_only.append((d.name, cwds[0][0], len(cwds)))
    return resolved, {
        "rows": rows, "no_cwd": no_cwd,
        "descendant_only": descendant_only, "multi_cwd": multi,
    }


def instrument_fs(shards):
    """Instrument B. -> (resolved, dirs_enumerated)"""
    by_hash = {}
    for root, max_depth in SWEEP_ROOTS:
        if not os.path.isdir(root):
            continue
        base = root.count("/")
        for dirpath, dirnames, _ in os.walk(root, topdown=True):
            if dirpath.count("/") - base >= max_depth:
                dirnames[:] = []
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
            by_hash.setdefault(shard_hash(dirpath), dirpath)
    return {s: by_hash[s] for s in shards if s in by_hash}, len(by_hash)


def anchors(*stores):
    """meta.json -- written by the store, derived by neither instrument."""
    out = {}
    for store in stores:
        if not store.is_dir():
            continue
        for d in store.iterdir():
            m = d / "meta.json"
            if m.is_file():
                try:
                    out[d.name] = json.loads(m.read_text())["dir"]
                except (ValueError, KeyError, OSError):
                    pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="anchored acceptance; exit 1 on red")
    ap.add_argument("--json", metavar="OUT", help="write the recovered map")
    ap.add_argument("--store", default=str(ARCHIVE), help="shard store to map")
    args = ap.parse_args()

    store = pathlib.Path(args.store)
    if not store.is_dir():
        print(f"no such store: {store}", file=sys.stderr)
        return 2
    shards = sorted(p.name for p in store.iterdir() if p.is_dir())

    a_map, a_stats = instrument_cwd(store)
    b_map, n_dirs = instrument_fs(shards)
    anc = anchors(store, LIVE)

    overlap = set(a_map) & set(b_map)
    conflicts = sorted(s for s in overlap if a_map[s] != b_map[s])
    union = dict(b_map)
    union.update(a_map)  # A wins ties; it is the store's own record

    rows = a_stats["rows"]
    total_rows = sum(rows.values())
    mapped_rows = sum(n for s, n in rows.items() if s in union)
    unresolved = [s for s in shards if s not in union]
    unresolved_rows = sum(rows.get(s, 0) for s in unresolved)

    # anchor scoring
    anchor_hits, anchor_misses = [], []
    for s, truth in anc.items():
        got = union.get(s)
        if got is None:
            continue
        (anchor_hits if got == truth else anchor_misses).append((s, truth, got))

    print(f"store {store}   shards {len(shards)}")
    print(f"  A  cwd self-map      {len(a_map):>4}/{len(shards)}"
          f"   blind: {len(a_stats['no_cwd'])} no-cwd, "
          f"{len(a_stats['descendant_only'])} descendant-only, "
          f"{a_stats['multi_cwd']} multi-cwd")
    print(f"  B  filesystem sweep  {len(b_map):>4}/{len(shards)}"
          f"   ({n_dirs} dirs enumerated under {len(SWEEP_ROOTS)} roots)")
    print(f"  overlap {len(overlap)}   CONFLICTS {len(conflicts)}")
    print(f"  union   {len(union):>4}/{len(shards)} = "
          f"{100 * len(union) / max(1, len(shards)):.1f}% of shards")
    print(f"  rows    {mapped_rows:,}/{total_rows:,} = "
          f"{100 * mapped_rows / max(1, total_rows):.1f}% attributed")
    print(f"  unresolved {len(unresolved)} shards holding {unresolved_rows} rows")
    print(f"  anchors (meta.json): {len(anchor_hits)} matched, {len(anchor_misses)} WRONG")
    for s, truth, got in anchor_misses:
        print(f"    ANCHOR MISS {s}: meta={truth} map={got}")
    for s in conflicts:
        print(f"    CONFLICT {s}: cwd={a_map[s]} fs={b_map[s]}")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(union, indent=1, sort_keys=True))
        print(f"  map -> {args.json}")

    if args.check:
        reds = []
        if anchor_misses:
            reds.append(f"{len(anchor_misses)} anchor(s) contradicted")
        if conflicts:
            reds.append(f"{len(conflicts)} instrument conflict(s)")
        if not anchor_hits:
            # An agreement with no anchor is not a result. Refuse to go green on one.
            reds.append("NO anchor was scored -- agreement is unanchored, verdict withheld")
        if unresolved_rows:
            reds.append(f"{unresolved_rows} rows in unresolved shards "
                        f"(expected 0: every unresolved shard should be empty)")
        if reds:
            print("\nRED:")
            for r in reds:
                print(f"  - {r}")
            return 1
        print(f"\nok: {len(anchor_hits)} anchors matched, 0 conflicts over {len(overlap)} "
              f"overlapping shards, 100% of rows attributed")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
