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


def test_quarterly_universe_filtre_et_classe_par_adv(svc):
    idx = pd.bdate_range('2021-01-01', periods=80)
    dvol = pd.DataFrame({
        'BIG': 50e6, 'MID': 10e6, 'SMALL': 1e6,  # SMALL < 5M$ → exclu
    }, index=idx)
    uni = svc.quarterly_universe(dvol, idx[-1], size=10)
    assert uni == ['BIG', 'MID']  # trié par ADV décroissant, SMALL filtré


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


def test_simulate_un_actif_egal_buy_and_hold(svc):
    didx = pd.bdate_range('2021-01-01', periods=60)
    ret = pd.Series(np.random.default_rng(1).normal(0.001, 0.01, 60), index=didx)
    daily_ret = pd.DataFrame({'X': ret})
    # poids = 100% X à la 1re date
    weights = pd.DataFrame({'X': [1.0]}, index=[didx[0]])
    port = svc.simulate(weights, daily_ret, didx[0], didx[-1])
    # le portefeuille = rendement de X décalé d'un jour (poids appliqués le lendemain)
    expected = ret.shift(0)  # w appliqué via shift(1) sur weights ffill → à partir du 2e jour
    # vérifie la cohérence du compounding : equity = produit cumulé
    eq = (1 + port).cumprod()
    assert eq.iloc[-1] > 0
    # à partir du 2e jour, port_ret doit égaler le rendement de X
    common = port.index.intersection(ret.index)[1:]
    assert np.allclose(port.loc[common].values, ret.loc[common].values, atol=1e-12)


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

    res = svc.run(capital=10000, years=3, nb_top=2, benchmark='SPY')
    assert res['success'] is True
    assert len(res['equity']) > 100
    assert res['stats']['final_value'] > 0
    assert res['stats']['cagr'] is not None
    assert res['stats']['n_rebalances'] > 0
    assert len(res['monthly_returns']) > 0
    assert res['meta']['benchmark'] == 'SPY'
    # la courbe d'équité démarre proche du capital
    assert abs(res['equity'][0]['v'] - 10000) < 10000
