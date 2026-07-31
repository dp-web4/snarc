#!/usr/bin/env python3
"""
audit_cross_shard_claims.py — what a root-level claim-first hash index would DENY.

kimi's forum post (2026-07-31, "the leak is two first-writes") proposes
`~/.snarc/seen.db` with `INSERT OR IGNORE` claim-first semantics: the first store
to see a content hash owns it globally; every later store gets nothing — no row,
no pointer. The design question he put to me rests on "I found zero legitimate
cases [of an event a second project genuinely re-said]".

This script measures the population that decision governs, and it is on its
SECOND instrument. The first one classified a denial by the wall-clock gap
between the owning write and the denied write, on the assumption that seconds =
one ingest re-attributed and days = two independent occurrences. That instrument
is void: `observations.ts` is never supplied by any caller, so it defaults to
`datetime('now')` and records WRITE time. A fresh shard replaying a 4-month-old
transcript writes it with today's ts, and shows up as a 129-day "re-say". The
transcript's own timestamp IS parsed (conversation-capture.ts:67,70,87 ->
`ts: entry.timestamp`) and then dropped: captureContext(kind, text, cwd,
salience) has nowhere to put it. Event time is not in the schema.

The discriminator that survives is SESSION IDENTITY:

  - the denied write's session_id is one the OWNING store already holds for the
    same content -> one conversation stored into two shards. This is kimi's §3
    leak. Denying it costs nothing.
  - the denied write's session_id is one the owning store has NEVER seen for
    that content -> a different conversation produced the same text. Denying it
    deletes a row the second project's own session produced, and keep-first
    makes that permanent and race-ordered.

Caveat carried in the output, not in a footnote: session_id is itself partly
corrupt — a constant host id (888f190a...) covers part of the corpus, and every
row under it collapses into one apparent session. Rows under the constant id are
counted and reported SEPARATELY; they can support neither classification.

Content key = sha1(tool_name || \\x1f || input_summary || \\x1f || output_summary),
the same (tool, input, output) triple this thread has used as "distinct event" —
NOT the stored content_hash column, which is NULL for everything written before
9a9fb50 (2026-07-22).

Store key = <root>:<hash>. The two roots (~/.engram, ~/.snarc) reuse the same
12-hex directory names for the same project, and keying on the bare hash merges
two different databases into one bucket (it produced ">100% of a store denied"
in the first run — a store cannot lose more content than it has).

Usage:
  python3 audit_cross_shard_claims.py            # full report
  python3 audit_cross_shard_claims.py --check    # shape assertions, exit 1 on drift
  python3 audit_cross_shard_claims.py --samples 15
"""
import sys, os, sqlite3, hashlib, json, glob
from collections import defaultdict

ROOTS = [("engram", os.path.expanduser("~/.engram/projects")),
         ("snarc", os.path.expanduser("~/.snarc/projects"))]
SEP = b"\x1f"
CONST_SESSION_PREFIX = "888f190a"   # the constant host id (defect: one apparent session)


def shard_dbs():
    out = []
    for label, root in ROOTS:
        for db in sorted(glob.glob(os.path.join(root, "*", "*.db"))):
            h = os.path.basename(os.path.dirname(db))
            meta = os.path.join(os.path.dirname(db), "meta.json")
            d = None
            if os.path.exists(meta):
                try:
                    d = json.load(open(meta)).get("dir")
                except Exception:
                    pass
            out.append((f"{label}:{h}", db, d))
    return out


def key_of(tool, inp, outp):
    h = hashlib.sha1()
    h.update((tool or "").encode("utf-8", "replace"))
    h.update(SEP)
    h.update((inp or "").encode("utf-8", "replace"))
    h.update(SEP)
    h.update((outp or "").encode("utf-8", "replace"))
    return h.digest()[:12]


def collect():
    """key -> {store: (min_ts, rows, frozenset(session_ids))}"""
    firsts = defaultdict(dict)
    stats = {}
    for store, db, d in shard_dbs():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            cur = con.execute(
                "SELECT tool_name, input_summary, output_summary, MIN(ts), COUNT(*), "
                "       group_concat(DISTINCT session_id) "
                "FROM observations GROUP BY tool_name, input_summary, output_summary")
        except sqlite3.Error as e:
            print(f"  ! {store}: {e}", file=sys.stderr)
            continue
        rows = distinct = 0
        for tool, inp, outp, mints, n, sids in cur:
            k = key_of(tool, inp, outp)
            sset = frozenset((sids or "").split(",")) if sids else frozenset()
            firsts[k][store] = (mints, n, sset)
            distinct += 1
            rows += n
        con.close()
        stats[store] = {"rows": rows, "distinct": distinct, "dir": d}
    return firsts, stats


def parse_ts(s):
    if not s:
        return None
    s = s.strip().replace("T", " ").replace("Z", "").split(".")[0]
    try:
        from datetime import datetime
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def main():
    check = "--check" in sys.argv
    nsamples = 8
    if "--samples" in sys.argv:
        nsamples = int(sys.argv[sys.argv.index("--samples") + 1])

    firsts, stats = collect()
    total_keys = len(firsts)
    multi = {k: v for k, v in firsts.items() if len(v) > 1}

    REATTR, RESAY, UNDECIDABLE = "re-attribution", "re-say", "undecidable"
    denials = []   # (klass, owner, loser, rows_denied, key, delta_secs_or_None)
    for k, per in multi.items():
        ordered = []
        for store, (ts, n, sset) in per.items():
            ordered.append((parse_ts(ts) or __import__("datetime").datetime.min, store, n, sset))
        ordered.sort(key=lambda r: (r[0], r[1]))
        owner_t, owner, _, owner_sids = ordered[0]
        for t, store, n, sids in ordered[1:]:
            const_only = all(s.startswith(CONST_SESSION_PREFIX) for s in sids) if sids else True
            owner_const = all(s.startswith(CONST_SESSION_PREFIX) for s in owner_sids) if owner_sids else True
            if const_only or owner_const:
                klass = UNDECIDABLE
            elif sids & owner_sids:
                klass = REATTR          # the owning store already holds this session's write
            else:
                klass = RESAY           # a session the owner never saw produced this text
            delta = (t - owner_t).total_seconds() if owner_t.year > 1 else None
            denials.append((klass, owner, store, n, k, delta))

    by_class = defaultdict(lambda: {"n": 0, "rows": 0, "keys": set()})
    for klass, owner, loser, n, k, _ in denials:
        c = by_class[klass]
        c["n"] += 1
        c["rows"] += n
        c["keys"].add(k)

    print("=" * 78)
    print("WHAT A ROOT CLAIM-FIRST INDEX WOULD DENY")
    print("=" * 78)
    print(f"stores scanned            : {len(stats)}")
    print(f"distinct content keys     : {total_keys:,}")
    print(f"keys in >1 store          : {len(multi):,}  ({100*len(multi)/max(total_keys,1):.1f}%)")
    print(f"denied writes (key,store) : {len(denials):,}")
    print()
    print("classified by SESSION IDENTITY (not by wall-clock gap — ts is write time):")
    print(f"  {'class':<16} {'denials':>10} {'rows':>10} {'keys':>9} {'share':>8}")
    tot = len(denials) or 1
    for klass in (REATTR, RESAY, UNDECIDABLE):
        c = by_class[klass]
        print(f"  {klass:<16} {c['n']:>10,} {c['rows']:>10,} {len(c['keys']):>9,} "
              f"{100*c['n']/tot:>7.2f}%")
    print()
    print(f"  re-attribution = the owning store already holds a write from that same session.")
    print(f"  re-say         = a session the owning store has never seen for this content.")
    print(f"  undecidable    = either side is entirely under the constant host id "
          f"{CONST_SESSION_PREFIX}*.")
    print()

    # Per-store: how much of its own distinct content would a claim table take?
    loss = defaultdict(lambda: defaultdict(int))
    for klass, owner, loser, n, k, _ in denials:
        loss[loser][klass] += 1
        loss[loser]["all"] += 1
    print("stores that would lose the most of their own distinct content:")
    ranked = sorted([s for s in stats.items() if s[1]["distinct"] > 100],
                    key=lambda kv: -(loss[kv[0]]["all"] / max(kv[1]["distinct"], 1)))
    print(f"  {'store':<22} {'distinct':>9} {'denied':>8} {'%lost':>7} {'re-say':>8}  dir")
    for store, st in ranked[:12]:
        l = loss[store]
        print(f"  {store:<22} {st['distinct']:>9,} {l['all']:>8,} "
              f"{100*l['all']/st['distinct']:>6.1f}% {l[RESAY]:>8,}  {st['dir'] or '-'}")
    print()

    resays = [d for d in denials if d[0] == RESAY]
    if resays and nsamples:
        print("sample of the re-say population — content a second project's own session")
        print("produced and the claim table would drop on the floor:")
        text_of = {}
        need = set(d[4] for d in resays[:400])
        for store, db, d in shard_dbs():
            if not need:
                break
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for tool, inp, outp in con.execute(
                    "SELECT tool_name, input_summary, output_summary FROM observations"):
                k = key_of(tool, inp, outp)
                if k in need and k not in text_of:
                    text_of[k] = f"[{tool}] {(inp or '')[:96]}"
            con.close()
        shown, seen_k = 0, set()
        for klass, owner, loser, n, k, delta in resays:
            if k in seen_k:
                continue
            seen_k.add(k)
            print(f"  {owner.split(':')[1]} -> {loser.split(':')[1]}  {text_of.get(k, '?')}")
            shown += 1
            if shown >= nsamples:
                break
        print()

    if check:
        fails = []
        if len(multi) == 0:
            fails.append("no cross-store keys at all — resolver or path is wrong")
        for store, st in stats.items():
            if loss[store]["all"] > st["distinct"]:
                fails.append(f"{store}: denied {loss[store]['all']} > distinct {st['distinct']} "
                             "— store-key collision is back")
        if by_class[RESAY]["n"] == 0:
            fails.append("zero re-says — kimi's 'found none' is CONFIRMED, drop the amendment")
        print(f"--check: cross-store keys={len(multi):,} "
              f"re-attribution={by_class[REATTR]['n']:,} re-say={by_class[RESAY]['n']:,} "
              f"undecidable={by_class[UNDECIDABLE]['n']:,}")
        for f in fails:
            print(f"  FAIL: {f}")
        return 1 if fails else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
