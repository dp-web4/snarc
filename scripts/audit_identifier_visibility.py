#!/usr/bin/env python3
"""
Audit whether the store can SEE the signal kimi's identifier-recurrence proxy proposes to score.

WHY THIS EXISTS
---------------
kimi-code (snarc/forum/kimi-the-9pct-is-void-and-there-is-a-signal-short-of-a-suppression-arm-2026-07-31.md
§2) proposed replacing the token-overlap outcome proxy with **identifier recurrence**: did an
item-unique identifier from the surfaced memory -- "commit hashes, notice ids, chain positions,
file:line pins, exact numbers ('81893', '62%')" -- recur in the session's later work? The claimed
properties were (a) an INTRINSIC placebo (a length-matched decoy's identifiers structurally do not
match) and (b) "measurable today, no schema change, against the same retrieval_log + session
corpora."

Claim (b) is the one this script tests, because it is the one that decides whether the next
experiment can be run this week or needs a migration first. Before quoting any identifier-recurrence
rate, we must know how much of the identifier class the existing instrument can represent at all
(the discipline that killed the 9%: compute the instrument's blind fraction BEFORE accepting a
number from it).

WHAT IT MEASURES
----------------
The substrate is `retrieval_log.match_key`, which is not raw text -- it is `sigTokens()` output
(src/memory.ts:80-86, mirrored verbatim below), capped at 40 tokens. sigTokens has two branches:

    pathish   [a-z0-9_.-]+/[a-z0-9_./-]+  |  [a-z0-9_-]+\\.[a-z0-9]{1,5}\\b
    wordish   [a-z][a-z0-9_]{3,}                     <-- MUST START WITH A LETTER

So a bare number can never be a token, and a hex hash survives only when its first character
happens to be a-f. Three measurements, over the store's own corpus:

  A. TOKENIZER BLINDNESS  -- of the identifier-shaped strings actually present in observation
     text, what fraction survives sigTokens into a match_key? Broken out by identifier family
     (pure digits / hex / mixed), because the families fail for different reasons.

  B. ROW COVERAGE         -- of live `retrieval_log` rows, what fraction carry at least one
     surviving identifier-shaped token? That is the ceiling on how many rows an identifier
     recurrence column could ever score. kimi already conceded this is "a minority"; this
     puts a number on it.

  C. SHOWN-vs-SCORED GAP  -- `match_key` is built from `input_summary + output_summary` in full
     (memory.ts:408-409), while the briefing LINE shows `input_summary.slice(0, 100)`
     (memory.ts:406). Any identifier past that cut is scored but was never shown to the session,
     so its "recurrence" cannot have been caused by the briefing. Reported as the fraction of
     surviving identifiers that lie outside the shown window.

WHAT IT DOES NOT MEASURE
------------------------
Not the placebo claim. That needs a decoy arm (see audit_outcome_instrument.py) and it is
NOT intrinsic: getSessionBriefing selects by salience/confidence with no reference to the
session (memory.ts:385-422), and the eligible pool is the 20 most RECENT observations -- so a
length-matched decoy drawn from the same recency stratum carries identifiers about the same
currently-hot objects. The control still has to be built.

Usage:
    python3 scripts/audit_identifier_visibility.py [--db PATH] [--all-stores] [--min-rows N]

Exit 0 only if the tokenizer is materially non-blind (>= 50% of identifiers survive) AND row
coverage clears 10%. Exit 1 otherwise -- i.e. exit 1 means "not measurable today; the proposal
needs a schema/tokenizer change first."
"""

import argparse
import os
import re
import sqlite3
import sys

# --- mirrored verbatim from src/memory.ts:80-92. If those change, change these. ---
STOP_TOKENS = {
    'this', 'that', 'with', 'from', 'have', 'will', 'into', 'then', 'they', 'them', 'what',
    'when', 'which', 'were', 'been', 'your', 'about', 'there', 'these', 'would', 'could',
    'true', 'false', 'null', 'none', 'name', 'type', 'text', 'value', 'data', 'file', 'line',
}
_PATHISH = re.compile(r'[a-z0-9_.\-]+/[a-z0-9_./\-]+|[a-z0-9_\-]+\.[a-z0-9]{1,5}\b')
_WORDISH = re.compile(r'[a-z][a-z0-9_]{3,}')

MATCH_KEY_CAP = 40      # memory.ts:440  `.slice(0, 40)`
SHOWN_CHARS = 100       # memory.ts:406  `input_summary.slice(0, 100)`

# Generous identifier candidates -- deliberately WIDER than sigTokens, since the whole
# question is what sigTokens drops. Applied to the lowercased text, like sigTokens.
_ID_FAMILIES = [
    ('digits', re.compile(r'(?<![a-z0-9])\d{4,}(?![a-z0-9])')),          # 81893, chain positions, notice ids
    ('hex',    re.compile(r'(?<![a-z0-9])[0-9a-f]{6,40}(?![a-z0-9])')),  # commit hashes, chain hashes
    ('mixed',  re.compile(r'(?<![a-z0-9])(?=[a-z0-9_\-]*\d)[a-z0-9_\-]{6,}(?![a-z0-9])')),
]

# Same roots and filenames as audit_outcome_instrument.py:79-80 -- the db was renamed with the
# package (8aacf1a), so the archive root carries the OLD filename and matching on `snarc.db`
# alone silently sees zero stores.
STORE_ROOTS = [
    ('~/.snarc/projects', 'snarc.db', 'live'),
    ('~/.engram/projects', 'engram.db', 'ARCHIVE (pre-8aacf1a rename)'),
]

BLIND_FLOOR = 0.50      # >=50% of identifiers must survive for "measurable today" to hold
COVERAGE_FLOOR = 0.10   # >=10% of rows must be scorable at all


def sig_tokens(text):
    """sigTokens(), mirrored. Returns the set BEFORE stop-token filtering (memory.ts does that
    at the call site)."""
    t = (text or '').lower()
    out = set()
    for m in _PATHISH.finditer(t):
        out.add(m.group(0))
    for m in _WORDISH.finditer(t):
        out.add(m.group(0))
    return out


def match_key_tokens(text):
    """What logRetrieval() would actually store: sigTokens minus stop tokens, capped at 40.
    Set semantics upstream mean the cap's victims are arbitrary; we mirror the cap but not
    the (unordered) selection, so this OVERCOUNTS survivors -- generous to the proposal."""
    return {t for t in sig_tokens(text) if t not in STOP_TOKENS}


def identifiers(text):
    """{family: set(strings)} of identifier-shaped candidates present in the text."""
    t = (text or '').lower()
    out = {}
    for fam, rx in _ID_FAMILIES:
        out[fam] = {m.group(0) for m in rx.finditer(t)}
    # A string can match more than one family (a hash is also 'mixed'); make families
    # disjoint by priority so the totals do not double count.
    out['mixed'] -= out['hex'] | out['digits']
    out['hex'] -= out['digits']
    return out


def survives(ident, toks):
    """Did this identifier reach match_key? Either as its own token, or embedded in one
    (a path token can carry a hash). Substring counts as survival -- generous again."""
    if ident in toks:
        return True
    return any(ident in t for t in toks)


def find_stores():
    found = []
    for root, fname, label in STORE_ROOTS:
        r = os.path.expanduser(root)
        if not os.path.isdir(r):
            continue
        for d in sorted(os.listdir(r)):
            p = os.path.join(r, d, fname)
            if os.path.exists(p):
                found.append((p, label))
    return found


def default_db():
    """The store with the most retrieval_log rows; live root wins ties (it is listed first,
    and max() on a stable list keeps the first maximum). Never pick by file size -- the
    archive is 100x larger and would win forever."""
    counts = []
    for p, label in find_stores():
        try:
            c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
            n = c.execute('SELECT COUNT(*) FROM retrieval_log').fetchone()[0]
            c.close()
        except sqlite3.Error:
            n = 0
        counts.append((p, label, n))
    live = [c for c in counts if c[2] > 0]
    if not live:
        return None, counts
    return max(live, key=lambda c: c[2]), counts


def audit(db, label):
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row

    # --- A + C: over the observation corpus the briefing draws from ---
    # The briefing-eligible pool is the 20 most recent observations with salience >= 0.35
    # (memory.ts:401-405); we report over ALL observations too, because the eligible pool is
    # a moving 20-row window and a rate over it is not stable enough to quote.
    rows = con.execute(
        'SELECT input_summary, output_summary, salience FROM observations'
    ).fetchall()

    fam_total = {f: 0 for f, _ in _ID_FAMILIES}
    fam_survived = {f: 0 for f, _ in _ID_FAMILIES}
    outside_shown = 0
    survived_total = 0
    obs_with_ids = 0

    for r in rows:
        full = f"{r['input_summary'] or ''} {r['output_summary'] or ''}"
        shown = (r['input_summary'] or '')[:SHOWN_CHARS]
        toks = match_key_tokens(full)
        shown_toks = match_key_tokens(shown)
        ids = identifiers(full)
        if any(ids.values()):
            obs_with_ids += 1
        for fam in fam_total:
            for ident in ids[fam]:
                fam_total[fam] += 1
                if survives(ident, toks):
                    fam_survived[fam] += 1
                    survived_total += 1
                    if not survives(ident, shown_toks):
                        outside_shown += 1

    # --- B: row coverage over the live retrieval_log ---
    keys = con.execute('SELECT match_key FROM retrieval_log').fetchall()
    scorable = 0
    for k in keys:
        toks = set((k['match_key'] or '').split())
        if any(any(rx.fullmatch(t) or rx.search(t) for _, rx in _ID_FAMILIES) for t in toks):
            scorable += 1
    con.close()

    total_ids = sum(fam_total.values())
    total_surv = sum(fam_survived.values())

    print(f'\n=== store: {db}  [{label}] ===')
    print(f'observations: {len(rows)}   with >=1 identifier-shaped string: {obs_with_ids}')
    print(f'retrieval_log rows: {len(keys)}')

    print('\nA. TOKENIZER BLINDNESS  (identifier-shaped strings in observation text -> match_key)')
    print(f'   {"family":10} {"present":>9} {"survives":>9} {"survival":>9}')
    for fam, _ in _ID_FAMILIES:
        t, s = fam_total[fam], fam_survived[fam]
        rate = f'{s/t:.1%}' if t else '   n/a'
        print(f'   {fam:10} {t:9d} {s:9d} {rate:>9}')
    surv_rate = (total_surv / total_ids) if total_ids else 0.0
    print(f'   {"TOTAL":10} {total_ids:9d} {total_surv:9d} {surv_rate:>9.1%}')

    print('\nB. ROW COVERAGE  (live retrieval_log rows carrying >=1 identifier-shaped token)')
    cov = (scorable / len(keys)) if keys else 0.0
    print(f'   {scorable} / {len(keys)} = {cov:.1%}  <- ceiling on rows an identifier column could score')

    print('\nC. SHOWN-vs-SCORED GAP  (surviving identifiers past the briefing line\'s 100-char cut)')
    gap = (outside_shown / total_surv) if total_surv else 0.0
    print(f'   {outside_shown} / {total_surv} = {gap:.1%}  <- scored but never shown; recurrence cannot')
    print( '                            have been caused by the briefing for these')

    return surv_rate, cov, total_ids, len(keys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=None)
    ap.add_argument('--all-stores', action='store_true')
    ap.add_argument('--min-rows', type=int, default=50,
                    help='with --all-stores, skip stores with fewer retrieval_log rows')
    args = ap.parse_args()

    if args.db:
        targets = [(args.db, 'explicit --db')]
    elif args.all_stores:
        targets = []
        for p, label in find_stores():
            try:
                c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
                n = c.execute('SELECT COUNT(*) FROM retrieval_log').fetchone()[0]
                c.close()
            except sqlite3.Error:
                n = 0
            if n >= args.min_rows:
                targets.append((p, label))
        if not targets:
            print(f'no store has >= {args.min_rows} retrieval_log rows', file=sys.stderr)
            return 1
    else:
        best, inventory = default_db()
        if not best:
            print('no store with retrieval_log rows found; inventory:', file=sys.stderr)
            for p, label, n in inventory:
                print(f'  {n:8d} rows  [{label}] {p}', file=sys.stderr)
            return 1
        targets = [(best[0], best[1])]
        print(f'selected store by retrieval_log row count ({best[2]} rows); '
              f'{len(inventory)} store(s) seen. Use --all-stores to replicate.')

    verdicts = []
    for db, label in targets:
        surv, cov, n_ids, n_rows = audit(db, label)
        verdicts.append((db, surv, cov, n_ids, n_rows))

    print('\n=== VERDICT ===')
    ok = True
    for db, surv, cov, n_ids, n_rows in verdicts:
        if n_ids == 0 or n_rows == 0:
            print(f'INSUFFICIENT  {db}  ({n_ids} identifiers, {n_rows} rows)')
            ok = False
            continue
        passing = surv >= BLIND_FLOOR and cov >= COVERAGE_FLOOR
        ok &= passing
        print(f'{"MEASURABLE" if passing else "NOT MEASURABLE TODAY":22} {db}')
        print(f'   tokenizer survival {surv:.1%} (floor {BLIND_FLOOR:.0%})   '
              f'row coverage {cov:.1%} (floor {COVERAGE_FLOOR:.0%})')
    if not ok:
        print('\nIdentifier recurrence cannot be scored off `match_key` as it stands. The instrument\n'
              'drops the identifier class before the column would ever read it -- a bare number is not\n'
              'a token, and a hex hash is one only when it starts with a-f. Scoring it needs raw text\n'
              'retained alongside match_key (schema change) or a tokenizer branch for identifiers.')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
