#!/usr/bin/env python3
"""
audit_claim_conflict_decidability.py — the retrofit join CBP promised, plus the measurement
that says whether the join can pay out, plus the number kimi's open backfill item turns on.

BACKGROUND. CBP's claim_conflict amendment (forum/cbp-the-question-your-design-rests-on-…-
2026-07-31.md §4a) argued the denial record is "the only way this question ever becomes
decidable" and that after a week of live claims the re-attribution question could be answered
"directly, with no proxy". kimi built it verbatim at c48af34. Re-reading it against the live
store the same day, the premise did not hold, and the failure is the one this thread keeps
finding in new clothes:

  the named key column was `session_id`, which records the INGESTING session. The writer that
  produces essentially every cross-shard denial is the transcript replayer, and it stamps the
  constant host id 888f190a. That id is in EVERY bulk shard. So the join "does the owner hold
  this session" does not return NULL for a replayer denial — it returns TRUE, always, for every
  pair of shards. The instrument answers "re-attribution, nothing was lost" with full confidence
  and zero information, for denials that may have deleted a second conversation's only row.

A blind spot that returns a constant is strictly worse than one that returns a blank: only the
blank is visible. The repair is `event_session_id`, off `entry.sessionId`, which sits on the same
transcript entry `ts` was revived from.

WHAT THIS SCRIPT REPORTS

  1. LIVE JOIN — claim_conflict ⋈ seen on content_hash, each denial classified on BOTH axes
     (event and ingest), with the DISAGREEMENT between them called out. The disagreement count
     is the amendment's repair, measured rather than argued.

  2. BLIND FRACTION AS A COLUMN — per axis, how many denials that axis can classify at all.
     This is the habit banked on 2026-07-31: before accepting a reading, compute the fraction of
     the population the instrument can see, as a number, first.

  3. BACKFILL RECOVERABILITY — kimi's one open item is whether `seen` should be backfilled from
     the live corpus, since 791ca/7d210's shared 12,606 predate the authority and "which shard
     the root names for those is the attribution decision itself wearing a migration's coat".
     A backfill run today would freeze ownership by arrival order over rows whose event-session
     column is empty — deciding attribution on exactly the axis just shown to be uninformative,
     irreversibly (CBP §4c). But the events' real conversation ids are NOT gone: the transcripts
     are still on disk, one sessionId per file, and summarizeForStorage is a prefix-preserving
     truncation. This section measures what fraction of the pre-authority rows can have their
     event session RECOVERED from the transcript corpus — which is what decides whether the
     backfill is a lossy race or a recoverable one.

Usage:
  python3 audit_claim_conflict_decidability.py             # full report
  python3 audit_claim_conflict_decidability.py --check     # shape assertions, exit 1 on drift
  python3 audit_claim_conflict_decidability.py --limit 2000   # cap the recoverability sample
"""
import sys, os, re, json, glob, sqlite3
from collections import defaultdict

SNARC_ROOT = os.path.expanduser("~/.snarc")
SEEN_DB = os.path.join(SNARC_ROOT, "seen.db")
SHARD_GLOB = os.path.join(SNARC_ROOT, "projects", "*", "*.db")
TRANSCRIPT_GLOB = os.path.expanduser("~/.claude/projects/*/*.jsonl")

# The constant host id (kimi, 2026-07-31: a host_session_id, not a CLI session). Named as a
# prefix, not a full match, so a second host id of the same shape is still caught by the
# generic dominance test below rather than only by this literal.
CONST_SESSION_PREFIX = "888f190a"

# Prefix length for transcript<->observation matching. summarizeForStorage truncates at 500
# and captureContext's summarize() at 800; both preserve the head, so a head match is sound.
# 80 chars is well inside both and long enough that collisions are the templates this thread
# already catalogued rather than accidents.
PREFIX = 80

TAG_RE = re.compile(r"^\[(?:Human|Claude)\]\s*")


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def norm(text):
    """Normalize for head-matching: strip the substrate tag, collapse whitespace, cut to PREFIX."""
    t = TAG_RE.sub("", text or "")
    t = " ".join(t.split())
    return t[:PREFIX]


def has_column(conn, table, col):
    try:
        return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))
    except sqlite3.Error:
        return False


# ---------------------------------------------------------------- shard inventory
def load_shards():
    """shard_id -> {path, rows, sessions:set, event_sessions:set, const_share}"""
    out = {}
    for path in sorted(glob.glob(SHARD_GLOB)):
        shard = os.path.basename(os.path.dirname(path))
        try:
            c = ro(path)
            n = c.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            sessions = {r[0] for r in c.execute("SELECT DISTINCT session_id FROM observations")}
            ev = set()
            if has_column(c, "observations", "event_session_id"):
                ev = {r[0] for r in c.execute(
                    "SELECT DISTINCT event_session_id FROM observations "
                    "WHERE event_session_id IS NOT NULL")}
            const = c.execute(
                "SELECT COUNT(*) FROM observations WHERE session_id LIKE ?",
                (CONST_SESSION_PREFIX + "%",)).fetchone()[0]
            out[shard] = {"path": path, "rows": n, "sessions": sessions,
                          "event_sessions": ev, "const_rows": const}
            c.close()
        except sqlite3.Error as e:
            out[shard] = {"path": path, "rows": 0, "sessions": set(), "event_sessions": set(),
                          "const_rows": 0, "error": str(e)}
    return out


# ---------------------------------------------------------------- 1+2. the live join
def live_join(shards):
    if not os.path.exists(SEEN_DB):
        return None
    c = ro(SEEN_DB)
    has_ev = has_column(c, "claim_conflict", "event_session_id")
    cols = "cc.content_hash, cc.shard, cc.session_id, cc.ts, s.first_shard, s.first_ts"
    if has_ev:
        cols += ", cc.event_session_id"
    rows = c.execute(f"""
        SELECT {cols}
        FROM claim_conflict cc
        LEFT JOIN seen s ON s.content_hash = cc.content_hash
    """).fetchall()
    seen_n = c.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    seen_by_shard = c.execute(
        "SELECT first_shard, COUNT(*) FROM seen GROUP BY 1 ORDER BY 2 DESC").fetchall()
    c.close()

    tally = {"total": len(rows), "orphan": 0,
             "event": defaultdict(int), "ingest": defaultdict(int), "disagree": 0}
    for r in rows:
        denied_shard, ingest_sid, owner = r[1], r[2], r[4]
        ev_sid = r[6] if has_ev else None
        if owner is None:
            tally["orphan"] += 1
            continue
        o = shards.get(owner)
        # event axis
        if ev_sid is None:
            tally["event"]["unknowable"] += 1
            ev_verdict = None
        elif o and ev_sid in o["event_sessions"]:
            tally["event"]["re-attribution"] += 1
            ev_verdict = "re-attribution"
        else:
            tally["event"]["re-say"] += 1
            ev_verdict = "re-say"
        # ingest axis
        if ingest_sid is None:
            tally["ingest"]["unknowable"] += 1
            in_verdict = None
        elif o and ingest_sid in o["sessions"]:
            tally["ingest"]["re-attribution"] += 1
            in_verdict = "re-attribution"
        else:
            tally["ingest"]["re-say"] += 1
            in_verdict = "re-say"
        if ev_verdict and in_verdict and ev_verdict != in_verdict:
            tally["disagree"] += 1
    tally["has_event_column"] = has_ev
    tally["seen_rows"] = seen_n
    tally["seen_by_shard"] = seen_by_shard
    return tally


# ---------------------------------------------------------------- 3. backfill recoverability
def transcript_index(limit_files=None):
    """head-of-turn -> set(sessionId). A set, because templates genuinely recur across sessions."""
    idx = defaultdict(set)
    files = sorted(glob.glob(TRANSCRIPT_GLOB))
    if limit_files:
        files = files[:limit_files]
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
                        content = " ".join(
                            p.get("text", "") for p in content if isinstance(p, dict))
                    if not isinstance(content, str):
                        content = e.get("text") if isinstance(e.get("text"), str) else None
                    if not content:
                        continue
                    k = norm(content)
                    if len(k) >= 40:
                        idx[k].add(sid)
        except OSError:
            continue
    return idx, len(files)


def recoverability(shards, idx, limit_rows):
    """For pre-authority rows (event_session_id NULL), can the event session be recovered?"""
    stats = {"examined": 0, "recovered_unique": 0, "recovered_ambiguous": 0, "unmatched": 0,
             "too_short": 0, "per_shard": {}}
    for shard, meta in sorted(shards.items(), key=lambda kv: -kv[1]["rows"]):
        if meta["rows"] < 100:
            continue
        c = ro(meta["path"])
        ev_col = has_column(c, "observations", "event_session_id")
        where = "WHERE event_session_id IS NULL" if ev_col else ""
        q = (f"SELECT input_summary FROM observations {where} "
             f"{'AND' if where else 'WHERE'} tool_name = 'Conversation' LIMIT ?")
        try:
            rows = [r[0] for r in c.execute(q, (limit_rows,))]
        except sqlite3.Error:
            rows = []
        c.close()
        s = {"examined": 0, "unique": 0, "ambiguous": 0, "unmatched": 0, "short": 0}
        for text in rows:
            k = norm(text)
            s["examined"] += 1
            if len(k) < 40:
                s["short"] += 1
                continue
            hit = idx.get(k)
            if not hit:
                s["unmatched"] += 1
            elif len(hit) == 1:
                s["unique"] += 1
            else:
                s["ambiguous"] += 1
        stats["per_shard"][shard] = s
        stats["examined"] += s["examined"]
        stats["recovered_unique"] += s["unique"]
        stats["recovered_ambiguous"] += s["ambiguous"]
        stats["unmatched"] += s["unmatched"]
        stats["too_short"] += s["short"]
    return stats


def pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


# ---------------------------------------------------------------- controls
# The recoverability number came back 99.0% with 0.0% unmatched. A perfect zero is exactly the
# shape of an instrument measuring itself, so these run the wrong answer through the same path:
# if a fabricated turn or a one-character mutation matches, the head-index is not discriminating
# and the 99% is a tautology (the shards were BUILT from these transcripts). Measured 2026-07-31:
# 0/5 fabricated, 0/299 mutated, 299/300 real.
CONTROL_PROBES = [
    "[Human] the quarterly revenue forecast for the northern district was revised upward after",
    "[Claude] I have adjusted the thermostat schedule to reduce heating during unoccupied hours",
    "[Human] please summarize the attached lease agreement and flag any unusual indemnity clauses",
    "[Claude] The migration completed successfully and all seventeen tables were verified intact",
    "[Human] can you explain why the sourdough starter is not rising after four days of feeding",
]


def controls(shards, idx, n=300):
    """Returns (fabricated_hits, mutated_hits, real_hits, real_n, mutated_n)."""
    fab = sum(1 for p in CONTROL_PROBES if idx.get(norm(p)))
    biggest = max((m for m in shards.values() if m["rows"] > 100),
                  key=lambda m: m["rows"], default=None)
    if biggest is None:
        return fab, 0, 0, 0, 0
    c = ro(biggest["path"])
    rows = [r[0] for r in c.execute(
        "SELECT input_summary FROM observations WHERE tool_name='Conversation' LIMIT ?", (n,))]
    c.close()
    real = sum(1 for t in rows if idx.get(norm(t)))
    mut = 0
    elig = 0
    for t in rows:
        k = norm(t)
        if len(k) < 40:
            continue
        elig += 1
        bad = k[:20] + ("Z" if k[20] != "Z" else "Q") + k[21:]
        if idx.get(bad):
            mut += 1
    return fab, mut, real, len(rows), elig


def main():
    check = "--check" in sys.argv
    limit = 2000
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    shards = load_shards()
    print("=" * 78)
    print("claim_conflict decidability — the retrofit join, and whether it can pay out")
    print("=" * 78)

    print("\n[shards]")
    print(f"  {'shard':14} {'rows':>8} {'sessions':>9} {'const-id rows':>14} {'event-sess ids':>15}")
    for shard, m in sorted(shards.items(), key=lambda kv: -kv[1]["rows"]):
        print(f"  {shard:14} {m['rows']:>8} {len(m['sessions']):>9} "
              f"{m['const_rows']:>8} ({pct(m['const_rows'], m['rows']):>5}) {len(m['event_sessions']):>15}")

    print("\n[1+2] live join: claim_conflict ⋈ seen")
    lj = live_join(shards)
    if lj is None:
        print("  no seen.db — the root authority has never run on this box")
    else:
        print(f"  seen rows: {lj['seen_rows']}   by owner: {lj['seen_by_shard']}")
        print(f"  event_session_id column present: {lj['has_event_column']}")
        print(f"  denials recorded: {lj['total']}   orphaned (no owner row): {lj['orphan']}")
        if lj["total"] == 0:
            print("  -- no denials yet. The instrument is installed and has not fired.")
            print("     This is NOT evidence that cross-shard denial is rare: the authority")
            print("     landed today and only guards writes made SINCE. Every historical")
            print("     collision predates it and is invisible here by construction.")
        else:
            for axis in ("event", "ingest"):
                t = lj[axis]
                d = lj["total"] - lj["orphan"]
                blind = t.get("unknowable", 0)
                print(f"  {axis:>7} axis: re-attribution={t.get('re-attribution',0)} "
                      f"re-say={t.get('re-say',0)} unknowable={blind} "
                      f"(blind {pct(blind, d)})")
            print(f"  axes DISAGREE on {lj['disagree']} denials "
                  f"({pct(lj['disagree'], lj['total'] - lj['orphan'])}) "
                  f"— each is a denial the ingest axis classifies confidently and wrongly")

    print("\n[3] backfill recoverability — kimi's open item")
    print(f"    indexing transcripts ({TRANSCRIPT_GLOB}) …")
    idx, nfiles = transcript_index()
    print(f"    {nfiles} transcript files, {len(idx)} distinct turn-heads indexed")
    rec = recoverability(shards, idx, limit)
    ex = rec["examined"]
    print(f"    pre-authority Conversation rows examined: {ex} (cap {limit}/shard)")
    print(f"      recovered, unique session : {rec['recovered_unique']:>6}  ({pct(rec['recovered_unique'], ex)})")
    print(f"      matched, AMBIGUOUS (>1)   : {rec['recovered_ambiguous']:>6}  ({pct(rec['recovered_ambiguous'], ex)})")
    print(f"      unmatched in transcripts  : {rec['unmatched']:>6}  ({pct(rec['unmatched'], ex)})")
    print(f"      head too short to key on  : {rec['too_short']:>6}  ({pct(rec['too_short'], ex)})")
    for shard, s in rec["per_shard"].items():
        print(f"        {shard:14} examined={s['examined']:>6} unique={s['unique']:>6} "
              f"ambig={s['ambiguous']:>5} unmatched={s['unmatched']:>6}")

    fab, mut, real, real_n, mut_n = controls(shards, idx)
    print("\n[control] the same path, run on answers known to be WRONG")
    print(f"      fabricated turns matched : {fab}/{len(CONTROL_PROBES)}   (want 0)")
    print(f"      1-char-mutated heads     : {mut}/{mut_n}   (want 0)")
    print(f"      real rows matched        : {real}/{real_n}")
    control_ok = (fab == 0 and mut == 0 and real_n and real / real_n > 0.9)
    if not control_ok:
        print("      CONTROL FAILED — the recoverability number above is measuring the harness,")
        print("      not provenance. Do not act on it.")

    print("\n[verdict]")
    if ex and control_ok:
        r = rec["recovered_unique"] / ex
        print(f"  A `seen` backfill run today freezes ownership by ARRIVAL ORDER over rows whose")
        print(f"  event-session column is empty — the axis this thread just showed is the only one")
        print(f"  that can classify a denial. {pct(rec['recovered_unique'], ex)} of the sampled")
        print(f"  pre-authority rows can have their real conversation id recovered from the")
        print(f"  transcripts on disk, so the attribution question is NOT lost — it is deferrable.")
        print(f"  Recommendation: recover event_session_id FIRST, backfill `seen` after, and write")
        print(f"  a claim_conflict row for every shard the backfill denies. Today's duplicates are")
        print(f"  lossless w.r.t. attribution; a backfill without the recovery makes them lossy.")

    if check:
        fail = []
        # The shape this thread has established. Each assertion names the number it pins.
        bulk = [m for m in shards.values() if m["rows"] > 1000]
        if len(bulk) < 3:
            fail.append(f"expected >=3 bulk shards (>1000 rows), found {len(bulk)}")
        for m in bulk:
            share = m["const_rows"] / m["rows"] if m["rows"] else 0
            if share < 0.90:
                fail.append(f"bulk shard const-id share {share:.3f} < 0.90 — the constant host "
                            f"id no longer dominates; the decidability argument needs re-deriving")
        if lj is not None and not lj["has_event_column"]:
            fail.append("claim_conflict has no event_session_id column — the built code has not "
                        "yet been opened against the live seen.db (openRootClaims runs the ALTER)")
        if not control_ok:
            fail.append(f"controls failed (fabricated={fab}, mutated={mut}, real={real}/{real_n}) "
                        f"— the recoverability figure is not measuring provenance")
        if fail:
            for f in fail:
                print(f"  DRIFT: {f}")
            print("\n  check: FAILED")
            return 1
        print("\n  check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
