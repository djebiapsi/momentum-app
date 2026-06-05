# -*- coding: utf-8 -*-
"""
Optimisation des paramètres du backtest momentum
=================================================
Cherche la meilleure combinaison vol_target / max_exposure (vol_scaling=True)
selon les critères :
  - max_drawdown ≥ -30 %
  - Sharpe maximal
  - CAGR maximal (départage)

Les données historiques sont chargées UNE seule fois, puis la boucle ne fait
tourner que build_weight_matrix + _simulate par combinaison.

Usage :
    python optimize_backtest.py
    python optimize_backtest.py --years 10 --nb-top 5 --capital 10000 --out resultats.csv
    python optimize_backtest.py --quick          # grille réduite (12 combos)
"""

import argparse
import csv
import math
import sys
import time
from itertools import product

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────
# GRILLES DE PARAMÈTRES
# ─────────────────────────────────────────────
GRID_FULL = {
    'vol_target_pct':   [8, 10, 12, 15, 18, 20, 25, 30, 35, 40, 45, 50],
    'max_exposure_pct': [100, 125, 150, 175, 200, 250, 300],
}
GRID_QUICK = {
    'vol_target_pct':   [10, 15, 20],
    'max_exposure_pct': [100, 150, 200, 300],
}

BASELINE = {'vol_scaling': False, 'vol_target_pct': 12.0, 'max_exposure_pct': 250.0,
            '_label': 'inverse-vol (sans levier)'}

MAX_DD_CONSTRAINT = -0.40   # drawdown maximum accepté

# ─────────────────────────────────────────────
# HELPERS STATS (sans quantstats, pour la vitesse)
# ─────────────────────────────────────────────
TRADING_DAYS = 252


def _cagr(twr_ret: pd.Series) -> float:
    if twr_ret.empty:
        return float('nan')
    n_years = len(twr_ret) / TRADING_DAYS
    total = float((1 + twr_ret).prod())
    if total <= 0 or n_years <= 0:
        return float('nan')
    return total ** (1 / n_years) - 1


def _sharpe(twr_ret: pd.Series, rf=0.0) -> float:
    excess = twr_ret - rf / TRADING_DAYS
    std = float(excess.std(ddof=1))
    if std < 1e-9:
        return float('nan')
    return float(excess.mean()) / std * math.sqrt(TRADING_DAYS)


def _max_drawdown(twr_ret: pd.Series) -> float:
    eq = (1 + twr_ret).cumprod()
    dd = eq / eq.cummax() - 1
    return float(dd.min())


def _sortino(twr_ret: pd.Series, rf=0.0) -> float:
    excess = twr_ret - rf / TRADING_DAYS
    down = excess[excess < 0]
    dstd = float(down.std(ddof=1)) if len(down) > 1 else 1e-9
    if dstd < 1e-9:
        return float('nan')
    return float(excess.mean()) / dstd * math.sqrt(TRADING_DAYS)


def _stats(sim) -> dict:
    twr = sim['twr_ret']
    return {
        'cagr':         round(_cagr(twr) * 100, 2),
        'sharpe':       round(_sharpe(twr), 3),
        'sortino':      round(_sortino(twr), 3),
        'max_dd':       round(_max_drawdown(twr) * 100, 2),
        'volatility':   round(float(twr.std(ddof=1)) * math.sqrt(TRADING_DAYS) * 100, 2),
        'avg_leverage': round(sim['avg_leverage'], 2),
        'max_leverage': round(sim['max_leverage'], 2),
        'n_margin_calls': len(sim['margin_calls']),
        'n_riskoff':    0,   # rempli depuis meta
        'ruined':       sim['ruined'],
    }


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Optimisation paramètres backtest')
    parser.add_argument('--years',    type=int,   default=10,     help='Horizon backtest (défaut 10)')
    parser.add_argument('--nb-top',   type=int,   default=5,      help='Nombre de positions (défaut 5)')
    parser.add_argument('--capital',  type=float, default=10000,  help='Capital initial (défaut 10 000)')
    parser.add_argument('--out',      type=str,   default='_opt_results.csv', help='Fichier CSV résultats')
    parser.add_argument('--quick',    action='store_true',        help='Grille réduite (12 combos)')
    parser.add_argument('--no-baseline', action='store_true',    help='Ne pas ajouter la baseline inverse-vol')
    args = parser.parse_args()

    grid_def = GRID_QUICK if args.quick else GRID_FULL
    combos_vs = list(product(grid_def['vol_target_pct'], grid_def['max_exposure_pct']))
    total_combos = len(combos_vs) + (0 if args.no_baseline else 1)
    print(f'Grille : {len(combos_vs)} combos vol_scaling=True'
          + ('' if args.no_baseline else ' + 1 baseline inverse-vol'))
    print(f'Paramètres fixes : years={args.years}, nb_top={args.nb_top}, capital={args.capital}')
    print()

    # ── Contexte Flask ──────────────────────────────────────────────────────
    import logging
    logging.disable(logging.CRITICAL)
    import app as app_module
    application = app_module.create_app()

    with application.app_context():
        from backtest_service import BacktestService
        from screener_service import ScreenerService
        from momentum_service import MomentumService
        from services import get_backtest_service

        bt = get_backtest_service()

        # ── Chargement des données (une seule fois) ─────────────────────────
        print('Chargement des données...', end=' ', flush=True)
        t0 = time.time()

        end = pd.Timestamp.now().normalize()
        start = end - pd.DateOffset(years=args.years)
        nb_jours = int((end - start).days) + 13 * 31 + 200

        pool = bt.build_candidate_pool()
        close_px, dvol, low_px, fmeta = bt.fetch_history(
            pool, nb_jours, start.date(), max_fetch=0  # uniquement depuis la DB
        )

        if close_px.empty:
            print('\nErreur : cache DB vide. Lance la collecte yfinance d\'abord.')
            sys.exit(1)

        daily_ret = close_px.pct_change()

        # monthly_px : priorité MonthlyPriceBar (20 ans)
        since_monthly = (start - pd.DateOffset(months=14)).date()
        monthly_px_db = bt._load_monthly_px(pool, since_monthly)
        monthly_px = monthly_px_db.combine_first(
            close_px.resample('ME').last()) if not monthly_px_db.empty \
            else close_px.resample('ME').last()

        # low_ret pour margin calls
        low_ret = None
        if low_px is not None and not low_px.empty:
            low_aligned = low_px.reindex(index=close_px.index, columns=close_px.columns)
            low_ret = low_aligned / close_px.shift(1) - 1.0

        print(f'OK ({time.time()-t0:.1f}s) | pool={len(pool)} tickers | '
              f'monthly={monthly_px.shape} | skipped={fmeta.get("skipped",0)}')
        print()

        # Paramètres de simulation fixes (coûts réalistes, pas de DCA ni de levier exotique)
        sim_p = {
            'tx_cost_bps':          5.0,
            'margin_rate_pct':      6.5,
            'cash_yield_pct':       0.0,
            'dca_amount':           0.0,
            'margin_call_enabled':  True,
            'maintenance_margin_pct': 25.0,
            'post_call_leverage':   1.0,
        }

        # ── Boucle d'optimisation ───────────────────────────────────────────
        results = []

        def run_combo(label, params_w, params_s):
            """Exécute une combinaison et retourne un dict de résultats."""
            weights_df, meta_w = bt.build_weight_matrix(
                monthly_px, daily_ret, dvol, start, params_w)
            if weights_df.empty:
                return None
            sim = bt._simulate(
                weights_df, daily_ret, start, end,
                args.capital, {**sim_p, **params_s},
                low_ret=low_ret,
                max_dd_stop=MAX_DD_CONSTRAINT,  # arrêt anticipé si DD > 30%
            )
            if sim is None or sim['equity'].empty or sim['ruined'] or sim.get('early_stop'):
                return None
            st = _stats(sim)
            st['n_riskoff'] = meta_w.get('n_riskoff_months', 0)
            return {'label': label, **params_w, **st}

        all_combos = []
        for vt, me in combos_vs:
            all_combos.append({
                'label':            f'vt={vt}% me={me}%',
                'vol_scaling':      True,
                'vol_target_pct':   float(vt),
                'max_exposure_pct': float(me),
                'portfolio_filter': False,
                'portfolio_vol_threshold_pct': 20.0,
                'nb_top':           args.nb_top,
            })
        if not args.no_baseline:
            all_combos.append({
                'label':            BASELINE['_label'],
                'vol_scaling':      False,
                'vol_target_pct':   12.0,
                'max_exposure_pct': 250.0,
                'portfolio_filter': False,
                'portfolio_vol_threshold_pct': 20.0,
                'nb_top':           args.nb_top,
            })

        t_loop = time.time()
        for idx, combo in enumerate(all_combos, 1):
            label = combo.pop('label')
            params_w = {k: combo[k] for k in
                        ('nb_top','vol_scaling','vol_target_pct','max_exposure_pct',
                         'portfolio_filter','portfolio_vol_threshold_pct')}
            elapsed = time.time() - t_loop
            eta = elapsed / idx * (total_combos - idx) if idx > 1 else 0
            print(f'\r[{idx:3}/{total_combos}] {label:<32}  '
                  f'ETA {eta:.0f}s', end='', flush=True)

            try:
                row = run_combo(label, params_w, {})
                if row:
                    results.append(row)
            except Exception as e:
                pass  # combo invalide (ex: données insuffisantes pour ce levier)

        print(f'\r{" "*70}\r'
              f'Terminé en {time.time()-t_loop:.1f}s — {len(results)}/{total_combos} combos OK')
        print()

        if not results:
            print('Aucun résultat valide.')
            sys.exit(1)

        # ── Filtrage et classement ──────────────────────────────────────────
        df = pd.DataFrame(results)
        df_valid = df[df['max_dd'] >= MAX_DD_CONSTRAINT * 100].copy()
        df_invalid = df[df['max_dd'] < MAX_DD_CONSTRAINT * 100].copy()

        df_valid.sort_values(['sharpe', 'cagr'], ascending=False, inplace=True)
        df_invalid.sort_values('max_dd', ascending=False, inplace=True)

        # ── Affichage ──────────────────────────────────────────────────────
        COLS_DISPLAY = ['label', 'vol_scaling', 'vol_target_pct', 'max_exposure_pct',
                        'sharpe', 'sortino', 'cagr', 'max_dd', 'volatility',
                        'avg_leverage', 'n_margin_calls', 'n_riskoff']

        def _print_table(title, dff, highlight_first=False):
            if dff.empty:
                return
            print(f'{"─"*110}')
            print(f'  {title}')
            print(f'{"─"*110}')
            hdr = (f"{'#':>3}  {'Paramètres':<34}  {'Sharpe':>7}  {'Sortino':>7}  "
                   f"{'CAGR':>7}  {'MaxDD':>7}  {'Vol':>6}  {'AvgLev':>6}  "
                   f"{'Appels':>6}  {'RiskOff':>7}")
            print(hdr)
            print(f'{"─"*110}')
            for rank, (_, row) in enumerate(dff.iterrows(), 1):
                vs = 'VS' if row.get('vol_scaling') else 'EW'
                lbl = f"{vs} vt={row.get('vol_target_pct','-'):.0f}% me={row.get('max_exposure_pct','-'):.0f}%"
                if not row.get('vol_scaling'):
                    lbl = 'EW inverse-vol (base)'
                prefix = '★ ' if highlight_first and rank == 1 else '  '
                dd_str = f"{row['max_dd']:+.1f}%"
                if row['max_dd'] < -25:
                    dd_str = f'\033[91m{dd_str}\033[0m'
                print(f"{prefix}{rank:>3}  {lbl:<34}  {row['sharpe']:>7.3f}  "
                      f"{row['sortino']:>7.3f}  {row['cagr']:>6.1f}%  {dd_str:>9}  "
                      f"{row['volatility']:>5.1f}%  {row['avg_leverage']:>6.2f}  "
                      f"{row['n_margin_calls']:>6}  {int(row['n_riskoff']):>7}")
            print()

        print(f'\n{"═"*110}')
        print(f'  RÉSULTATS — backtest {args.years} ans | nb_top={args.nb_top} | '
              f'contrainte MaxDD ≥ {MAX_DD_CONSTRAINT*100:.0f}%')
        print(f'{"═"*110}\n')

        _print_table(
            f'ÉLIGIBLES ({len(df_valid)} combos) — classés par Sharpe ↓',
            df_valid.head(20), highlight_first=True)

        if not df_invalid.empty:
            _print_table(
                f'HORS CONTRAINTE MaxDD ({len(df_invalid)} combos) — classés par MaxDD ↓',
                df_invalid.head(10))

        # Best combo
        if not df_valid.empty:
            best = df_valid.iloc[0]
            print(f'{"═"*110}')
            print(f'  ★  MEILLEURE COMBINAISON')
            print(f'     vol_target    = {best["vol_target_pct"]:.0f} %')
            print(f'     max_exposure  = {best["max_exposure_pct"]:.0f} %')
            print(f'     vol_scaling   = {bool(best["vol_scaling"])}')
            print(f'     → Sharpe {best["sharpe"]:.3f}  |  CAGR {best["cagr"]:.1f}%  '
                  f'|  MaxDD {best["max_dd"]:.1f}%  |  Levier moy {best["avg_leverage"]:.2f}×')
            print(f'{"═"*110}\n')

        # ── Sauvegarde CSV ─────────────────────────────────────────────────
        all_sorted = pd.concat(
            [df_valid, df_invalid], ignore_index=True
        )[COLS_DISPLAY] if not df_valid.empty else df_invalid[COLS_DISPLAY]
        all_sorted.to_csv(args.out, index=False, float_format='%.4f')
        print(f'Résultats sauvegardés → {args.out}')


if __name__ == '__main__':
    main()
