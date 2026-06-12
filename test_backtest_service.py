# -*- coding: utf-8 -*-
"""Tests du moteur de backtest sur données SYNTHÉTIQUES (aucun appel réseau)."""
import numpy as np
import pandas as pd
import pytest

from backtest_service import BacktestService


def _daily_frame(start, n, drift, vol, vol_dollar, seed=0):
    """Construit un df journalier (adjClose/close/volume) avec une tendance donnée."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    rets = rng.normal(drift, vol, n)
    px = 100 * np.cumprod(1 + rets)
    volume = np.full(n, vol_dollar) / px  # volume tel que close*volume = vol_dollar
    return pd.DataFrame({'adjClose': px, 'close': px, 'volume': volume}, index=idx)


@pytest.fixture
def svc():
    return BacktestService(momentum_service=None, screener_service=None)


def test_listed_after_start_accepte_ipo_post_start(svc):
    """Un titre coté APRÈS le début du backtest (IPO) doit être gardé depuis la DB."""
    from datetime import date
    df = _daily_frame('2012-05-18', 200, 0.001, 0.01, 10e6)  # META : IPO mai 2012
    start = date(2006, 6, 1)
    # 1ʳᵉ barre mensuelle ≈ 1ʳᵉ barre daily → historique complet, on garde
    assert svc._listed_after_start(df, date(2012, 5, 1), start)
    # Mensuel bien plus ancien → daily tronqué, il faut aller chercher plus profond
    assert not svc._listed_after_start(df, date(1994, 7, 1), start)
    # Pas d'info mensuelle → comportement historique (réseau)
    assert not svc._listed_after_start(df, None, start)
    # Historique couvrant déjà start → géré par _db_covers, pas ici
    df_old = _daily_frame('2005-01-03', 200, 0.001, 0.01, 10e6)
    assert not svc._listed_after_start(df_old, date(2005, 1, 1), start)


def test_momentum_12_1(svc):
    # 14 mois croissants → momentum positif ; le dernier mois est exclu
    idx = pd.date_range('2020-01-31', periods=14, freq='ME')
    s = pd.Series(np.linspace(100, 130, 14), index=idx)
    m = svc._momentum_12_1(s, idx[-1])
    assert m is not None and m > 0


def test_compute_weights_inverse_vol_somme_100(svc):
    # 3 actifs, momentum positif ; pondération inverse-vol normalisée à 1
    idx_m = pd.date_range('2020-01-31', periods=14, freq='ME')
    monthly = pd.DataFrame({
        'A': np.linspace(100, 160, 14),
        'B': np.linspace(100, 140, 14),
        'C': np.linspace(100, 120, 14),
    }, index=idx_m)
    didx = pd.bdate_range('2019-12-01', periods=300)
    daily_ret = pd.DataFrame(
        np.random.default_rng(0).normal(0.0005, 0.01, (300, 3)),
        index=didx, columns=['A', 'B', 'C'])
    params = dict(nb_top=3, vol_scaling=False, vol_target_pct=12, max_exposure_pct=250,
                  portfolio_filter=False, portfolio_vol_threshold_pct=20)
    w = svc.compute_weights(idx_m[-1], ['A', 'B', 'C'], monthly, daily_ret, params)
    assert len(w) == 3
    assert abs(w.sum() - 1.0) < 1e-9


def test_compute_weights_exclut_momentum_negatif(svc):
    idx_m = pd.date_range('2020-01-31', periods=14, freq='ME')
    monthly = pd.DataFrame({
        'UP': np.linspace(100, 160, 14),
        'DOWN': np.linspace(160, 100, 14),  # momentum négatif → exclu
    }, index=idx_m)
    daily_ret = pd.DataFrame(columns=['UP', 'DOWN'])
    params = dict(nb_top=5, vol_scaling=False, vol_target_pct=12, max_exposure_pct=250,
                  portfolio_filter=False, portfolio_vol_threshold_pct=20)
    w = svc.compute_weights(idx_m[-1], ['UP', 'DOWN'], monthly, daily_ret, params)
    assert list(w.index) == ['UP']


def test_simulate_sans_levier(svc):
    """_simulate sur un actif unique, sans levier, sans DCA : equity > capital si drift positif."""
    didx = pd.bdate_range('2021-01-01', periods=60)
    np.random.seed(1)
    ret = pd.Series(np.random.normal(0.002, 0.01, 60), index=didx)
    daily_ret = pd.DataFrame({'X': ret})
    me = pd.date_range(didx[0], didx[-1], freq='ME')
    weights = pd.DataFrame({'X': 1.0}, index=me)
    p = dict(tx_cost_bps=0, margin_rate_pct=0, cash_yield_pct=0,
             dca_amount=0, margin_call_enabled=False,
             maintenance_margin_pct=25, post_call_leverage=1.0)
    sim = svc._simulate(weights, daily_ret, didx[0], didx[-1], 10000.0, p)
    assert sim is not None
    assert len(sim['equity']) == 60
    assert sim['final_equity'] > 0
    assert sim['max_leverage'] <= 1.01  # pas de levier
    assert len(sim['margin_calls']) == 0


def test_simulate_margin_call_declenche(svc):
    """_simulate : krach -35% sur book 2.5x → appel de marge attendu."""
    np.random.seed(2)
    didx = pd.bdate_range('2021-01-01', periods=600)
    dr = pd.DataFrame(np.random.normal(0.0004, 0.01, (600, 2)),
                      index=didx, columns=['A', 'B'])
    dr.loc[didx[300]] = [-0.35, -0.34]
    me = pd.date_range(didx[0], didx[-1], freq='ME')
    weights = pd.DataFrame({'A': 1.5, 'B': 1.0}, index=me)  # gross 2.5x
    p = dict(tx_cost_bps=5, margin_rate_pct=6.5, cash_yield_pct=0,
             dca_amount=0, margin_call_enabled=True,
             maintenance_margin_pct=25, post_call_leverage=1.0)
    sim = svc._simulate(weights, dr, didx[0], didx[-1], 10000.0, p)
    assert len(sim['margin_calls']) >= 1
    # désactivé : 0 appel
    p2 = dict(p); p2['margin_call_enabled'] = False
    sim2 = svc._simulate(weights, dr, didx[0], didx[-1], 10000.0, p2)
    assert len(sim2['margin_calls']) == 0


def test_simulate_dca(svc):
    """_simulate avec DCA : total_invested = capital + n_rebalances * dca_amount."""
    didx = pd.bdate_range('2022-01-01', periods=252)
    dr = pd.DataFrame({'A': np.random.default_rng(3).normal(0.0003, 0.01, 252)}, index=didx)
    me = pd.date_range(didx[0], didx[-1], freq='ME')
    weights = pd.DataFrame({'A': 1.0}, index=me)
    p = dict(tx_cost_bps=0, margin_rate_pct=0, cash_yield_pct=0,
             dca_amount=500, margin_call_enabled=False,
             maintenance_margin_pct=25, post_call_leverage=1.0)
    sim = svc._simulate(weights, dr, didx[0], didx[-1], 10000.0, p)
    # total_invested = capital + nb apports
    assert sim['total_invested'] == 10000 + sim['contrib_total']
    assert sim['contrib_total'] > 0


def test_run_end_to_end_synthetique(svc, monkeypatch):
    tickers = ['AAA', 'BBB', 'CCC', 'DDD', 'EEE']

    def fake_pool(pool_size=None):
        return tickers

    frames = {}
    for i, t in enumerate(tickers):
        frames[t] = _daily_frame('2018-06-01', 1700, drift=0.0006 + i * 0.0001,
                                  vol=0.012, vol_dollar=20e6, seed=i)
    frames['SPY'] = _daily_frame('2018-06-01', 1700, drift=0.0004, vol=0.009,
                                 vol_dollar=500e6, seed=99)

    def fake_fetch(ticker, nb_jours):
        df = frames.get(ticker.upper())
        return (df, None) if df is not None else (None, 'absent')

    monkeypatch.setattr(svc, 'build_candidate_pool', fake_pool)
    monkeypatch.setattr(svc, '_fetch_ticker', fake_fetch)

    res = svc.run(capital=10000, years=3, nb_top=2, benchmark='SPY',
                  tx_cost_bps=5, dca_amount=0, margin_call_enabled=False)
    assert res['success'] is True
    assert len(res['equity']) > 100
    assert res['stats']['final_value'] > 0
    assert res['stats']['cagr'] is not None
    assert res['stats']['n_rebalances'] > 0
    assert len(res['monthly_returns']) > 0
    assert len(res['yearly_returns']) > 0
    assert res['meta']['benchmark'] == 'SPY'
    # la courbe d'équité démarre proche du capital
    assert abs(res['equity'][0]['v'] - 10000) < 10000
    # les nouvelles clés du payload sont présentes
    assert 'drawdown_periods' in res
    assert 'benchmark_stats' in res
    assert res['stats']['tx_costs'] >= 0
