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

    # Can either of the two driving inputs be measured today? Probe the corpus.
    cap = capture_audit(db)
    print('CAN THIS BE SIZED TODAY? the two inputs, measured vs assumed')
    print(f'  k  = {k}                MEASURED (retrieval_log, this store)')
    print(f'  rate = {per_day:.1f}/day      MEASURED (retrieval_log, this store)')
    print(f'  p  = {args.p}              ASSUMED -- P(mismatch recurs). Needs '
          f'outcome capture.')
    print(f'  rho = unmeasured        ASSUMED 0 above -- needs outcome capture.')
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
