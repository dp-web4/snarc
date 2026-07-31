#!/usr/bin/env python3
"""
audit_claim_exposure.py — the denominator under `claim_conflict rows: 0`.

THE READING THIS SCRIPT EXISTS TO REPLACE. The root claim authority (seen.db, c48af34) went
live 2026-07-31 and has recorded zero denials. Both seats read that as "installed and unfired",
with a caveat attached: "that is 'no writes guarded since', not 'cross-shard denial is rare'".

The caveat is wrong in its specific and right in its instinct. Writes WERE guarded — 44 of
them, in the authority's first hour. What was never guarded is a write of the *class that can
be denied*. That distinction is a number, and a caveat is not a number:

    exposed claims = claims since go-live whose content_hash ALSO exists in another live shard,
                     i.e. the ones a seeded authority could have denied.

If exposed == 0, then denials == 0 carries no information about the denial rate. It is not weak
evidence of rarity; it is the absence of a trial. This is the habit banked on 2026-07-31 —
compute the instrument's blind fraction as a column before quoting its reading — applied to the
instrument the thread built that same day.

WHAT ELSE IT REPORTS

  1. COPIES-PER-HASH over the live shards, and the shard SET the multi-copy population occupies.
     The thread recorded the leak as two shards. It is four, and they are the same four for
     every one of the 12,659 hashes — one transcript corpus written four times over.

  2. ORDER, NOT CLOCK. Whether the duplicated population was written before the authority's
     first claim — decided on within-shard AUTOINCREMENT id, because `observations.ts` is write
     time before c48af34 and the event's own transcript time after, mixed in one column with no
     write clock anywhere. The first draft of this section used timestamps and returned a
     confident wrong answer; the comment at the section says what it was.

  3. CONTROLS, because "0/44 exposed" is exactly the shape of a broken join. The hash spaces
     must be shown comparable before a zero across them means anything:
       positive — every claimed hash must be found in its OWN shard's observations (same
                  content_hash function, so a miss indicts the join, not the corpus).
       negative — fabricated hashes must match nothing.
     A zero cross-shard is only readable when the positive control is 100%.

  4. COVERAGE — what fraction of the duplicated population the authority actually holds. This is
     what `--check` asserts, and it is RED today by design: the acceptance criterion for the
     backfill decision must fail on the tree that has not made it.

Usage:
  python3 audit_claim_exposure.py            # full report
  python3 audit_claim_exposure.py --check    # assertions, exit 1 on the disputed premise
"""
import sys, os, glob, sqlite3, hashlib
from collections import Counter, defaultdict

ROOT = os.path.expanduser(os.environ.get('SNARC_ROOT', '~/.snarc'))
CHECK = '--check' in sys.argv


def ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def load_shards():
    """hash -> set(shard), plus per-shard (min_ts, max_ts, n)."""
    owner, meta = defaultdict(set), {}
    for db in sorted(glob.glob(os.path.join(ROOT, 'projects', '*', 'snarc.db'))):
        shard = os.path.basename(os.path.dirname(db))
        c = ro(db)
        for (h,) in c.execute("SELECT content_hash FROM observations WHERE content_hash IS NOT NULL"):
            owner[h].add(shard)
        meta[shard] = c.execute("SELECT min(ts), max(ts), count(*) FROM observations").fetchone()
        c.close()
    return owner, meta


def main():
    seen_path = os.path.join(ROOT, 'seen.db')
    if not os.path.exists(seen_path):
        print(f"no authority at {seen_path} — nothing to audit")
        return 1 if CHECK else 0

    owner, meta = load_shards()
    s = ro(seen_path)
    claims = s.execute("SELECT content_hash, first_shard, first_ts FROM seen").fetchall()
    denials = s.execute("SELECT COUNT(*) FROM claim_conflict").fetchone()[0]
    go_live = s.execute("SELECT min(first_ts) FROM seen").fetchone()[0]
    s.close()

    reds = []

    # -- 1. the leak population -------------------------------------------------
    dist = Counter(len(v) for v in owner.values())
    print("=== 1. copies-per-hash across live shards ===")
    for k in sorted(dist):
        print(f"  in {k} shard(s): {dist[k]:>7}")
    multi = {h: v for h, v in owner.items() if len(v) > 1}
    setcount = Counter(tuple(sorted(v)) for v in multi.values())
    print(f"  duplicated hashes: {len(multi)}/{len(owner)} = {100*len(multi)/max(len(owner),1):.1f}%")
    for shards, n in setcount.most_common(3):
        print(f"    {n:>7} hashes in exactly: {', '.join(shards)}")

    # -- 2. the three minutes ---------------------------------------------------
    # ORDERING, NOT TIMESTAMPS. The first draft of this section compared `observations.ts`
    # against seen.first_ts and produced a confident wrong reading ("a duplicated write landed
    # 10 minutes AFTER go-live"). `ts` changed meaning underneath it: write time before c48af34,
    # the EVENT's own transcript time after — and the store holds both generations in one column,
    # with no write clock anywhere. seen.first_ts is COALESCE(event ts, now), so the comparison
    # was event-time against event-time and dated nothing. The instrument that survives is
    # within-shard write ORDER: `id` is AUTOINCREMENT, so "did the duplicated population land
    # before the authority's first claim" is a total order inside each shard, and it needs no
    # clock at all. This is the thread's own lesson arriving one level down — a column whose
    # semantics moved is exactly the constant-wearing-provenance's-clothes shape.
    print("\n=== 2. was the duplicated population written before the authority started claiming? ===")
    dup_shards = set()
    for shards in setcount:
        if setcount[shards] >= 100:
            dup_shards |= set(shards)
    claimed_hashes = {h for h, _, _ in claims}
    print(f"  seen.db first claim (event-time, NOT a write clock): {go_live}")
    verdicts = []
    for shard in sorted(dup_shards):
        db = os.path.join(ROOT, 'projects', shard, 'snarc.db')
        c = ro(db)
        dup_ids, claim_ids = [], []
        for (i, h) in c.execute("SELECT id, content_hash FROM observations WHERE content_hash IS NOT NULL"):
            if h in multi:
                dup_ids.append(i)
            if h in claimed_hashes:
                claim_ids.append(i)
        c.close()
        if not claim_ids:
            print(f"  {shard}: {len(dup_ids)} duplicated rows, 0 post-go-live claims "
                  f"— this shard has not written since the authority came up")
            continue
        before = max(dup_ids) < min(claim_ids) if dup_ids else True
        verdicts.append(before)
        print(f"  {shard}: duplicated rows id<={max(dup_ids)}, first post-go-live claim id="
              f"{min(claim_ids)}  -> all duplicated writes precede the authority: {before}")
    if verdicts and all(verdicts):
        print("  -> in every shard that can answer, the whole duplicated population was written")
        print("     BEFORE the authority's first claim. The guard came up after the incident,")
        print("     and CREATE TABLE inherits nothing already on disk.")
    elif verdicts:
        print("  -> at least one duplicated row was written AFTER a claim in the same shard: the")
        print("     leak is running with the authority up. That is a different (worse) defect.")
        reds.append("a duplicated row post-dates a claim in the same shard")

    # -- 3. controls ------------------------------------------------------------
    print("\n=== 3. controls — is the hash space comparable at all? ===")
    self_hit = sum(1 for h, sh, _ in claims if sh in owner.get(h, set()))
    print(f"  positive: claimed hash found in its OWN shard   {self_hit}/{len(claims)}"
          f" = {100*self_hit/max(len(claims),1):.1f}%")
    fabricated = [hashlib.sha256(f'fabricated-{i}'.encode()).hexdigest() for i in range(300)]
    fab_hit = sum(1 for h in fabricated if h in owner)
    print(f"  negative: fabricated hashes matching anything    {fab_hit}/300")
    if len(claims) and self_hit != len(claims):
        reds.append(f"positive control {self_hit}/{len(claims)} — the cross-shard zero below is "
                    f"unreadable; a broken join looks exactly like an absent collision")
    if fab_hit:
        reds.append(f"negative control matched {fab_hit}/300 fabricated hashes")

    # -- 4. the exposure denominator -------------------------------------------
    print("\n=== 4. the denominator under `denials: 0` ===")
    exposed = [(h, sh) for h, sh, _ in claims if (owner.get(h, set()) - {sh})]
    print(f"  claims since go-live:                    {len(claims)}")
    print(f"  of those, EXPOSED (content also in another shard, i.e. deniable): {len(exposed)}")
    print(f"  denials recorded:                        {denials}")
    if len(exposed) == 0:
        print("  -> denials/exposed = 0/0. The zero is not a low rate. It is no trial:")
        print("     the population that produces denials had stopped writing before go-live.")
    else:
        print(f"  -> denial rate over the exposed population: {denials}/{len(exposed)}"
              f" = {100*denials/len(exposed):.1f}%")

    # -- 5. coverage: the acceptance criterion for the backfill decision --------
    print("\n=== 5. authority coverage of the duplicated corpus ===")
    claimed_hashes = {h for h, _, _ in claims}
    covered = sum(1 for h in multi if h in claimed_hashes)
    print(f"  duplicated hashes the authority holds:  {covered}/{len(multi)}"
          f" = {100*covered/max(len(multi),1):.1f}%")
    if multi and covered == 0:
        print("  -> a replay of this corpus into a NEW shard would be claimed, not denied.")
        print("     Behavioural, not inferred: acceptance_claim_recurrence.mjs check 1.")
        reds.append(f"the authority holds 0 of {len(multi)} duplicated hashes — it is inert "
                    f"against its own motivating incident until seen.db is backfilled")

    if CHECK:
        print()
        for r in reds:
            print(f"RED: {r}")
        print(f"ok: {len(reds)} failure(s)" if reds else "ok: 0 failure(s)")
        return 1 if reds else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
