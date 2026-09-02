# -*- coding: utf-8 -*-
"""
Service de collecte de prix historiques via yfinance
====================================================
Collecte la nuit le maximum d'historique de prix des constituants du S&P 500 et
du Nasdaq-100 :

  - **Mensuel** (jusqu'à ~20 ans) → table `MonthlyPriceBar` : base longue du
    momentum 12-1.
  - **Journalier** (~6 ans, couvre un backtest 5 ans + le lookback momentum de
    13 mois) → table existante `MarketPriceBar` avec `source='yfinance'`, que le
    moteur de backtest lit déjà → les backtests se basent sur ces données.

Comportement :
  - 1ère exécution = lourde (tout l'historique). Ensuite **incrémental** : on ne
    récupère que ce qui manque depuis la dernière barre stockée.
  - **Composition des indices** revérifiée ~1×/mois (scraping Wikipédia, repli
    codé en dur). Tiingo ne fournit pas la composition d'indices en clair.
  - **Rate limiting** : téléchargements par lots (`CHUNK_SIZE`) avec threads
    internes yfinance + petite pause entre lots → rapide sans se faire bloquer.
  - **Exécution en arrière-plan** (thread) avec état interrogeable par l'UI.
  - **Alerte email** si ≥ 25 % des tickers échouent.

yfinance est « best effort » / non officiel : on le traite comme une source
fragile (try/except large, upsert idempotent, jamais bloquant pour l'app).
"""

import io
import time
import logging
import threading
from datetime import datetime, date, timedelta

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

# Indices/ETF de référence collectés en plus des constituants (benchmarks backtest)
# + ETF sectoriels GICS (SPDR Select Sector) : servent au calcul de l'alpha relatif
# sectoriel de la stratégie short (Couche 1 momentum relatif vs secteur).
SECTOR_ETFS = ['XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'XLP', 'XLI', 'XLB', 'XLU', 'XLRE', 'XLC']
BENCHMARKS = ['SPY', 'QQQ', '^GSPC', '^NDX'] + SECTOR_ETFS

# Mapping secteur (taxonomie Yahoo Finance, champ .info['sector']) → ETF sectoriel.
# Utilisé pour l'alpha relatif sectoriel de la stratégie short.
SECTOR_TO_ETF = {
    'Technology':             'XLK',
    'Financial Services':     'XLF',
    'Energy':                 'XLE',
    'Healthcare':             'XLV',
    'Consumer Cyclical':      'XLY',
    'Consumer Defensive':     'XLP',
    'Industrials':            'XLI',
    'Basic Materials':        'XLB',
    'Utilities':              'XLU',
    'Real Estate':            'XLRE',
    'Communication Services': 'XLC',
}


class PriceDataService:
    # --- Horizons de collecte -------------------------------------------------
    MONTHLY_YEARS = 32      # historique mensuel max
    DAILY_YEARS = 21         # daily : 20 ans de backtest + ~13 mois de lookback momentum

    # --- Rate limiting --------------------------------------------------------
    CHUNK_SIZE = 40         # tickers par lot yf.download (threads internes)
    SLEEP_BETWEEN = 1.2     # pause (s) entre deux lots pour ménager Yahoo
    INCREMENTAL_BUFFER_D = 7    # marge de recouvrement daily (jours)
    INCREMENTAL_BUFFER_M = 70   # marge de recouvrement monthly (jours)

    # --- Politique de rafraîchissement de la composition ----------------------
    CONSTITUENTS_TTL_DAYS = 28  # revérifier la compo des indices ~1×/mois

    # --- Seuil d'alerte échec -------------------------------------------------
    FAILURE_ALERT_RATIO = 0.25

    def __init__(self, email_service=None):
        self.email_service = email_service
        self._lock = threading.Lock()
        # État partagé pour l'UI (thread d'arrière-plan)
        self._state = {
            'running': False, 'phase': 'idle', 'started_at': None,
            'finished_at': None, 'progress': {'done': 0, 'total': 0},
            'summary': None, 'error': None,
        }

    # =====================================================================
    # ÉTAT (lu par /api/prices/status)
    # =====================================================================
    def get_state(self):
        with self._lock:
            return dict(self._state)

    def _set_state(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _set_phase(self, phase, done=0, total=0):
        with self._lock:
            self._state['phase'] = phase
            self._state['progress'] = {'done': done, 'total': total}

    # =====================================================================
    # COMPOSITION DES INDICES
    # =====================================================================
    @staticmethod
    def _norm_symbol(sym):
        """Normalise un symbole Wikipédia vers la convention yfinance (BRK.B → BRK-B)."""
        return str(sym).strip().upper().replace('.', '-')

    def _scrape_wikipedia(self, url, symbol_col_candidates):
        """Récupère une table Wikipédia (avec User-Agent) et renvoie la liste (symbol, name)."""
        r = requests.get(url, headers={'User-Agent': _UA}, timeout=25)
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        for t in tables:
            cols = {str(c): c for c in t.columns}
            sym_key = next((cols[c] for c in cols
                            if any(cand.lower() == c.lower() for cand in symbol_col_candidates)), None)
            if sym_key is None:
                continue
            name_key = next((cols[c] for c in cols
                             if 'name' in c.lower() or 'company' in c.lower()), None)
            out = []
            for _, row in t.iterrows():
                sym = self._norm_symbol(row[sym_key])
                if not sym or sym == 'NAN':
                    continue
                name = str(row[name_key]) if name_key is not None else None
                out.append((sym, name))
            if out:
                return out
        raise ValueError(f'Aucune colonne symbole trouvée sur {url}')

    def fetch_constituents(self):
        """
        Renvoie {'SP500': [(sym,name),...], 'NDX100': [...]}.
        Scraping Wikipédia ; en cas d'échec, repli sur DEFAULT_PANEL (config).
        """
        result = {}
        try:
            result['SP500'] = self._scrape_wikipedia(
                'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
                ['Symbol', 'Ticker'])
            logger.info('S&P 500 : %d constituants', len(result['SP500']))
        except Exception as e:
            logger.warning('Scrape S&P 500 échoué : %s', e)
        try:
            result['NDX100'] = self._scrape_wikipedia(
                'https://en.wikipedia.org/wiki/Nasdaq-100',
                ['Ticker', 'Symbol'])
            logger.info('Nasdaq-100 : %d constituants', len(result['NDX100']))
        except Exception as e:
            logger.warning('Scrape Nasdaq-100 échoué : %s', e)
        return result

    def refresh_constituents(self, force=False):
        """
        Met à jour la table IndexConstituent si la dernière vérif date de plus de
        CONSTITUENTS_TTL_DAYS (ou si `force`). Renvoie (refreshed: bool, count: int).
        Les titres absents du nouveau scrape passent is_active=False (on garde leur
        historique de prix). Repli DEFAULT_PANEL si le scrape échoue ET base vide.
        """
        from models import db, IndexConstituent, Settings

        last = Settings.get('constituents_refreshed_at', '')
        if not force and last:
            try:
                if datetime.fromisoformat(last) > datetime.utcnow() - timedelta(days=self.CONSTITUENTS_TTL_DAYS):
                    # Bootstrap : si l'historique point-in-time n'a jamais été
                    # construit (table nouvelle), le faire même compo à jour.
                    from models import IndexMembership
                    if IndexMembership.query.count() == 0 and IndexConstituent.query.count() > 0:
                        try:
                            self.rebuild_membership_history()
                        except Exception as e:
                            logger.warning('rebuild_membership_history (bootstrap) échoué : %s', e)
                    return False, IndexConstituent.query.filter_by(is_active=True).count()
            except ValueError:
                pass

        data = self.fetch_constituents()
        scraped_total = sum(len(v) for v in data.values())

        if scraped_total == 0:
            # Repli : ne peuple que si la base est vide (sinon on garde l'existant)
            if IndexConstituent.query.count() == 0:
                from config import get_config
                panel = getattr(get_config(), 'DEFAULT_PANEL', [])
                for sym in panel:
                    db.session.add(IndexConstituent(
                        ticker=self._norm_symbol(sym), index_name='SP500', is_active=True))
                db.session.commit()
                logger.warning('Repli composition : DEFAULT_PANEL (%d tickers)', len(panel))
                return True, len(panel)
            return False, IndexConstituent.query.filter_by(is_active=True).count()

        now = datetime.utcnow()
        seen = set()
        for index_name, members in data.items():
            existing = {c.ticker: c for c in IndexConstituent.query.filter_by(index_name=index_name).all()}
            for sym, name in members:
                seen.add((sym, index_name))
                row = existing.get(sym)
                if row:
                    row.is_active = True
                    row.last_seen_at = now
                    if name:
                        row.name = name[:120]
                else:
                    db.session.add(IndexConstituent(
                        ticker=sym, index_name=index_name,
                        name=(name[:120] if name else None), is_active=True, last_seen_at=now))
            # Désactive les titres scrapés précédemment mais absents cette fois
            for sym, row in existing.items():
                if (sym, index_name) not in seen and row.is_active:
                    row.is_active = False
        db.session.commit()
        Settings.set('constituents_refreshed_at', now.isoformat())
        active = IndexConstituent.query.filter_by(is_active=True).count()
        logger.info('Composition rafraîchie : %d actifs', active)
        # Reconstruit l'historique point-in-time dans la foulée (non bloquant)
        try:
            self.rebuild_membership_history()
        except Exception as e:
            logger.warning('rebuild_membership_history échoué : %s', e)
        return True, active

    # =====================================================================
    # HISTORIQUE POINT-IN-TIME DES CONSTITUANTS (biais de survivance)
    # =====================================================================
    def _fetch_sp500_changes(self):
        """
        Scrape la table « Selected changes » de la page Wikipédia du S&P 500
        (ajouts/retraits datés, remonte aux années 90). Renvoie une liste
        d'événements [(date, ticker, 'add'|'remove')] triée par date.
        """
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        r = requests.get(url, headers={'User-Agent': _UA}, timeout=25)
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        for t in tables:
            events = self._parse_changes_table(t)
            if events:
                return events
        raise ValueError('Table « Selected changes » introuvable sur la page S&P 500')

    @classmethod
    def _parse_changes_table(cls, df):
        """
        Parse un DataFrame candidat de la table « Selected changes » (colonnes
        MultiIndex : Date / Added Ticker / Removed Ticker). Renvoie la liste
        d'événements [(date, ticker, 'add'|'remove')] triée, ou [] si le
        DataFrame n'a pas la structure attendue.
        """
        def _flat(c):
            if isinstance(c, tuple):
                parts = [str(x) for x in c if str(x).lower() != 'nan']
                return ' '.join(parts).strip().lower()
            return str(c).strip().lower()

        cols = [_flat(c) for c in df.columns]

        def _find(*words):
            for i, c in enumerate(cols):
                if all(w in c for w in words):
                    return i
            return None

        i_date = _find('date')
        i_add = _find('added', 'ticker')
        i_rem = _find('removed', 'ticker')
        if i_date is None or i_add is None or i_rem is None:
            return []

        events = []
        for _, row in df.iterrows():
            d = pd.to_datetime(row.iloc[i_date], errors='coerce')
            if pd.isna(d):
                continue
            d = d.date()
            for idx, action in ((i_add, 'add'), (i_rem, 'remove')):
                sym = row.iloc[idx]
                if pd.isna(sym):
                    continue
                sym = cls._norm_symbol(sym)
                if sym and sym != 'NAN':
                    events.append((d, sym, action))
        events.sort(key=lambda e: e[0])
        return events

    @staticmethod
    def build_membership_intervals(events, current_members):
        """
        Reconstruit les intervalles d'appartenance [start, end) par ticker.

        events          : [(date, ticker, 'add'|'remove')] (tri quelconque)
        current_members : set des tickers actuellement dans l'indice

        Renvoie {ticker: [(start|None, end|None), ...]} —
        start None = membre depuis avant l'historique ; end None = encore membre.
        Réconcilie avec la composition actuelle : un membre actuel sans événement
        est membre « depuis toujours » ; un membre actuel dont le dernier
        intervalle est fermé est rouvert (ré-entrée non tracée) ; un non-membre
        avec intervalle ouvert est fermé à aujourd'hui (sortie non encore tracée).
        """
        by_ticker = {}
        for d, sym, action in sorted(events, key=lambda e: e[0]):
            by_ticker.setdefault(sym, []).append((d, action))

        intervals = {}
        for sym, evs in by_ticker.items():
            ivs, open_start, has_open = [], None, False
            for d, action in evs:
                if action == 'add':
                    if has_open:
                        continue  # double ajout (bruit) → on garde l'intervalle ouvert
                    open_start, has_open = d, True
                else:
                    if has_open:
                        ivs.append((open_start, d))
                        has_open = False
                    else:
                        # retiré sans ajout connu → membre depuis avant l'historique
                        ivs.append((None, d))
            if has_open:
                ivs.append((open_start, None))
            intervals[sym] = ivs

        for sym in current_members:
            ivs = intervals.get(sym)
            if not ivs:
                intervals[sym] = [(None, None)]  # aucun événement → depuis toujours
            elif ivs[-1][1] is not None:
                # membre actuel mais dernier intervalle fermé → ré-entrée non
                # tracée ; on rouvre prudemment à la date de la dernière sortie
                ivs.append((ivs[-1][1], None))

        today = date.today()
        for sym, ivs in intervals.items():
            if sym not in current_members and ivs and ivs[-1][1] is None:
                ivs[-1] = (ivs[-1][0], today)
        return intervals

    def rebuild_membership_history(self):
        """
        Reconstruit la table IndexMembership (delete + insert, idempotent) :
          - S&P 500 : intervalles point-in-time depuis « Selected changes » ;
          - Nasdaq-100 : pas d'historique exploitable sur Wikipédia → membres
            actuels en intervalle ouvert (limitation : le biais de survivance
            n'est pas corrigé pour les titres NDX100 jamais passés par le S&P 500).
        Renvoie un résumé {tickers, intervals, events, earliest_event}.
        """
        from models import db, IndexMembership, IndexConstituent

        events = self._fetch_sp500_changes()
        current_sp = {c.ticker for c in IndexConstituent.query
                      .filter_by(index_name='SP500', is_active=True).all()}
        current_ndx = {c.ticker for c in IndexConstituent.query
                       .filter_by(index_name='NDX100', is_active=True).all()}
        if not current_sp:
            # Base vide (1er lancement / dev) : scrape direct de la compo actuelle.
            # Sans elle, la réconciliation fermerait à tort les intervalles des
            # membres actuels et ignorerait ceux sans événement (AAPL, NVDA…).
            data = self.fetch_constituents()
            current_sp = {sym for sym, _ in data.get('SP500', [])}
            if not current_ndx:
                current_ndx = {sym for sym, _ in data.get('NDX100', [])}
        if not current_sp:
            raise ValueError('Composition actuelle du S&P 500 indisponible — rebuild annulé')
        intervals = self.build_membership_intervals(events, current_sp)

        rows = [IndexMembership(ticker=sym, index_name='SP500',
                                start_date=s, end_date=e, source='wikipedia')
                for sym, ivs in intervals.items() for s, e in ivs]
        for sym in current_ndx:
            rows.append(IndexMembership(ticker=sym, index_name='NDX100',
                                        start_date=None, end_date=None, source='current'))

        IndexMembership.query.delete()
        db.session.add_all(rows)
        db.session.commit()

        summary = {
            'tickers': len({r.ticker for r in rows}),
            'intervals': len(rows),
            'events': len(events),
            'earliest_event': events[0][0].isoformat() if events else None,
        }
        logger.info('Historique d\'appartenance reconstruit : %s', summary)
        return summary

    def target_tickers(self):
        """Liste dédupliquée des tickers à collecter : constituants actifs + benchmarks."""
        from models import IndexConstituent
        rows = IndexConstituent.query.filter_by(is_active=True).all()
        tickers = {r.ticker for r in rows}
        tickers.update(BENCHMARKS)
        return sorted(tickers)

    def former_member_tickers(self):
        """
        Tickers passés par un indice mais plus membres aujourd'hui. Collectés en
        plus de target_tickers() pour donner au backtest point-in-time
        l'historique des titres SORTIS de l'indice (réduction du biais de
        survivance). Beaucoup sont délistés → yfinance ne renverra rien :
        toléré, et exclu du calcul d'alerte d'échec.

        « Membre actuel » est déduit d'IndexMembership même (intervalle ouvert,
        end_date NULL) et non d'IndexConstituent : reste correct si la table des
        constituants n'a pas encore été peuplée (1er lancement / dev).
        """
        from models import IndexMembership
        rows = IndexMembership.query.all()
        current = {r.ticker for r in rows if r.end_date is None}
        return sorted({r.ticker for r in rows} - current)

    # =====================================================================
    # COLLECTE DES PRIX
    # =====================================================================
    @staticmethod
    def _last_dates(model, tickers, source=None):
        """{ticker: max(bar_date)} pour les tickers déjà présents (1 requête groupée)."""
        from models import db
        from sqlalchemy import func
        q = db.session.query(model.ticker, func.max(model.bar_date)).filter(model.ticker.in_(tickers))
        if source is not None:
            q = q.filter(model.source == source)
        return {t: d for t, d in q.group_by(model.ticker).all() if d is not None}

    @staticmethod
    def _df_to_bars(sub, monthly=False):
        """Sous-DataFrame yfinance (Adj Close/Close/High/Low/Volume) → liste de barres."""
        if sub is None or sub.empty:
            return []
        adj_col = 'Adj Close' if 'Adj Close' in sub.columns else 'Close'
        bars = []
        for idx, row in sub.iterrows():
            close = row.get('Close')
            adj = row.get(adj_col)
            if pd.isna(adj) and pd.isna(close):
                continue
            adj = float(adj) if not pd.isna(adj) else float(close)
            close = float(close) if not pd.isna(close) else adj
            vol = row.get('Volume')
            vol = float(vol) if vol is not None and not pd.isna(vol) else None
            # Low/High uniquement pour le daily (n'ont pas de sens sur un mois entier)
            low_v = high_v = None
            if not monthly:
                lv = row.get('Low')
                hv = row.get('High')
                low_v = float(lv) if lv is not None and not pd.isna(lv) else None
                high_v = float(hv) if hv is not None and not pd.isna(hv) else None
            d = idx.date() if hasattr(idx, 'date') else idx
            if monthly:
                d = d.replace(day=1)  # clé de mois stable
            bars.append({'date': d.isoformat(), 'adj_close': adj, 'close': close,
                         'volume': vol, 'low': low_v, 'high': high_v})
        return bars

    @staticmethod
    def _upsert(model, ticker, bars):
        """Upsert idempotent (ticker, bar_date) dans `model`. Renvoie le nb de barres écrites."""
        from models import db
        if not bars:
            return 0
        ticker = ticker.upper()
        existing = {r.bar_date.isoformat(): r
                    for r in model.query.filter_by(ticker=ticker).all()}
        has_low = hasattr(model, 'low')   # MonthlyPriceBar n'a pas low/high
        written = 0
        for b in bars:
            d = b['date'][:10]
            bd = date.fromisoformat(d)
            row = existing.get(d)
            if row:
                row.adj_close = b['adj_close']
                row.close = b['close']
                if b.get('volume') is not None:
                    row.volume = b['volume']
                if has_low:
                    if b.get('low') is not None:
                        row.low = b['low']
                    if b.get('high') is not None:
                        row.high = b['high']
                row.source = 'yfinance'
            else:
                kwargs = dict(ticker=ticker, bar_date=bd, adj_close=b['adj_close'],
                              close=b['close'], volume=b.get('volume'), source='yfinance')
                if has_low:
                    kwargs['low'] = b.get('low')
                    kwargs['high'] = b.get('high')
                db.session.add(model(**kwargs))
            written += 1
        db.session.commit()
        return written

    def _download_chunk(self, chunk, start, interval):
        """Télécharge un lot via yfinance. Renvoie {ticker: sous-DataFrame} (peut être vide)."""
        import yfinance as yf
        try:
            df = yf.download(chunk, start=start.isoformat(), interval=interval,
                             auto_adjust=False, group_by='ticker', threads=True,
                             progress=False)
        except Exception as e:
            logger.warning('yf.download lot échoué (%s…) : %s', chunk[:3], e)
            return {}
        out = {}
        if df is None or df.empty:
            return out
        level0 = set(df.columns.get_level_values(0)) if hasattr(df.columns, 'get_level_values') else set()
        for t in chunk:
            try:
                out[t] = df[t] if t in level0 else None
            except Exception as e:
                logger.warning('Extraction ticker %s depuis DataFrame yfinance : %s', t, e)
                out[t] = None
        return out

    def _collect_interval(self, tickers, model, interval, full, phase_label):
        """
        Collecte un intervalle ('1mo' ou '1d') dans `model`, en incrémental.
        Renvoie un dict de stats {total, ok, new_bars, failed:[...]}.
        Un ticker est 'failed' si, après collecte, il n'a TOUJOURS aucune barre.
        """
        monthly = interval == '1mo'
        years = self.MONTHLY_YEARS if monthly else self.DAILY_YEARS
        buffer_days = self.INCREMENTAL_BUFFER_M if monthly else self.INCREMENTAL_BUFFER_D
        full_start = date.today() - timedelta(days=int(years * 365.25))

        last_dates = {} if full else self._last_dates(model, tickers)
        had_data = set(last_dates.keys())

        total = len(tickers)
        ok, new_bars, failed = set(), 0, []
        done = 0

        for i in range(0, total, self.CHUNK_SIZE):
            chunk = tickers[i:i + self.CHUNK_SIZE]
            # start commun au lot : la plus ancienne « dernière barre » du lot (avec marge),
            # sinon historique complet pour les tickers neufs.
            chunk_last = [last_dates[t] for t in chunk if t in last_dates]
            if chunk_last and len(chunk_last) == len(chunk):
                start = min(chunk_last) - timedelta(days=buffer_days)
            else:
                start = full_start  # ≥1 ticker neuf → on remonte loin (upsert dédoublonne)

            data = self._download_chunk(chunk, start, interval)
            for t in chunk:
                bars = self._df_to_bars(data.get(t), monthly=monthly)
                if bars:
                    try:
                        new_bars += self._upsert(model, t, bars)
                        ok.add(t)
                    except Exception as e:
                        logger.warning('Upsert %s (%s) : %s', t, interval, e)
                elif t in had_data:
                    ok.add(t)  # déjà en base, simplement rien de neuf
            done += len(chunk)
            self._set_phase(phase_label, done=done, total=total)
            if i + self.CHUNK_SIZE < total:
                time.sleep(self.SLEEP_BETWEEN)

        # Échecs = tickers sans aucune barre en base au final
        present = self._last_dates(model, tickers)
        failed = [t for t in tickers if t not in present]
        return {'total': total, 'ok': len(tickers) - len(failed),
                'new_bars': new_bars, 'failed': failed}

    def collect(self, full=False, progress=None):
        """
        Point d'entrée synchrone de la collecte (monthly + daily). Renvoie un dict
        de stats. Doit être appelé dans un contexte d'application Flask.
        """
        from models import MonthlyPriceBar, MarketPriceBar
        t0 = time.time()

        self._set_phase('constituents')
        try:
            refreshed, n_const = self.refresh_constituents()
        except Exception as e:
            logger.warning('refresh_constituents : %s', e)
            refreshed, n_const = False, 0

        tickers = self.target_tickers()
        if not tickers:
            return {'success': False, 'error': 'Aucun ticker à collecter'}

        monthly_stats = self._collect_interval(
            tickers, MonthlyPriceBar, '1mo', full, 'monthly')
        daily_stats = self._collect_interval(
            tickers, MarketPriceBar, '1d', full, 'daily')

        # Anciens membres d'indices (backtest point-in-time) — collectés à part :
        # leurs échecs (titres délistés, symboles disparus) ne déclenchent PAS
        # l'alerte. En incrémental, on ne retente que ceux qui ont déjà des barres
        # (les morts ne sont re-tentés qu'en collecte complète full=True).
        former_summary = None
        try:
            former = self.former_member_tickers()
            if former:
                if full:
                    former_m_list = former_d_list = former
                else:
                    former_m_list = sorted(self._last_dates(MonthlyPriceBar, former))
                    former_d_list = sorted(self._last_dates(MarketPriceBar, former))
                fm = self._collect_interval(
                    former_m_list, MonthlyPriceBar, '1mo', full, 'monthly_former') \
                    if former_m_list else None
                fd = self._collect_interval(
                    former_d_list, MarketPriceBar, '1d', full, 'daily_former') \
                    if former_d_list else None
                former_summary = {
                    'candidates': len(former),
                    'monthly': fm,
                    'daily': fd,
                }
        except Exception as e:
            logger.warning('Collecte anciens membres : %s', e)

        # Taux d'échec (sur la base mensuelle = source de vérité du momentum)
        n = len(tickers)
        fail_ratio_m = len(monthly_stats['failed']) / n if n else 0.0
        fail_ratio_d = len(daily_stats['failed']) / n if n else 0.0
        alert = max(fail_ratio_m, fail_ratio_d) >= self.FAILURE_ALERT_RATIO

        summary = {
            'success': True,
            'tickers': n,
            'constituents_refreshed': refreshed,
            'monthly': monthly_stats,
            'daily': daily_stats,
            'former_members': former_summary,
            'fail_ratio_monthly': round(fail_ratio_m, 3),
            'fail_ratio_daily': round(fail_ratio_d, 3),
            'alert_sent': False,
            'elapsed_s': round(time.time() - t0, 1),
            'finished_at': datetime.utcnow().isoformat(),
        }

        # Alerte email si ≥ 25 % d'échecs
        if alert and self.email_service is not None:
            try:
                if self.email_service.is_configured():
                    res = self.email_service.envoyer_echec_collecte(summary)
                    summary['alert_sent'] = bool(res.get('success'))
            except Exception as e:
                logger.warning('Email échec collecte : %s', e)

        return summary

    # =====================================================================
    # EXÉCUTION EN ARRIÈRE-PLAN (bouton UI + cron)
    # =====================================================================
    def run_background(self, app, full=False):
        """
        Lance la collecte dans un thread démon. Renvoie False si déjà en cours.
        `app` = instance Flask (pour le contexte dans le thread).
        """
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
                    summary = self.collect(full=full)
                    self._set_state(summary=summary, phase='done')
                except Exception as e:
                    logger.exception('Collecte prix échouée')
                    self._set_state(error=str(e), phase='error')
                finally:
                    self._set_state(running=False,
                                    finished_at=datetime.utcnow().isoformat())

        threading.Thread(target=_worker, name='price-collect', daemon=True).start()
        return True

    def coverage(self):
        """Statistiques de couverture de la base (pour l'UI)."""
        from models import db, MonthlyPriceBar, MarketPriceBar, IndexConstituent
        from sqlalchemy import func

        def _stats(model, source=None):
            q = db.session.query(
                func.count(func.distinct(model.ticker)),
                func.min(model.bar_date), func.max(model.bar_date),
                func.count(model.id))
            if source is not None:
                q = q.filter(model.source == source)
            tk, mn, mx, rows = q.one()
            return {'tickers': tk or 0,
                    'start': mn.isoformat() if mn else None,
                    'end': mx.isoformat() if mx else None,
                    'rows': rows or 0}

        from models import Settings
        return {
            'constituents': {
                'sp500': IndexConstituent.query.filter_by(index_name='SP500', is_active=True).count(),
                'ndx100': IndexConstituent.query.filter_by(index_name='NDX100', is_active=True).count(),
                'refreshed_at': Settings.get('constituents_refreshed_at', None),
            },
            'monthly': _stats(MonthlyPriceBar),
            'daily': _stats(MarketPriceBar, source='yfinance'),
        }
