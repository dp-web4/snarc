#!/usr/bin/env python3
"""
audit_recovery_payout.py — what the event_session_id recovery can and cannot decide, measured
over the WHOLE duplicated corpus rather than a per-shard sample.

WHY THIS EXISTS. audit_claim_conflict_decidability.py established that `session_id` is a
constant (888f190a) across every bulk shard, so the join "does the owner hold this denial's
session" returns TRUE with zero information — and proposed `event_session_id`, recovered from
the transcripts, as the axis that can actually classify a denial. It reported 99.0% of sampled
pre-authority rows recoverable to a unique conversation, and on the strength of that number the
thread has held the `seen` backfill behind the recovery for five posts:

    1. recover event_session_id   2. backfill seen   3. every loser is a claim_conflict row
    "Running step 2 first is not wrong so much as irreversible on the axis step 1 repairs."

That premise needs the same treatment its predecessor got. The recovery's key is `norm(content)`
— the row's own text, and nothing else. It does not read the shard, the row id, or the write
time. For a DUPLICATED hash the copies are the same content by definition, so the recovery is a
constant function across them: winner and loser receive the same value, always. The question the
backfill turns on is not "which conversation is this content from" but "did denying THIS COPY
destroy a second conversation's only record" — and a content-keyed instrument cannot reach it.

WHAT THIS REPORTS

  1. THE DUPLICATED CORPUS — copies per hash, and which shards.
  2. IS THE KEY CONSTANT ACROSS COPIES — byte-identity of `input_summary` per duplicated hash.
     This is the empirical half of the argument above; the structural half is the function
     signature. If it comes back < 100% the constancy claim is REFUTED and the recovery can
     discriminate after all, so this section is written to be able to say so.
  3. WHICH COLUMN COULD CARRY PER-COPY PROVENANCE — variation across copies, per column. A
     column that is constant across copies cannot separate them; one that varies might.
  4. FULL-POPULATION CLASSIFICATION — every duplicated hash against the transcript index:
     unique / ambiguous / unmatched / too-short. The decision-relevant cell is AMBIGUOUS: the
     rows where two conversations really are in play and the instrument declines.
  5. CONTROLS — fabricated and one-character-mutated heads through the same path. The shards
     were BUILT from these transcripts, so a high match rate is the null result, not the finding.

  --check is RED when the evidence is ABSENT (no shards, no duplicates, no transcript index) and
  when a control matches. An instrument whose evidence is missing must be loud: the first draft
  of audit_arrival_anchor.py returned green with its entire outside anchor absent.

Usage:
  python3 audit_recovery_payout.py            # full report
  python3 audit_recovery_payout.py --check    # assertions, exit 1 on drift or absent evidence
"""
import sys, os, re, json, glob, sqlite3
from collections import defaultdict, Counter

SNARC_ROOT = os.path.expanduser("~/.snarc")
SHARD_GLOB = os.path.join(SNARC_ROOT, "projects", "*", "*.db")
TRANSCRIPT_GLOB = os.path.expanduser("~/.claude/projects/*/*.jsonl")

PREFIX = 80          # same head length as audit_claim_conflict_decidability.py — one key, one meaning
MIN_KEY = 40
TAG_RE = re.compile(r"^\[(?:Human|Claude)\]\s*")

# The columns a per-copy instrument could possibly use. `cwd` is listed knowing the answer:
# the shard id IS sha256(cwd), so cwd varying across copies is the shard restated, not provenance.
COPY_COLS = ["input_summary", "session_id", "scored_by", "cwd", "tags", "tool_name"]

CONTROL_PROBES = [
    "[Human] the quarterly revenue forecast for the northern district was revised upward after",
    "[Claude] I have adjusted the thermostat schedule to reduce heating during unoccupied hours",
    "[Human] please summarize the attached lease agreement and flag any unusual indemnity clauses",
    "[Claude] The migration completed successfully and all seventeen tables were verified intact",
    "[Human] can you explain why the sourdough starter is not rising after four days of feeding",
]


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def norm(text):
    t = TAG_RE.sub("", text or "")
    return " ".join(t.split())[:PREFIX]


def load_corpus():
    """content_hash -> {shard: row_tuple}, over every shard on disk."""
    per = defaultdict(dict)
    shard_rows = {}
    for path in sorted(glob.glob(SHARD_GLOB)):
        shard = os.path.basename(os.path.dirname(path))
        try:
            c = ro(path)
            cols = [r[1] for r in c.execute("PRAGMA table_info(observations)")]
            sel = [x for x in COPY_COLS if x in cols]
            q = f"SELECT content_hash, {', '.join(sel)} FROM observations WHERE content_hash IS NOT NULL"
            n = 0
            for row in c.execute(q):
                h, rest = row[0], row[1:]
                if shard not in per[h]:
                    per[h][shard] = dict(zip(sel, rest))
                n += 1
            shard_rows[shard] = n
            c.close()
        except sqlite3.Error:
            shard_rows[shard] = None
    return per, shard_rows


def transcript_index():
    idx = defaultdict(set)
    files = sorted(glob.glob(TRANSCRIPT_GLOB))
    for f in files:
        try:
            with open(f, errors="replace") as fh:
                for line in fh:
                    if '"sessionId"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    sid = e.get("sessionId")
                    if not sid:
                        continue
                    m = e.get("message") or {}
                    content = m.get("content") if isinstance(m, dict) else None
                    if isinstance(content, list):
                        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                    if not isinstance(content, str):
                        content = e.get("text") if isinstance(e.get("text"), str) else None
                    if not content:
                        continue
                    k = norm(content)
                    if len(k) >= MIN_KEY:
                        idx[k].add(sid)
        except OSError:
            continue
    return idx, len(files)


def pct(n, d):
    return f"{100.0 * n / d:.2f}%" if d else "n/a"


def main():
    check = "--check" in sys.argv
    fails = []

    print("=" * 78)
    print("recovery payout — can a content-keyed event_session_id decide the backfill?")
    print("=" * 78)

    per, shard_rows = load_corpus()
    dup = {h: v for h, v in per.items() if len(v) >= 2}

    # ---------------------------------------------------------------- 1
    print("\n[1] the duplicated corpus")
    print(f"  shards on disk: {len(shard_rows)}   rows: " +
          ", ".join(f"{s}={n}" for s, n in sorted(shard_rows.items(), key=lambda kv: -(kv[1] or 0))[:8]))
    copies = Counter(len(v) for v in per.values())
    print(f"  copies per hash: " + "  ".join(f"{k}->{v}" for k, v in sorted(copies.items())))
    print(f"  duplicated hashes (>=2 shards): {len(dup)}")
    shard_sets = Counter(tuple(sorted(v)) for v in dup.values())
    for combo, n in shard_sets.most_common(4):
        print(f"    {n:6d} in {', '.join(combo)}")
    if not dup:
        fails.append("no duplicated hashes — the population this audit measures is ABSENT, "
                     "and every ratio below would be 0/0 rather than a finding")

    # ---------------------------------------------------------------- 2
    print("\n[2] is the recovery key constant across the copies of a hash?")
    same_raw = same_key = 0
    differing = []
    for h, v in dup.items():
        vals = [d.get("input_summary") for d in v.values()]
        if len(set(vals)) == 1:
            same_raw += 1
        else:
            differing.append(h)
        if len({norm(x) for x in vals}) == 1:
            same_key += 1
    n = len(dup) or 1
    print(f"  input_summary byte-identical across copies : {same_raw}/{len(dup)}  ({pct(same_raw, len(dup))})")
    print(f"  norm(input_summary) — the actual key       : {same_key}/{len(dup)}  ({pct(same_key, len(dup))})")
    if differing:
        print(f"  -> {len(differing)} hashes DIFFER between copies. The constancy claim is REFUTED for these;")
        print(f"     a content-keyed recovery CAN separate them and the payout argument changes.")
        fails.append(f"{len(differing)} duplicated hashes carry differing content across copies — "
                     f"this audit's shipped claim (0 of 12,668 on 2026-07-31) no longer holds")
    else:
        print("  -> the key is a CONSTANT function across the copies of every duplicated hash.")
        print("     Winner and loser of a denial receive the same recovered value, by construction")
        print("     and by measurement. The column cannot classify a denial.")

    # ---------------------------------------------------------------- 3
    print("\n[3] which column could carry per-copy provenance?")
    var = {c: [0, 0] for c in COPY_COLS}
    for h, v in dup.items():
        for c in COPY_COLS:
            vals = {d.get(c) for d in v.values() if c in d}
            if not vals:
                continue
            var[c][0 if len(vals) == 1 else 1] += 1
    print(f"  {'column':16s} {'constant':>10s} {'varies':>10s}   reading")
    READING = {
        "input_summary": "the recovery key — constant is the finding of [2]",
        "session_id":    "the INGEST session (888f190a) — constant, the defect already named",
        "scored_by":     "the scorer version at write time — not the event's conversation",
        "cwd":           "the shard restated (shard id = sha256(cwd)) — not provenance",
        "tags":          "content-derived",
        "tool_name":     "content-derived",
    }
    for c in COPY_COLS:
        print(f"  {c:16s} {var[c][0]:10d} {var[c][1]:10d}   {READING[c]}")
    print("  -> no column carries the EVENT's conversation per copy. What remains that varies is")
    print("     `id` and `ts`, which are write order — they say when a copy landed, never who said it.")

    # ---------------------------------------------------------------- 4
    print("\n[4] full-population classification against the transcript corpus")
    idx, nfiles = transcript_index()
    print(f"  {nfiles} transcript files, {len(idx)} distinct turn-heads indexed")
    if not idx:
        fails.append("the transcript index is EMPTY — section 4 can classify nothing and its "
                     "zeros would be an absent instrument, not a measurement")
    st = Counter()
    amb_sizes = Counter()
    for h, v in dup.items():
        text = next(iter(v.values())).get("input_summary")
        k = norm(text)
        if len(k) < MIN_KEY:
            st["too_short"] += 1
            continue
        hit = idx.get(k)
        if not hit:
            st["unmatched"] += 1
        elif len(hit) == 1:
            st["unique"] += 1
        else:
            st["ambiguous"] += 1
            amb_sizes[len(hit)] += 1
    for label in ("unique", "ambiguous", "unmatched", "too_short"):
        print(f"    {label:11s} {st[label]:6d}  ({pct(st[label], len(dup))})")
    if amb_sizes:
        print(f"    ambiguity sizes: " + ", ".join(f"{k} convs -> {v}" for k, v in sorted(amb_sizes.items())))
    print("  -> `unique` is the cell that changes nothing: the content traces to one conversation,")
    print("     so BOTH copies are that conversation's and the denial destroys no distinct record.")
    print("     `ambiguous` is the only cell where the answer could matter — and there the")
    print("     instrument returns >1 and declines, because no per-copy input exists (section 3).")

    # ---------------------------------------------------------------- 5
    print("\n[5] controls — the shards were BUILT from these transcripts, so a high match rate is null")
    fab = sum(1 for p in CONTROL_PROBES if idx.get(norm(p)))
    sample = [next(iter(v.values())).get("input_summary") for v in list(dup.values())[:300]]
    real = sum(1 for t in sample if idx.get(norm(t)))
    mut = elig = 0
    for t in sample:
        k = norm(t)
        if len(k) < MIN_KEY:
            continue
        elig += 1
        bad = k[:20] + ("Z" if k[20] != "Z" else "Q") + k[21:]
        if idx.get(bad):
            mut += 1
    print(f"  fabricated turns matched : {fab}/{len(CONTROL_PROBES)}   (want 0)")
    print(f"  1-char-mutated heads     : {mut}/{elig}   (want 0)")
    print(f"  real rows matched        : {real}/{len(sample)}")
    if fab:
        fails.append(f"{fab} fabricated probes matched — the head index is not discriminating")
    if mut:
        fails.append(f"{mut} one-character mutations matched — the key is too loose to trust")

    # ---------------------------------------------------------------- verdict
    print("\n[verdict]")
    print(f"  Of {len(dup)} duplicated hashes, {st['unique']} ({pct(st['unique'], len(dup))}) resolve to one")
    print(f"  conversation — an answer that is the SAME for winner and loser, so it moves no")
    print(f"  assignment. {st['ambiguous']} ({pct(st['ambiguous'], len(dup))}) resolve to more than one, and there")
    print(f"  the instrument declines. The recovery is decision-irrelevant on the first group and")
    print(f"  unable on the second. Its real payout is this measurement: the duplication is")
    print(f"  attribution-lossless for {pct(st['unique'], len(dup))} of the corpus and undecidable for {st['ambiguous']} hashes.")
    print(f"  Sequencing the backfill behind it buys nothing — see acceptance_recovery_ordering.mjs,")
    print(f"  which shows recover-first and backfill-then-recover reaching the identical state.")

    if check:
        print()
        for f in fails:
            print(f"FAIL: {f}")
        print(f"ok: {len(fails)} failure(s)")
        sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
