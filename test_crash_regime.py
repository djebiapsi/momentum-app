# -*- coding: utf-8 -*-
"""Tests du détecteur de régime post-krach et de son intégration au backtest (synthétique)."""
import numpy as np
import pandas as pd
import pytest

from crash_regime import compute_crash_regime, episodes_summary, lever_at, LEVER, NORMAL
from backtest_service import BacktestService


def _crash_path(seed=0):
    """Hausse calme → krach -40 % volatil → rebond calme. Retourne une série de clôtures."""
    rng = np.random.default_rng(seed)
    up = rng.normal(0.0004, 0.006, 400)
    crash = rng.normal(-0.006, 0.035, 80)
    rebound = rng.normal(0.0015, 0.008, 300)
    rets = np.concatenate([up, crash, rebound])
    idx = pd.bdate_range('2005-01-03', periods=len(rets))
    return pd.Series(100 * np.cumprod(1 + rets), index=idx)


def test_regime_normal_sans_krach():
    rng = np.random.default_rng(1)
    idx = pd.bdate_range('2010-01-04', periods=600)
    s = pd.Series(100 * np.cumprod(1 + rng.normal(0.0005, 0.008, 600)), index=idx)
    reg = compute_crash_regime(s)
    assert not reg['lever'].any()
    assert (reg['state'] == NORMAL).all()


def test_regime_leve_apres_le_krach_pas_pendant():
    s = _crash_path()
    reg = compute_crash_regime(s)
    assert reg['lever'].any(), 'le rebond calme doit ouvrir la fenêtre de levier'
    first_lever = reg.index[reg['lever']][0]
    first_arm = reg.index[reg['state'] != NORMAL][0]
    # le levier ne s'ouvre qu'après l'armement (drawdown ≤ -25 %) …
    assert first_lever > first_arm
    assert reg.loc[first_arm, 'drawdown'] <= -0.25
    # … et quand la vol courte est repassée sous la vol longue
    assert reg.loc[first_lever, 'vol_short'] < reg.loc[first_lever, 'vol_long']
    # la fenêtre dure au plus window_days séances
    assert reg['lever'].sum() <= 189


def test_regime_sans_lookahead():
    """L'état à la date t ne change pas si l'on tronque la série après t."""
    s = _crash_path(seed=3)
    full = compute_crash_regime(s)
    for cut in (420, 470, 520, 600):
        part = compute_crash_regime(s.iloc[:cut])
        pd.testing.assert_series_equal(part['lever'], full['lever'].iloc[:cut],
                                       check_names=False)


@pytest.mark.parametrize('seed', [0, 3])
def test_un_seul_episode_et_budget_cumule(seed):
    """Un drawdown qui oscille autour de -25 % reste UN épisode, et le levier
    total de l'épisode ne dépasse pas window_days séances (re-entrées comprises)."""
    s = _crash_path(seed=seed)
    eps = episodes_summary(s, window_days=21)
    assert len(eps) == 1
    reg = compute_crash_regime(s, window_days=21)
    assert 0 < reg['lever'].sum() <= 21


def test_lever_at():
    idx = pd.bdate_range('2020-01-01', periods=5)
    sig = pd.Series([False, True, True, False, False], index=idx)
    assert lever_at(sig, idx[1]) is True
    assert lever_at(sig, idx[3]) is False
    assert lever_at(sig, pd.Timestamp('2019-12-01')) is False
    assert lever_at(None, idx[0]) is False


# ── Intégration backtest ────────────────────────────────────────────────────

@pytest.fixture
def svc():
    return BacktestService(momentum_service=None, screener_service=None)


def test_build_monthly_px_une_obs_par_mois(svc):
    """Barres mensuelles datées du 1er (= clôture du mois) + daily → 1 point par mois."""
    daily_idx = pd.bdate_range('2023-01-02', '2023-06-30')
    close = pd.DataFrame({'AAA': np.linspace(100, 160, len(daily_idx))}, index=daily_idx)
    month_close = close.resample('ME').last()
    monthly_db = month_close.copy()
    monthly_db.index = monthly_db.index.to_period('M').to_timestamp()  # 1er du mois
    out = svc._build_monthly_px(monthly_db, close, pd.Timestamp('2023-06-30'))
    assert len(out) == 6
    assert (out.index == month_close.index).all()
    pd.testing.assert_frame_equal(out, month_close, check_freq=False)


def test_build_monthly_px_exclut_mois_non_cloture(svc):
    daily_idx = pd.bdate_range('2023-01-02', '2023-03-15')
    close = pd.DataFrame({'AAA': np.linspace(100, 130, len(daily_idx))}, index=daily_idx)
    out = svc._build_monthly_px(None, close, pd.Timestamp('2023-03-15'))
    assert out.index.max() == pd.Timestamp('2023-02-28')


def test_resolve_window(svc):
    end, start = svc._resolve_window(5, '2007-07-01', '2012-12-31')
    assert start == pd.Timestamp('2007-07-01') and end == pd.Timestamp('2012-12-31')
    end, start = svc._resolve_window(3)
    assert (end - start).days > 3 * 360
    with pytest.raises(ValueError):
        svc._resolve_window(5, '2012-01-01', '2011-01-01')


def test_weight_matrix_levier_post_krach(svc):
    """Le levier relève l'exposition pendant le signal et ajoute une date de bascule."""
    idx = pd.bdate_range('2019-01-01', '2021-12-31')
    rng = np.random.default_rng(0)
    rets = pd.DataFrame({t: rng.normal(0.002, 0.01, len(idx)) for t in ['A', 'B', 'C']},
                        index=idx)
    close = 100 * (1 + rets).cumprod()
    monthly = close.resample('ME').last()
    start = pd.Timestamp('2020-03-01')
    params = {'nb_top': 3, 'vol_scaling': True, 'vol_target_pct': 40.0,
              'max_exposure_pct': 100.0, 'portfolio_filter': False,
              'portfolio_vol_threshold_pct': 20.0}
    sig = pd.Series(False, index=idx)
    sig.loc['2020-06-10':'2020-09-15'] = True
    base, _ = svc.build_weight_matrix(monthly, rets, None, start, params)
    lev, meta = svc.build_weight_matrix(monthly, rets, None, start, params,
                                        lever_signal=sig, lever=1.4)
    assert base.sum(axis=1).max() == pytest.approx(1.0, abs=1e-9)
    assert lev.loc['2020-06-30'].sum() == pytest.approx(1.4, abs=1e-9)
    assert lev.loc['2020-10-31'].sum() == pytest.approx(1.0, abs=1e-9)
    # bascules intra-mois : ON le 10/06, OFF le 16/09
    assert pd.Timestamp('2020-06-10') in lev.index
    assert pd.Timestamp('2020-09-16') in lev.index
    assert meta['n_regime_switches'] == 2
    assert meta['n_lever_months'] == 3  # fins juin, juillet, août
