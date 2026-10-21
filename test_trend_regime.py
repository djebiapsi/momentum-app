# -*- coding: utf-8 -*-
"""Tests du régime de tendance 4 états (trend_regime) et de son branchement au backtest."""
import numpy as np
import pandas as pd
import pytest

import trend_regime as tr
from backtest_service import BacktestService


def _path(rets, start='2010-01-04'):
    idx = pd.bdate_range(start, periods=len(rets))
    return pd.Series(100 * np.cumprod(1 + np.asarray(rets)), index=idx)


def _bull_bear_rebound(seed=0):
    """Hausse 2 ans → baisse 1 an → reprise 1 an (séances)."""
    rng = np.random.default_rng(seed)
    r = np.concatenate([rng.normal(0.0008, 0.008, 504),
                        rng.normal(-0.0020, 0.012, 252),
                        rng.normal(0.0020, 0.010, 252)])
    return _path(r)


def test_classify_quatre_etats():
    slow = pd.Series([0.1, 0.1, -0.1, -0.1])
    fast = pd.Series([0.02, -0.02, 0.02, -0.02])
    assert list(tr.classify(slow, fast)) == [tr.BULL, tr.CORRECTION, tr.REBOUND, tr.BEAR]


def test_exposition_cash_seulement_en_bear():
    s = _bull_bear_rebound()
    expo, st = tr.exposure_series(s, mode='monthly')
    assert set(expo.unique()) <= {0.0, 1.0}
    assert (expo[st == tr.BEAR] == 0.0).all()
    assert (expo[st.isin([tr.BULL, tr.CORRECTION, tr.REBOUND])] == 1.0).all()
    assert (expo == 0.0).any(), 'la baisse d’un an doit déclencher une sortie'
    assert expo.iloc[-1] == 1.0, 'la reprise doit faire rentrer'


def test_mensuel_tenu_jusqu_a_la_fin_de_mois_suivante():
    s = _bull_bear_rebound()
    expo, _ = tr.exposure_series(s, mode='monthly')
    # l'exposition ne change qu'aux dernières séances de mois
    changes = expo.index[expo.ne(expo.shift(1))][1:]
    for d in changes:
        assert (d + pd.offsets.BDay(1)).month != d.month


@pytest.mark.parametrize('mode', ['monthly', 'daily', 'blend'])
def test_aucun_lookahead(mode):
    """Tronquer l'historique ne change aucune exposition passée."""
    s = _bull_bear_rebound(3)
    full, _ = tr.exposure_series(s, mode=mode)
    for cut in (600, 700, 850):
        part, _ = tr.exposure_series(s.iloc[:cut], mode=mode)
        common = part.index[:-25]          # le mois en cours peut être provisoire
        pd.testing.assert_series_equal(part.loc[common], full.loc[common], check_names=False)


def test_blend_et_levier_bull():
    s = _bull_bear_rebound(5)
    expo, st_m = tr.exposure_series(s, mode='blend', bull_exposure=1.25)
    assert set(np.round(expo.unique(), 4)) <= {0.0, 0.5, 1.0, 1.25, 1.5, 0.75}
    assert (expo[st_m == tr.BULL] >= 1.0).all()
    assert expo.max() == pytest.approx(1.25)


def test_current_regime_pivot():
    s = _bull_bear_rebound(1)
    cur = tr.current_regime(s)
    assert cur['error'] is None
    assert cur['state'] in (tr.BULL, tr.CORRECTION, tr.REBOUND, tr.BEAR)
    assert cur['pivot'] <= cur['bull_level']
    # sous le pivot à la prochaine fin de mois ⇔ BEAR
    px = cur['pivot'] * 0.99
    ref = [cur['pivot'], cur['bull_level']]
    assert px < min(ref)
    assert cur['action'] in ('RESTER_INVESTI', 'SORTIR', 'RESTER_EN_CASH', 'RENTRER')


def test_episodes():
    s = _bull_bear_rebound()
    eps = tr.episodes(s, mode='monthly')
    assert eps and eps[0]['exit'] < (eps[0]['reentry'] or '9999')
    assert all(e['min_exposure'] == 0.0 for e in eps)


def test_build_weight_matrix_regime_cash():
    """Le multiplicateur d'exposition met la ligne de poids à zéro en BEAR."""
    svc = BacktestService(momentum_service=None, screener_service=None)
    s = _bull_bear_rebound(2)
    rng = np.random.default_rng(9)
    tick = ['AAA', 'BBB', 'CCC']
    close = pd.DataFrame({t: 50 * np.cumprod(1 + rng.normal(0.001, 0.01, len(s))) for t in tick},
                         index=s.index)
    monthly = close.resample('ME').last()
    daily_ret = close.pct_change()
    params = {'nb_top': 2, 'vol_scaling': False, 'vol_target_pct': 20.0,
              'max_exposure_pct': 100.0, 'portfolio_filter': False,
              'portfolio_vol_threshold_pct': 20.0}
    expo, st = tr.exposure_series(s, mode='monthly')
    start = monthly.index[14]
    wdf, meta = svc.build_weight_matrix(monthly, daily_ret, None, start, params,
                                        exposure_signal=expo)
    assert meta['n_regime_cash_months'] > 0
    bear_me = [d for d in wdf.index if tr.exposure_at(expo, d) == 0.0]
    assert bear_me and all(wdf.loc[d].sum() == 0.0 for d in bear_me)
    bull_me = [d for d in wdf.index if tr.exposure_at(expo, d) == 1.0]
    assert all(wdf.loc[d].sum() == pytest.approx(1.0) for d in bull_me)


def test_build_weight_matrix_poche_indice():
    svc = BacktestService(momentum_service=None, screener_service=None)
    s = _bull_bear_rebound(4)
    rng = np.random.default_rng(1)
    close = pd.DataFrame({t: 50 * np.cumprod(1 + rng.normal(0.001, 0.01, len(s))) for t in ['AAA', 'BBB']},
                         index=s.index)
    monthly = close.resample('ME').last()
    daily_ret = close.pct_change()
    daily_ret['SPY'] = s.pct_change()
    params = {'nb_top': 2, 'vol_scaling': False, 'vol_target_pct': 20.0,
              'max_exposure_pct': 100.0, 'portfolio_filter': False,
              'portfolio_vol_threshold_pct': 20.0}
    sig = pd.Series(False, index=s.index)
    sig.iloc[700:760] = True
    wdf, meta = svc.build_weight_matrix(monthly, daily_ret, None, monthly.index[14], params,
                                        index_signal=sig, index_ticker='SPY')
    on = [d for d in wdf.index if tr.exposure_at(sig.astype(float), d) == 1.0]
    assert on and all(wdf.loc[d, 'SPY'] == 1.0 and wdf.loc[d].drop('SPY').sum() == 0 for d in on)


def test_build_weight_matrix_regime_par_poche():
    """Poche en BEAR exclue du classement ; poche en BULL × bull_exposure ; deux BEAR → cash."""
    svc = BacktestService(momentum_service=None, screener_service=None)
    idx = pd.bdate_range('2010-01-04', periods=900)
    rng = np.random.default_rng(3)
    close = pd.DataFrame({t: 50 * np.cumprod(1 + rng.normal(0.0008, 0.01, len(idx)))
                          for t in ['NQ1', 'NQ2', 'SP1', 'SP2']}, index=idx)
    monthly = close.resample('ME').last()
    daily_ret = close.pct_change()
    params = {'nb_top': 2, 'vol_scaling': False, 'vol_target_pct': 20.0,
              'max_exposure_pct': 100.0, 'portfolio_filter': False,
              'portfolio_vol_threshold_pct': 20.0}
    start = monthly.index[14]
    me = [d for d in monthly.index if d >= start]
    # SPY : BULL (1,25) puis BEAR ; QQQ : BEAR puis BULL (1,25) ; puis les deux BEAR
    expo_main = pd.Series(1.25, index=idx); expo_main.iloc[450:] = 0.0
    expo_alt = pd.Series(0.0, index=idx); expo_alt.iloc[450:700] = 1.25
    sleeve = {'tickers': {'NQ1', 'NQ2'}, 'expo_main': expo_main, 'expo_alt': expo_alt}
    wdf, meta = svc.build_weight_matrix(monthly, daily_ret, None, start, params, sleeve=sleeve)
    for d in me:
        if d not in wdf.index:
            continue
        row = wdf.loc[d]
        m_main, m_alt = tr.exposure_at(expo_main, d), tr.exposure_at(expo_alt, d)
        if m_main > 0 and m_alt == 0:
            assert row[['NQ1', 'NQ2']].sum() == 0 and row.sum() == pytest.approx(1.25)
        elif m_alt > 0 and m_main == 0:
            assert row[['SP1', 'SP2']].sum() == 0 and row.sum() == pytest.approx(1.25)
        elif m_main == 0 and m_alt == 0:
            assert row.sum() == 0
    assert meta['sleeve_months']['cash'] > 0
    assert meta['sleeve_months']['spy_seul'] > 0 and meta['sleeve_months']['qqq_seul'] > 0
