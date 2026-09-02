# -*- coding: utf-8 -*-
"""
Collecte du short interest FINRA (source gratuite, à valider)
=============================================================
Alimente la table **ShortInterestSnapshot** (Couche 3 du score short — Dechow
et al. 2001). FINRA publie deux fois par mois un fichier consolidé pipe-délimité,
téléchargeable librement (sans API ni authentification) :

    https://cdn.finra.org/equity/otcmarket/biweekly/shrt{YYYYMMDD}.csv

où `YYYYMMDD` est la **date de règlement** (settlement date). Colonnes du fichier
(délimiteur = `|`) :

    accountingYearMonthNumber | symbolCode | issueName |
    issuerServicesGroupExchangeCode | marketClassCode |
    currentShortPositionQuantity | previousShortPositionQuantity |
    stockSplitFlag | averageDailyVolumeQuantity | daysToCoverQuantity |
    revisionFlag | changePercent | changePreviousNumber | settlementDate

⚠️ **Limites à valider (le plan demande un test explicite avant d'en dépendre) :**
  - Avant **juin 2021**, le fichier ne couvre que les titres OTC (pas les titres
    cotés NYSE/Nasdaq). L'historique exploitable pour SP500/NDX100 démarre donc
    ~mi-2021 → la Couche 3 n'est backtestable que sur période récente.
  - La date de publication réelle n'est pas dans le fichier. On l'estime à
    `settlement_date + ~8 jours` (dissémination FINRA) et on la stocke dans
    `report_date` pour l'anti-look-ahead du backtest.
  - Disponibilité des fichiers historiques sur le CDN à vérifier au cas par cas.
"""

import io
import csv
import logging
import threading
from datetime import datetime, date, timedelta

import requests

logger = logging.getLogger(__name__)

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

FINRA_URL = 'https://cdn.finra.org/equity/otcmarket/biweekly/shrt{date}.csv'

# Délai estimé entre date de règlement et publication effective (dissémination).
PUBLICATION_LAG_DAYS = 8

# Seuil absolu de risque de short squeeze (jours pour couvrir).
SQUEEZE_DTC_THRESHOLD = 20.0


class FinraCollector:
    REQUEST_TIMEOUT = 30
    LOG_EVERY = 1

    def __init__(self):
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
    # TÉLÉCHARGEMENT + PARSING D'UN FICHIER
    # =====================================================================
    def fetch_file(self, settlement_date):
        """
        Télécharge et parse le fichier FINRA pour une date de règlement.
        Renvoie (rows, status) où rows est une liste de dicts normalisés, ou
        (None, 'not_found' | 'error').
        """
        url = FINRA_URL.format(date=settlement_date.strftime('%Y%m%d'))
        try:
            r = requests.get(url, headers={'User-Agent': _UA},
                             timeout=self.REQUEST_TIMEOUT)
        except Exception as e:
            logger.warning('FINRA download %s : %s', settlement_date, e)
            return None, 'error'
        # Le CDN FINRA (S3) renvoie 403 *ou* 404 pour une date sans fichier
        # (les dates hors calendrier de règlement) → on traite les deux comme
        # « fichier absent », pas comme une erreur.
        if r.status_code in (403, 404):
            return None, 'not_found'
        if r.status_code != 200 or not r.text:
            logger.warning('FINRA %s : HTTP %s', settlement_date, r.status_code)
            return None, 'error'

        rows = []
        reader = csv.DictReader(io.StringIO(r.text), delimiter='|')
        for raw in reader:
            parsed = self._parse_row(raw)
            if parsed is not None:
                rows.append(parsed)
        return rows, 'ok'

    @staticmethod
    def _parse_row(raw):
        """Normalise une ligne CSV FINRA → dict prêt pour upsert (ou None si invalide)."""
        ticker = (raw.get('symbolCode') or '').strip().upper()
        if not ticker:
            return None
        settle_str = (raw.get('settlementDate') or '').strip()
        try:
            settle = date.fromisoformat(settle_str)
        except (ValueError, TypeError):
            return None

        def _f(key):
            v = (raw.get(key) or '').strip()
            if v == '':
                return None
            try:
                return float(v)
            except ValueError:
                return None

        cur = _f('currentShortPositionQuantity')
        prev = _f('previousShortPositionQuantity')
        adv = _f('averageDailyVolumeQuantity')
        dtc = _f('daysToCoverQuantity')
        change_prev = _f('changePreviousNumber')
        change_pct = _f('changePercent')

        # days_to_cover : fourni par FINRA, sinon recalcul si ADV exploitable
        if dtc is None and cur is not None and adv not in (None, 0):
            dtc = cur / adv

        if change_prev is None and cur is not None and prev is not None:
            change_prev = cur - prev
        if change_pct is None and prev not in (None, 0) and change_prev is not None:
            change_pct = (change_prev / prev) * 100.0

        if change_prev is None:
            trend = 'stable'
        elif change_prev > 0:
            trend = 'up'
        elif change_prev < 0:
            trend = 'down'
        else:
            trend = 'stable'

        squeeze = bool(dtc is not None and dtc > SQUEEZE_DTC_THRESHOLD)

        return {
            'ticker': ticker,
            'settlement_date': settle,
            'report_date': settle + timedelta(days=PUBLICATION_LAG_DAYS),
            'short_interest': cur,
            'avg_daily_volume': adv,
            'days_to_cover': dtc,
            'previous_short_interest': prev,
            'change_from_previous': change_prev,
            'change_pct': change_pct,
            'sir_trend': trend,
            'squeeze_risk': squeeze,
            'raw': {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items()},
        }

    # =====================================================================
    # UPSERT
    # =====================================================================
    @staticmethod
    def _upsert_rows(rows, only_tickers=None):
        """
        Upsert idempotent par (ticker, settlement_date). Si `only_tickers` est
        fourni (set), n'insère que ces tickers (filtre univers). Renvoie le nb écrit.
        """
        import json as _json
        from models import db, ShortInterestSnapshot
        if not rows:
            return 0

        settle = rows[0]['settlement_date']
        existing = {r.ticker: r for r in ShortInterestSnapshot.query
                    .filter_by(settlement_date=settle).all()}

        written = 0
        for row in rows:
            if only_tickers is not None and row['ticker'] not in only_tickers:
                continue
            r = existing.get(row['ticker'])
            payload = dict(
                short_interest=row['short_interest'],
                avg_daily_volume=row['avg_daily_volume'],
                days_to_cover=row['days_to_cover'],
                previous_short_interest=row['previous_short_interest'],
                change_from_previous=row['change_from_previous'],
                change_pct=row['change_pct'],
                sir_trend=row['sir_trend'],
                squeeze_risk=row['squeeze_risk'],
                report_date=row['report_date'],
                raw_data=_json.dumps(row['raw']),
            )
            if r:
                for k, v in payload.items():
                    setattr(r, k, v)
            else:
                db.session.add(ShortInterestSnapshot(
                    ticker=row['ticker'], settlement_date=settle,
                    collected_at=datetime.utcnow(), source='FINRA', **payload))
            written += 1
        db.session.commit()
        return written

    # =====================================================================
    # GÉNÉRATION DES DATES CANDIDATES
    # =====================================================================
    @staticmethod
    def _candidate_dates(start, end):
        """
        Dates de règlement candidates entre `start` et `end`. FINRA publie ~2×/mois
        (mi-mois et fin de mois). On propose une fenêtre de jours ouvrés autour du
        15 et de la fin de mois ; le collecteur tolère les 404 (dates invalides).
        """
        out = []
        cur = date(start.year, start.month, 1)
        while cur <= end:
            y, m = cur.year, cur.month
            # dernier jour du mois
            if m == 12:
                last = date(y, 12, 31)
            else:
                last = date(y, m + 1, 1) - timedelta(days=1)
            # fenêtres : autour du 15 et de la fin de mois
            candidates = list(range(13, 18))  # 13..17
            candidates += [last.day - i for i in range(0, 5)]  # 5 derniers jours
            for d in candidates:
                if 1 <= d <= last.day:
                    dt = date(y, m, d)
                    if dt.weekday() < 5 and start <= dt <= end:  # jour ouvré
                        out.append(dt)
            # mois suivant
            cur = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        return sorted(set(out))

    # =====================================================================
    # ORCHESTRATION
    # =====================================================================
    def _universe(self):
        """Univers de filtrage : constituants actifs. None si table vide (tout garder)."""
        from models import IndexConstituent
        rows = IndexConstituent.query.filter_by(is_active=True).all()
        tickers = {r.ticker for r in rows}
        return tickers or None

    def _known_settlement_dates(self):
        """Dates de règlement déjà présentes en base (pour l'incrémental)."""
        from models import db, ShortInterestSnapshot
        rows = db.session.query(ShortInterestSnapshot.settlement_date).distinct().all()
        return {d for (d,) in rows}

    def collect_range(self, start, end, only_universe=True, skip_existing=True):
        """
        Collecte toutes les dates de règlement disponibles entre start et end.
        Renvoie des stats {dates_tried, files_ok, files_missing, rows_written}.
        À appeler dans un app_context Flask.
        """
        only = self._universe() if only_universe else None
        known = self._known_settlement_dates() if skip_existing else set()
        candidates = [d for d in self._candidate_dates(start, end) if d not in known]

        total = len(candidates)
        files_ok, files_missing, rows_written = 0, 0, 0
        for i, dt in enumerate(candidates):
            rows, status = self.fetch_file(dt)
            if status == 'ok' and rows:
                rows_written += self._upsert_rows(rows, only_tickers=only)
                files_ok += 1
            elif status == 'not_found':
                files_missing += 1
            self._set_progress('finra', i + 1, total)

        return {
            'success': True,
            'dates_tried': total,
            'files_ok': files_ok,
            'files_missing': files_missing,
            'rows_written': rows_written,
            'range': [start.isoformat(), end.isoformat()],
            'universe_filtered': only is not None,
            'finished_at': datetime.utcnow().isoformat(),
        }

    def collect_recent(self, months_back=2, **kw):
        """Collecte les dates récentes (défaut : 2 derniers mois)."""
        end = date.today()
        start = end - timedelta(days=int(months_back * 31))
        return self.collect_range(start, end, **kw)

    def run_background(self, app, start=None, end=None, months_back=2, **kw):
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
                    if start and end:
                        summary = self.collect_range(start, end, **kw)
                    else:
                        summary = self.collect_recent(months_back=months_back, **kw)
                    self._set_state(summary=summary, phase='done')
                except Exception as e:
                    logger.exception('Collecte FINRA échouée')
                    self._set_state(error=str(e), phase='error')
                finally:
                    self._set_state(running=False,
                                    finished_at=datetime.utcnow().isoformat())

        threading.Thread(target=_worker, name='finra-collect', daemon=True).start()
        return True

    def coverage(self):
        """Statistiques de couverture (UI/diagnostic)."""
        from models import db, ShortInterestSnapshot
        from sqlalchemy import func
        tickers, rows, mn, mx = db.session.query(
            func.count(func.distinct(ShortInterestSnapshot.ticker)),
            func.count(ShortInterestSnapshot.id),
            func.min(ShortInterestSnapshot.settlement_date),
            func.max(ShortInterestSnapshot.settlement_date)).one()
        return {
            'tickers': tickers or 0,
            'rows': rows or 0,
            'start': mn.isoformat() if mn else None,
            'end': mx.isoformat() if mx else None,
        }
