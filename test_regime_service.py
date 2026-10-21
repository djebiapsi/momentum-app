# -*- coding: utf-8 -*-
"""Tests du régime live par poche (regime_service), des recommandations et des alertes."""
import numpy as np
import pandas as pd
import pytest

import regime_service as rs
import trend_regime as tr

CFG = {'regime_scope': 'sleeve', 'regime_mode': 'blend', 'regime_bull_exposure': 1.25,
       'regime_alerts': True}


def _closes(drift_up=0.0008, drift_down=-0.002, n_up=500, n_down=0, seed=0):
    rng = np.random.default_rng(seed)
    r = np.concatenate([rng.normal(drift_up, 0.008, n_up), rng.normal(drift_down, 0.01, n_down)])
    idx = pd.bdate_range('2019-01-02', periods=len(r))
    return pd.Series(100 * np.cumprod(1 + r), index=idx)


@pytest.mark.parametrize('m,d,mode,expected', [
    ('BULL', 'BULL', 'blend', 1.25), ('CORRECTION', 'CORRECTION', 'blend', 1.0),
    ('CORRECTION', 'BEAR', 'blend', 0.5), ('BEAR', 'BEAR', 'blend', 0.0),
    ('BEAR', 'REBOUND', 'blend', 0.5), ('REBOUND', 'REBOUND', 'blend', 1.0),
    ('BULL', 'BEAR', 'monthly', 1.25), ('BEAR', 'BULL', 'monthly', 0.0), (None, None, 'blend', 1.0),
    ('BULL', 'BEAR', 'daily', 0.0), ('BEAR', 'BULL', 'daily', 1.25), ('BEAR', 'REBOUND', 'daily', 1.0)])
def test_exposure_from_states(m, d, mode, expected):
    assert rs.exposure_from_states(m, d, mode, 1.25) == pytest.approx(expected)


def test_live_regime_par_poche():
    up = _closes(seed=1)
    down = _closes(n_up=300, n_down=260, seed=2)
    reg = rs.live_regime(CFG, closes={'spy': up, 'qqq': down})
    assert reg['indexes']['spy']['state'] in (tr.BULL, tr.CORRECTION)
    assert reg['indexes']['qqq']['state'] == tr.BEAR
    assert reg['sleeves']['nasdaq']['exposure'] == 0.0
    assert reg['sleeves']['other']['exposure'] >= 1.0
    assert reg['status'] == 'REDUIT'
    kpi = reg['indexes']['qqq']
    for k in ('pivot', 'state_since', 'history', 'next_decision', 'trading_days_to_decision'):
        assert k in kpi
    assert 1 <= len(kpi['history']) <= 24


def test_live_regime_portees():
    up, down = _closes(seed=3), _closes(n_up=300, n_down=260, seed=4)
    either = rs.live_regime({**CFG, 'regime_scope': 'either'}, closes={'spy': up, 'qqq': down})
    assert either['sleeves']['nasdaq']['exposure'] == either['sleeves']['other']['exposure'] == 0.0
    assert either['status'] == 'CASH'
    spy_only = rs.live_regime({**CFG, 'regime_scope': 'spy'}, closes={'spy': up, 'qqq': down})
    assert spy_only['sleeves']['nasdaq']['exposure'] == spy_only['sleeves']['other']['exposure'] > 0


def test_multipliers_for():
    reg = {'sleeves': {'nasdaq': {'exposure': 0.5}, 'other': {'exposure': 1.25}}}
    m = rs.multipliers_for(['AAPL', 'XOM'], reg, nasdaq_set={'AAPL'})
    assert m == {'AAPL': 0.5, 'XOM': 1.25}


def _analyse():
    rows = [('NQA', 50), ('NQB', 40), ('SPA', 35), ('SPB', 30), ('SPC', 20), ('SPD', 10)]
    res = []
    for i, (t, mom) in enumerate(rows):
        dm = [{'rendement_mensuel': x} for x in (2.0, -1.0, 3.0, 1.5, -0.5, 2.5)]
        res.append({'ticker': t, 'momentum': mom, 'perf_recent_1m': 1.0,
                    'details_mensuels': dm, 'rank': i + 1})
    return {'success': True, 'date_calcul': '2026-10-01', 'resultats': res, 'erreurs': [],
            'market_regime': None}


def _service():
    from momentum_service import MomentumService
    return MomentumService.__new__(MomentumService)


def test_recommandations_poche_bear_exclue():
    reg = {'sleeves': {'nasdaq': {'exposure': 0.0}, 'other': {'exposure': 1.25}}}
    out = _service().generer_recommandations(_analyse(), 3, regime=reg, nasdaq_set={'NQA', 'NQB'})
    inv = [r for r in out['recommandations'] if r['signal'] == 'Investir']
    assert [r['ticker'] for r in inv] == ['SPA', 'SPB', 'SPC']
    assert sum(r['allocation'] for r in inv) == pytest.approx(125.0, abs=0.1)
    blocked = {r['ticker'] for r in out['recommandations'] if r['regime_blocked']}
    assert blocked == {'NQA', 'NQB'}


def test_recommandations_tout_cash():
    reg = {'sleeves': {'nasdaq': {'exposure': 0.0}, 'other': {'exposure': 0.0}}}
    out = _service().generer_recommandations(_analyse(), 3, regime=reg, nasdaq_set={'NQA', 'NQB'})
    assert out['total_investir'] == 0 and out['exposition_brute'] == 0
    assert out['cash_pct'] == 100.0


def test_recommandations_sans_regime_inchangees():
    out = _service().generer_recommandations(_analyse(), 3)
    inv = [r['ticker'] for r in out['recommandations'] if r['signal'] == 'Investir']
    assert inv == ['NQA', 'NQB', 'SPA']
    assert sum(r['allocation'] for r in out['recommandations']) == pytest.approx(100.0, abs=0.05)


def _reg(nq, ot, scope='sleeve', watch=False):
    return {'settings': {**CFG, 'regime_scope': scope},
            'sleeves': {'nasdaq': {'label': 'Nasdaq', 'exposure': nq, 'index': 'qqq'},
                        'other': {'label': 'S&P 500', 'exposure': ot, 'index': 'spy'}},
            'indexes': {'spy': {'symbol': 'SPY', 'state': 'BULL', 'provisional_state': 'BEAR',
                                'month_end_watch': watch},
                        'qqq': {'symbol': 'QQQ', 'state': 'BULL', 'provisional_state': 'BULL',
                                'month_end_watch': False}},
            'month_end_watch': watch, 'next_decision': '2026-10-30'}


def test_regime_events():
    from core import _regime_events
    last = {'sleeves': {'nasdaq': 1.0, 'other': 1.25}}
    ev = _regime_events(last, _reg(0.5, 1.25))
    assert [(e['kind'], e['severity']) for e in ev] == [('REDUCE', 'critical')]
    ev = _regime_events({'sleeves': {'nasdaq': 0.5, 'other': 1.0}}, _reg(0.0, 1.0))
    assert ev[0]['kind'] == 'CASH'
    ev = _regime_events({'sleeves': {'nasdaq': 0.5, 'other': 0.5}}, _reg(0.0, 0.0))
    assert ev[0]['kind'] == 'ALL_CASH'
    ev = _regime_events({'sleeves': {'nasdaq': 0.0, 'other': 1.0}}, _reg(0.5, 1.0))
    assert ev[0]['kind'] == 'REENTER'
    ev = _regime_events({'sleeves': {'nasdaq': 1.0, 'other': 1.0}}, _reg(1.25, 1.0))
    assert ev[0]['kind'] == 'LEVER_ON'
    assert _regime_events({'sleeves': {'nasdaq': 1.0, 'other': 1.0}}, _reg(1.0, 1.0)) == []
    ev = _regime_events({'sleeves': {'nasdaq': 1.0, 'other': 1.0}}, _reg(1.0, 1.0, watch=True))
    assert ev[0]['kind'] == 'WATCH'
    # préalerte envoyée une seule fois par fin de mois
    assert _regime_events({'sleeves': {'nasdaq': 1.0, 'other': 1.0}, 'watch_sent': '2026-10-30'},
                          _reg(1.0, 1.0, watch=True)) == []
    # portée commune : une seule alerte pour les deux poches
    ev = _regime_events({'sleeves': {'nasdaq': 1.0, 'other': 1.0}}, _reg(0.5, 0.5, scope='spy'))
    assert len(ev) == 1 and ev[0]['kind'] == 'REDUCE' and set(ev[0]['sleeves']) == {'nasdaq', 'other'}


def test_regime_message_ordres():
    from core import _regime_message
    ev = {'kind': 'CASH', 'severity': 'critical', 'sleeves': ['nasdaq'],
          'changes': {'nasdaq': (0.5, 0.0)}}
    title, body, orders = _regime_message(ev, _reg(0.0, 1.0),
                                          {'AAPL': {'allocation': 20.0}, 'XOM': {'allocation': 25.0}},
                                          {'XOM': {'allocation': 30.0}, 'CVX': {'allocation': 25.0}})
    assert 'Nasdaq' in title
    acts = {o['ticker']: o['action'] for o in orders}
    assert acts == {'AAPL': 'VENDRE', 'XOM': 'ACHETER', 'CVX': 'ACHETER'}


def test_drop_partial_session():
    idx = pd.bdate_range('2026-10-19', periods=3)  # lun 19 → mer 21
    s = pd.Series([1.0, 2.0, 3.0], index=idx)
    before_close = pd.Timestamp('2026-10-21 11:00', tz='America/New_York')
    after_close = pd.Timestamp('2026-10-21 16:30', tz='America/New_York')
    assert rs._drop_partial_session(s, before_close).index[-1] == pd.Timestamp('2026-10-20')
    assert rs._drop_partial_session(s, after_close).index[-1] == pd.Timestamp('2026-10-21')
