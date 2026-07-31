#!/usr/bin/env python3
"""Fleet census over the sharded snarc/engram stores.

Exists because the number it prints shipped once without it. The PRD's SCOPE block
said "386 identity rows across 41 shards"; the correct count is 40, and neither the
author nor the second seat could re-derive 41 because the query was ad-hoc and lived
only in a session transcript. Every number in the SCOPE block is now produced here,
with its predicate printed next to it.

Predicates (stated, not implied):
  population  : sorted(glob('<root>/projects/*/<db>'))  -- NOT the root-level store,
                which is a pre-sharding db with a different schema (see --root-store)
  access      : sqlite3 'file:<path>?mode=ro' URI, so the -wal is read through.
                A flat file copy silently drops it (10,715 vs 10,724 retrieval rows).
  holder      : count(*) FROM identity > 0   (a shard with the table and zero rows
                is NOT a holder; all shards have the table)

Usage:
  census_shards.py                 # archive (~/.engram) census
  census_shards.py --live          # live store (~/.snarc)
  census_shards.py --check         # assert the PRD SCOPE numbers, exit 1 on drift
"""
import argparse
import glob
import json
import os
import sqlite3
import sys

ARCHIVE = ("~/.engram", "engram.db")
LIVE = ("~/.snarc", "snarc.db")

# The SCOPE block in docs/PRD_ACT_GRAIN_SALIENCE.md, as of 2026-07-31.
EXPECT_ARCHIVE = {
    "shards": 195,
    "observations": 921478,
    "retrieval_log": 19953,
    "identity": 386,
    "identity_holders": 40,
    "max_identity_shard": ("f79e5e81cf37", 84),
    "our_shard": ("791cace57ce9", 704049, 10724, 6),
    "shards_with_meta": 1,
}


def count(conn, table):
    try:
        return conn.execute(f"select count(*) from {table}").fetchone()[0]
    except sqlite3.Error:
        return None


def census(root, dbname):
    root = os.path.expanduser(root)
    paths = sorted(glob.glob(os.path.join(root, "projects", "*", dbname)))
    rows, errors = [], []
    for p in paths:
        h = os.path.basename(os.path.dirname(p))
        meta = os.path.exists(os.path.join(os.path.dirname(p), "meta.json"))
        d = None
        if meta:
            try:
                d = json.load(open(os.path.join(os.path.dirname(p), "meta.json"))).get("dir")
            except (OSError, ValueError):
                d = "<unreadable>"
        try:
            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        except sqlite3.Error as e:
            errors.append((h, str(e)))
            continue
        try:
            last_ts = conn.execute("select max(ts) from observations").fetchone()[0]
        except sqlite3.Error:
            last_ts = None
        rows.append({
            "hash": h,
            "obs": count(conn, "observations"),
            "ret": count(conn, "retrieval_log"),
            "identity": count(conn, "identity"),
            "meta_dir": d,
            "last_ts": last_ts,
        })
        conn.close()
    return paths, rows, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="census ~/.snarc instead of ~/.engram")
    ap.add_argument("--check", action="store_true", help="assert PRD SCOPE numbers (archive only)")
    ap.add_argument("--root-store", action="store_true", help="also probe the non-sharded root db")
    args = ap.parse_args()

    root, dbname = LIVE if args.live else ARCHIVE
    paths, rows, errors = census(root, dbname)

    # realpath collision check: a symlinked shard would be counted twice by the glob
    real = {}
    for p in paths:
        real.setdefault(os.path.realpath(p), []).append(p)
    collisions = {k: v for k, v in real.items() if len(v) > 1}

    tot = {k: sum(r[k] or 0 for r in rows) for k in ("obs", "ret", "identity")}
    holders = sorted([(r["hash"], r["identity"]) for r in rows if (r["identity"] or 0) > 0],
                     key=lambda x: -x[1])
    no_table = [r["hash"] for r in rows if r["identity"] is None]
    with_meta = [(r["hash"], r["meta_dir"]) for r in rows if r["meta_dir"]]

    print(f"store            {root}/projects/*/{dbname}   (mode=ro, WAL read through)")
    print(f"shards globbed   {len(paths)}   distinct realpaths {len(real)}   collisions {len(collisions)}")
    print(f"open errors      {len(errors)} {errors if errors else ''}")
    print(f"observations     {tot['obs']:,}")
    print(f"retrieval_log    {tot['ret']:,}")
    print(f"identity rows    {tot['identity']:,}")
    print(f"identity holders {len(holders)}   [predicate: count(*) FROM identity > 0]")
    print(f"  zero-row identity tables {len(rows) - len(holders) - len(no_table)}   no identity table {len(no_table)}")
    last = max((r["last_ts"] for r in rows if r["last_ts"]), default=None)
    print(f"last write       {last}   <- this store is not frozen by decree; a census before the "
          "2026-07-31 04:22Z handover to ~/.snarc will not reproduce these numbers")
    print(f"shards w/ meta   {len(with_meta)}")
    for h, d in with_meta:
        print(f"                 {h}  {d}")
    print("top identity shards:")
    for h, n in holders[:6]:
        print(f"                 {h}  {n}")
    if holders:
        print(f"  ... tail: {holders[-3:]}")

    if args.root_store:
        rp = os.path.join(os.path.expanduser(root), dbname)
        if os.path.exists(rp):
            conn = sqlite3.connect(f"file:{rp}?mode=ro", uri=True)
            print(f"root store       {rp}  obs={count(conn, 'observations')} "
                  f"ret={count(conn, 'retrieval_log')} identity={count(conn, 'identity')}  "
                  "(older schema — no retrieval_log table; NOT in the 195)")
            conn.close()

    if not args.check:
        return 0

    if args.live:
        print("--check applies to the archive census only", file=sys.stderr)
        return 2

    e, bad = EXPECT_ARCHIVE, []
    got = {
        "shards": len(paths),
        "observations": tot["obs"],
        "retrieval_log": tot["ret"],
        "identity": tot["identity"],
        "identity_holders": len(holders),
        "shards_with_meta": len(with_meta),
    }
    for k, v in got.items():
        if v != e[k]:
            bad.append(f"{k}: PRD says {e[k]}, store says {v}")
    if holders and (holders[0][0], holders[0][1]) != e["max_identity_shard"]:
        bad.append(f"max identity shard: PRD says {e['max_identity_shard']}, store says {holders[0]}")
    ours = next((r for r in rows if r["hash"] == e["our_shard"][0]), None)
    if not ours:
        bad.append(f"our shard {e['our_shard'][0]} not in census")
    elif (ours["obs"], ours["ret"], ours["identity"]) != e["our_shard"][1:]:
        bad.append(f"our shard: PRD says {e['our_shard'][1:]}, store says "
                   f"{(ours['obs'], ours['ret'], ours['identity'])}")
    if collisions:
        bad.append(f"realpath collisions: {collisions}")

    print()
    if bad:
        print(f"DRIFT vs the PRD snapshot (store's last write: {last}):")
        for b in bad:
            print(f"  {b}")
        return 1
    print(f"OK — all {len(got) + 2} SCOPE numbers reproduce")
    return 0


if __name__ == "__main__":
    sys.exit(main())
