# -*- coding: utf-8 -*-
"""
Backtest PORTEFEUILLE Quality-Value (courbe d'équité + stats, comme le momentum)
================================================================================
Simule la stratégie réelle : à chaque rééquilibrage (annuel), sélectionne le
top-N Quality-Value (équipondéré), le détient jusqu'au rebalance suivant, et trace
la courbe d'équité vs un benchmark (univers équipondéré). Point-in-time strict
(fondamentaux dispo à report_date), univers point-in-time via IndexMembership.

Réutilise le scoring de `backtest_fundamental` (source de vérité) et les loaders
de `short_data`. Conçu pour tourner en SUBPROCESS (`_qv_bt_worker.py`).
"""

from collections import defaultdict

import pandas as pd

import short_scoring as sc
import short_data as sd
import backtest_fundamental as bf


def _composite_at(as_of, candidates, prices, fundamentals, membership,
                  wq, wv, wm):
    """{ticker: composite} à la date as_of (percentiles cross-sectionnels)."""
    has_mem = len(membership) > 0
    rawm, mom = {}, {}
    for t in candidates:
        if has_mem and not sd.member_at(membership, t, as_of):
            continue
        e = sd.latest_before(fundamentals.get(t, []), as_of)
        if e is None:
            continue
        series = prices.get(t)
        if series is None:
            continue
        _, price = sd.price_at_pos(series, as_of)
        if price is None:
            continue
        rawm[t] = bf.raw_metrics(e[1], price)
        if wm > 0:
            m = sc.perf_window(sd.window_prices(series, as_of, bf.MOM_WINDOW),
                               lookback=bf.MOM_LOOKBACK, skip=bf.MOM_SKIP)
            if m is not None:
                mom[t] = m
    if len(rawm) < 10:
        return {}
    pcts = {}
    for key, orient in bf.QUALITY + bf.VALUE:
        pcts[key] = bf.percentiles({t: rawm[t].get(key) for t in rawm}, orient)
    mom_pct = bf.percentiles(mom, 'high') if wm > 0 else {}
    comp = {}
    for t in rawm:
        qs = [pcts[k].get(t) for k, _ in bf.QUALITY if pcts[k].get(t) is not None]
        vs = [pcts[k].get(t) for k, _ in bf.VALUE if pcts[k].get(t) is not None]
        if len(qs) < 4 or len(vs) < 2:
            continue
        q = sum(qs) / len(qs)
        v = sum(vs) / len(vs)
        if wm > 0:
            mp = mom_pct.get(t)
            if mp is None:
                continue
            comp[t] = wq * q + wv * v + wm * mp
        else:
            comp[t] = wq * q + wv * v
    return comp


def _ret(series, d0, d1):
    """Rendement d'un titre entre deux dates (dernière barre ≤ d)."""
    _, p0 = sd.price_at_pos(series, d0)
    _, p1 = sd.price_at_pos(series, d1)
    if p0 and p1 and p0 > 0:
        return (p1 / p0) - 1.0
    return None


def _stats(monthly_returns, equity):
    """CAGR, vol, Sharpe, max drawdown à partir des rendements mensuels + équité."""
    import math
    n = len(monthly_returns)
    if n < 2 or not equity:
        return {}
    years = n / 12.0
    total = equity[-1] / equity[0] - 1.0
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if years > 0 and equity[0] > 0 else None
    mean = sum(monthly_returns) / n
    var = sum((r - mean) ** 2 for r in monthly_returns) / (n - 1)
    vol = math.sqrt(var) * math.sqrt(12)
    sharpe = (mean * 12) / vol if vol > 0 else None
    peak = equity[0]
    maxdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = v / peak - 1.0
        if dd < maxdd:
            maxdd = dd
    return {
        'total_return': round(total, 4),
        'cagr': round(cagr, 4) if cagr is not None else None,
        'volatility': round(vol, 4),
        'sharpe': round(sharpe, 2) if sharpe is not None else None,
        'max_drawdown': round(maxdd, 4),
        'months': n,
        'years': round(years, 1),
    }


def run_portfolio_backtest(market='us', wq=0.5, wv=0.5, wm=0.0, top_n=20,
                           rebalance_months=12, start=None, end=None, min_bars=300):
    """
    Renvoie {success, equity:[{t,v}], benchmark_equity:[...], stats, benchmark_stats, meta}.
    equity/benchmark_equity : courbes normalisées base 1.0 (dates mensuelles).
    """
    from eu_universe import universe_for_market
    region = universe_for_market(market) if market in ('us', 'eu') else None

    prices = sd.load_daily_prices(min_bars=min_bars, tickers=region)
    if 'SPY' not in prices:
        # SPY sert de calendrier ; le charger si l'univers régional l'exclut
        spy_only = sd.load_daily_prices(min_bars=min_bars, tickers={'SPY'})
        prices.update(spy_only)
    if 'SPY' not in prices:
        return {'success': False, 'error': "SPY absent — calendrier indisponible."}

    membership = sd.load_membership()
    fundamentals = bf.load_annual_fundamentals()
    spy = prices['SPY']
    eval_dates = [pd.Timestamp(d) for d in sd.month_end_dates(spy, start, end)]
    if len(eval_dates) < 13:
        return {'success': False, 'error': "Historique insuffisant pour ce marché."}

    from price_data_service import BENCHMARKS
    bench = set(BENCHMARKS)
    candidates = [t for t in prices if t not in bench and t in fundamentals]
    if len(candidates) < 15:
        return {'success': False,
                'error': f"Univers trop petit ({len(candidates)}) — collecter les fondamentaux."}

    equity, bench_equity = [1.0], [1.0]
    curve, bench_curve = [], []
    monthly, bench_monthly = [], []
    holdings = []
    tot = wq + wv + wm
    wqn, wvn, wmn = (wq / tot, wv / tot, wm / tot) if tot else (0.5, 0.5, 0.0)

    for i in range(len(eval_dates) - 1):
        d0, d1 = eval_dates[i], eval_dates[i + 1]
        # Rééquilibrage périodique
        if i % rebalance_months == 0 or not holdings:
            comp = _composite_at(d0, candidates, prices, fundamentals, membership, wqn, wvn, wmn)
            if comp:
                ranked = sorted(comp.items(), key=lambda x: -x[1])
                holdings = [t for t, _ in ranked[:top_n]]
                universe_now = list(comp.keys())
            else:
                universe_now = candidates
        else:
            comp = None

        # Rendement du portefeuille (équipondéré) sur [d0, d1]
        rs = [_ret(prices[t], d0, d1) for t in holdings if t in prices]
        rs = [r for r in rs if r is not None]
        r_pf = sum(rs) / len(rs) if rs else 0.0
        # Benchmark : univers éligible équipondéré
        try:
            uni = universe_now
        except NameError:
            uni = candidates
        rb = [_ret(prices[t], d0, d1) for t in uni if t in prices]
        rb = [r for r in rb if r is not None]
        r_bm = sum(rb) / len(rb) if rb else 0.0

        equity.append(equity[-1] * (1 + r_pf))
        bench_equity.append(bench_equity[-1] * (1 + r_bm))
        monthly.append(r_pf)
        bench_monthly.append(r_bm)
        ds = d1.date().isoformat()
        curve.append({'t': ds, 'v': round(equity[-1], 4)})
        bench_curve.append({'t': ds, 'v': round(bench_equity[-1], 4)})

    return {
        'success': True,
        'market': market,
        'equity': curve,
        'benchmark_equity': bench_curve,
        'stats': _stats(monthly, equity),
        'benchmark_stats': _stats(bench_monthly, bench_equity),
        'meta': {
            'top_n': top_n, 'weights': {'quality': round(wqn, 2), 'value': round(wvn, 2),
                                        'momentum': round(wmn, 2)},
            'rebalance_months': rebalance_months,
            'universe': len(candidates),
            'start': curve[0]['t'] if curve else None,
            'end': curve[-1]['t'] if curve else None,
            'current_holdings': holdings,
        },
    }
