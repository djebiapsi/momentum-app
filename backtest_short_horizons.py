# -*- coding: utf-8 -*-
"""
Redesign short — comparaison d'horizons de momentum (analyse par quintiles)
===========================================================================
Le backtest directionnel a montré que le momentum 63-5 (3 mois) sélectionne des
titres qui REBONDISSENT (reversal court terme) → thèse short invalidée sur cette
fenêtre. Ce script cherche s'il existe une fenêtre où le quintile des « losers »
CONTINUE de baisser (donc un signal short directionnel viable).

Méthode (sans seuil arbitraire) : à chaque fin de mois, pour chaque fenêtre de
momentum, on classe l'univers en quintiles (Q1 = pires performeurs) et on mesure
le rendement forward moyen J+20/J+60. Un signal short viable = Q1 avec rendement
forward négatif ET monotone croissant Q1→Q5.

Fenêtres testées :
  63-5   : ~3 mois (fenêtre actuelle, zone de reversal)
  126-5  : ~6 mois
  126-21 : ~6 mois hors mois récent
  252-21 : ~12 mois hors mois récent (momentum « académique » 12-1)

Usage : python backtest_short_horizons.py [--start 2005-01-01] [--end ...]
"""

import sys
import argparse
from collections import defaultdict

import pandas as pd

import short_scoring as sc
import short_data as sd

# Fenêtres (lookback, skip) en jours de bourse
WINDOWS = {
    '63-5':   (63, 5),
    '126-5':  (126, 5),
    '126-21': (126, 21),
    '252-21': (252, 21),
}
FORWARD = [20, 60]
N_QUANTILES = 5


def run(start=None, end=None, min_bars=300):
    prices = sd.load_daily_prices(min_bars=min_bars)
    if 'SPY' not in prices:
        raise SystemExit("SPY absent — lancer la collecte de prix.")
    membership = sd.load_membership()
    has_mem = len(membership) > 0
    spy = prices['SPY']
    eval_dates = sd.month_end_dates(spy, start, end)

    from price_data_service import BENCHMARKS
    bench = set(BENCHMARKS)
    candidates = [t for t in prices if t not in bench]

    print(f"[info] tickers: {len(candidates)} | dates: {len(eval_dates)} "
          f"| membership: {'oui' if has_mem else 'non'}")

    # accumulateur : {window: {quantile: {horizon: [rendements]}}}
    acc = {w: defaultdict(lambda: defaultdict(list)) for w in WINDOWS}
    max_look = max(lb + sk for lb, sk in WINDOWS.values()) + 5

    for as_of in eval_dates:
        as_of = pd.Timestamp(as_of)
        # momentum de chaque ticker pour chaque fenêtre + rendements forward
        mom = {w: {} for w in WINDOWS}
        fwd = {}
        for t in candidates:
            if has_mem and not sd.member_at(membership, t, as_of):
                continue
            series = prices.get(t)
            if series is None:
                continue
            win = sd.window_prices(series, as_of, max_look)
            if len(win) < max_look:
                continue
            for w, (lb, sk) in WINDOWS.items():
                m = sc.perf_window(win, lookback=lb, skip=sk)
                if m is not None:
                    mom[w][t] = m
            fwd[t] = {h: sd.forward_return(series, as_of, h) for h in FORWARD}

        # quintiles par fenêtre
        for w in WINDOWS:
            items = [(t, m) for t, m in mom[w].items() if t in fwd]
            if len(items) < N_QUANTILES * 4:
                continue
            items.sort(key=lambda x: x[1])   # croissant : Q1 = pires
            n = len(items)
            for i, (t, _) in enumerate(items):
                q = min(N_QUANTILES - 1, i * N_QUANTILES // n)  # 0..4
                for h in FORWARD:
                    r = fwd[t][h]
                    if r is not None:
                        acc[w][q][h].append(r)

    return acc


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _fmt(v):
    return f"{v * 100:+6.2f}%" if v is not None else "   n/a"


def print_report(acc):
    for w in WINDOWS:
        print(f"\n=== Fenêtre momentum {w} : rendement forward par quintile ===")
        print(f"{'quintile':>10} {'n':>7} " + " ".join(f"{'fwd' + str(h):>10}" for h in FORWARD))
        q_means = {h: [] for h in FORWARD}
        for q in range(N_QUANTILES):
            label = f"Q{q+1}" + (" (losers)" if q == 0 else " (winners)" if q == N_QUANTILES - 1 else "")
            n = len(acc[w][q][FORWARD[0]])
            line = f"{label:>10} {n:>7} "
            for h in FORWARD:
                m = _mean(acc[w][q][h])
                q_means[h].append(m)
                line += f" {_fmt(m):>10}"
            print(line)
        # Diagnostic : Q1 négatif ? spread Q1-Q5 ?
        for h in FORWARD:
            q1, q5 = q_means[h][0], q_means[h][-1]
            if q1 is not None and q5 is not None:
                spread = q1 - q5
                short_ok = q1 < 0
                print(f"   → H{h}: Q1={_fmt(q1)} Q5={_fmt(q5)} spread(Q1-Q5)={_fmt(spread)}"
                      f"  short Q1 {'VIABLE' if short_ok else 'adverse (Q1 monte)'}")


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Comparaison d'horizons momentum (short)")
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
        acc = run(start=args.start, end=args.end, min_bars=args.min_bars)

    print(f"\n{'=' * 68}")
    print("REDESIGN SHORT — COMPARAISON D'HORIZONS DE MOMENTUM (quintiles)")
    print(f"{'=' * 68}")
    print_report(acc)
    print("\nLecture : pour shorter, on cherche une fenêtre où Q1 (pires performeurs)")
    print("a un rendement forward NÉGATIF et où le spread Q1-Q5 est négatif (continuation).")


if __name__ == '__main__':
    main()
