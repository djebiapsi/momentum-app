# -*- coding: utf-8 -*-
"""
Chargement des données + assemblage du score short (source de vérité unique)
============================================================================
Ce module centralise :
  - le chargement DB (prix journaliers, membership, secteurs, accruals, short interest),
  - les helpers point-in-time (fenêtres de prix, rendement forward),
  - l'assemblage du score composite pour une date donnée (`score_candidates_asof`).

Il est partagé par :
  - le backtest directionnel (`backtest_short_directional.py`), appelé pour chaque
    date d'évaluation historique ;
  - le service de signal live (`short_signal_service.py`), appelé pour aujourd'hui.

Garantit que backtest et live appliquent EXACTEMENT la même logique de données et
de scoring. Aucune sortie/print : pur calcul.
"""

from collections import defaultdict

import pandas as pd

import short_scoring as sc
from short_scoring import ScoreInputs, compute_score

LONGEST_MOM = sc.MOM_LOOKBACK + sc.MOM_SKIP + 5


# =============================================================================
# CHARGEMENT DES DONNÉES (DB-only)
# =============================================================================
def load_daily_prices(min_bars=300, tickers=None):
    """
    Charge les barres journalières de MarketPriceBar.
    Renvoie {ticker: pd.Series(adj_close indexée par date, triée)}.
    Ne garde que les tickers ayant ≥ min_bars barres. `tickers` restreint l'univers.
    """
    from models import db, MarketPriceBar
    from sqlalchemy import func

    q = (db.session.query(MarketPriceBar.ticker, func.count(MarketPriceBar.id))
         .group_by(MarketPriceBar.ticker)
         .having(func.count(MarketPriceBar.id) >= min_bars))
    if tickers is not None:
        q = q.filter(MarketPriceBar.ticker.in_(list(tickers)))
    keep = {t for t, _ in q.all()}
    if not keep:
        return {}

    rows = (db.session.query(MarketPriceBar.ticker, MarketPriceBar.bar_date,
                             MarketPriceBar.adj_close)
            .filter(MarketPriceBar.ticker.in_(keep))
            .order_by(MarketPriceBar.ticker, MarketPriceBar.bar_date).all())

    by_ticker = defaultdict(list)
    for t, d, adj in rows:
        if adj is not None:
            by_ticker[t].append((d, adj))

    out = {}
    for t, series in by_ticker.items():
        if len(series) < min_bars:
            continue
        idx = [d for d, _ in series]
        vals = [v for _, v in series]
        out[t] = pd.Series(vals, index=pd.to_datetime(idx)).sort_index()
    return out


def load_membership():
    """{ticker: [(start_ts|None, end_ts|None), ...]} depuis IndexMembership."""
    from models import IndexMembership
    out = defaultdict(list)
    for r in IndexMembership.query.all():
        s = pd.Timestamp(r.start_date) if r.start_date else None
        e = pd.Timestamp(r.end_date) if r.end_date else None
        out[r.ticker].append((s, e))
    return out


def member_at(membership, ticker, as_of):
    """True si `ticker` appartenait à un indice à la date `as_of` (Timestamp)."""
    ivs = membership.get(ticker)
    if not ivs:
        return False
    for s, e in ivs:
        if (s is None or s <= as_of) and (e is None or as_of < e):
            return True
    return False


def load_sector_map():
    """{ticker: ETF sectoriel} depuis le dernier TickerInfoSnapshot de chaque ticker."""
    from models import db, TickerInfoSnapshot
    from price_data_service import SECTOR_TO_ETF
    from sqlalchemy import func

    sub = (db.session.query(TickerInfoSnapshot.ticker,
                            func.max(TickerInfoSnapshot.collected_at).label('mx'))
           .group_by(TickerInfoSnapshot.ticker).subquery())
    rows = (db.session.query(TickerInfoSnapshot.ticker, TickerInfoSnapshot.sector)
            .join(sub, (TickerInfoSnapshot.ticker == sub.c.ticker)
                  & (TickerInfoSnapshot.collected_at == sub.c.mx)).all())
    out = {}
    for t, sector in rows:
        etf = SECTOR_TO_ETF.get(sector)
        if etf:
            out[t] = etf
    return out


def load_accruals():
    """
    {ticker: [(available_ts, accruals_ratio), ...]} trié par date.
    available_ts = period_date + FILING_LAG_DAYS (anti-look-ahead).
    """
    from models import FundamentalSnapshot
    out = defaultdict(list)
    rows = (FundamentalSnapshot.query
            .with_entities(FundamentalSnapshot.ticker, FundamentalSnapshot.period_date,
                           FundamentalSnapshot.accruals_ratio)
            .filter(FundamentalSnapshot.accruals_ratio.isnot(None)).all())
    for t, pdate, acc in rows:
        avail = pd.Timestamp(pdate) + pd.Timedelta(days=sc.FILING_LAG_DAYS)
        out[t].append((avail, acc))
    for t in out:
        out[t].sort(key=lambda x: x[0])
    return out


def load_short_interest():
    """
    {ticker: [(available_ts, days_to_cover, sir_trend), ...]} trié par date.
    available_ts = report_date (disponibilité réelle, anti-look-ahead).
    """
    from models import ShortInterestSnapshot
    out = defaultdict(list)
    rows = (ShortInterestSnapshot.query
            .with_entities(ShortInterestSnapshot.ticker, ShortInterestSnapshot.report_date,
                           ShortInterestSnapshot.settlement_date,
                           ShortInterestSnapshot.days_to_cover, ShortInterestSnapshot.sir_trend)
            .filter(ShortInterestSnapshot.days_to_cover.isnot(None)).all())
    for t, rdate, sdate, dtc, trend in rows:
        avail = pd.Timestamp(rdate) if rdate else pd.Timestamp(sdate)
        out[t].append((avail, dtc, trend))
    for t in out:
        out[t].sort(key=lambda x: x[0])
    return out


# =============================================================================
# HELPERS point-in-time
# =============================================================================
def latest_before(entries, as_of):
    """Dernière valeur d'une liste [(date, ...)] triée dont date ≤ as_of. None sinon."""
    result = None
    for entry in entries:
        if entry[0] <= as_of:
            result = entry
        else:
            break
    return result


def price_at_pos(series, as_of):
    """(position, prix) de la dernière barre ≤ as_of dans une Series triée. (None,None) sinon."""
    pos = series.index.searchsorted(as_of, side='right') - 1
    if pos < 0:
        return None, None
    return pos, float(series.iloc[pos])


def window_prices(series, as_of, n_back):
    """Liste des n_back derniers prix jusqu'à as_of (pour perf_window/sma)."""
    pos, _ = price_at_pos(series, as_of)
    if pos is None:
        return []
    start = max(0, pos - n_back + 1)
    return [float(v) for v in series.iloc[start:pos + 1].values]


def forward_return(series, as_of, horizon):
    """Rendement du sous-jacent de as_of à as_of + horizon jours de bourse. None si tronqué."""
    pos, p0 = price_at_pos(series, as_of)
    if pos is None or p0 is None or p0 <= 0:
        return None
    fpos = pos + horizon
    if fpos >= len(series):
        return None
    p1 = float(series.iloc[fpos])
    if p1 is None or p1 <= 0:
        return None
    return (p1 / p0) - 1.0


def month_end_dates(spy_series, start=None, end=None):
    """Dernier jour de bourse de chaque mois de l'index SPY, dans [start, end]."""
    idx = spy_series.index
    if start:
        idx = idx[idx >= pd.Timestamp(start)]
    if end:
        idx = idx[idx <= pd.Timestamp(end)]
    df = pd.DataFrame({'d': idx}, index=idx)
    df['ym'] = df['d'].dt.to_period('M')
    return list(df.groupby('ym')['d'].max().values)


# =============================================================================
# ASSEMBLAGE DU SCORE POUR UNE DATE (cœur partagé backtest/live)
# =============================================================================
def score_candidates_asof(as_of, candidates, prices, *, version='C',
                          membership=None, sector_map=None,
                          accruals=None, short_int=None, spy=None,
                          with_forward=False, apply_timing=False):
    """
    Calcule le score composite de chaque candidat à la date `as_of` (Timestamp).

    version : 'A' momentum seul | 'B' +accruals | 'C' +short interest.
    apply_timing : si True, ne retient que les candidats passant les filtres
                   d'entrée de la Partie 6 (RSI ∈ [35,55], pas de capitulation).
    Renvoie une liste de dicts :
      {ticker, score (ScoreResult), perf_63_5, alpha_sector, regime,
       inputs (ScoreInputs), rsi, perf_5_0, [fwd_H...] si with_forward}
    Seuls les candidats avec signal momentum (non-stop) sont retournés.
    """
    membership = membership or {}
    sector_map = sector_map or {}
    accruals = accruals or {}
    short_int = short_int or {}
    spy = spy if spy is not None else prices.get('SPY')

    has_membership = len(membership) > 0

    # Régime marché + perf SPY (alpha marché de repli)
    spy_perf, regime = None, None
    if spy is not None:
        spy_perf = sc.perf_window(window_prices(spy, as_of, LONGEST_MOM))
        _, spy_px = price_at_pos(spy, as_of)
        spy_sma200 = sc.sma(window_prices(spy, as_of, sc.SMA_SLOW), sc.SMA_SLOW)
        if spy_px is not None and spy_sma200 is not None:
            regime = 'bull' if spy_px > spy_sma200 else 'bear'

    # Perf sectorielle (une fois par ETF)
    sector_perf = {}
    for etf in set(sector_map.values()):
        s = prices.get(etf)
        if s is not None:
            sector_perf[etf] = sc.perf_window(window_prices(s, as_of, LONGEST_MOM))

    # Pré-calcul cross-sectionnel (populations pour percentiles)
    acc_pop, sir_pop = [], []
    per_acc, per_sir = {}, {}
    if version in ('B', 'C'):
        for t in candidates:
            e = latest_before(accruals.get(t, []), as_of)
            if e is not None:
                per_acc[t] = e[1]
                acc_pop.append(e[1])
    if version == 'C':
        for t in candidates:
            e = latest_before(short_int.get(t, []), as_of)
            if e is not None:
                per_sir[t] = (e[1], e[2])
                sir_pop.append(e[1])

    results = []
    for t in candidates:
        if has_membership and not member_at(membership, t, as_of):
            continue
        series = prices.get(t)
        if series is None:
            continue
        win = window_prices(series, as_of, LONGEST_MOM)
        perf = sc.perf_window(win)
        if perf is None:
            continue

        etf = sector_map.get(t)
        ref_perf = sector_perf.get(etf) if etf else spy_perf
        alpha_sector = (perf - ref_perf) if ref_perf is not None else None

        inp = ScoreInputs(
            perf_63_5=perf, alpha_sector=alpha_sector, price=win[-1],
            sma50=sc.sma(win, sc.SMA_FAST),
            sma200=sc.sma(window_prices(series, as_of, sc.SMA_SLOW), sc.SMA_SLOW))

        if version in ('B', 'C') and t in per_acc:
            inp.accruals_ratio = per_acc[t]
            inp.accruals_percentile = sc.percentile_rank(per_acc[t], acc_pop)
        if version == 'C' and t in per_sir:
            dtc, trend = per_sir[t]
            inp.sir_days_to_cover = dtc
            inp.sir_trend = trend
            inp.sir_percentile = sc.percentile_rank(dtc, sir_pop)

        res = compute_score(inp)
        if res.stop:
            continue

        # Filtre de timing d'entrée (Partie 6) — optionnel
        timing_ok, timing = sc.entry_timing_ok(win)
        if apply_timing and not timing_ok:
            continue

        entry = {
            'ticker': t, 'score': res, 'inputs': inp,
            'perf_63_5': perf, 'alpha_sector': alpha_sector, 'regime': regime,
            'rsi': timing.get('rsi'), 'perf_5_0': timing.get('perf_5_0'),
        }
        if with_forward:
            for h in sc.FORWARD_HORIZONS:
                entry[f'fwd_{h}'] = forward_return(series, as_of, h)
        results.append(entry)

    return results
