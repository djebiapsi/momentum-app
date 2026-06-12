# -*- coding: utf-8 -*-
"""
Tests de l'historique point-in-time des constituants (biais de survivance) :
parsing de la table « Selected changes », reconstruction des intervalles
d'appartenance, et filtre point-in-time du backtest. Données SYNTHÉTIQUES
(aucun appel réseau, aucune base).
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from price_data_service import PriceDataService
from backtest_service import BacktestService


# ---------------------------------------------------------------------------
# _parse_changes_table
# ---------------------------------------------------------------------------
def _changes_df(rows):
    """DataFrame imitant la table « Selected changes » de Wikipédia (MultiIndex)."""
    cols = pd.MultiIndex.from_tuples([
        ('Date', 'Date'), ('Added', 'Ticker'), ('Added', 'Security'),
        ('Removed', 'Ticker'), ('Removed', 'Security'), ('Reason', 'Reason')])
    return pd.DataFrame(rows, columns=cols)


def test_parse_changes_table_extrait_ajouts_et_retraits():
    df = _changes_df([
        ['September 30, 2024', 'NEW', 'New Co', 'OLD', 'Old Co', 'remplacement'],
        ['June 2, 2020', 'BRK.B', 'Berkshire', np.nan, np.nan, 'ajout seul'],
    ])
    events = PriceDataService._parse_changes_table(df)
    assert (date(2020, 6, 2), 'BRK-B', 'add') in events      # normalisation . → -
    assert (date(2024, 9, 30), 'NEW', 'add') in events
    assert (date(2024, 9, 30), 'OLD', 'remove') in events
    assert events == sorted(events, key=lambda e: e[0])       # trié par date


def test_parse_changes_table_ignore_lignes_sans_date():
    df = _changes_df([['pas une date', 'A', 'A Co', np.nan, np.nan, '']])
    assert PriceDataService._parse_changes_table(df) == []


def test_parse_changes_table_rejette_table_constituants():
    # La table des constituants actuels (pas de colonnes Added/Removed) → []
    df = pd.DataFrame({'Symbol': ['AAPL'], 'Security': ['Apple'], 'Date added': ['1982-11-30']})
    assert PriceDataService._parse_changes_table(df) == []


# ---------------------------------------------------------------------------
# build_membership_intervals
# ---------------------------------------------------------------------------
D1, D2, D3 = date(2010, 3, 1), date(2015, 7, 1), date(2020, 1, 1)


def test_intervalle_simple_ajout_puis_retrait():
    ivs = PriceDataService.build_membership_intervals(
        [(D1, 'X', 'add'), (D2, 'X', 'remove')], current_members=set())
    assert ivs['X'] == [(D1, D2)]


def test_retrait_sans_ajout_connu_membre_depuis_avant():
    ivs = PriceDataService.build_membership_intervals(
        [(D2, 'X', 'remove')], current_members=set())
    assert ivs['X'] == [(None, D2)]


def test_membre_actuel_sans_evenement_depuis_toujours():
    ivs = PriceDataService.build_membership_intervals([], current_members={'AAPL'})
    assert ivs['AAPL'] == [(None, None)]


def test_reentree_sortie_puis_retour():
    # Sorti en D1 (membre avant l'historique), revenu en D2, encore membre
    ivs = PriceDataService.build_membership_intervals(
        [(D1, 'X', 'remove'), (D2, 'X', 'add')], current_members={'X'})
    assert ivs['X'] == [(None, D1), (D2, None)]


def test_membre_actuel_dernier_intervalle_ferme_est_rouvert():
    # La table dit retiré en D2, mais le scrape actuel le voit membre → rouvert à D2
    ivs = PriceDataService.build_membership_intervals(
        [(D1, 'X', 'add'), (D2, 'X', 'remove')], current_members={'X'})
    assert ivs['X'] == [(D1, D2), (D2, None)]


def test_non_membre_avec_intervalle_ouvert_est_ferme_aujourdhui():
    ivs = PriceDataService.build_membership_intervals(
        [(D1, 'X', 'add')], current_members=set())
    assert ivs['X'] == [(D1, date.today())]


# ---------------------------------------------------------------------------
# BacktestService._member_at + filtre dans compute_weights
# ---------------------------------------------------------------------------
def test_member_at_intervalle_semi_ouvert():
    membership = {'X': [(None, pd.Timestamp('2015-07-01'))],
                  'Y': [(pd.Timestamp('2015-07-01'), None)]}
    t_avant, t_apres = pd.Timestamp('2015-06-30'), pd.Timestamp('2015-07-01')
    assert BacktestService._member_at(membership, 'X', t_avant)
    assert not BacktestService._member_at(membership, 'X', t_apres)  # fin exclusive
    assert not BacktestService._member_at(membership, 'Y', t_avant)
    assert BacktestService._member_at(membership, 'Y', t_apres)      # début inclusif
    # Ticker absent de la table → jamais exclu (pas de faux négatif)
    assert BacktestService._member_at(membership, 'ZZZ', t_avant)


@pytest.fixture
def svc():
    return BacktestService(momentum_service=None, screener_service=None)


def _monthly_2_tickers():
    idx_m = pd.date_range('2020-01-31', periods=14, freq='ME')
    return pd.DataFrame({
        'IN':  np.linspace(100, 160, 14),   # momentum positif
        'OUT': np.linspace(100, 200, 14),   # momentum encore meilleur
    }, index=idx_m), idx_m


def test_compute_weights_exclut_non_membre_point_in_time(svc):
    monthly, idx_m = _monthly_2_tickers()
    daily_ret = pd.DataFrame(columns=['IN', 'OUT'])
    params = dict(nb_top=5, vol_scaling=False, vol_target_pct=12, max_exposure_pct=250,
                  portfolio_filter=False, portfolio_vol_threshold_pct=20)
    # OUT est sorti de l'indice avant la date de calcul → exclu malgré son momentum
    membership = {'OUT': [(None, pd.Timestamp('2020-06-30'))],
                  'IN':  [(None, None)]}
    w = svc.compute_weights(idx_m[-1], ['IN', 'OUT'], monthly, daily_ret, params,
                            membership=membership)
    assert list(w.index) == ['IN']
    # Sans filtre : les deux sont sélectionnés (comportement historique conservé)
    w2 = svc.compute_weights(idx_m[-1], ['IN', 'OUT'], monthly, daily_ret, params)
    assert set(w2.index) == {'IN', 'OUT'}


def test_build_weight_matrix_meta_pit(svc):
    monthly, idx_m = _monthly_2_tickers()
    daily_ret = pd.DataFrame(columns=['IN', 'OUT'])
    params = dict(nb_top=5, vol_scaling=False, vol_target_pct=12, max_exposure_pct=250,
                  portfolio_filter=False, portfolio_vol_threshold_pct=20)
    membership = {'IN': [(None, None)], 'OUT': [(None, None)]}
    _, meta = svc.build_weight_matrix(monthly, daily_ret, None, idx_m[-1], params,
                                      membership=membership)
    assert meta['pit_universe'] is True
    _, meta2 = svc.build_weight_matrix(monthly, daily_ret, None, idx_m[-1], params)
    assert meta2['pit_universe'] is False
