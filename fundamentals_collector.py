# -*- coding: utf-8 -*-
"""
Collecte des données fondamentales via yfinance
===============================================
Alimente deux tables (stratégie SHORT multi-facteurs) :

  - **FundamentalSnapshot** : états financiers trimestriels + annuels
    (income statement, balance sheet, cash flow) par ticker. Sert au calcul de
    l'anomalie des accruals (Sloan 1996) → Couche 2 du score short.
  - **TickerInfoSnapshot** : ratios de marché courants + secteur (yf.Ticker.info),
    rafraîchis mensuellement. Sert au mapping ticker→secteur (alpha relatif) et aux
    filtres d'éligibilité.

Comportement (calqué sur `price_data_service.PriceDataService`) :
  - Chunking + pause entre tickers pour ménager Yahoo (yfinance non officiel).
  - Upsert idempotent (clé métier), jamais bloquant (try/except large).
  - Incrémental : on n'insère une période fondamentale que si sa date de fin
    (`period_date`) est absente de la base pour ce ticker.
  - Exécution en arrière-plan (thread) avec état interrogeable.

⚠️ Limite look-ahead : yfinance ne fournit PAS la date de publication réelle du
10-Q/10-K. Le backtest doit donc estimer la disponibilité par
`period_date + délai de dépôt` (voir `short_scoring.FILING_LAG_DAYS`). On stocke
`collected_at` = instant de collecte (utile en live, pas en backtest historique).
"""

import json
import time
import logging
import threading
from datetime import datetime, date

import pandas as pd

logger = logging.getLogger(__name__)

# Libellés de lignes yfinance/Yahoo (plusieurs candidats par grandeur : la
# taxonomie varie selon les tickers et les versions de yfinance).
_INCOME_FIELDS = {
    'total_revenue':    ['Total Revenue', 'Operating Revenue'],
    'gross_profit':     ['Gross Profit'],
    'operating_income': ['Operating Income', 'Total Operating Income As Reported'],
    'ebitda':           ['EBITDA', 'Normalized EBITDA'],
    'net_income':       ['Net Income', 'Net Income Common Stockholders',
                         'Net Income Continuous Operations'],
    'eps_diluted':      ['Diluted EPS'],
}
_BALANCE_FIELDS = {
    'total_assets':         ['Total Assets'],
    'total_liabilities':    ['Total Liabilities Net Minority Interest',
                             'Total Liabilities'],
    'total_equity':         ['Stockholders Equity',
                             'Total Equity Gross Minority Interest'],
    'cash_and_equivalents': ['Cash And Cash Equivalents',
                             'Cash Cash Equivalents And Short Term Investments'],
    'total_debt':           ['Total Debt'],
    'current_assets':       ['Current Assets'],
    'current_liabilities':  ['Current Liabilities'],
    'inventory':            ['Inventory'],
    'accounts_receivable':  ['Accounts Receivable', 'Receivables'],
}
_CASHFLOW_FIELDS = {
    'operating_cash_flow': ['Operating Cash Flow',
                            'Cash Flow From Continuing Operating Activities'],
    'investing_cash_flow': ['Investing Cash Flow',
                            'Cash Flow From Continuing Investing Activities'],
    'financing_cash_flow': ['Financing Cash Flow',
                            'Cash Flow From Continuing Financing Activities'],
    'capital_expenditure': ['Capital Expenditure', 'Purchase Of PPE'],
    'free_cash_flow':      ['Free Cash Flow'],
}

# Champs conservés depuis yf.Ticker.info (le reste va dans raw_info JSON).
_INFO_NUM_FIELDS = {
    'market_cap':        'marketCap',
    'enterprise_value':  'enterpriseValue',
    'trailing_pe':       'trailingPE',
    'forward_pe':        'forwardPE',
    'price_to_book':     'priceToBook',
    'price_to_sales':    'priceToSalesTrailing12Months',
    'ev_to_ebitda':      'enterpriseToEbitda',
    'gross_margins':     'grossMargins',
    'operating_margins': 'operatingMargins',
    'profit_margins':    'profitMargins',
    'revenue_growth':    'revenueGrowth',
    'earnings_growth':   'earningsGrowth',
    'return_on_equity':  'returnOnEquity',
    'return_on_assets':  'returnOnAssets',
    'debt_to_equity':    'debtToEquity',
    'current_ratio':     'currentRatio',
    'quick_ratio':       'quickRatio',
    'short_ratio':       'shortRatio',
    'short_percent_float': 'shortPercentOfFloat',
    'dividend_yield':    'dividendYield',
    'payout_ratio':      'payoutRatio',
    'beta':              'beta',
}
_INFO_STR_FIELDS = {
    'sector':   'sector',
    'industry': 'industry',
    'country':  'country',
}


class FundamentalsCollector:
    SLEEP_BETWEEN = 0.4      # pause (s) entre deux tickers
    LOG_EVERY = 25          # log de progression tous les N tickers

    def __init__(self, email_service=None):
        self.email_service = email_service
        self._lock = threading.Lock()
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
    # HELPERS d'extraction yfinance
    # =====================================================================
    @staticmethod
    def _pick(df, candidates):
        """Retourne la ligne (Series indexée par date) du 1er libellé présent, sinon None."""
        if df is None or df.empty:
            return None
        for label in candidates:
            if label in df.index:
                return df.loc[label]
        return None

    @staticmethod
    def _num(series, col):
        """Valeur float d'une Series à la colonne (date) `col`, ou None."""
        if series is None or col not in series.index:
            return None
        v = series.get(col)
        try:
            if v is None or pd.isna(v):
                return None
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _raw_column(df, col):
        """Sérialise en JSON la colonne `col` d'un DataFrame yfinance ({libellé: valeur})."""
        if df is None or df.empty or col not in df.columns:
            return None
        out = {}
        for label, val in df[col].items():
            try:
                out[str(label)] = None if (val is None or pd.isna(val)) else float(val)
            except (TypeError, ValueError):
                out[str(label)] = None
        return json.dumps(out)

    # =====================================================================
    # COLLECTE DES ÉTATS FINANCIERS → FundamentalSnapshot
    # =====================================================================
    def _existing_periods(self, ticker):
        """{ (period_type) : set(period_date) } déjà en base pour ce ticker."""
        from models import FundamentalSnapshot
        rows = (FundamentalSnapshot.query
                .with_entities(FundamentalSnapshot.period_type,
                               FundamentalSnapshot.period_date)
                .filter(FundamentalSnapshot.ticker == ticker).all())
        out = {'Q': set(), 'A': set()}
        for pt, pd_ in rows:
            out.setdefault(pt, set()).add(pd_)
        return out

    def _build_snapshots(self, ticker, income, balance, cash, period_type, existing):
        """Construit les FundamentalSnapshot manquants pour un jeu (income/balance/cash)."""
        from models import FundamentalSnapshot

        if income is None and balance is None and cash is None:
            return []
        # Colonnes = dates de fin de période (union des 3 états)
        cols = set()
        for df in (income, balance, cash):
            if df is not None and not df.empty:
                cols.update(df.columns)

        picks = {
            'income':  {k: self._pick(income, c) for k, c in _INCOME_FIELDS.items()},
            'balance': {k: self._pick(balance, c) for k, c in _BALANCE_FIELDS.items()},
            'cash':    {k: self._pick(cash, c) for k, c in _CASHFLOW_FIELDS.items()},
        }

        rows = []
        for col in cols:
            try:
                period_dt = col.date() if hasattr(col, 'date') else col
            except Exception:
                continue
            if not isinstance(period_dt, date):
                continue
            if period_dt in existing.get(period_type, set()):
                continue  # déjà collecté → incrémental

            vals = {}
            for grp, keyset in (('income', _INCOME_FIELDS), ('balance', _BALANCE_FIELDS),
                                ('cash', _CASHFLOW_FIELDS)):
                for k in keyset:
                    vals[k] = self._num(picks[grp][k], col)

            # Ratios dérivés (None si dénominateur manquant/nul)
            ni, ocf, ta = vals.get('net_income'), vals.get('operating_cash_flow'), vals.get('total_assets')
            ca, cl = vals.get('current_assets'), vals.get('current_liabilities')
            debt, eq = vals.get('total_debt'), vals.get('total_equity')
            fcf, rev = vals.get('free_cash_flow'), vals.get('total_revenue')

            accruals = ((ni - ocf) / ta) if (ni is not None and ocf is not None
                                             and ta not in (None, 0)) else None
            current_ratio = (ca / cl) if (ca is not None and cl not in (None, 0)) else None
            d2e = (debt / eq) if (debt is not None and eq not in (None, 0)) else None
            fcf_margin = (fcf / rev) if (fcf is not None and rev not in (None, 0)) else None

            rows.append(FundamentalSnapshot(
                ticker=ticker, period_date=period_dt, period_type=period_type,
                collected_at=datetime.utcnow(), source='yfinance',
                total_revenue=vals.get('total_revenue'), gross_profit=vals.get('gross_profit'),
                operating_income=vals.get('operating_income'), ebitda=vals.get('ebitda'),
                net_income=ni, eps_diluted=vals.get('eps_diluted'),
                total_assets=ta, total_liabilities=vals.get('total_liabilities'),
                total_equity=eq, cash_and_equivalents=vals.get('cash_and_equivalents'),
                total_debt=debt, current_assets=ca, current_liabilities=cl,
                inventory=vals.get('inventory'), accounts_receivable=vals.get('accounts_receivable'),
                operating_cash_flow=ocf, investing_cash_flow=vals.get('investing_cash_flow'),
                financing_cash_flow=vals.get('financing_cash_flow'),
                capital_expenditure=vals.get('capital_expenditure'), free_cash_flow=fcf,
                accruals_ratio=accruals, current_ratio=current_ratio,
                debt_to_equity=d2e, fcf_margin=fcf_margin,
                raw_income_stmt=self._raw_column(income, col),
                raw_balance_sheet=self._raw_column(balance, col),
                raw_cashflow=self._raw_column(cash, col),
            ))
        return rows

    def collect_statements(self, tickers, full=False):
        """Collecte états financiers Q + A pour la liste de tickers. Renvoie des stats."""
        from models import db
        import yfinance as yf

        total = len(tickers)
        ok, new_rows, failed = 0, 0, []
        for i, ticker in enumerate(tickers):
            ticker = ticker.upper().strip()
            try:
                existing = self._existing_periods(ticker)
                tk = yf.Ticker(ticker)
                batch = []
                # Trimestriel
                batch += self._build_snapshots(
                    ticker, _safe(lambda: tk.quarterly_income_stmt),
                    _safe(lambda: tk.quarterly_balance_sheet),
                    _safe(lambda: tk.quarterly_cashflow), 'Q', existing)
                # Annuel
                batch += self._build_snapshots(
                    ticker, _safe(lambda: tk.income_stmt),
                    _safe(lambda: tk.balance_sheet),
                    _safe(lambda: tk.cashflow), 'A', existing)
                if batch:
                    db.session.add_all(batch)
                    db.session.commit()
                    new_rows += len(batch)
                    ok += 1
                elif existing['Q'] or existing['A']:
                    ok += 1  # déjà en base, rien de neuf
                else:
                    failed.append(ticker)
            except Exception as e:
                db.session.rollback()
                logger.warning('Fondamentaux %s : %s', ticker, e)
                failed.append(ticker)

            if (i + 1) % self.LOG_EVERY == 0:
                self._set_progress('statements', i + 1, total)
            time.sleep(self.SLEEP_BETWEEN)

        return {'total': total, 'ok': ok, 'new_rows': new_rows, 'failed': failed}

    # =====================================================================
    # COLLECTE INFO MARCHÉ → TickerInfoSnapshot
    # =====================================================================
    def collect_info(self, tickers):
        """Collecte yf.Ticker.info → TickerInfoSnapshot (1 ligne par ticker et par run)."""
        from models import db, TickerInfoSnapshot
        import yfinance as yf

        total = len(tickers)
        ok, failed = 0, []
        for i, ticker in enumerate(tickers):
            ticker = ticker.upper().strip()
            try:
                info = _safe(lambda: yf.Ticker(ticker).info) or {}
                if not info:
                    failed.append(ticker)
                    continue
                kwargs = {'ticker': ticker, 'collected_at': datetime.utcnow(),
                          'source': 'yfinance', 'raw_info': _safe_json(info)}
                for model_key, info_key in _INFO_NUM_FIELDS.items():
                    kwargs[model_key] = _to_float(info.get(info_key))
                for model_key, info_key in _INFO_STR_FIELDS.items():
                    v = info.get(info_key)
                    kwargs[model_key] = (str(v)[:100] if v else None)
                emp = info.get('fullTimeEmployees')
                kwargs['full_time_employees'] = int(emp) if isinstance(emp, (int, float)) else None
                db.session.add(TickerInfoSnapshot(**kwargs))
                db.session.commit()
                ok += 1
            except Exception as e:
                db.session.rollback()
                logger.warning('Info %s : %s', ticker, e)
                failed.append(ticker)

            if (i + 1) % self.LOG_EVERY == 0:
                self._set_progress('info', i + 1, total)
            time.sleep(self.SLEEP_BETWEEN)

        return {'total': total, 'ok': ok, 'failed': failed}

    # =====================================================================
    # ORCHESTRATION
    # =====================================================================
    def _universe(self):
        """Univers de collecte : constituants actifs (hors ETF/benchmarks)."""
        from models import IndexConstituent
        rows = IndexConstituent.query.filter_by(is_active=True).all()
        return sorted({r.ticker for r in rows})

    def collect(self, full=False, with_info=True):
        """
        Collecte synchrone (états financiers + info). À appeler dans un app_context.
        `with_info=True` collecte aussi TickerInfoSnapshot (mensuel).
        """
        t0 = time.time()
        tickers = self._universe()
        if not tickers:
            return {'success': False, 'error': 'Univers vide (IndexConstituent)'}

        self._set_progress('statements', 0, len(tickers))
        stmt_stats = self.collect_statements(tickers, full=full)

        info_stats = None
        if with_info:
            self._set_progress('info', 0, len(tickers))
            info_stats = self.collect_info(tickers)

        return {
            'success': True,
            'tickers': len(tickers),
            'statements': stmt_stats,
            'info': info_stats,
            'elapsed_s': round(time.time() - t0, 1),
            'finished_at': datetime.utcnow().isoformat(),
        }

    def run_background(self, app, full=False, with_info=True):
        """Lance la collecte dans un thread démon. Renvoie False si déjà en cours."""
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
                    summary = self.collect(full=full, with_info=with_info)
                    self._set_state(summary=summary, phase='done')
                except Exception as e:
                    logger.exception('Collecte fondamentaux échouée')
                    self._set_state(error=str(e), phase='error')
                finally:
                    self._set_state(running=False,
                                    finished_at=datetime.utcnow().isoformat())

        threading.Thread(target=_worker, name='fundamentals-collect', daemon=True).start()
        return True

    def coverage(self):
        """Statistiques de couverture (UI/diagnostic)."""
        from models import db, FundamentalSnapshot, TickerInfoSnapshot
        from sqlalchemy import func
        f_tickers, f_rows = db.session.query(
            func.count(func.distinct(FundamentalSnapshot.ticker)),
            func.count(FundamentalSnapshot.id)).one()
        i_tickers, i_rows = db.session.query(
            func.count(func.distinct(TickerInfoSnapshot.ticker)),
            func.count(TickerInfoSnapshot.id)).one()
        return {
            'fundamentals': {'tickers': f_tickers or 0, 'rows': f_rows or 0},
            'ticker_info':  {'tickers': i_tickers or 0, 'rows': i_rows or 0},
        }


# =========================================================================
# Utilitaires module
# =========================================================================
def _safe(fn):
    """Exécute fn() en avalant toute exception yfinance (renvoie None)."""
    try:
        return fn()
    except Exception:
        return None


def _to_float(v):
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_json(info):
    """Sérialise le dict .info en JSON en ne gardant que les valeurs primitives."""
    clean = {}
    for k, v in info.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            clean[k] = v
    try:
        return json.dumps(clean)
    except (TypeError, ValueError):
        return None
