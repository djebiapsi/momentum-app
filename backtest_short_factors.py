# -*- coding: utf-8 -*-
"""
Redesign short — test des facteurs FONDAMENTAUX (quintiles, absolu + relatif)
=============================================================================
Le momentum a échoué comme déclencheur short (à tout horizon). Ce script teste
des facteurs FONDAMENTAUX, orthogonaux au prix, pour voir s'ils prédisent une
sous-performance (base du short de Sloan 1996 : accruals élevés → surperf. des
bénéfices non convertie en cash → correction).

Point clé méthodologique : vu la dérive haussière du marché (tous les titres
montent en absolu), on mesure le rendement forward EN ABSOLU **et EN RELATIF**
(rendement du titre − rendement médian de l'univers ce mois-là). Un facteur short
viable = quintile « pire fondamental » avec rendement RELATIF négatif et monotone.

Facteur testé ici : accruals_ratio (FundamentalSnapshot), le seul disponible tant
que TickerInfoSnapshot (valorisation) n'est pas collecté. Anti-look-ahead :
accruals dispo à period_date + FILING_LAG_DAYS.

Usage : python backtest_short_factors.py [--start ...] [--end ...]
"""

import sys
import argparse
from collections import defaultdict

import pandas as pd

import short_scoring as sc
import short_data as sd

FORWARD = [20, 60]
N_QUANTILES = 5


def run(start=None, end=None, min_bars=300):
    prices = sd.load_daily_prices(min_bars=min_bars)
    if 'SPY' not in prices:
        raise SystemExit("SPY absent — lancer la collecte de prix.")
    membership = sd.load_membership()
    has_mem = len(membership) > 0
    accruals = sd.load_accruals()
    spy = prices['SPY']
    eval_dates = sd.month_end_dates(spy, start, end)

    from price_data_service import BENCHMARKS
    bench = set(BENCHMARKS)
    candidates = [t for t in prices if t not in bench]

    print(f"[info] tickers: {len(candidates)} | dates: {len(eval_dates)} "
          f"| tickers avec accruals: {len(accruals)}")

    # {quantile: {horizon: [rendements]}} en absolu et en relatif
    acc_abs = defaultdict(lambda: defaultdict(list))
    acc_rel = defaultdict(lambda: defaultdict(list))
    dates_used = 0

    for as_of in eval_dates:
        as_of = pd.Timestamp(as_of)
        # accruals dispo + rendements forward par ticker
        vals, fwd = {}, {}
        for t in candidates:
            if has_mem and not sd.member_at(membership, t, as_of):
                continue
            e = sd.latest_before(accruals.get(t, []), as_of)
            if e is None:
                continue
            series = prices.get(t)
            if series is None:
                continue
            f = {h: sd.forward_return(series, as_of, h) for h in FORWARD}
            if all(f[h] is None for h in FORWARD):
                continue
            vals[t] = e[1]
            fwd[t] = f

        if len(vals) < N_QUANTILES * 4:
            continue
        dates_used += 1

        # moyenne univers ce mois (pour le relatif), par horizon
        month_mean = {}
        for h in FORWARD:
            xs = [fwd[t][h] for t in vals if fwd[t][h] is not None]
            month_mean[h] = (sum(xs) / len(xs)) if xs else None

        # quintiles par accruals croissants : Q1 = accruals bas (sain),
        # Q5 = accruals élevés (candidat short Sloan)
        items = sorted(vals.items(), key=lambda x: x[1])
        n = len(items)
        for i, (t, _) in enumerate(items):
            q = min(N_QUANTILES - 1, i * N_QUANTILES // n)
            for h in FORWARD:
                r = fwd[t][h]
                if r is None:
                    continue
                acc_abs[q][h].append(r)
                if month_mean[h] is not None:
                    acc_rel[q][h].append(r - month_mean[h])

    return acc_abs, acc_rel, dates_used


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _fmt(v):
    return f"{v * 100:+6.2f}%" if v is not None else "   n/a"


def print_report(acc_abs, acc_rel, dates_used):
    print(f"\n[dates exploitables: {dates_used}]")
    for title, acc in (("ABSOLU", acc_abs), ("RELATIF (vs médiane univers)", acc_rel)):
        print(f"\n=== Accruals — rendement forward {title} par quintile ===")
        print(f"{'quintile':>14} {'n':>7} " + " ".join(f"{'fwd' + str(h):>10}" for h in FORWARD))
        means = {h: [] for h in FORWARD}
        for q in range(N_QUANTILES):
            label = (f"Q{q+1}" + (" (sain)" if q == 0
                     else " (accruals+)" if q == N_QUANTILES - 1 else ""))
            n = len(acc[q][FORWARD[0]])
            line = f"{label:>14} {n:>7} "
            for h in FORWARD:
                m = _mean(acc[q][h])
                means[h].append(m)
                line += f" {_fmt(m):>10}"
            print(line)
        for h in FORWARD:
            q5, q1 = means[h][-1], means[h][0]
            if q5 is not None and q1 is not None:
                spread = q5 - q1
                ok = q5 < 0 if title.startswith("RELATIF") else None
                verdict = ('' if ok is None else
                           f"  short Q5 {'VIABLE' if ok else 'adverse'}")
                print(f"   → H{h}: Q5(accruals+)={_fmt(q5)} Q1(sain)={_fmt(q1)} "
                      f"spread(Q5-Q1)={_fmt(spread)}{verdict}")


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Test facteurs fondamentaux (short)")
    ap.add_argument('--start', default=None)
    ap.add_argument('--end', default=None)
    ap.add_argument('--min-bars', type=int, default=300)
    args = ap.parse_args()

    import app as app_module
    application = app_module.create_app()
    with application.app_context():
        try:
            from services import ibkr_service as _ib
            _ib.ensure_connected = lambda: False
        except Exception:
            pass
        acc_abs, acc_rel, dates_used = run(start=args.start, end=args.end,
                                           min_bars=args.min_bars)

    print(f"\n{'=' * 68}")
    print("REDESIGN SHORT — FACTEUR ACCRUALS (Sloan 1996)")
    print(f"{'=' * 68}")
    print_report(acc_abs, acc_rel, dates_used)
    print("\nLecture : un short accruals viable = Q5 (accruals élevés) avec rendement")
    print("RELATIF négatif et spread Q5-Q1 négatif (les 'sains' surperforment les 'douteux').")


if __name__ == '__main__':
    main()
