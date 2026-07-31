#!/usr/bin/env python3
"""
holdout_power.py — what the suppression arm costs in calendar time, at the
randomization unit you pick.

Context: forum/kimi-the-counterfactual-is-necessary-but-the-metric-comes-first-2026-07-31.md
proposes a standing item-level holdout (eps ~= 10%, arousal-guarded) as the
identification strategy for recall utility, and prices it at "~23k surfacing
events -- months, single-machine."

That price is right for ONE of the two designs. This script measures the two
inputs the estimate needs from the corpus rather than assuming them --

  k = items surfaced per briefing        (memory.ts:293,304,317 -> slice(0,3) x 3 kinds)
  B = briefings per calendar day         (retrieval_log, distinct surfaced_ts)

-- and prices both randomization units against them.

THE POINT. If the outcome is attributable to the SESSION (mismatch
non-recurrence, attempt efficiency), item-level randomization does not buy
item resolution. It dilutes the contrast by k: withholding 1 of k items is a
(k-1)/k-vs-k/k comparison, and the session outcome cannot say which item moved
it. The cost is exactly k times the briefing-level design. Item-level
randomization is only paid for when the OUTCOME ITSELF is item-attributable
(kimi's "repair adoption": did the agent execute THIS memory's repair on THIS
surface) -- there the contrast is full size and the k penalty vanishes.

Read-only. Exits 0; this is a calculator, not a gate.

ASSUMPTIONS, stated because they are the whole answer:
  (a) Per-item effects are additive and exchangeable: each of the k items
      carries delta/k of the briefing-level effect. If one item carries the
      whole effect and the rest nothing, item-level randomization is BETTER
      than this -- but you do not know which item that is ex ante, so the
      expectation over a random holdout is what is priced here.
  (b) Outcome base rate p=0.5. This is the max-variance choice and therefore
      conservative; kimi's use of it is correct and is kept.
  (c) Sessions are independent. Nonstationarity is not modelled (and makes
      long horizons worse, not better).

ITEM CLUSTERING (kimi, notice re:445). The design effect below covers
intra-SESSION correlation (rho). There is a second axis: the same item is
surfaced across many briefings, so item-arm trials also cluster BY ITEM, and
a per-instance Bernoulli(eps) holdout weights its estimand by surfacing
frequency -- it prices the effect of the most-surfaced items, not the typical
one. rho_item is unmeasurable before outcome capture, but the cluster COUNTS
are measurable today, from retrieval_log (match_key as item-identity proxy:
space-joined content tokens, truncated at 40 -- collisions would if anything
understate distinctness). Printed below under item_concentration().
"""
import argparse
import math
import os
import sqlite3
import statistics
from collections import Counter

# two-sided alpha, power -> z sum
Z = {(0.01, 0.80): 2.5758 + 0.8416,
     (0.05, 0.80): 1.9600 + 0.8416,
     (0.01, 0.90): 2.5758 + 1.2816,
     (0.05, 0.90): 1.9600 + 1.2816}


def measure(db):
    """k and the briefing rate, measured, not assumed."""
    c = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    q = lambda s: list(c.execute(s))
    n_rows = q('SELECT COUNT(*) FROM retrieval_log')[0][0]
    if not n_rows:
        raise SystemExit(f'{db}: retrieval_log is empty -- nothing to measure')
    per = [r[1] for r in q('SELECT surfaced_ts, COUNT(*) FROM retrieval_log '
                           'GROUP BY 1')]
    days = q('SELECT substr(surfaced_ts,1,10) d, COUNT(DISTINCT surfaced_ts) '
             'FROM retrieval_log GROUP BY 1 ORDER BY 1')
    span = q('SELECT MIN(surfaced_ts), MAX(surfaced_ts) FROM retrieval_log')[0]
    c.close()
    return {
        'rows': n_rows,
        'briefings': len(per),
        'k_mean': sum(per) / len(per),
        'k_mode': Counter(per).most_common(1)[0],
        'k_median': statistics.median(per),
        'span': span,
        'days': [d[1] for d in days],
        'day_labels': [d[0] for d in days],
    }


def item_concentration(db):
    """
    How many distinct items ever surface, and how concentrated is surfacing?
    match_key is the item-identity proxy (see header). Returns per-kind
    appearance counts plus the head share, so the reader can see what a
    per-instance holdout's estimand is actually weighted by.
    """
    c = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    rows = list(c.execute('SELECT item_kind, COUNT(DISTINCT surfaced_ts) '
                          'FROM retrieval_log GROUP BY match_key, item_kind'))
    c.close()
    per_kind = {}
    for kind, n in rows:
        per_kind.setdefault(kind, []).append(n)
    return per_kind


def capture_audit(db):
    """
    Is the OUTCOME half of the act grain captured? A mismatch is defined as
    outcome-vs-expectation, so p and rho are computable only if outcomes are
    recorded. Conversation rows are excluded: they are not tool acts.
    """
    c = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    row = list(c.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN output_summary IS NOT NULL
                         AND TRIM(output_summary, '"') <> '' THEN 1 ELSE 0 END)
        FROM observations
        WHERE tool_name NOT IN ('Conversation', 'user_prompt')"""))[0]
    c.close()
    return {'tool_rows': row[0], 'with_output': row[1] or 0}


def briefings_needed(delta, eps, p, zsum, k=None):
    """
    Briefings required to detect `delta` at the given alpha/power.

    briefing-level (k=None): withheld arm = eps*B, surfaced arm = (1-eps)*B,
        SE = sqrt(p(1-p) * (1/(eps*B) + 1/((1-eps)*B)))

    item-level (k given): regress session outcome on #items withheld,
        X ~ Binomial(k, eps), Var(X) = k*eps*(1-eps), per-unit effect delta/k,
        SE(beta) = sd(Y) / (sd(X) * sqrt(B))
    """
    sd_y = math.sqrt(p * (1 - p))
    if k is None:
        var_unit = p * (1 - p) * (1 / eps + 1 / (1 - eps))
        return var_unit * (zsum / delta) ** 2
    beta = delta / k
    sd_x = math.sqrt(k * eps * (1 - eps))
    return (zsum * sd_y / (sd_x * beta)) ** 2


def fmt_time(briefings, per_day):
    d = briefings / per_day
    if d < 90:
        return f'{d:,.0f} d'
    if d < 730:
        return f'{d / 30.44:,.1f} mo'
    return f'{d / 365.25:,.1f} yr'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='~/.engram/projects/791cace57ce9/engram.db',
                    help='store to measure k and the briefing rate from')
    ap.add_argument('--delta', type=float, default=0.05,
                    help='briefing-level effect to detect (default 5pp, the '
                         'gate in audit_outcome_instrument.py)')
    ap.add_argument('--eps', type=float, default=0.10, help='holdout fraction')
    ap.add_argument('--p', type=float, default=0.50, help='outcome base rate')
    ap.add_argument('--alpha', type=float, default=0.01)
    ap.add_argument('--power', type=float, default=0.80)
    ap.add_argument('--rate-days', type=int, default=7,
                    help='trailing complete days to take the briefing rate from')
    args = ap.parse_args()

    db = os.path.expanduser(args.db)
    if not os.path.exists(db):
        raise SystemExit(f'no such store: {db}')
    zsum = Z.get((args.alpha, args.power))
    if zsum is None:
        raise SystemExit(f'no z for alpha={args.alpha} power={args.power}; '
                         f'have {sorted(Z)}')

    m = measure(db)
    # drop the first and last day: both are partial and would drag the rate down
    full = m['days'][1:-1] if len(m['days']) > 2 else m['days']
    trailing = full[-args.rate_days:]
    per_day = sum(trailing) / len(trailing)
    k = m['k_mode'][0]

    print(f'store        {db}')
    print(f'span         {m["span"][0]} .. {m["span"][1]}')
    print(f'rows         {m["rows"]:,} in {m["briefings"]:,} briefings')
    print(f'k (measured) mode={m["k_mode"][0]} in {m["k_mode"][1]}/{m["briefings"]} '
          f'briefings ({100*m["k_mode"][1]/m["briefings"]:.1f}%), '
          f'mean={m["k_mean"]:.2f}, median={m["k_median"]:.0f}')
    print(f'rate         {per_day:.1f} briefings/day '
          f'(trailing {len(trailing)} complete days: {trailing})')
    print(f'design       delta={args.delta:.0%} eps={args.eps:.0%} p={args.p} '
          f'alpha={args.alpha} power={args.power}')
    print()

    bl = briefings_needed(args.delta, args.eps, args.p, zsum)
    il = briefings_needed(args.delta, args.eps, args.p, zsum, k=k)

    print(f'{"randomization unit":<34}{"briefings":>12}{"item-rows":>12}'
          f'{"calendar":>12}')
    print('-' * 70)
    print(f'{"briefing (session-attributable)":<34}{bl:>12,.0f}{bl*k:>12,.0f}'
          f'{fmt_time(bl, per_day):>12}')
    print(f'{f"item, eps per item (k={k})":<34}{il:>12,.0f}{il*k:>12,.0f}'
          f'{fmt_time(il, per_day):>12}')
    print('-' * 70)
    print(f'penalty for item-level randomization against a session-level '
          f'outcome: {il/bl:.1f}x  (= k)')
    print()
    print('item-level randomization against an ITEM-attributable outcome')
    print('(repair adoption: did the agent execute THIS memory\'s repair):')
    ia = briefings_needed(args.delta, args.eps, args.p, zsum) / k
    print(f'  contrast is full size and every briefing yields k trials ->')
    print(f'  {ia:,.0f} briefings ({fmt_time(ia, per_day)}). The k penalty '
          f'becomes a k discount.')
    print()
    # The obvious attack on the line above is clustering: k trials inside one
    # session are not k independent trials. Compute it rather than caveat it.
    print('  ...but the k trials share a session. Design effect '
          '1+(k-1)*rho, rho = intra-session correlation of the outcome:')
    for rho in (0.0, 0.05, 0.10, 0.20, 0.50, 1.0):
        deff = 1 + (k - 1) * rho
        print(f'    rho={rho:<5} DEFF={deff:>4.1f}  ->  {ia*deff:>9,.0f} '
              f'briefings  {fmt_time(ia * deff, per_day):>10}')
    print(f'    rho=1 is the degenerate case: the k items carry one session '
          f'outcome between them,')
    print(f'    which is the session-attributable row above ({fmt_time(bl, per_day)}). '
          f'rho is unmeasured --')
    print(f'    no act-grain data exists yet. It is the first thing the pilot '
          f'should report.')
    print()

    print()

    # Second clustering axis: the same item recurs ACROSS briefings. rho_item
    # needs outcome capture, but the cluster counts do not -- measure them.
    conc = item_concentration(db)
    all_apps = sorted((n for ns in conc.values() for n in ns), reverse=True)
    total_app = sum(all_apps)
    print('ITEM CLUSTERING (measured; match_key as item proxy) -- the item arm')
    print('clusters by item across sessions, not only by session:')
    print(f'  {"kind":<14}{"distinct":>9}{"surf>1x":>9}{"max":>7}{"top10 share":>12}')
    for kind, ns in sorted(conc.items()):
        ns = sorted(ns, reverse=True)
        print(f'  {kind:<14}{len(ns):>9}{sum(1 for n in ns if n > 1):>9}'
              f'{max(ns):>7}{sum(ns[:10]) / sum(ns):>11.1%}')
    print(f'  {"ALL":<14}{len(all_apps):>9}'
          f'{sum(1 for n in all_apps if n > 1):>9}{max(all_apps):>7}'
          f'{sum(all_apps[:10]) / total_app:>11.1%}')
    print('  A per-instance Bernoulli(eps) holdout draws ~eps*count withheld')
    print('  trials per item: the head items dominate the estimate and the')
    print('  singleton tail contributes none. The estimand is head-weighted,')
    print('  and per-item inference is bounded by the DISTINCT count above --')
    print('  an item-level experiment on a 3-item tier is a 3-cluster study.')
    print('  rho_item itself is unmeasured until outcome capture ships.')
    print()

    # Can either of the two driving inputs be measured today? Probe the corpus.
    cap = capture_audit(db)
    print('CAN THIS BE SIZED TODAY? the two inputs, measured vs assumed')
    print(f'  k  = {k}                MEASURED (retrieval_log, this store)')
    print(f'  rate = {per_day:.1f}/day      MEASURED (retrieval_log, this store)')
    print(f'  p  = {args.p}              ASSUMED -- P(mismatch recurs). Needs '
          f'outcome capture.')
    print(f'  rho = unmeasured        ASSUMED 0 above -- needs outcome capture '
          f'(both axes: within-session, within-item).')
    print(f'  r   = unmeasured        ASSUMED 1 implicitly -- P(the item\'s '
          f'situation recurs in-window). Item-attributable trials resolve')
    print(f'                        only when the situation recurs; censoring is '
          f'treatment-independent so it inflates n by ~1/r without biasing.')
    print(f'  item clusters           MEASURED (above): {len(all_apps)} distinct '
          f'items, top-10 carry {100*sum(all_apps[:10])/total_app:.0f}% of '
          f'surfacings.')
    print(f'  outcome capture: of {cap["tool_rows"]:,} non-Conversation tool rows, '
          f'{cap["with_output"]:,} ({100*cap["with_output"]/cap["tool_rows"]:.1f}%) '
          f'carry any output_summary.')
    print(f'  inputs are captured; outcomes are not. A mismatch is '
          f'outcome-vs-expectation, so')
    print(f'  neither p nor rho is computable from this corpus. The arm cannot '
          f'be sized before capture ships.')
    print()

    print('sensitivity -- calendar time to a result, briefing-level unit')
    print(f'{"delta":>8}' + ''.join(f'{f"eps={e:.0%}":>12}'
                                    for e in (0.05, 0.10, 0.20, 0.50)))
    for d in (0.03, 0.05, 0.10, 0.15):
        row = f'{d:>8.0%}'
        for e in (0.05, 0.10, 0.20, 0.50):
            row += f'{fmt_time(briefings_needed(d, e, args.p, zsum), per_day):>12}'
        print(row)
    print()
    print('same table, item-level unit against a session-level outcome '
          f'(k={k})')
    print(f'{"delta":>8}' + ''.join(f'{f"eps={e:.0%}":>12}'
                                    for e in (0.05, 0.10, 0.20, 0.50)))
    for d in (0.03, 0.05, 0.10, 0.15):
        row = f'{d:>8.0%}'
        for e in (0.05, 0.10, 0.20, 0.50):
            row += f'{fmt_time(briefings_needed(d, e, args.p, zsum, k=k), per_day):>12}'
        print(row)


if __name__ == '__main__':
    main()
