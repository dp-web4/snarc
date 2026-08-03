#!/usr/bin/env python3
"""
spread_falsification_kimi.py — the run owed from notice 534 / forum re 528.

Runs CBP's falsification check (never-shown placebo vs paired within-briefing
spread) against the ARCHIVE store (~/.engram), per CBP's cut-over warning:
the live store (~/.snarc/projects) only holds post-2026-07-31-04:20 data.

Arms:
  A (surfaced):   retrieval_log rows source='briefing', relevant as scored.
  B (never-shown placebo): for every surfaced row, a same-kind item from the
     same shard, eligible under the selector's own filters at that time, that
     was NOT surfaced in any briefing within [.., T+6h]. Scored by the exact
     rule of scoreRetrievals(): >=2 shared significant tokens with same-cwd
     observations in (T, T+6h]. Control pick reported under three strategies
     (newest / oldest / seeded-random eligible item) as a sensitivity range.

Also: replication of CBP's archive headline numbers, within-briefing and
within-block differential-recurrence fractions (the spread), and a per-briefing
reconstruction of briefing length to test the truncation seam
(memory.ts:427 — logged-but-unshown tail) on the archive. Reconstruction uses
end-of-archive table state (frequency/confidence/salience as frozen at cutover),
so per-briefing lengths are approximate; ordering replicates
db.ts (patterns: frequency DESC, confidence DESC; identity: confidence DESC;
observations: 20 most recent, salience>=0.35).

Registered prediction (kimi, notice 534 reply):
  (a) the never-shown version reports a large recurrence premium;
  (b) the within-briefing spread on the same briefings is flat or near-flat.

Token rule replicated from snarc/src/memory.ts:80-92 (sigTokens, STOP_TOKENS).
"""

import os
import random
import re
import sqlite3
import sys
from collections import defaultdict, Counter
from datetime import datetime, timedelta

ARCHIVE = os.path.expanduser("~/.engram/projects")
LIVE = os.path.expanduser("~/.snarc/projects")
CAP = 2000  # maxTokens(500) * 4, memory.ts:427

SIG1 = re.compile(r"[a-z0-9_.\-]+/[a-z0-9_./\-]+")
SIG2 = re.compile(r"[a-z0-9_\-]+\.[a-z0-9]{1,5}\b")
SIG3 = re.compile(r"[a-z][a-z0-9_]{3,}")

STOP_TOKENS = {
    'this', 'that', 'with', 'from', 'have', 'will', 'into', 'then', 'they',
    'them', 'what', 'when', 'which', 'were', 'been', 'your', 'about', 'there',
    'these', 'would', 'could', 'true', 'false', 'null', 'none', 'name',
    'type', 'text', 'value', 'data', 'file', 'line',
}


def sig_tokens(text):
    t = (text or '').lower()
    return set(SIG1.findall(t)) | set(SIG2.findall(t)) | set(SIG3.findall(t))


def match_key(content):
    return [t for t in sig_tokens(content) if t not in STOP_TOKENS][:40]


def score(mem_toks, session_toks):
    if not mem_toks:
        return 0
    return 1 if len(set(mem_toks) & session_toks) >= 2 else 0


def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def load_shard(path):
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {r[0] for r in db.execute(
            "select name from sqlite_master where type='table'")}
        if 'retrieval_log' not in tables:
            db.close()
            return None
        rl = db.execute(
            "select id, surfaced_ts, cwd, source, item_kind, estimate, "
            "match_key, relevant from retrieval_log").fetchall()
        if not rl:
            db.close()
            return None
        obs = db.execute(
            "select ts, cwd, tool_name, input_summary, output_summary, "
            "salience from observations").fetchall() \
            if 'observations' in tables else []
        pats = db.execute(
            "select kind, summary, detail, confidence, frequency, created_at "
            "from patterns").fetchall() if 'patterns' in tables else []
        iden = db.execute(
            "select key, value, confidence, created_at from identity"
        ).fetchall() if 'identity' in tables else []
        db.close()
        return {'rl': rl, 'obs': obs, 'pats': pats, 'iden': iden}
    except sqlite3.Error:
        return None


def reconstruct_briefing_len(d, T):
    """Approximate rendered length of the briefing the selector would emit in
    shard d at time T (end-state attributes; see module docstring)."""
    lines = []
    pats = [(p[4], p[3], p) for p in d['pats']
            if p[3] >= 0.6 and p[0] != 'proposed_identity' and p[5] <= T]
    pats.sort(key=lambda x: (-x[0], -x[1]))
    if pats:
        lines.append("Inferred patterns (heuristic — may not be accurate):")
        for _, _, p in pats[:3]:
            lines.append(f"  - [{p[0]}] {p[1]} (confidence: {p[3]:.2f})")
    recent = sorted((o for o in d['obs'] if o[0] <= T),
                    key=lambda o: o[0], reverse=True)[:20]
    hs = [o for o in recent if o[5] >= 0.35]
    if hs:
        lines.append("Recent observations (directly recorded):")
        for o in hs[:3]:
            lines.append(f"  - [{o[2]}] {(o[3] or '')[:100]} ({o[0]})")
    iden = sorted((i for i in d['iden'] if i[2] >= 0.7 and i[3] <= T),
                  key=lambda i: -i[2])
    if iden:
        lines.append("Project facts (auto-extracted, verify if unsure):")
        for i in iden[:3]:
            lines.append(f"  - {i[0]}: {i[1]}")
    return len("\n".join(lines))


def main():
    tree = sys.argv[1] if len(sys.argv) > 1 else ARCHIVE
    print(f"TREE OPENED: {tree}")
    shards = sorted(
        os.path.join(tree, d, 'engram.db')
        for d in os.listdir(tree)
        if os.path.exists(os.path.join(tree, d, 'engram.db'))
    )
    print(f"shard dbs found: {len(shards)}")

    # identity row count across ALL shards (CBP's basis), light scan
    iden_rows_all = iden_shards_all = 0
    for sp in shards:
        try:
            db = sqlite3.connect(f"file:{sp}?mode=ro", uri=True)
            t = {r[0] for r in db.execute(
                "select name from sqlite_master where type='table'")}
            if 'identity' in t:
                n = db.execute("select count(*) from identity").fetchone()[0]
                if n:
                    iden_rows_all += n
                    iden_shards_all += 1
            db.close()
        except sqlite3.Error:
            pass

    loaded = {}
    for sp in shards:
        data = load_shard(sp)
        if data:
            loaded[sp] = data
    print(f"shards with a retrieval log: {len(loaded)}")

    # ---------- Pass 1: headline replication ----------
    rows = []
    for sp, d in loaded.items():
        for r in d['rl']:
            rows.append((sp, r[1], r[2] or '', r[3], r[4], r[7]))

    br_rows = [r for r in rows if r[3] == 'briefing']
    briefings = defaultdict(list)
    for r in br_rows:
        briefings[(r[0], r[1], r[2])].append(r)

    span = (min(r[1] for r in br_rows), max(r[1] for r in br_rows))
    scored = [r for r in br_rows if r[5] is not None]
    print(f"\n== headline ==")
    print(f"briefing rows: {len(br_rows)}   briefings: {len(briefings)}")
    print(f"span: {span[0]} -> {span[1]}")
    print(f"scored: {len(scored)} ({100*len(scored)/max(1,len(br_rows)):.1f}%)")

    n_all = defaultdict(int)
    rate = defaultdict(lambda: [0, 0])
    for r in br_rows:
        n_all[r[4]] += 1
        if r[5] is not None:
            rate[r[4]][0] += r[5]
            rate[r[4]][1] += 1
    for k in sorted(n_all):
        s, n = rate[k]
        print(f"  {k:11s}: rows {n_all[k]:5d} "
              f"({100*n_all[k]/len(br_rows):.1f}% of briefing rows); "
              f"scored {n}; relevant {100*s/max(1,n):5.1f}%")

    kdist = Counter(len(v) for v in briefings.values())
    print(f"  k distribution: {dict(sorted(kdist.items()))}")
    print(f"  identity rows across ALL shards: {iden_rows_all} "
          f"across {iden_shards_all} shards")

    # ---------- Pass 2: truncation seam, per actual briefing ----------
    print(f"\n== truncation seam (cap = {CAP} chars), per-briefing ==")
    lens = {}
    for (sp, T, cwd), v in briefings.items():
        lens[(sp, T, cwd)] = reconstruct_briefing_len(loaded[sp], T)
    over = {k: l for k, l in lens.items() if l > CAP}
    vals = sorted(lens.values())
    pct = lambda p: vals[min(len(vals) - 1, int(p * len(vals)))]
    print(f"  reconstructed lengths: p50={pct(.5)} p90={pct(.9)} "
          f"p99={pct(.99)} max={vals[-1]}")
    print(f"  briefings over cap: {len(over)} of {len(lens)} "
          f"({100*len(over)/len(lens):.1f}%), in "
          f"{len({k[0] for k in over})} shards")
    if over:
        rows_over = sum(len(briefings[k]) for k in over)
        print(f"  logged rows in over-cap briefings: {rows_over} "
              f"(logged; tail unshown once the cut lands mid-briefing)")

    # ---------- Pass 3: the spread ----------
    print(f"\n== spread (scored rows only) ==")
    b_mixed = b_eligible = 0
    block_stats = defaultdict(lambda: [0, 0])
    block_k = defaultdict(Counter)
    for key, v in briefings.items():
        sc = [r for r in v if r[5] is not None]
        if len(sc) >= 2:
            b_eligible += 1
            if len({r[5] for r in sc}) > 1:
                b_mixed += 1
        by_kind = defaultdict(list)
        for r in sc:
            by_kind[r[4]].append(r[5])
        for kind, rels in by_kind.items():
            block_k[kind][len(rels)] += 1
            if len(rels) >= 2:
                block_stats[kind][1] += 1
                if len(set(rels)) > 1:
                    block_stats[kind][0] += 1
    print(f"  briefings with >=2 scored rows: {b_eligible}; "
          f"differential recurrence: {b_mixed} "
          f"({100*b_mixed/max(1,b_eligible):.1f}%)")
    for kind in sorted(block_stats):
        m, e = block_stats[kind]
        print(f"  within-block {kind:11s}: blocks {e}, k={dict(block_k[kind])}, "
              f"differential {m} ({100*m/max(1,e):.1f}%)")

    # ---------- Pass 4: never-shown placebo, 3 control strategies ----------
    print(f"\n== never-shown placebo vs surfaced ==")
    rng = random.Random(20260731)
    arm_a = defaultdict(lambda: [0, 0])
    arm_b = {s: defaultdict(lambda: [0, 0])
             for s in ('newest', 'oldest', 'random')}
    n_missing = defaultdict(int)

    for sp, d in loaded.items():
        obs_toks = []
        for t, c, tool, i, o, sal in d['obs']:
            toks = {x for x in sig_tokens(f"{i or ''} {o or ''}")
                    if x not in STOP_TOKENS}
            obs_toks.append((t, c or '', toks))
        obs_toks.sort(key=lambda x: x[0])

        rl = sorted(d['rl'], key=lambda r: r[1])
        surf_events = [(r[1], frozenset((r[6] or '').split())) for r in rl]

        pat_pool = []
        for k, s, det, c, f, ca in d['pats']:
            if c >= 0.6 and k != 'proposed_identity':
                mk = match_key(f"{s} {det or ''}")
                if mk:
                    pat_pool.append((ca, frozenset(mk)))
        iden_pool = []
        for k, v, c, ca in d['iden']:
            if c >= 0.7:
                mk = match_key(f"{k} {v}")
                if mk:
                    iden_pool.append((ca, frozenset(mk)))
        obs_pool = []
        for t, c, tool, i, o, sal in d['obs']:
            if sal >= 0.35:
                mk = match_key(f"{i or ''} {o or ''}")
                if mk:
                    obs_pool.append((t, frozenset(mk)))
        for p in (pat_pool, iden_pool, obs_pool):
            p.sort(key=lambda x: x[0])
        pools = {'pattern': pat_pool, 'identity': iden_pool,
                 'observation': obs_pool}

        rows_by_b = defaultdict(list)
        for r in rl:
            if r[3] == 'briefing':
                rows_by_b[(r[1], r[2] or '')].append(r)

        for (T, cwd) in sorted(rows_by_b):
            t1s = (parse_ts(T) + timedelta(hours=6)).strftime(
                "%Y-%m-%d %H:%M:%S")
            sess = set()
            for ts, c, toks in obs_toks:
                if ts <= T:
                    continue
                if ts > t1s:
                    break
                if c == cwd:
                    sess |= toks
            excl = {mk for sts, mk in surf_events if sts <= t1s}
            elig = {}
            for kind, pool in pools.items():
                elig[kind] = [mk for ca, mk in pool
                              if ca <= T and mk not in excl]
            for r in rows_by_b[(T, cwd)]:
                kind = r[4]
                if r[7] is None:
                    continue
                pool = elig.get(kind, [])
                if not pool:
                    for s in arm_b:
                        n_missing[s] += 1
                    continue
                # paired restriction: arm A counts only rows that HAVE a
                # control, so the premium compares identical denominators
                arm_a[kind][0] += r[7]
                arm_a[kind][1] += 1
                picks = {'newest': pool[-1], 'oldest': pool[0],
                         'random': rng.choice(pool)}
                for s, mk in picks.items():
                    arm_b[s][kind][0] += score(mk, sess)
                    arm_b[s][kind][1] += 1

    print(f"  surfaced rows scored: {sum(v[1] for v in arm_a.values())}; "
          f"rows with no eligible control: {n_missing['newest']}")
    for kind in sorted(arm_a):
        a = arm_a[kind]
        pa = 100*a[0]/max(1, a[1])
        line = f"  {kind:11s}: surfaced {pa:5.1f}% (n={a[1]}) |"
        for s in ('newest', 'oldest', 'random'):
            b = arm_b[s][kind]
            pb = 100*b[0]/max(1, b[1])
            line += f" never-shown[{s}] {pb:5.1f}% (prem {pa-pb:+5.1f}) |"
        print(line)
    a_tot = [sum(v[0] for v in arm_a.values()),
             sum(v[1] for v in arm_a.values())]
    pa = 100*a_tot[0]/max(1, a_tot[1])
    line = f"  {'TOTAL':11s}: surfaced {pa:5.1f}% (n={a_tot[1]}) |"
    for s in ('newest', 'oldest', 'random'):
        b = [sum(v[0] for v in arm_b[s].values()),
             sum(v[1] for v in arm_b[s].values())]
        pb = 100*b[0]/max(1, b[1])
        line += f" never-shown[{s}] {pb:5.1f}% (prem {pa-pb:+5.1f}) |"
    print(line)


if __name__ == '__main__':
    main()
