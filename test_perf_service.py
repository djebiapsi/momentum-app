# -*- coding: utf-8 -*-
"""Tests de l'import Flex et du cycle de vie des positions (bugs « positions fantômes »)."""
from datetime import date, datetime

import pandas as pd
import pytest

import flex_service
import perf_service as ps


def _tx(ts, tk, typ, qty, price=100.0, amount=None, seq=0):
    if amount is None:
        amount = -qty * price if typ == 'BUY' else qty * price
    return {'ts': datetime.fromisoformat(ts), 'ticker': tk, 'type': typ, 'quantity': qty,
            'price': price, 'amount': amount, 'seq': seq}


def _by(positions):
    return {p['ticker']: p for p in positions}


# ---------------------------------------------------------------- parseur Flex
FLEX_XML = """<FlexQueryResponse><FlexStatements count="1">
<FlexStatement accountId="U123" fromDate="20260101" toDate="20260331">
  <Trades>
    <Trade assetCategory="STK" symbol="MU" buySell="BUY" quantity="10" tradePrice="100" netCash="-1000" dateTime="20260105;100000" levelOfDetail="EXECUTION" ibExecID="e1"/>
    <Trade assetCategory="STK" symbol="MU" buySell="SELL" quantity="-5" tradePrice="120" netCash="600" dateTime="20260210;100000" levelOfDetail="EXECUTION" ibExecID="e2"/>
    <Trade assetCategory="STK" symbol="MU" buySell="SELL" quantity="-5" tradePrice="120" netCash="600" dateTime="20260210;100001" levelOfDetail="EXECUTION" ibExecID="e3"/>
    <Trade assetCategory="STK" symbol="MU" buySell="SELL" quantity="-10" tradePrice="120" netCash="1200" dateTime="20260210" levelOfDetail="ORDER"/>
    <Trade assetCategory="STK" symbol="MU" quantity="0" levelOfDetail="SYMBOL_SUMMARY"/>
    <Trade assetCategory="CASH" symbol="EUR.USD" buySell="BUY" quantity="1000" tradePrice="1.08" dateTime="20260105" levelOfDetail="EXECUTION"/>
    <Trade assetCategory="OPT" symbol="AAPL  260320C00200000" buySell="BUY" quantity="1" tradePrice="5" dateTime="20260105" levelOfDetail="EXECUTION"/>
  </Trades>
  <CorporateActions>
    <CorporateAction assetCategory="STK" symbol="NVDA" quantity="90" proceeds="0" dateTime="20260301;202500" type="FS" transactionID="ca1"/>
  </CorporateActions>
  <CashTransactions>
    <CashTransaction type="Dividends" symbol="MSFT" amount="10" dateTime="20260115"/>
    <CashTransaction type="Withholding Tax" symbol="MSFT" amount="-1.5" dateTime="20260115"/>
    <CashTransaction type="Deposits/Withdrawals" amount="1000" dateTime="20260117"/>
    <CashTransaction type="Deposits/Withdrawals" amount="1000" dateTime="20260117"/>
  </CashTransactions>
  <OpenPositions>
    <OpenPosition assetCategory="STK" symbol="NVDA" position="100" markPrice="180" positionValue="18000" costBasisMoney="9000" fifoPnlUnrealized="9000" reportDate="20260331" levelOfDetail="SUMMARY"/>
  </OpenPositions>
</FlexStatement></FlexStatements></FlexQueryResponse>"""


def test_parse_flex_executions_identiques_conservees():
    p = flex_service.parse_statement(FLEX_XML)
    mu = [t for t in p['trades'] if t['ticker'] == 'MU']
    # 1 achat + 2 exécutions de vente identiques ; la ligne ORDER et le résumé sont ignorés
    assert [(t['type'], t['quantity']) for t in mu] == [('BUY', 10), ('SELL', 5), ('SELL', 5)]
    assert {t['ext_id'] for t in mu} == {'e1', 'e2', 'e3'}
    assert not any(t['ticker'] in ('EUR.USD',) or ' ' in t['ticker'] for t in p['trades'])
    assert p['from_date'] == date(2026, 1, 1) and p['to_date'] == date(2026, 3, 31)


def test_parse_flex_split_retenue_depots_positions():
    p = flex_service.parse_statement(FLEX_XML)
    adj = [t for t in p['trades'] if t['type'] == 'ADJ']
    assert adj and adj[0]['ticker'] == 'NVDA' and adj[0]['quantity'] == 90
    assert sum(d['amount'] for d in p['dividends'] if d['ticker'] == 'MSFT') == pytest.approx(8.5)
    assert len(p['cash_flows']) == 2           # deux dépôts identiques le même jour
    assert p['open_positions'][0]['ticker'] == 'NVDA' and p['open_positions'][0]['qty'] == 100


# ---------------------------------------------------------------- cycle de vie
def test_ventes_en_plusieurs_executions_soldent_la_position():
    txs = [_tx('2026-01-05 10:00', 'MU', 'BUY', 10, seq=1),
           _tx('2026-02-10 10:00', 'MU', 'SELL', 5, 120, seq=2),
           _tx('2026-02-10 10:00', 'MU', 'SELL', 5, 120, seq=3)]
    pos, tot, ev, _ = ps.position_lifecycles(txs, prices={'MU': 130})
    mu = _by(pos)['MU']
    assert mu['status'] == 'closed' and mu['realized_pnl'] == pytest.approx(200)
    assert tot['open_count'] == 0


def test_reliquat_fractionnaire_negligeable_ferme():
    txs = [_tx('2026-01-05', 'AMD', 'BUY', 10.123456, seq=1),
           _tx('2026-03-05', 'AMD', 'SELL', 10.1234, 150, seq=2)]
    pos, *_ = ps.position_lifecycles(txs, prices={'AMD': 160})
    assert _by(pos)['AMD']['status'] == 'closed'


def test_achat_et_vente_le_meme_jour_sans_heure():
    # anciennes lignes datées à minuit : l'achat doit précéder la vente
    txs = [_tx('2026-01-05', 'TSLA', 'SELL', 3, 210, seq=2),
           _tx('2026-01-05', 'TSLA', 'BUY', 3, 200, seq=1)]
    pos, *_ = ps.position_lifecycles(txs)
    t = _by(pos)['TSLA']
    assert t['status'] == 'closed' and not t['unknown_cost'] and t['realized_pnl'] == pytest.approx(30)


def test_division_ne_change_pas_le_cout():
    txs = [_tx('2026-01-05', 'NVDA', 'BUY', 10, 1000, seq=1),
           {'ts': datetime(2026, 3, 1, 20, 25), 'ticker': 'NVDA', 'type': 'ADJ', 'quantity': 90,
            'price': 0, 'amount': 0, 'seq': 2},
           _tx('2026-04-01', 'NVDA', 'SELL', 100, 120, seq=3)]
    pos, *_ = ps.position_lifecycles(txs)
    n = _by(pos)['NVDA']
    assert n['status'] == 'closed' and n['realized_pnl'] == pytest.approx(2000)
    assert not n['unknown_cost']


def test_changement_de_ticker_transfere_le_cout():
    ts = datetime(2026, 2, 1, 9, 0)
    txs = [_tx('2026-01-05', 'FB', 'BUY', 10, 300, seq=1),
           {'ts': ts, 'ticker': 'FB', 'type': 'ADJ', 'quantity': -10, 'price': 0, 'amount': 0, 'seq': 2},
           {'ts': ts, 'ticker': 'META', 'type': 'ADJ', 'quantity': 10, 'price': 0, 'amount': 0, 'seq': 3}]
    pos, *_ = ps.position_lifecycles(txs, prices={'META': 350})
    p = _by(pos)
    assert p['FB']['status'] == 'closed' and p['FB']['realized_pnl'] == 0
    assert p['META']['status'] == 'open' and p['META']['unrealized_pnl'] == pytest.approx(500)


def test_reconciliation_position_fantome_fermee():
    # La vente manquante (ancien bug) laisse 5 actions ; le courtier n'en a plus.
    txs = [_tx('2026-01-05', 'MU', 'BUY', 10, seq=1), _tx('2026-02-10', 'MU', 'SELL', 5, 120, seq=2)]
    pos, tot, _, recon = ps.position_lifecycles(txs, prices={'MU': 130}, actual={'XOM': {'qty': 20, 'price': 110}})
    mu = _by(pos)['MU']
    assert mu['status'] == 'closed' and mu['incomplete'] and mu['reconciled'] == 'missing_exit'
    assert mu['unrealized_pnl'] == 0 and mu['perf_pct'] is None
    xom = _by(pos)['XOM']    # détenue sans historique : ouverte, coût inconnu
    assert xom['status'] == 'open' and xom['reconciled'] == 'missing_entry' and xom['unknown_cost']
    assert {r['ticker'] for r in recon} == {'MU', 'XOM'}
    assert tot['open_count'] == 1


def test_reconciliation_quantite_split_non_suivi():
    txs = [_tx('2026-01-05', 'AVGO', 'BUY', 10, 1000, seq=1)]
    pos, *_ = ps.position_lifecycles(txs, actual={'AVGO': {'qty': 100, 'price': 150}})
    a = _by(pos)['AVGO']
    assert a['status'] == 'open' and a['remaining_qty'] == 100 and a['reconciled'] == 'qty_mismatch'
    assert a['unrealized_pnl'] == pytest.approx(100 * 150 - 10000)


# ---------------------------------------------------------------- tableau de bord
def test_depot_un_jour_sans_nav_n_est_pas_du_rendement():
    idx = pd.to_datetime(['2026-01-09', '2026-01-12', '2026-01-13'])   # ven, lun, mar
    nav = pd.Series([10000.0, 11000.0, 11000.0], index=idx)
    # dépôt de 1000 le samedi : la hausse du lundi n'est pas une performance
    twr, daily = ps.twr_from_nav(nav, [(date(2026, 1, 10), 1000.0)])
    assert twr.iloc[-1] == pytest.approx(1.0)


def test_metriques_non_annualisees_sur_periode_courte():
    idx = pd.bdate_range('2026-01-05', periods=10)
    twr = pd.Series([1 + i * 0.01 for i in range(10)], index=idx)
    m = ps.risk_metrics(twr, twr.pct_change().dropna())
    assert m['cagr'] is None and m['sharpe'] is None and m['total'] == pytest.approx(0.09)


def test_rendements_mensuels_incluent_le_premier_mois():
    idx = pd.bdate_range('2026-01-15', '2026-03-31')
    twr = pd.Series(1.0, index=idx)
    twr.loc['2026-01-30':] = 1.10
    m = ps.period_returns(twr, 'ME')
    assert m.index[0].month == 1 and m.iloc[0] == pytest.approx(0.10)


def test_cout_inconnu_utilise_le_prix_de_revient_du_courtier():
    txs = [{'ts': datetime(2026, 3, 1, 20, 25), 'ticker': 'NVDA', 'type': 'ADJ', 'quantity': 90,
            'price': 0, 'amount': 0, 'seq': 1}]
    pos, *_ = ps.position_lifecycles(txs, actual={'NVDA': {'qty': 100, 'price': 180, 'cost_basis': 9000}})
    n = _by(pos)['NVDA']
    assert n['unrealized_pnl'] == pytest.approx(9000) and n['cost_basis'] == pytest.approx(9000)
    # sans prix de revient du courtier : pas de faux gain égal à toute la valeur
    pos, *_ = ps.position_lifecycles(txs, actual={'NVDA': {'qty': 100, 'price': 180}})
    assert _by(pos)['NVDA']['unrealized_pnl'] == 0
