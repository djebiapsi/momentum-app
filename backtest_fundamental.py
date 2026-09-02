# -*- coding: utf-8 -*-
"""
Sanity-backtest QUALITY-VALUE (fenêtre fondamentale courte ~2-3 ans)
===================================================================
Reconstitue le score Quality-Value HISTORIQUEMENT et mesure si le quintile le
mieux classé (Q5) surperforme le moins bien classé (Q1) sur les mois suivants.

Reconstruction historique (sans TickerInfoSnapshot, qui n'est qu'un instantané) :
  - Qualité : ratios intra-période depuis FundamentalSnapshot annuel
    (ROA, ROE, marges, GP/actifs, accruals, dette/FP, current ratio).
  - Valeur : market cap reconstituée = actions × prix, avec
    actions ≈ net_income / eps_diluted (tous deux dans le snapshot). D'où
    earnings/FCF/book/sales yield à chaque date passée.

Anti-look-ahead : un snapshot annuel n'est utilisable qu'à partir de
period_date + FILING_LAG_10K jours (dépôt du 10-K).

⚠️ LIMITE : yfinance ne remonte que ~2-3 ans de fondamentaux → peu de dates
exploitables, puissance statistique faible. Sanity-check, PAS validation robuste.
Pour un vrai backtest long : collecter les fondamentaux via SEC EDGAR company facts.

Usage : python backtest_fundamental.py [--quality-weight 0.5]
"""

import sys
import argparse
from collections import defaultdict

import pandas as pd

import short_data as sd

FILING_LAG_10K = 90          # délai estimé fin d'exercice → dépôt 10-K
FORWARD = [63, 126, 252]     # ~3, 6, 12 mois
N_QUANTILES = 5

# (clé, orientation) — 'high' plus haut meilleur, 'low' plus bas meilleur
QUALITY = [('roa', 'high'), ('roe', 'high'), ('gross_margin', 'high'),
           ('op_margin', 'high'), ('fcf_margin', 'high'), ('gp_to_assets', 'high'),
           ('accruals', 'low'), ('debt_to_equity', 'low'), ('current_ratio', 'high')]
VALUE = [('earnings_yield', 'high'), ('fcf_yield', 'high'),
         ('book_yield', 'high'), ('sales_yield', 'high')]
# Momentum 12-1 (jours de bourse) comme 3e facteur optionnel (QVM)
MOM_LOOKBACK = 252
MOM_SKIP = 21
MOM_WINDOW = MOM_LOOKBACK + MOM_SKIP + 5


def load_annual_fundamentals():
    """
    {ticker: [(avail_ts, snapshot), ...]} trié par date, depuis les snapshots
    ANNUELS. Disponibilité = report_date (date de dépôt SEC réelle, EDGAR) si
    présente, sinon repli period_date + FILING_LAG_10K (yfinance).
    """
    from models import FundamentalSnapshot
    rows = (FundamentalSnapshot.query
            .filter(FundamentalSnapshot.period_type == 'A')
            .order_by(FundamentalSnapshot.ticker, FundamentalSnapshot.period_date).all())
    out = defaultdict(list)
    for r in rows:
        if r.report_date is not None:
            avail = pd.Timestamp(r.report_date)
        else:
            avail = pd.Timestamp(r.period_date) + pd.Timedelta(days=FILING_LAG_10K)
        out[r.ticker].append((avail, r))
    # tri par disponibilité (report_date peut réordonner vs period_date)
    for t in out:
        out[t].sort(key=lambda x: x[0])
    return out


def raw_metrics(snap, price):
    """Métriques brutes quality+value depuis un snapshot annuel + prix courant."""
    ni = snap.net_income
    rev = snap.total_revenue
    eq = snap.total_equity
    ta = snap.total_assets
    gp = snap.gross_profit
    op = snap.operating_income
    fcf = snap.free_cash_flow
    ca = snap.current_assets
    cl = snap.current_liabilities
    eps = snap.eps_diluted
    debt = snap.total_debt

    m = {}
    # Qualité
    m['roa'] = (ni / ta) if (ni is not None and ta not in (None, 0)) else None
    m['roe'] = (ni / eq) if (ni is not None and eq not in (None, 0)) else None
    m['gross_margin'] = (gp / rev) if (gp is not None and rev not in (None, 0)) else None
    m['op_margin'] = (op / rev) if (op is not None and rev not in (None, 0)) else None
    m['fcf_margin'] = (fcf / rev) if (fcf is not None and rev not in (None, 0)) else None
    m['gp_to_assets'] = (gp / ta) if (gp is not None and ta not in (None, 0)) else None
    m['accruals'] = snap.accruals_ratio
    m['debt_to_equity'] = (debt / eq) if (debt is not None and eq not in (None, 0)) else None
    m['current_ratio'] = (ca / cl) if (ca is not None and cl not in (None, 0)) else None

    # Valeur : actions reconstituées = ni / eps (si eps > 0)
    shares = (ni / eps) if (ni is not None and eps not in (None, 0) and eps > 0) else None
    mktcap = (shares * price) if (shares is not None and price) else None
    m['earnings_yield'] = (eps / price) if (eps is not None and price) else None
    m['fcf_yield'] = (fcf / mktcap) if (fcf is not None and mktcap not in (None, 0)) else None
    m['book_yield'] = (eq / mktcap) if (eq is not None and mktcap not in (None, 0)) else None
    m['sales_yield'] = (rev / mktcap) if (rev is not None and mktcap not in (None, 0)) else None
    return m


def percentiles(values, orientation):
    """{ticker: valeur} → {ticker: percentile 0-100, 100=meilleur}."""
    pairs = [(t, v) for t, v in values.items() if v is not None]
    if len(pairs) < 5:
        return {}
    pairs.sort(key=lambda x: x[1])
    n = len(pairs)
    out = {}
    for i, (t, _) in enumerate(pairs):
        p = 100.0 * i / (n - 1)
        out[t] = p if orientation == 'high' else (100.0 - p)
    return out


def run(wq=0.5, wv=0.5, wm=0.0, start=None, end=None, min_bars=300):
    import short_scoring as sc
    prices = sd.load_daily_prices(min_bars=min_bars)
    if 'SPY' not in prices:
        raise SystemExit("SPY absent — lancer la collecte de prix.")
    membership = sd.load_membership()
    has_mem = len(membership) > 0
    fundamentals = load_annual_fundamentals()
    spy = prices['SPY']
    eval_dates = sd.month_end_dates(spy, start, end)

    from price_data_service import BENCHMARKS
    bench = set(BENCHMARKS)
    candidates = [t for t in prices if t not in bench and t in fundamentals]

    # normalisation des poids
    tot = wq + wv + wm
    wq, wv, wm = (wq / tot, wv / tot, wm / tot) if tot else (0.5, 0.5, 0.0)
    print(f"[info] tickers: {len(candidates)} | dates: {len(eval_dates)} | "
          f"poids Q/V/M = {wq:.2f}/{wv:.2f}/{wm:.2f}")

    acc_abs = defaultdict(lambda: defaultdict(list))
    acc_rel = defaultdict(lambda: defaultdict(list))
    dates_used = 0

    for as_of in eval_dates:
        as_of = pd.Timestamp(as_of)
        # métriques brutes par ticker
        rawm, fwd, mom = {}, {}, {}
        for t in candidates:
            if has_mem and not sd.member_at(membership, t, as_of):
                continue
            e = sd.latest_before(fundamentals.get(t, []), as_of)
            if e is None:
                continue
            series = prices.get(t)
            _, price = sd.price_at_pos(series, as_of)
            if price is None:
                continue
            f = {h: sd.forward_return(series, as_of, h) for h in FORWARD}
            if all(f[h] is None for h in FORWARD):
                continue
            rawm[t] = raw_metrics(e[1], price)
            fwd[t] = f
            if wm > 0:
                m = sc.perf_window(sd.window_prices(series, as_of, MOM_WINDOW),
                                   lookback=MOM_LOOKBACK, skip=MOM_SKIP)
                if m is not None:
                    mom[t] = m

        if len(rawm) < N_QUANTILES * 4:
            continue

        # percentiles par métrique
        pcts = {}
        for key, orient in QUALITY + VALUE:
            pcts[key] = percentiles({t: rawm[t].get(key) for t in rawm}, orient)
        mom_pct = percentiles(mom, 'high') if wm > 0 else {}

        # composite par ticker (min de métriques requises)
        comp = {}
        for t in rawm:
            qs = [pcts[k].get(t) for k, _ in QUALITY if pcts[k].get(t) is not None]
            vs = [pcts[k].get(t) for k, _ in VALUE if pcts[k].get(t) is not None]
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

        if len(comp) < N_QUANTILES * 4:
            continue
        dates_used += 1

        month_mean = {}
        for h in FORWARD:
            xs = [fwd[t][h] for t in comp if fwd[t][h] is not None]
            month_mean[h] = (sum(xs) / len(xs)) if xs else None

        # quintiles par composite croissant : Q1 = pire, Q5 = meilleur
        items = sorted(comp.items(), key=lambda x: x[1])
        n = len(items)
        for i, (t, _) in enumerate(items):
            qi = min(N_QUANTILES - 1, i * N_QUANTILES // n)
            for h in FORWARD:
                r = fwd[t][h]
                if r is None:
                    continue
                acc_abs[qi][h].append(r)
                if month_mean[h] is not None:
                    acc_rel[qi][h].append(r - month_mean[h])

    return acc_abs, acc_rel, dates_used


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _fmt(v):
    return f"{v * 100:+6.2f}%" if v is not None else "   n/a"


def print_report(acc_abs, acc_rel, dates_used, label):
    print(f"\n[dates exploitables: {dates_used} | {label}]")
    for title, acc in (("ABSOLU", acc_abs), ("RELATIF (vs médiane univers)", acc_rel)):
        print(f"\n=== {label} — rendement forward {title} par quintile ===")
        print(f"{'quintile':>16} {'n':>6} " + " ".join(f"{'fwd' + str(h):>10}" for h in FORWARD))
        means = {h: [] for h in FORWARD}
        for q in range(N_QUANTILES):
            label = (f"Q{q+1}" + (" (pire QV)" if q == 0
                     else " (meilleur QV)" if q == N_QUANTILES - 1 else ""))
            n = len(acc[q][FORWARD[0]])
            line = f"{label:>16} {n:>6} "
            for h in FORWARD:
                m = _mean(acc[q][h])
                means[h].append(m)
                line += f" {_fmt(m):>10}"
            print(line)
        for h in FORWARD:
            q5, q1 = means[h][-1], means[h][0]
            if q5 is not None and q1 is not None:
                spread = q5 - q1
                ok = spread > 0
                print(f"   → H{h}: Q5(meilleur)={_fmt(q5)} Q1(pire)={_fmt(q1)} "
                      f"spread(Q5-Q1)={_fmt(spread)}  QV {'VIABLE' if ok else 'adverse'}")


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Backtest Quality-Value(-Momentum)")
    ap.add_argument('--wq', type=float, default=0.5, help="poids qualité")
    ap.add_argument('--wv', type=float, default=0.5, help="poids value")
    ap.add_argument('--wm', type=float, default=0.0, help="poids momentum 12-1 (QVM)")
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
        acc_abs, acc_rel, dates_used = run(
            wq=args.wq, wv=args.wv, wm=args.wm, start=args.start,
            end=args.end, min_bars=args.min_bars)

    label = f"QVM Q{args.wq:g}/V{args.wv:g}/M{args.wm:g}"
    print(f"\n{'=' * 70}")
    print(f"BACKTEST FONDAMENTAL — {label}")
    print(f"{'=' * 70}")
    print_report(acc_abs, acc_rel, dates_used, label)
    print("\nLecture : viable = Q5 (meilleur composite) surperforme Q1 (spread Q5-Q1 > 0),")
    print("en ABSOLU (positions longues) ET en RELATIF.")


if __name__ == '__main__':
    main()
