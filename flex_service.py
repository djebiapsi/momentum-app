# -*- coding: utf-8 -*-
"""
Service IBKR Flex Web Service
=============================
Récupère les données historiques officielles du compte IBKR (NAV jour par jour,
transactions, dividendes) via les Flex Queries — données exactes, complémentaires
à la TWS API (qui ne donne que le temps réel).

Flux en 2 étapes :
  1. SendRequest(token, query_id) → ReferenceCode
  2. GetStatement(token, ReferenceCode) → XML du rapport (généré en asynchrone)
"""

import time
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, date

logger = logging.getLogger(__name__)

FLEX_BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"


class FlexError(Exception):
    pass


def _parse_flex_date(s):
    """
    Parse une date Flex. Gère les formats courants : yyyyMMdd (recommandé),
    yyyy-MM-dd, MM/dd/yyyy, dd/MM/yyyy. Sépare aussi l'heure (séparée par ';' ou espace).
    """
    if not s:
        return None
    s = s.split(';')[0].split(' ')[0].strip()  # enlever l'heure éventuelle
    for fmt in ('%Y%m%d', '%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def fetch_statement(token: str, query_id: str, timeout=120) -> str:
    """
    Récupère le XML brut d'une Flex Query.
    Lève FlexError en cas d'échec.
    """
    # Étape 1 : SendRequest
    r = requests.get(f"{FLEX_BASE}/SendRequest",
                     params={'t': token, 'q': query_id, 'v': 3}, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    status = root.findtext('Status')
    if status != 'Success':
        msg = root.findtext('ErrorMessage') or root.findtext('ErrorCode') or 'SendRequest échoué'
        raise FlexError(f"Flex SendRequest : {msg}")

    ref_code = root.findtext('ReferenceCode')
    base_url = root.findtext('Url') or f"{FLEX_BASE}/GetStatement"
    if not ref_code:
        raise FlexError("Flex : ReferenceCode manquant")

    # Étape 2 : GetStatement (le rapport est généré en asynchrone → polling)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r2 = requests.get(base_url, params={'t': token, 'q': ref_code, 'v': 3}, timeout=30)
        r2.raise_for_status()
        text = r2.text
        if '<FlexQueryResponse' in text:
            return text
        # Rapport pas encore prêt
        if 'Statement generation in progress' in text or 'try again' in text.lower():
            time.sleep(4)
            continue
        # Erreur explicite
        try:
            err_root = ET.fromstring(text)
            if err_root.findtext('Status') == 'Fail':
                raise FlexError(err_root.findtext('ErrorMessage') or 'GetStatement échoué')
        except ET.ParseError:
            pass
        time.sleep(4)
    raise FlexError("Flex : délai dépassé (rapport non généré)")


def _parse_flex_datetime(s):
    """Date + heure Flex ('20240115;093015', '2024-01-15, 09:30:15'…) → datetime (minuit si pas d'heure)."""
    if not s:
        return None
    d = _parse_flex_date(s)
    if d is None:
        return None
    rest = s.replace(',', ' ').replace(';', ' ').split()
    hhmmss = rest[1] if len(rest) > 1 else ''
    hhmmss = hhmmss.replace(':', '')
    try:
        if len(hhmmss) >= 6:
            return datetime(d.year, d.month, d.day, int(hhmmss[0:2]), int(hhmmss[2:4]), int(hhmmss[4:6]))
    except ValueError:
        pass
    return datetime(d.year, d.month, d.day)


def _f(x, default=0.0):
    try:
        return float(x) if x not in (None, '') else default
    except ValueError:
        return default


# Niveaux de détail des lignes <Trade> : on garde les exécutions réelles et on
# ignore les lignes de synthèse (sinon les quantités sont comptées deux fois).
_EXEC_LEVELS = ('EXECUTION',)
_ORDER_LEVELS = ('ORDER',)
_SUMMARY_LEVELS = ('SYMBOL_SUMMARY', 'ASSET_SUMMARY', 'CLOSED_LOT', 'WASH_SALE', 'SUMMARY')
# Catégories d'actifs conservées (actions et ETF) ; options, forex, futures ignorés
_EQUITY_CATEGORIES = ('STK', '')


def parse_statement(xml_text: str) -> dict:
    """
    Parse le XML Flex en données structurées.
    Returns: {
        'nav': [{'date', 'nav', 'cash'}],
        'trades': [{'date', 'ts', 'ticker', 'type', 'quantity', 'price', 'amount', 'currency', 'ext_id'}],
            type ∈ BUY | SELL | ADJ (opération sur titres : division, changement de
            ticker, fusion — quantité signée, sans flux de trésorerie)
        'dividends': [{'date','ticker','amount','currency'}],   # retenues à la source en négatif
        'cash_flows': [{'date','amount','description','currency'}],  # dépôts/retraits
        'open_positions': [{'ticker','qty','mark_price','value','cost_basis','unrealized','report_date'}],
        'from_date', 'to_date': fenêtre couverte par le relevé (None si absente),
        'account_id': str,
    }

    Robustesse (corrige les positions « fantômes ») :
      - seules les exécutions sont gardées ; si le rapport n'en contient pas, les
        ordres ; jamais les lignes de synthèse (SYMBOL_SUMMARY, CLOSED_LOT…) ;
      - chaque exécution porte son identifiant IBKR (ibExecID / tradeID) : deux
        exécutions identiques (même quantité, même prix, même jour) restent deux
        transactions ;
      - les opérations sur titres (divisions, regroupements, changements de
        ticker) deviennent des ajustements de quantité.
    """
    root = ET.fromstring(xml_text)
    nav, dividends, cash_flows, open_positions = [], [], [], []
    trades_by_level = {'exec': [], 'order': [], 'plain': []}
    ca_rows = []
    account_id = None
    from_date = to_date = None

    for el in root.iter():
        val = el.get('accountId')
        if val:
            account_id = val
            break

    for stmt in root.iter('FlexStatement'):
        if not account_id:
            account_id = stmt.get('accountId') or account_id
        fd, td = _parse_flex_date(stmt.get('fromDate')), _parse_flex_date(stmt.get('toDate'))
        if fd:
            from_date = fd if from_date is None else min(from_date, fd)
        if td:
            to_date = td if to_date is None else max(to_date, td)

        # NAV + cash jour par jour (EquitySummaryByReportDateInBase)
        for row in stmt.iter('EquitySummaryByReportDateInBase'):
            d = _parse_flex_date(row.get('reportDate'))
            total = row.get('total')
            if d and total:
                try:
                    cash_val = row.get('cash') or row.get('cashReportCurrency')
                    nav.append({
                        'date': d,
                        'nav': float(total),
                        'cash': float(cash_val) if cash_val else None,
                    })
                except ValueError:
                    pass

        # Transactions (exécutions)
        for tr in stmt.iter('Trade'):
            cat = (tr.get('assetCategory') or '').upper()
            if cat not in _EQUITY_CATEGORIES:
                continue
            level = (tr.get('levelOfDetail') or '').upper()
            if level in _SUMMARY_LEVELS:
                continue
            ts = _parse_flex_datetime(tr.get('dateTime') or tr.get('tradeDate'))
            sym = tr.get('symbol')
            if not ts or not sym:
                continue
            qty = _f(tr.get('quantity'))
            price = _f(tr.get('tradePrice'))
            if qty == 0:
                continue
            buysell = (tr.get('buySell') or ('BUY' if qty >= 0 else 'SELL')).upper()
            amount = tr.get('netCash') or tr.get('proceeds')
            amount = _f(amount, qty * price) if amount else qty * price
            row = {
                'date': ts.date(), 'ts': ts, 'ticker': sym,
                'type': 'BUY' if 'BUY' in buysell else 'SELL',
                'quantity': abs(qty), 'price': price, 'amount': amount,
                'currency': tr.get('currency', 'USD'),
                'ext_id': (tr.get('ibExecID') or tr.get('tradeID') or tr.get('transactionID') or None),
            }
            if level in _EXEC_LEVELS:
                trades_by_level['exec'].append(row)
            elif level in _ORDER_LEVELS:
                trades_by_level['order'].append(row)
            else:
                trades_by_level['plain'].append(row)

        # Opérations sur titres : divisions, regroupements, changements de ticker…
        for ca in stmt.iter('CorporateAction'):
            cat = (ca.get('assetCategory') or '').upper()
            if cat not in _EQUITY_CATEGORIES:
                continue
            level = (ca.get('levelOfDetail') or '').upper()
            if level in _SUMMARY_LEVELS:
                continue
            ts = _parse_flex_datetime(ca.get('dateTime') or ca.get('reportDate'))
            sym = ca.get('symbol')
            qty = _f(ca.get('quantity'))
            if not ts or not sym or qty == 0:
                continue
            proceeds = _f(ca.get('proceeds'))
            ext = ca.get('transactionID') or ca.get('actionID')
            base = {'date': ts.date(), 'ts': ts, 'ticker': sym, 'currency': ca.get('currency', 'USD'),
                    'ext_id': f'CA-{ext}-{sym}' if ext else None}
            if proceeds and qty < 0:
                # Fusion / OPA payée en cash : équivalent d'une vente
                ca_rows.append({**base, 'type': 'SELL', 'quantity': abs(qty),
                                'price': abs(proceeds / qty), 'amount': proceeds})
            else:
                ca_rows.append({**base, 'type': 'ADJ', 'quantity': qty, 'price': 0.0, 'amount': 0.0})

        # CashTransactions : dividendes (nets des retenues) ET dépôts/retraits
        for ct in stmt.iter('CashTransaction'):
            ttype = (ct.get('type') or '').lower()
            d = _parse_flex_date(ct.get('dateTime') or ct.get('settleDate') or ct.get('reportDate'))
            amount = ct.get('amount')
            if not d or not amount:
                continue
            level = (ct.get('levelOfDetail') or '').upper()
            if level in _SUMMARY_LEVELS:
                continue

            if 'dividend' in ttype or 'withholding' in ttype:
                try:
                    dividends.append({
                        'date': d, 'ticker': ct.get('symbol') or '',
                        'amount': float(amount), 'currency': ct.get('currency', 'USD'),
                    })
                except ValueError:
                    pass
            elif any(kw in ttype for kw in ('deposit', 'withdrawal', 'transfer')):
                try:
                    cash_flows.append({
                        'date': d,
                        'amount': float(amount),
                        'description': ct.get('description') or ttype,
                        'currency': ct.get('currency', 'USD'),
                    })
                except ValueError:
                    pass

        # Positions ouvertes à la date du relevé (vérité du courtier)
        for op in stmt.iter('OpenPosition'):
            cat = (op.get('assetCategory') or '').upper()
            if cat not in _EQUITY_CATEGORIES:
                continue
            level = (op.get('levelOfDetail') or '').upper()
            if level == 'LOT':
                continue
            sym = op.get('symbol')
            qty = _f(op.get('position'))
            if not sym or qty == 0:
                continue
            open_positions.append({
                'ticker': sym, 'qty': qty,
                'mark_price': _f(op.get('markPrice'), None),
                'value': _f(op.get('positionValue'), None),
                'cost_basis': _f(op.get('costBasisMoney'), None),
                'unrealized': _f(op.get('fifoPnlUnrealized'), None),
                'report_date': (_parse_flex_date(op.get('reportDate')) or to_date),
            })

    # Exécutions si présentes, sinon ordres, sinon lignes sans niveau ; + opérations sur titres
    trades = (trades_by_level['exec'] or trades_by_level['order'] or trades_by_level['plain']) + ca_rows
    trades = sorted(trades, key=lambda t: t['ts'])

    # Enrichir les lignes NAV avec le cash depuis CashReportCurrency si absent
    cash_by_date = {}
    for row in root.iter('CashReportCurrency'):
        d = _parse_flex_date(row.get('reportDate') or row.get('toDate') or row.get('toDateLong'))
        end_cash = row.get('endingCash') or row.get('endingSettledCash')
        currency = (row.get('currency') or '').upper()
        if d and end_cash and currency in ('BASE', 'USD', ''):
            try:
                cash_by_date[d] = float(end_cash)
            except ValueError:
                pass
    for row in nav:
        if row.get('cash') is None and row['date'] in cash_by_date:
            row['cash'] = cash_by_date[row['date']]

    if trades and from_date is None:
        from_date = trades[0]['date']
    if trades and to_date is None:
        to_date = trades[-1]['date']

    return {'nav': nav, 'trades': trades, 'dividends': dividends,
            'cash_flows': cash_flows, 'open_positions': open_positions,
            'from_date': from_date, 'to_date': to_date,
            'account_id': account_id}


def fetch_and_parse(token: str, query_id: str) -> dict:
    """Récupère et parse une Flex Query en une étape."""
    xml_text = fetch_statement(token, query_id)
    return parse_statement(xml_text)
