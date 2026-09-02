# -*- coding: utf-8 -*-
"""
Backtest DIRECTIONNEL de la stratégie SHORT (validation de la thèse pure)
=========================================================================
Objectif (réduction d'ambition volontaire) : avant toute dépense en données
d'options, valider que **le sous-jacent baisse en moyenne après un score élevé**,
avec des données actions GRATUITES déjà en base. Ce script NE PRICE AUCUNE OPTION.

Métrique retenue : **rendement forward moyen** du sous-jacent à J+20/J+30/J+45/J+60
après chaque signal, agrégé par bucket de score. Le signal est jugé valide si le
rendement forward est négatif ET décroît (plus négatif) quand le score monte.

Versions testées (gain marginal de chaque couche — Partie 13 de la stratégie) :
  A : momentum seul (Couche 1)                → données prix uniquement
  B : + accruals (Couche 2)                    → FundamentalSnapshot
  C : + short interest (Couche 3, SIR seul)    → ShortInterestSnapshot

Anti-biais :
  - Univers point-in-time via IndexMembership (biais de survivance).
  - Anti-look-ahead : accruals dispo à period_date + FILING_LAG_DAYS ;
    short interest dispo à report_date.
  - Walk-forward : split in-sample / out-of-sample (--split-year).

Usage :
  python backtest_short_directional.py --version A
  python backtest_short_directional.py --version C --start 2021-07-01
  python backtest_short_directional.py --version A --split-year 2021
"""

import os
import sys
import csv
import argparse
from collections import defaultdict
from datetime import datetime

import pandas as pd

import short_scoring as sc
import short_data as sd


# =============================================================================
# MOTEUR DE BACKTEST
# =============================================================================
def run_backtest(version, start=None, end=None, min_bars=300, verbose=True,
                 apply_timing=False):
    """
    Boucle principale. Renvoie une liste de dicts « signaux » :
    {date, ticker, score, bucket, perf_63_5, regime, fwd_20/30/45/60}.
    Le chargement des données et l'assemblage du score sont délégués à
    `short_data` (source de vérité unique, partagée avec le service live).
    `apply_timing` active les filtres d'entrée de la Partie 6.
    """
    prices = sd.load_daily_prices(min_bars=min_bars)
    if 'SPY' not in prices:
        raise SystemExit("SPY absent de MarketPriceBar — lancer la collecte de prix d'abord.")
    membership = sd.load_membership()
    sector_map = sd.load_sector_map()
    accruals = sd.load_accruals() if version in ('B', 'C') else {}
    short_int = sd.load_short_interest() if version == 'C' else {}

    spy = prices['SPY']
    eval_dates = sd.month_end_dates(spy, start, end)
    if verbose:
        print(f"[info] tickers prix: {len(prices)} | dates d'éval: {len(eval_dates)} "
              f"| secteurs mappés: {len(sector_map)} | accruals: {len(accruals)} "
              f"| short interest: {len(short_int)}")

    from price_data_service import BENCHMARKS
    bench = set(BENCHMARKS)
    candidates = [t for t in prices if t not in bench]

    signals = []
    for as_of in eval_dates:
        as_of = pd.Timestamp(as_of)
        scored = sd.score_candidates_asof(
            as_of, candidates, prices, version=version, membership=membership,
            sector_map=sector_map, accruals=accruals, short_int=short_int,
            spy=spy, with_forward=True, apply_timing=apply_timing)
        for e in scored:
            res = e['score']
            signals.append({
                'date': as_of.date().isoformat(),
                'ticker': e['ticker'],
                'score': res.total,
                'bucket': res.bucket,
                'perf_63_5': round(e['perf_63_5'], 4),
                'regime': e['regime'],
                **{f'fwd_{h}': (round(e[f'fwd_{h}'], 4) if e.get(f'fwd_{h}') is not None else None)
                   for h in sc.FORWARD_HORIZONS},
            })

    return signals


# =============================================================================
# AGRÉGATION & RAPPORTS
# =============================================================================
def _agg(values):
    """(n, moyenne, médiane, hit_rate<0) sur une liste de rendements (None ignorés)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return 0, None, None, None
    n = len(vals)
    mean = sum(vals) / n
    s = sorted(vals)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    hit = sum(1 for v in vals if v < 0) / n
    return n, mean, median, hit


def report_by_score(signals):
    """Tableau score exact → rendement forward moyen par horizon."""
    groups = defaultdict(lambda: {h: [] for h in sc.FORWARD_HORIZONS})
    for s in signals:
        for h in sc.FORWARD_HORIZONS:
            groups[s['score']][h].append(s.get(f'fwd_{h}'))
    rows = []
    for score in sorted(groups):
        row = {'score': score, 'n': len(groups[score][sc.FORWARD_HORIZONS[0]])}
        for h in sc.FORWARD_HORIZONS:
            n, mean, median, hit = _agg(groups[score][h])
            row[f'mean_{h}'] = mean
            row[f'hit_{h}'] = hit
        rows.append(row)
    return rows


def report_by_bucket(signals, regime=None):
    """Tableau bucket → rendement forward moyen, optionnellement filtré par régime."""
    groups = defaultdict(lambda: {h: [] for h in sc.FORWARD_HORIZONS})
    for s in signals:
        if regime is not None and s['regime'] != regime:
            continue
        for h in sc.FORWARD_HORIZONS:
            groups[s['bucket']][h].append(s.get(f'fwd_{h}'))
    rows = []
    for bucket in sorted(groups, key=_bucket_sort_key):
        row = {'bucket': bucket, 'n': len(groups[bucket][sc.FORWARD_HORIZONS[0]])}
        for h in sc.FORWARD_HORIZONS:
            n, mean, median, hit = _agg(groups[bucket][h])
            row[f'mean_{h}'] = mean
            row[f'hit_{h}'] = hit
        rows.append(row)
    return rows


def _bucket_sort_key(b):
    try:
        return int(str(b).split('-')[0])
    except ValueError:
        return 999


def check_monotonicity(score_rows, horizon=30):
    """Vérifie que mean_{horizon} décroît quand le score monte. Renvoie (monotone, détails)."""
    seq = [(r['score'], r[f'mean_{horizon}']) for r in score_rows
           if r[f'mean_{horizon}'] is not None and r['n'] >= 10]
    ok = all(seq[i][1] >= seq[i + 1][1] for i in range(len(seq) - 1))
    return ok, seq


# =============================================================================
# AFFICHAGE
# =============================================================================
def _fmt_pct(v):
    return f"{v * 100:+6.2f}%" if v is not None else "   n/a"


def print_table(title, rows, key_col):
    print(f"\n=== {title} ===")
    if not rows:
        print("  (aucun signal)")
        return
    header = f"{key_col:>8} {'n':>6} " + " ".join(
        f"{'mean' + str(h):>9} {'hit' + str(h):>7}" for h in sc.FORWARD_HORIZONS)
    print(header)
    for r in rows:
        line = f"{str(r[key_col]):>8} {r['n']:>6} "
        for h in sc.FORWARD_HORIZONS:
            mean = r.get(f'mean_{h}')
            hit = r.get(f'hit_{h}')
            line += f" {_fmt_pct(mean):>9}" + (f" {hit * 100:5.1f}% " if hit is not None else "   n/a ")
        print(line)


def write_csv(signals, path):
    if not signals:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fields = list(signals[0].keys())
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(signals)


# =============================================================================
# MAIN
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Backtest directionnel stratégie short")
    ap.add_argument('--version', choices=['A', 'B', 'C'], default='A',
                    help="A=momentum, B=+accruals, C=+short interest")
    ap.add_argument('--start', default=None, help="Date début YYYY-MM-DD")
    ap.add_argument('--end', default=None, help="Date fin YYYY-MM-DD")
    ap.add_argument('--split-year', type=int, default=None,
                    help="Année de séparation in-sample / out-of-sample (walk-forward)")
    ap.add_argument('--min-bars', type=int, default=300)
    ap.add_argument('--timing', action='store_true',
                    help="Applique les filtres d'entrée Partie 6 (RSI 35-55, pas de capitulation)")
    ap.add_argument('--out', default=None, help="Chemin CSV de sortie (défaut logs/)")
    args = ap.parse_args()

    # Console Windows en cp1252 → forcer UTF-8 pour les accents/symboles
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    import app as app_module
    application = app_module.create_app()
    with application.app_context():
        # Neutralise IBKR (comme _bt_worker) — ce script est DB-only
        try:
            from services import ibkr_service as _ib
            _ib.ensure_connected = lambda: False
        except Exception:
            pass

        signals = run_backtest(args.version, start=args.start, end=args.end,
                               min_bars=args.min_bars, apply_timing=args.timing)

    print(f"\n{'=' * 70}")
    print(f"BACKTEST DIRECTIONNEL SHORT — version {args.version}"
          f"{' + timing' if args.timing else ''} — {len(signals)} signaux")
    print(f"{'=' * 70}")

    score_rows = report_by_score(signals)
    print_table("Rendement forward par SCORE exact", score_rows, 'score')

    bucket_rows = report_by_bucket(signals)
    print_table("Rendement forward par BUCKET de score", bucket_rows, 'bucket')

    # Monotonicity (horizon 30j)
    mono, seq = check_monotonicity(score_rows, horizon=30)
    print(f"\n=== Monotonicity (horizon 30j) ===")
    print(f"  Monotone (rendement decroit quand score monte) : {'OUI' if mono else 'NON'}")
    print("  sequence score->mean30 :", [(s, _fmt_pct(m)) for s, m in seq])

    # Split par régime
    for reg in ('bull', 'bear'):
        rows = report_by_bucket(signals, regime=reg)
        print_table(f"Par bucket — régime {reg.upper()}", rows, 'bucket')

    # Walk-forward
    if args.split_year:
        cut = f"{args.split_year}-01-01"
        ins = [s for s in signals if s['date'] < cut]
        outs = [s for s in signals if s['date'] >= cut]
        print(f"\n{'=' * 70}\nWALK-FORWARD (split {args.split_year})\n{'=' * 70}")
        print_table(f"IN-SAMPLE (< {args.split_year})", report_by_bucket(ins), 'bucket')
        print_table(f"OUT-OF-SAMPLE (≥ {args.split_year})", report_by_bucket(outs), 'bucket')

    out = args.out or os.path.join('logs', f'short_directional_{args.version}_'
                                   f'{datetime.now():%Y%m%d_%H%M%S}.csv')
    write_csv(signals, out)
    print(f"\n[csv] {len(signals)} signaux → {out}")


if __name__ == '__main__':
    main()
