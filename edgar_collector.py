# -*- coding: utf-8 -*-
"""
Collecte des fondamentaux LONGS via SEC EDGAR (API « company facts »)
=====================================================================
Source de vérité pour un backtest fondamental multi-régimes (~15-20 ans). Deux
avantages majeurs sur yfinance :
  1. **Historique complet** (toutes les déclarations XBRL depuis ~2009, souvent
     avec comparatifs remontant plus loin).
  2. **Date de dépôt réelle** (`filed`) → anti-look-ahead EXACT (on stocke
     `report_date` = 1ère divulgation publique de la donnée).

Endpoints (gratuits, sans clé) :
  - ticker→CIK : https://www.sec.gov/files/company_tickers.json
  - facts      : https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json

⚠️ SEC exige un User-Agent identifiant + ≤ 10 req/s. On reste poli (~5/s).

V1 : extraction ANNUELLE (10-K, valeurs de fin d'exercice) — suffisant et propre
pour le backtest Quality-Value long. Écrit dans FundamentalSnapshot
(source='edgar', report_date=filed), écrasant les lignes annuelles yfinance.
"""

import time
import logging
import threading
from datetime import datetime, date

import requests

logger = logging.getLogger(__name__)

CIK_MAP_URL = 'https://www.sec.gov/files/company_tickers.json'
FACTS_URL = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'

# SEC impose un User-Agent identifiant (nom + contact).
USER_AGENT = 'momentum-app fundamental research (kouatebryan38@gmail.com)'

# Concepts XBRL us-gaap → champ FundamentalSnapshot (listes de candidats par ordre
# de priorité ; le 1er disponible pour une période est retenu).
MONEY = 'USD'
FIELD_CONCEPTS = {
    'total_revenue':    ['RevenueFromContractWithCustomerExcludingAssessedTax',
                         'Revenues', 'SalesRevenueNet',
                         'RevenueFromContractWithCustomerIncludingAssessedTax'],
    'gross_profit':     ['GrossProfit'],
    'operating_income': ['OperatingIncomeLoss'],
    'net_income':       ['NetIncomeLoss', 'ProfitLoss'],
    'total_assets':     ['Assets'],
    'total_liabilities': ['Liabilities'],
    'total_equity':     ['StockholdersEquity',
                         'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
    'cash_and_equivalents': ['CashAndCashEquivalentsAtCarryingValue'],
    'current_assets':   ['AssetsCurrent'],
    'current_liabilities': ['LiabilitiesCurrent'],
    'inventory':        ['InventoryNet'],
    'accounts_receivable': ['AccountsReceivableNetCurrent'],
    'operating_cash_flow': ['NetCashProvidedByUsedInOperatingActivities',
                            'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'],
    'investing_cash_flow': ['NetCashProvidedByUsedInInvestingActivities'],
    'financing_cash_flow': ['NetCashProvidedByUsedInFinancingActivities'],
    'capital_expenditure': ['PaymentsToAcquirePropertyPlantAndEquipment',
                            'PaymentsForCapitalImprovements'],
    'long_term_debt':   ['LongTermDebtNoncurrent', 'LongTermDebt'],
    'short_term_debt':  ['LongTermDebtCurrent', 'ShortTermBorrowings',
                         'DebtCurrent'],
}
EPS_CONCEPTS = ['EarningsPerShareDiluted', 'EarningsPerShareBasicAndDiluted']

# Champs « flux » (durée annuelle) vs « instantanés » (bilan, date unique)
FLOW_FIELDS = {'total_revenue', 'gross_profit', 'operating_income', 'net_income',
               'operating_cash_flow', 'investing_cash_flow', 'financing_cash_flow',
               'capital_expenditure'}


class EdgarCollector:
    REQUEST_TIMEOUT = 30
    SLEEP_BETWEEN = 0.2       # ~5 req/s (SEC max 10/s)
    LOG_EVERY = 25

    def __init__(self, email_service=None):
        self.email_service = email_service
        self._lock = threading.Lock()
        self._cik_map = None
        self._state = {
            'running': False, 'phase': 'idle', 'started_at': None,
            'finished_at': None, 'progress': {'done': 0, 'total': 0},
            'summary': None, 'error': None,
        }

    # =====================================================================
    # ÉTAT
    # =====================================================================
    def get_state(self):
        with self._lock:
            return dict(self._state)

    def _set_state(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _set_progress(self, phase, done, total):
        with self._lock:
            self._state['phase'] = phase
            self._state['progress'] = {'done': done, 'total': total}

    # =====================================================================
    # CIK MAP
    # =====================================================================
    def load_cik_map(self):
        """{ticker_upper: cik_int} depuis company_tickers.json (mis en cache)."""
        if self._cik_map is not None:
            return self._cik_map
        r = requests.get(CIK_MAP_URL, headers={'User-Agent': USER_AGENT},
                         timeout=self.REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        out = {}
        for _, row in data.items():
            t = str(row.get('ticker', '')).upper()
            cik = row.get('cik_str')
            if t and cik is not None:
                out[t] = int(cik)
        self._cik_map = out
        return out

    def _resolve_cik(self, ticker):
        """CIK d'un ticker (essaie les variantes '-'/'.')."""
        m = self._cik_map or {}
        t = ticker.upper()
        for cand in (t, t.replace('-', '.'), t.replace('.', '-')):
            if cand in m:
                return m[cand]
        return None

    # =====================================================================
    # FETCH + PARSE
    # =====================================================================
    def fetch_facts(self, cik):
        """companyfacts JSON pour un CIK, ou (None, status)."""
        url = FACTS_URL.format(cik=cik)
        try:
            r = requests.get(url, headers={'User-Agent': USER_AGENT},
                             timeout=self.REQUEST_TIMEOUT)
        except Exception as e:
            logger.warning('EDGAR facts CIK %s : %s', cik, e)
            return None, 'error'
        if r.status_code == 404:
            return None, 'not_found'
        if r.status_code != 200:
            return None, 'error'
        try:
            return r.json(), 'ok'
        except ValueError:
            return None, 'error'

    @staticmethod
    def _annual_series(facts, concept, is_flow, unit=MONEY):
        """
        {end_date: (val, filed)} pour un concept us-gaap, valeurs ANNUELLES 10-K.
        Pour un flux : durée ~365j. Pour un instant : date de fin unique.
        En cas de doublons sur un end, garde la 1ère divulgation (filed min).
        """
        node = facts.get('facts', {}).get('us-gaap', {}).get(concept)
        if not node:
            return {}
        units = node.get('units', {})
        items = units.get(unit) or (next(iter(units.values())) if units else [])
        out = {}
        for it in items:
            form = it.get('form', '')
            if not form.startswith('10-K'):
                continue
            end = it.get('end')
            filed = it.get('filed')
            val = it.get('val')
            if end is None or val is None or filed is None:
                continue
            if is_flow:
                start = it.get('start')
                if not start:
                    continue
                try:
                    d0 = date.fromisoformat(start)
                    d1 = date.fromisoformat(end)
                except ValueError:
                    continue
                if not (340 <= (d1 - d0).days <= 400):
                    continue  # écarte trimestriels/YTD partiels
            try:
                fd = date.fromisoformat(filed)
            except ValueError:
                continue
            prev = out.get(end)
            if prev is None or fd < prev[1]:
                out[end] = (float(val), fd)
        return out

    def parse_annual(self, facts):
        """
        Assemble les périodes annuelles → {end_date(str): {champ: val, '_filed': date}}.
        """
        series = {}
        for field, concepts in FIELD_CONCEPTS.items():
            is_flow = field in FLOW_FIELDS
            merged = {}
            for c in concepts:
                s = self._annual_series(facts, c, is_flow)
                for end, (val, fd) in s.items():
                    if end not in merged:      # priorité au 1er concept dispo
                        merged[end] = (val, fd)
            series[field] = merged
        # EPS (unité USD/shares)
        eps_series = {}
        for c in EPS_CONCEPTS:
            s = self._annual_series(facts, c, True, unit='USD/shares')
            for end, (val, fd) in s.items():
                if end not in eps_series:
                    eps_series[end] = (val, fd)
        series['eps_diluted'] = eps_series

        # Union des dates de fin d'exercice
        ends = set()
        for field, s in series.items():
            ends.update(s.keys())

        periods = {}
        for end in ends:
            row = {}
            fileds = []
            for field, s in series.items():
                if end in s:
                    row[field] = s[end][0]
                    fileds.append(s[end][1])
            if 'total_assets' not in row and 'net_income' not in row:
                continue  # période trop vide
            row['_filed'] = min(fileds) if fileds else None
            periods[end] = row
        return periods

    # =====================================================================
    # UPSERT
    # =====================================================================
    @staticmethod
    def _upsert_periods(ticker, periods):
        """Upsert des périodes annuelles dans FundamentalSnapshot (source='edgar')."""
        from models import db, FundamentalSnapshot
        written = 0
        existing = {(r.period_date.isoformat()): r for r in
                    FundamentalSnapshot.query.filter_by(ticker=ticker, period_type='A').all()}
        for end, row in periods.items():
            try:
                pdate = date.fromisoformat(end)
            except ValueError:
                continue

            debt = None
            ltd, std = row.get('long_term_debt'), row.get('short_term_debt')
            if ltd is not None or std is not None:
                debt = (ltd or 0.0) + (std or 0.0)

            ni = row.get('net_income')
            ocf = row.get('operating_cash_flow')
            capex = row.get('capital_expenditure')
            ta = row.get('total_assets')
            eq = row.get('total_equity')
            rev = row.get('total_revenue')
            ca, cl = row.get('current_assets'), row.get('current_liabilities')

            fcf = (ocf - capex) if (ocf is not None and capex is not None) else None
            accruals = ((ni - ocf) / ta) if (ni is not None and ocf is not None
                                             and ta not in (None, 0)) else None
            current_ratio = (ca / cl) if (ca is not None and cl not in (None, 0)) else None
            d2e = (debt / eq) if (debt is not None and eq not in (None, 0)) else None
            fcf_margin = (fcf / rev) if (fcf is not None and rev not in (None, 0)) else None

            fields = dict(
                report_date=row.get('_filed'), collected_at=datetime.utcnow(),
                source='edgar',
                total_revenue=rev, gross_profit=row.get('gross_profit'),
                operating_income=row.get('operating_income'), net_income=ni,
                eps_diluted=row.get('eps_diluted'),
                total_assets=ta, total_liabilities=row.get('total_liabilities'),
                total_equity=eq, cash_and_equivalents=row.get('cash_and_equivalents'),
                total_debt=debt, current_assets=ca, current_liabilities=cl,
                inventory=row.get('inventory'), accounts_receivable=row.get('accounts_receivable'),
                operating_cash_flow=ocf, investing_cash_flow=row.get('investing_cash_flow'),
                financing_cash_flow=row.get('financing_cash_flow'),
                capital_expenditure=capex, free_cash_flow=fcf,
                accruals_ratio=accruals, current_ratio=current_ratio,
                debt_to_equity=d2e, fcf_margin=fcf_margin,
            )
            r = existing.get(end)
            if r:
                for k, v in fields.items():
                    setattr(r, k, v)
            else:
                db.session.add(FundamentalSnapshot(
                    ticker=ticker, period_date=pdate, period_type='A', **fields))
            written += 1
        db.session.commit()
        return written

    # =====================================================================
    # ORCHESTRATION
    # =====================================================================
    def _universe(self):
        from models import IndexConstituent
        rows = IndexConstituent.query.filter_by(is_active=True).all()
        return sorted({r.ticker for r in rows})

    def collect(self, tickers=None):
        """Collecte EDGAR annuelle pour l'univers (ou `tickers`). À appeler en app_context."""
        t0 = time.time()
        try:
            self.load_cik_map()
        except Exception as e:
            return {'success': False, 'error': f'CIK map: {e}'}

        tickers = tickers or self._universe()
        if not tickers:
            return {'success': False, 'error': 'Univers vide (IndexConstituent)'}

        total = len(tickers)
        ok, periods_written, no_cik, failed = 0, 0, [], []
        for i, ticker in enumerate(tickers):
            ticker = ticker.upper().strip()
            cik = self._resolve_cik(ticker)
            if cik is None:
                no_cik.append(ticker)
                continue
            facts, status = self.fetch_facts(cik)
            if status != 'ok' or not facts:
                failed.append(ticker)
            else:
                try:
                    periods = self.parse_annual(facts)
                    if periods:
                        periods_written += self._upsert_periods(ticker, periods)
                        ok += 1
                    else:
                        failed.append(ticker)
                except Exception as e:
                    from models import db
                    db.session.rollback()
                    logger.warning('EDGAR parse %s : %s', ticker, e)
                    failed.append(ticker)
            if (i + 1) % self.LOG_EVERY == 0:
                self._set_progress('edgar', i + 1, total)
            time.sleep(self.SLEEP_BETWEEN)

        return {
            'success': True, 'tickers': total, 'ok': ok,
            'periods_written': periods_written, 'no_cik': len(no_cik),
            'failed': len(failed), 'failed_sample': failed[:10],
            'elapsed_s': round(time.time() - t0, 1),
            'finished_at': datetime.utcnow().isoformat(),
        }

    def run_background(self, app, tickers=None):
        with self._lock:
            if self._state['running']:
                return False
            self._state.update({
                'running': True, 'phase': 'starting', 'error': None,
                'summary': None, 'started_at': datetime.utcnow().isoformat(),
                'finished_at': None, 'progress': {'done': 0, 'total': 0},
            })

        def _worker():
            with app.app_context():
                try:
                    summary = self.collect(tickers=tickers)
                    self._set_state(summary=summary, phase='done')
                except Exception as e:
                    logger.exception('Collecte EDGAR échouée')
                    self._set_state(error=str(e), phase='error')
                finally:
                    self._set_state(running=False,
                                    finished_at=datetime.utcnow().isoformat())

        threading.Thread(target=_worker, name='edgar-collect', daemon=True).start()
        return True

    def coverage(self):
        from models import db, FundamentalSnapshot
        from sqlalchemy import func
        n, nt, mn, mx = db.session.query(
            func.count(FundamentalSnapshot.id),
            func.count(func.distinct(FundamentalSnapshot.ticker)),
            func.min(FundamentalSnapshot.period_date),
            func.max(FundamentalSnapshot.period_date)
        ).filter(FundamentalSnapshot.source == 'edgar').one()
        return {'rows': n or 0, 'tickers': nt or 0,
                'start': mn.isoformat() if mn else None,
                'end': mx.isoformat() if mx else None}
