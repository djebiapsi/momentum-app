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


def parse_statement(xml_text: str) -> dict:
    """
    Parse le XML Flex en données structurées.
    Returns: {
        'nav': [{'date', 'nav'}],
        'trades': [{'date','ticker','type','quantity','price','amount','currency'}],
        'dividends': [{'date','ticker','amount','currency'}],
        'account_id': str,
    }
    """
    root = ET.fromstring(xml_text)
    nav, trades, dividends = [], [], []
    account_id = None

    for stmt in root.iter('FlexStatement'):
        account_id = stmt.get('accountId') or account_id

        # NAV jour par jour (EquitySummaryByReportDateInBase)
        for row in stmt.iter('EquitySummaryByReportDateInBase'):
            d = _parse_flex_date(row.get('reportDate'))
            total = row.get('total')
            if d and total:
                try:
                    nav.append({'date': d, 'nav': float(total)})
                except ValueError:
                    pass

        # Transactions (Trade / Order)
        for tr in stmt.iter('Trade'):
            d = _parse_flex_date(tr.get('tradeDate') or tr.get('dateTime'))
            sym = tr.get('symbol')
            if not d or not sym:
                continue
            try:
                qty = float(tr.get('quantity') or 0)
                price = float(tr.get('tradePrice') or 0)
            except ValueError:
                continue
            buysell = (tr.get('buySell') or ('BUY' if qty >= 0 else 'SELL')).upper()
            amount = tr.get('netCash') or tr.get('proceeds')
            try:
                amount = float(amount) if amount else qty * price
            except ValueError:
                amount = qty * price
            trades.append({
                'date': d, 'ticker': sym, 'type': 'BUY' if 'BUY' in buysell else 'SELL',
                'quantity': abs(qty), 'price': price, 'amount': amount,
                'currency': tr.get('currency', 'USD'),
            })

        # Dividendes (CashTransaction type contenant 'Dividend')
        for ct in stmt.iter('CashTransaction'):
            ttype = (ct.get('type') or '').lower()
            if 'dividend' not in ttype:
                continue
            d = _parse_flex_date(ct.get('dateTime') or ct.get('settleDate') or ct.get('reportDate'))
            amount = ct.get('amount')
            if not d or not amount:
                continue
            try:
                dividends.append({
                    'date': d, 'ticker': ct.get('symbol') or '',
                    'amount': float(amount), 'currency': ct.get('currency', 'USD'),
                })
            except ValueError:
                pass

    return {'nav': nav, 'trades': trades, 'dividends': dividends, 'account_id': account_id}


def fetch_and_parse(token: str, query_id: str) -> dict:
    """Récupère et parse une Flex Query en une étape."""
    xml_text = fetch_statement(token, query_id)
    return parse_statement(xml_text)
