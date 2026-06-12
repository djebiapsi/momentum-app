# -*- coding: utf-8 -*-
"""
Service de backtest de la stratégie momentum 12-1
=================================================
Rejoue la stratégie sur l'historique avec **intérêt composé** et la compare à un
benchmark (SPY).

Spécificités (validées avec l'utilisateur) :
  - **Méthode identique à `calculate`** : le momentum 12-1 est classé sur TOUT
    l'univers disponible (constituants S&P 500 + Nasdaq-100 collectés), sans aucun
    screener — ni cap top-N, ni filtre de liquidité ADV. Un titre entre point-in-time
    dès qu'il a ≥13 mois d'historique, et sort si son momentum passe ≤ 0.
  - **Config live** : `nb_top`, vol_scaling, frein anti-krach lus dans les `Settings`
    et appliqués (formules de `momentum_service.generer_recommandations` recalculées
    point-in-time, sans appel API « now »).

Moteur de simulation **réaliste, jour par jour** (inspiré des bonnes pratiques de
backtesting — Bailey & López de Prado, Frazzini-Israel-Moskowitz « Trading Costs »,
Harvey & Liu) :
  - **coûts de transaction** sur le turnover (commission + spread, en bps) ;
  - **intérêts d'emprunt** sur la partie financée quand on est à levier (cash < 0),
    et **rémunération** du cash oisif ;
  - **apports périodiques (DCA)** optionnels, avec séparation propre **TWR**
    (performance de la stratégie, base des ratios) / **MWR/XIRR** (expérience de
    l'investisseur qui verse régulièrement) ;
  - **appels de marge** : si, sur une clôture, `equity < taux_maintenance × exposition
    brute`, liquidation forcée (désendettement) avec coûts — exactement le scénario
    d'un mois où l'on est surexposé et où le marché décroche ;
  - **risk-off = cash** : un mois sans momentum positif passe réellement en liquidités.

Métriques pro via **quantstats** + calculs maison (VaR/CVaR, Omega, Ulcer, durées de
drawdown, levier moyen, coûts payés…). Import paresseux de quantstats (matplotlib).

Limites assumées (affichées dans l'UI) : biais de survivance résiduel (univers
candidat = constituants existant aujourd'hui), plancher de liquidité (pas fondamental),
appel de marge évalué sur cours de clôture (pas de plus-bas intra-séance), 1ʳᵉ
exécution lente (fetch Tiingo). Nécessite `TIINGO_API_KEY`.
"""

import math
import logging
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

from cache_utils import TTLCache
from screener_service import ScreenerService

logger = logging.getLogger(__name__)

# Import paresseux de quantstats : il tire matplotlib (lourd). On l'importe au
# premier calcul de stats — ainsi un éventuel souci d'import ne casse PAS le
# démarrage de l'app, seulement la feature backtest.
_qs = None


def _get_qs():
    global _qs
    if _qs is None:
        import matplotlib
        matplotlib.use('Agg')
        import quantstats as qs
        _qs = qs
    return _qs

TRADING_DAYS = 252


class BacktestService:
    # Garde-fous / paramètres de la stratégie
    POOL_SIZE = 150            # taille du pool candidat (borne le coût data)
    MIN_ADV = 5_000_000        # plancher ADV — UNIQUEMENT pour le pool de repli Tiingo IEX
                               # (build_candidate_pool) ; PAS de filtre liquidité au backtest
    VOL_WINDOW = 126           # fenêtre vol réalisée (= live, 126 j)
    MOM_MIN_MONTHS = 13        # mois nécessaires au momentum 12-1
    VOL_DEFAULT = 0.20         # vol par défaut si données insuffisantes (= live)
    MAX_ONDEMAND_FETCH = 40    # tickers récupérés au réseau par exécution (borne le temps)

    # --- Hypothèses de réalisme (défauts ; surchargés par l'UI) -------------
    DEFAULT_TX_COST_BPS = 5.0          # coût aller (commission+spread) sur |Δnotionnel|, en points de base
    DEFAULT_MARGIN_RATE_PCT = 6.5      # taux d'emprunt annuel sur la partie financée (cash < 0)
    DEFAULT_CASH_YIELD_PCT = 0.0       # rémunération annuelle du cash oisif (conservateur)
    DEFAULT_MAINT_MARGIN_PCT = 25.0    # marge de maintenance Reg T (appel de marge sous ce seuil)
    DEFAULT_POST_CALL_LEVERAGE = 1.0   # levier cible après liquidation forcée (1.0 = désendettement total)

    def __init__(self, momentum_service, screener_service):
        self.ms = momentum_service
        self.ss = screener_service
        # Cache mémoire court (dédoublonne les fetches réseau dans une même exécution).
        self._hist_cache = TTLCache(ttl_seconds=12 * 3600)

    # Seuil d'historique minimum pour figurer dans le pool du backtest
    DB_MIN_BARS = 200           # ~1 an de daily minimum (filtre les tickers trop récents)

    # ------------------------------------------------------------------
    # 1) Pool candidat (univers de départ)
    # ------------------------------------------------------------------
    def build_candidate_pool(self, pool_size=None):
        """
        Construit le pool candidat pour le backtest.

        Priorité :
          1. Tickers de la DB yfinance (SP500 + Nasdaq-100 collectés nuitamment)
             → univers le plus large possible (500+ tickers) sans cap arbitraire.
             On garde tous les symboles US valides ayant assez de barres daily.
          2. Tiingo IEX bulk (si pool DB insuffisant) — top-N par ADV.
          3. DB générique (repli ultime).

        Le biais de survivant reste présent (on collecte les constituants actuels),
        mais l'univers est bien plus large qu'avant (120 → 500+ tickers).
        Aucune sélection ADV ensuite : le momentum se classe sur tout le pool, comme
        le calcul live (build_weight_matrix).
        """
        # 1) Priorité : DB yfinance (constituants SP500/NDX100 collectés)
        pool = self._pool_from_yfinance_db()
        if pool and len(pool) >= 50:
            logger.info('Pool backtest depuis DB yfinance : %d tickers', len(pool))
            return pool

        # 2) Fallback Tiingo IEX (si DB pas encore remplie)
        pool_size = pool_size or self.POOL_SIZE
        if self.ss is not None:
            data, err = self.ss.get_iex_bulk_data()
            if data and not err:
                rows = [
                    (t, d['adv']) for t, d in data.items()
                    if d.get('adv', 0) >= self.MIN_ADV and ScreenerService._is_valid_us_symbol(t)
                ]
                rows.sort(key=lambda x: x[1], reverse=True)
                pool = [t for t, _ in rows[:pool_size]]
                if pool:
                    logger.info('Pool backtest depuis Tiingo IEX : %d tickers', len(pool))
                    return pool
            logger.warning('build_candidate_pool IEX indispo (%s) — repli cache DB', err)

        # 3) Repli générique DB (tickers les plus couverts, sans filtre source)
        return self._pool_from_db(pool_size)

    def _pool_from_yfinance_db(self):
        """
        Pool depuis les données yfinance en base (MarketPriceBar source='yfinance').
        Retourne tous les symboles US valides avec suffisamment de barres daily —
        c'est l'univers complet sur lequel le momentum sera classé (aucun filtre ADV).
        """
        try:
            from models import MarketPriceBar, db
            from sqlalchemy import func
            rows = (db.session.query(MarketPriceBar.ticker,
                                     func.count(MarketPriceBar.id).label('n'))
                    .filter(MarketPriceBar.source == 'yfinance')
                    .group_by(MarketPriceBar.ticker)
                    .having(func.count(MarketPriceBar.id) >= self.DB_MIN_BARS)
                    .all())
            pool = [r.ticker for r in rows if ScreenerService._is_valid_us_symbol(r.ticker)]
            return pool
        except Exception as e:
            logger.warning('_pool_from_yfinance_db échec : %s', e)
            return []

    def _pool_from_db(self, pool_size):
        """Pool candidat générique depuis le cache DB — repli ultime."""
        try:
            from models import MarketPriceBar, db
            from sqlalchemy import func
            rows = (db.session.query(MarketPriceBar.ticker,
                                     func.count(MarketPriceBar.id).label('n'))
                    .group_by(MarketPriceBar.ticker)
                    .order_by(func.count(MarketPriceBar.id).desc())
                    .limit(pool_size * 3).all())
            pool = [r.ticker for r in rows
                    if ScreenerService._is_valid_us_symbol(r.ticker)][:pool_size]
            if pool:
                logger.info('Pool candidat depuis cache DB générique : %d tickers', len(pool))
                return pool
        except Exception as e:
            logger.warning('_pool_from_db échec : %s', e)
        raise RuntimeError(
            "Univers indisponible : API IEX rate-limitée et cache DB vide. "
            "Lance la collecte yfinance (bouton Config) puis réessaie."
        )

    # ------------------------------------------------------------------
    # 2) Récupération de l'historique : cache DB d'abord, IBKR/Tiingo pour les trous
    # ------------------------------------------------------------------
    @staticmethod
    def _bars_to_df(bars):
        """Liste de barres {date, adj_close, close, volume} → DataFrame indexé par date."""
        df = pd.DataFrame([{
            'date': b['date'], 'adjClose': b['adj_close'],
            'close': b.get('close', b['adj_close']), 'volume': b.get('volume'),
        } for b in bars])
        df['date'] = pd.to_datetime(df['date'])
        return df.set_index('date').sort_index()

    @staticmethod
    def _earliest_daily_date():
        """
        Date de la plus ancienne barre daily yfinance en base (profondeur réelle du
        cache de simulation). Sert à clamper la période d'un backtest 20-30 ans tant
        que le daily n'a pas été backfillé en profondeur. None si indisponible.
        """
        try:
            from models import MarketPriceBar, db
            from sqlalchemy import func
            d = (db.session.query(func.min(MarketPriceBar.bar_date))
                 .filter(MarketPriceBar.source == 'yfinance').scalar())
            return d
        except Exception as e:
            logger.warning('_earliest_daily_date : %s', e)
            return None

    def _load_db(self, ticker, start_date):
        """Charge adjClose/close/volume/low/high depuis MarketPriceBar (≥ start_date)."""
        try:
            from models import MarketPriceBar
            rows = (MarketPriceBar.query.filter_by(ticker=ticker.upper())
                    .filter(MarketPriceBar.bar_date >= start_date)
                    .order_by(MarketPriceBar.bar_date).all())
            if not rows:
                return None
            df = pd.DataFrame([{'date': r.bar_date, 'adjClose': r.adj_close,
                                 'close': r.close, 'volume': r.volume,
                                 'low': r.low, 'high': r.high} for r in rows])
            df['date'] = pd.to_datetime(df['date'])
            return df.set_index('date').sort_index()
        except Exception as e:
            logger.warning('_load_db %s : %s', ticker, e)
            return None

    def _db_covers(self, df, start_date):
        """Vrai si la DB couvre ~start_date ET contient du volume exploitable."""
        if df is None or df.empty:
            return False
        if df.index.min().date() > start_date + timedelta(days=20):
            return False  # historique trop court pour la période demandée
        return bool(df['volume'].notna().any())

    def _fetch_ticker(self, ticker, nb_jours):
        """
        Récupère + persiste l'historique d'un ticker (avec volume) :
        IBKR d'abord (pas de quota, paced), sinon Tiingo en repli. Returns (df|None, err).
        """
        key = f"{ticker.upper()}:{nb_jours}"
        cached, hit = self._hist_cache.get(key)
        if hit:
            return cached

        out, err = None, None
        # 1) IBKR (pas de quota mensuel ; le throttle de pacing est géré par le service)
        ib = getattr(self.ms, 'ibkr_service', None)
        if ib is not None:
            try:
                if ib.ensure_connected():
                    duration = self.ms._jours_to_ib_duration(nb_jours + 45)
                    bars = ib.get_daily_bars(ticker, duration=duration)
                    if bars:
                        self.ms._save_bars_to_db(ticker, bars, source='ibkr')
                        out = self._bars_to_df(bars)
            except Exception as e:
                logger.info('IBKR indispo pour %s (%s) — repli Tiingo', ticker, e)

        # 2) Tiingo (repli ; fournit aussi le volume) — persiste avec volume
        if out is None:
            df, err = self.ms._fetch_daily_tiingo(ticker, nb_jours)
            if df is not None and not df.empty and 'adjClose' in df.columns:
                cols = [c for c in ('adjClose', 'close', 'volume') if c in df.columns]
                out = df[cols].copy()
                if getattr(out.index, 'tz', None) is not None:
                    out.index = out.index.tz_localize(None)
                try:
                    bars = [{
                        'date': idx.strftime('%Y-%m-%d'),
                        'adj_close': float(r['adjClose']),
                        'close': float(r.get('close', r['adjClose'])),
                        'volume': float(r['volume']) if r.get('volume') is not None else None,
                    } for idx, r in out.iterrows()]
                    self.ms._save_bars_to_db(ticker, bars, source='tiingo')
                except Exception as e:
                    logger.warning('Persistance Tiingo %s : %s', ticker, e)

        result = (out, None if out is not None else (err or 'pas de données'))
        self._hist_cache.set(key, result)
        return result

    def fetch_history(self, tickers, nb_jours, start_date, progress=None, max_fetch=None):
        """
        Construit close_px (prix ajusté) + dvol (volume $) + low_px (plus-bas intraday).
        Lit d'abord le cache DB (instantané, 0 API) ; ne récupère au réseau que les
        tickers manquants, borné à `max_fetch`.
        Returns (close_px, dvol, low_px, meta).
        """
        max_fetch = self.MAX_ONDEMAND_FETCH if max_fetch is None else max_fetch
        closes, dollar_vol, lows, missing = {}, {}, {}, []

        def _add(t, df):
            closes[t] = df['adjClose']
            if 'close' in df.columns and 'volume' in df.columns:
                dollar_vol[t] = (df['close'].astype(float) * df['volume'].astype(float))
            if 'low' in df.columns:
                low_series = df['low'].astype(float)
                if low_series.notna().any():
                    lows[t] = low_series

        # Passe 1 : cache DB
        for t in tickers:
            df = self._load_db(t, start_date)
            if self._db_covers(df, start_date):
                _add(t, df)
            else:
                missing.append(t)

        # Passe 2 : récupération réseau des trous, capée
        fetched = 0
        for t in missing:
            if fetched >= max_fetch:
                break
            df, _ = self._fetch_ticker(t, nb_jours)
            if df is None or 'adjClose' not in df.columns:
                continue
            _add(t, df)
            fetched += 1
            if progress:
                progress(fetched, min(len(missing), max_fetch))

        close_px = pd.DataFrame(closes).sort_index()
        dvol = pd.DataFrame(dollar_vol).sort_index()
        low_px = pd.DataFrame(lows).sort_index() if lows else None
        meta = {'from_db': len(tickers) - len(missing), 'fetched': fetched,
                'skipped': max(0, len(missing) - fetched)}
        return close_px, dvol, low_px, meta

    def _load_monthly_px(self, tickers, since_date):
        """
        Charge les prix mensuels depuis MonthlyPriceBar (yfinance 20 ans) pour tous
        les tickers du pool. Retourne un DataFrame pivot (date × ticker, adjClose).
        Utilisé pour le calcul du momentum dans le backtest, afin d'avoir un lookback
        suffisant même quand le daily ne couvre que 6 ans.
        """
        try:
            from models import MonthlyPriceBar, db
            upper = [t.upper() for t in tickers]
            rows = (db.session.query(
                        MonthlyPriceBar.ticker,
                        MonthlyPriceBar.bar_date,
                        MonthlyPriceBar.adj_close)
                    .filter(MonthlyPriceBar.ticker.in_(upper))
                    .filter(MonthlyPriceBar.bar_date >= since_date)
                    .all())
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=['ticker', 'date', 'adjClose'])
            df['date'] = pd.to_datetime(df['date'])
            pivot = df.pivot(index='date', columns='ticker', values='adjClose')
            pivot.columns.name = None
            return pivot.sort_index()
        except Exception as e:
            logger.warning('_load_monthly_px échec : %s', e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Univers point-in-time (IndexMembership — réduction du biais de survivance)
    # ------------------------------------------------------------------
    def _load_membership(self, tickers):
        """
        Charge les intervalles d'appartenance aux indices (IndexMembership) pour
        le filtre point-in-time : {ticker: [(start_ts|None, end_ts|None), ...]}.
        Union sur les indices (membre du S&P 500 OU du Nasdaq-100 → éligible).
        Renvoie None si la table est vide (pas encore reconstruite) → aucun
        filtrage, comportement historique conservé.
        """
        try:
            from models import IndexMembership
            upper = [t.upper() for t in tickers]
            rows = IndexMembership.query.filter(IndexMembership.ticker.in_(upper)).all()
            if not rows:
                if IndexMembership.query.count() == 0:
                    logger.info('IndexMembership vide — filtre point-in-time désactivé')
                    return None
                # Table peuplée mais aucun ticker du pool dedans : on filtre quand
                # même (les absents sont traités comme membres, cf. _member_at).
            membership = {}
            for r in rows:
                s = pd.Timestamp(r.start_date) if r.start_date else None
                e = pd.Timestamp(r.end_date) if r.end_date else None
                membership.setdefault(r.ticker, []).append((s, e))
            return membership
        except Exception as e:
            logger.warning('_load_membership échec : %s — filtre désactivé', e)
            return None

    @staticmethod
    def _member_at(membership, ticker, as_of):
        """
        Vrai si `ticker` appartient à un indice à la date `as_of` (intervalle
        [start, end)). Un ticker absent de la table n'est PAS exclu (benchmarks,
        données manquantes) — le filtre ne doit jamais créer de faux négatifs.
        """
        ivs = membership.get(ticker)
        if ivs is None:
            return True
        for s, e in ivs:
            if (s is None or s <= as_of) and (e is None or as_of < e):
                return True
        return False

    # ------------------------------------------------------------------
    # Optimisation des paramètres (grid search vol_target × max_exposure)
    # ------------------------------------------------------------------
    def optimize(self, years=10, nb_top=5, capital=10000.0, quick=False,
                 progress_cb=None):
        """
        Grid search sur vol_target_pct × max_exposure_pct (vol_scaling=True).
        Les données sont chargées une seule fois ; seuls build_weight_matrix +
        _simulate tournent pour chaque combinaison.

        Critères de sélection :
          - max_drawdown ≥ -30 % (filtre dur)
          - tri principal : Sharpe ↓
          - départage : CAGR ↓

        progress_cb(done, total, label) appelé à chaque combo.
        Retourne une liste de dicts JSON-sérialisables triée (éligibles d'abord).
        """
        import math as _math

        GRID = {
            'vol_target_pct':   [10, 15, 20] if quick else [8, 10, 12, 15, 18, 20, 25],
            'max_exposure_pct': [100, 150, 250] if quick else [100, 125, 150, 175, 200, 250, 300],
        }
        MAX_DD_LIMIT = -0.40

        # ── helpers stats (sans quantstats pour la vitesse) ──────────────
        def _cagr(r):
            if r.empty: return None
            n = len(r) / TRADING_DAYS
            t = float((1 + r).prod())
            return (t ** (1 / n) - 1) * 100 if t > 0 and n > 0 else None

        def _sharpe(r):
            s = float(r.std(ddof=1))
            return float(r.mean()) / s * _math.sqrt(TRADING_DAYS) if s > 1e-9 else None

        def _sortino(r):
            dn = r[r < 0]
            s = float(dn.std(ddof=1)) if len(dn) > 1 else 1e-9
            return float(r.mean()) / s * _math.sqrt(TRADING_DAYS) if s > 1e-9 else None

        def _maxdd(r):
            eq = (1 + r).cumprod()
            return float((eq / eq.cummax() - 1).min())

        # ── chargement des données (1 seule fois) ────────────────────────
        end = pd.Timestamp.now().normalize()
        start = end - pd.DateOffset(years=int(years))
        # Clamp à la profondeur daily réelle (cf. run()) — évite un cache vide sur 20-30 ans
        earliest = self._earliest_daily_date()
        if earliest is not None and start < pd.Timestamp(earliest):
            start = pd.Timestamp(earliest)
        nb_jours = int((end - start).days) + 13 * 31 + 200

        pool = self.build_candidate_pool()
        membership = self._load_membership(pool)
        close_px, dvol, low_px, _ = self.fetch_history(
            pool, nb_jours, start.date(), max_fetch=0)

        if close_px.empty:
            raise RuntimeError(
                "Cache DB vide — lance la collecte yfinance (Config → "
                "Données de marché) puis réessaie.")

        daily_ret = close_px.pct_change()

        since_m = (start - pd.DateOffset(months=14)).date()
        monthly_db = self._load_monthly_px(pool, since_m)
        monthly_px = monthly_db.combine_first(close_px.resample('ME').last()) \
            if not monthly_db.empty else close_px.resample('ME').last()

        low_ret = None
        if low_px is not None and not low_px.empty:
            lw = low_px.reindex(index=close_px.index, columns=close_px.columns)
            low_ret = lw / close_px.shift(1) - 1.0

        sim_p = {
            'tx_cost_bps': 5.0, 'margin_rate_pct': 6.5, 'cash_yield_pct': 0.0,
            'dca_amount': 0.0, 'margin_call_enabled': True,
            'maintenance_margin_pct': 25.0, 'post_call_leverage': 1.0,
        }

        # ── grille ───────────────────────────────────────────────────────
        combos = [{'vol_scaling': True, 'vol_target_pct': float(vt),
                   'max_exposure_pct': float(me)}
                  for vt in GRID['vol_target_pct']
                  for me in GRID['max_exposure_pct']]
        combos.append({'vol_scaling': False, 'vol_target_pct': 12.0,
                       'max_exposure_pct': 250.0, '_baseline': True})
        total = len(combos)

        # Cache momentum partagé sur toute la grille : le momentum 12-1 est identique
        # d'un combo à l'autre (seule la pondération varie) → calcul ~600 titres une fois.
        mom_cache = {}

        results = []
        for done, combo in enumerate(combos, 1):
            is_baseline = combo.pop('_baseline', False)
            if is_baseline:
                label = 'inverse-vol (sans levier)'
            else:
                label = (f"vt={combo['vol_target_pct']:.0f}% "
                         f"me={combo['max_exposure_pct']:.0f}%")
            if progress_cb:
                progress_cb(done, total, label)
            try:
                params_w = {
                    'nb_top': nb_top, 'vol_scaling': combo['vol_scaling'],
                    'vol_target_pct': combo['vol_target_pct'],
                    'max_exposure_pct': combo['max_exposure_pct'],
                    'portfolio_filter': False,
                    'portfolio_vol_threshold_pct': 20.0,
                }
                wdf, meta_w = self.build_weight_matrix(
                    monthly_px, daily_ret, dvol, start, params_w, mom_cache=mom_cache,
                    membership=membership)
                if wdf.empty:
                    continue
                sim = self._simulate(wdf, daily_ret, start, end,
                                     capital, sim_p, low_ret=low_ret,
                                     max_dd_stop=MAX_DD_LIMIT)
                if sim is None or sim['equity'].empty or sim['ruined'] or sim.get('early_stop'):
                    continue
                twr = sim['twr_ret']
                row = {
                    'label':            label,
                    'vol_scaling':      combo['vol_scaling'],
                    'vol_target_pct':   combo['vol_target_pct'],
                    'max_exposure_pct': combo['max_exposure_pct'],
                    'sharpe':           round(_sharpe(twr) or 0, 3),
                    'sortino':          round(_sortino(twr) or 0, 3),
                    'cagr':             round(_cagr(twr) or 0, 2),
                    'max_dd':           round(_maxdd(twr) * 100, 2),
                    'volatility':       round(float(twr.std(ddof=1)) * _math.sqrt(TRADING_DAYS) * 100, 2),
                    'avg_leverage':     round(sim['avg_leverage'], 2),
                    'max_leverage':     round(sim['max_leverage'], 2),
                    'n_margin_calls':   len(sim['margin_calls']),
                    'n_riskoff':        meta_w.get('n_riskoff_months', 0),
                    'eligible':         _maxdd(twr) >= MAX_DD_LIMIT,
                }
                results.append(row)
            except Exception as e:
                logger.debug('optimize combo %s : %s', label, e)

        # tri : éligibles d'abord, puis par Sharpe ↓, CAGR ↓
        results.sort(key=lambda x: (
            0 if x['eligible'] else 1,
            -x['sharpe'],
            -x['cagr'],
        ))
        for i, r in enumerate(results, 1):
            r['rank'] = i
        return results

    def prefill_pool(self, years=5, max_fetch=50, pool_size=None):
        """
        Pré-remplit le cache DB (avec volume) pour le pool candidat via IBKR/Tiingo.
        Appelé par le cron nocturne ; borné par `max_fetch` (pacing IBKR).
        """
        pool = self.build_candidate_pool(pool_size) + ['SPY', 'QQQ']
        nb_jours = int(years * 365) + 13 * 31 + 200
        start_date = (datetime.now() - timedelta(days=nb_jours)).date()
        fetched = 0
        for t in pool:
            if fetched >= max_fetch:
                break
            if self._db_covers(self._load_db(t, start_date), start_date):
                continue
            df, _ = self._fetch_ticker(t, nb_jours)
            if df is not None:
                fetched += 1
        return {'pool': len(pool), 'fetched': fetched}

    # ------------------------------------------------------------------
    # 3) Pondération point-in-time (reproduit generer_recommandations)
    # ------------------------------------------------------------------
    @staticmethod
    def _momentum_12_1(monthly_series, as_of):
        """Momentum 12-1 (décimal) à `as_of` : (p[-2]/p[-13] - 1), le mois courant exclu."""
        s = monthly_series.loc[:as_of].dropna()
        if len(s) < BacktestService.MOM_MIN_MONTHS:
            return None
        p_start = s.iloc[-13]
        p_end = s.iloc[-2]
        if p_start <= 0:
            return None
        return (p_end - p_start) / p_start

    def _vol_monthly(self, monthly_series, as_of):
        """Vol annualisée depuis les rendements mensuels (base inverse-vol, = live)."""
        s = monthly_series.loc[:as_of].dropna()
        rets = s.pct_change().dropna().tail(12)
        if len(rets) < 3:
            return self.VOL_DEFAULT
        v = float(rets.std(ddof=0)) * math.sqrt(12)
        return v if v > 1e-6 else self.VOL_DEFAULT

    def _vol_realized(self, daily_ret_series, as_of):
        """Vol réalisée annualisée sur VOL_WINDOW jours (= _vol_realisee_126j live)."""
        r = daily_ret_series.loc[:as_of].dropna().tail(self.VOL_WINDOW)
        if len(r) < 20:
            return self.VOL_DEFAULT
        v = float(r.std(ddof=0)) * math.sqrt(TRADING_DAYS)
        return v if v > 1e-6 else self.VOL_DEFAULT

    def compute_weights(self, as_of, universe, monthly_px, daily_ret, params, mom_cache=None,
                        membership=None):
        """
        Poids cibles à `as_of` pour `universe` (reproduit la config live) :
          1. momentum 12-1 > 0, garder top `nb_top` ;
          2. base : inverse-volatilité normalisée à 100 % ;
          3. vol_scaling : w_i = σ_target/σ_i (vol 126 j), exposition plafonnée ;
          4. portfolio_filter : facteur f = min(1, σ_seuil/σ_panier) (vol 126 j pondérée).
        Retourne une pd.Series (index = tickers), somme ≤ 1 (ou levier si vol_scaling).

        `mom_cache` (dict optionnel) : mémoïse le momentum par (ticker, as_of). Le
        momentum 12-1 ne dépend pas des paramètres de pondération → partager ce cache
        entre les combos d'optimize() évite de recalculer ~600 momentums × 50 combos.

        `membership` (dict optionnel, cf. _load_membership) : filtre point-in-time —
        seuls les titres membres d'un indice à `as_of` sont éligibles (réduction du
        biais de survivance). None = pas de filtrage.
        """
        nb_top = params['nb_top']
        # 1) sélection momentum
        moms = []
        for t in universe:
            if t not in monthly_px.columns:
                continue
            if membership is not None and not self._member_at(membership, t, as_of):
                continue
            if mom_cache is not None:
                ckey = (t, as_of)
                if ckey in mom_cache:
                    m = mom_cache[ckey]
                else:
                    m = self._momentum_12_1(monthly_px[t], as_of)
                    mom_cache[ckey] = m
            else:
                m = self._momentum_12_1(monthly_px[t], as_of)
            if m is not None and m > 0:
                moms.append((t, m))
        if not moms:
            return pd.Series(dtype=float)
        moms.sort(key=lambda x: x[1], reverse=True)
        selected = [t for t, _ in moms[:nb_top]]

        # 2/3) volatilité par titre
        if params['vol_scaling']:
            vols = {t: self._vol_realized(daily_ret[t], as_of) if t in daily_ret.columns
                    else self.VOL_DEFAULT for t in selected}
            target = params['vol_target_pct'] / 100.0
            w = pd.Series({t: target / v for t, v in vols.items()})
            gross = w.sum()
            cap = params['max_exposure_pct'] / 100.0
            if gross > cap and gross > 0:
                w *= cap / gross
        else:
            inv = pd.Series({t: 1.0 / self._vol_monthly(monthly_px[t], as_of) for t in selected})
            w = inv / inv.sum()

        # 4) frein anti-krach au niveau du panier
        if params['portfolio_filter'] and not w.empty:
            cols = [t for t in w.index if t in daily_ret.columns]
            if cols:
                pr = (daily_ret[cols].loc[:as_of].tail(self.VOL_WINDOW) * w[cols]).sum(axis=1)
                sigma_port = float(pr.std(ddof=0)) * math.sqrt(TRADING_DAYS)
                if sigma_port > 1e-6:
                    f = min(1.0, (params['portfolio_vol_threshold_pct'] / 100.0) / sigma_port)
                    w = w * f
        return w

    # ------------------------------------------------------------------
    # 5) Construction de la matrice de poids (univers trimestriel + poids mensuels)
    # ------------------------------------------------------------------
    def build_weight_matrix(self, monthly_px, daily_ret, dvol, start, params, mom_cache=None,
                            membership=None):
        """
        Pour chaque fin de mois ≥ start : calcule les poids cibles en classant le
        momentum sur TOUT l'univers disponible — **aucun screener, identique à
        `calculate`**. Un titre entre point-in-time dès qu'il a ≥13 mois d'historique
        (sinon _momentum_12_1 renvoie None → exclu) ; il sort si son momentum ≤ 0.

        `dvol` n'est plus utilisé (le filtre de liquidité ADV a été retiré pour aligner
        la méthode sur le calcul live). `mom_cache` : mémoïse le momentum entre appels
        successifs (optimize() partage un seul cache sur toute la grille).

        Retourne (weights_df [dates × tickers], meta dict).
        """
        month_ends = [d for d in monthly_px.index if d >= start]
        # Univers = pool complet, comme calculate. La disponibilité du momentum
        # (≥13 mois à la date) gère l'entrée point-in-time, pas un screener.
        universe = list(monthly_px.columns)
        weights = {}
        n_riskoff = 0

        for d in month_ends:
            w = self.compute_weights(d, universe, monthly_px, daily_ret, params,
                                     mom_cache=mom_cache, membership=membership)
            # Risk-off (aucun momentum positif) → on passe réellement en cash
            # (ligne de poids nulle), au lieu de conserver le panier du mois précédent.
            if w.empty:
                w = pd.Series(0.0, index=universe)
                n_riskoff += 1
            weights[d] = w

        meta = {'n_rebalances': 0, 'n_universe_changes': 0,
                'n_riskoff_months': n_riskoff,
                'pit_universe': membership is not None}
        if not weights:
            return pd.DataFrame(), meta
        wdf = pd.DataFrame(weights).T.reindex(columns=monthly_px.columns).fillna(0.0)
        wdf = wdf.sort_index()
        meta['n_rebalances'] = len(weights)
        return wdf, meta

    # ------------------------------------------------------------------
    # 6) Moteur de simulation réaliste (jour par jour, stateful)
    # ------------------------------------------------------------------
    def _simulate(self, weights_df, daily_ret, start, end, capital, p, low_ret=None,
                  max_dd_stop=None):
        """
        Simulation jour par jour, sans lookahead (les poids décidés en fin de mois
        s'appliquent au 1ᵉʳ jour de bourse suivant). Modélise :
          - variation de marché des positions (notionnel $ par titre) ;
          - intérêts d'emprunt si cash < 0 (levier), rémunération si cash > 0 ;
          - **appel de marge** sur clôture : equity < maint × exposition brute →
            désendettement forcé vers `post_call_leverage` (coûts inclus) ;
          - **apports DCA** mensuels (alignés sur les rebalances) ;
          - **coûts de transaction** sur le turnover à chaque rééquilibrage.
        Sépare le rendement TWR (hors flux, pour les ratios) du parcours en € (avec flux,
        pour le XIRR/MWR). Retourne un dict de séries + agrégats, ou None si vide.
        """
        daily = daily_ret.loc[start:end]
        cols = [c for c in weights_df.columns if c in daily.columns]
        if not cols:
            return None
        daily = daily[cols].fillna(0.0)
        dates = daily.index
        if len(dates) == 0:
            return None

        # Dates d'application = 1ᵉʳ jour de bourse STRICTEMENT après la fin de mois.
        col_pos = {c: i for i, c in enumerate(cols)}
        apply_vec = {}
        for d in weights_df.index:
            fut = dates[dates > d]
            if len(fut):
                arr = np.zeros(len(cols))
                row = weights_df.loc[d]
                for t, val in row.items():
                    j = col_pos.get(t)
                    if j is not None and pd.notna(val):
                        arr[j] = float(val)
                apply_vec.setdefault(fut[0], arr)
        apply_days = sorted(apply_vec.keys())

        # Apports DCA : à chaque rebalance sauf le 1ᵉʳ (le capital initial est déjà versé).
        dca_amount = float(p['dca_amount'])
        dca_days = set(apply_days[1:]) if dca_amount > 0 else set()

        # Taux journaliers (composition quotidienne sur base 252).
        cost = p['tx_cost_bps'] / 10000.0
        fin_daily = (1.0 + p['margin_rate_pct'] / 100.0) ** (1.0 / TRADING_DAYS) - 1.0
        cash_daily = (1.0 + p['cash_yield_pct'] / 100.0) ** (1.0 / TRADING_DAYS) - 1.0
        maint = p['maintenance_margin_pct'] / 100.0
        post_lev = float(p['post_call_leverage'])
        margin_on = bool(p['margin_call_enabled'])

        mat = daily.to_numpy(dtype=float)
        # Low returns (low[t] / close[t-1] - 1) pour le check de marge intraday
        low_mat = None
        if low_ret is not None:
            low_aligned = low_ret.reindex(columns=cols).reindex(dates).ffill()
            low_mat = low_aligned.to_numpy(dtype=float)

        N = len(cols)
        pos = np.zeros(N)              # notionnel $ par titre
        cash = float(capital)         # capital initial en cash dès J0
        equity_prev = float(capital)
        invested = float(capital)

        twr_list, eq_list, lev_list, inv_list, idx_list = [], [], [], [], []
        cashflows = [(dates[0], -float(capital))]
        margin_calls = []
        tx_total = fin_total = contrib_total = 0.0
        ruined = False
        early_stop = False
        _peak_eq = float(capital)   # suivi du pic pour le drawdown courant

        for i in range(len(dates)):
            dt = dates[i]
            r = mat[i]
            # 1) variation de marché
            pos_open = pos.copy()   # positions avant le move du jour (pour check intraday)
            cash_open = cash
            pos = pos * (1.0 + r)
            # 2) intérêts (levier) / rémunération (cash)
            if cash >= 0:
                cash *= (1.0 + cash_daily)
            else:
                interest = -cash * fin_daily
                fin_total += interest
                cash *= (1.0 + fin_daily)
            equity = float(pos.sum() + cash)

            # Ruine : l'equity passe à zéro (levier balayé par une chute violente).
            if equity <= 0:
                twr_list.append(-1.0 if equity_prev > 0 else 0.0)
                eq_list.append(0.0); lev_list.append(0.0)
                inv_list.append(invested); idx_list.append(dt)
                ruined = True
                break

            flow_today = 0.0
            # 3a) APPEL DE MARGE INTRADAY (low) — déclenché si l'equity estimée au
            #     plus-bas du jour passe sous le seuil, même si la clôture récupère.
            #     Utilise pos_open × (1 + low_return) pour estimer le pire intraday.
            #     Liquidation exécutée aux prix de clôture (conservateur).
            #     Pour les tickers sans low data, on utilise le close return (fallback sûr).
            gross = float(np.abs(pos).sum())
            if margin_on and low_mat is not None and gross > 0:
                low_r = low_mat[i]
                # Fallback close return pour les tickers sans donnée low
                low_r_safe = np.where(np.isnan(low_r), r, low_r)
                intra_pos = pos_open * (1.0 + low_r_safe)
                intra_equity = float(np.nansum(intra_pos)) + cash_open
                intra_gross = float(np.nansum(np.abs(intra_pos)))
                if intra_gross > 0 and intra_equity < maint * intra_gross:
                    # Margin call intraday : liquidation aux prix du LOW (réaliste).
                    # Le broker force la vente quand le seuil est franchi — pas à la clôture.
                    # Les positions restantes (scale_low × pos) continuent jusqu'au close.
                    # Algébriquement : pos_restant_close = intra_pos*scale × (close/low)
                    #                               = pos_open*(1+r)*scale_low (s'annule)
                    #                               = pos * scale_low
                    lev_before = intra_gross / max(intra_equity, 1e-9)
                    target_gross_low = max(0.0, intra_equity * post_lev)
                    scale_low = target_gross_low / intra_gross if intra_gross > 0 else 0.0
                    liquidated = float(np.nansum(np.abs(intra_pos))) * (1.0 - scale_low)
                    c = liquidated * cost
                    tx_total += c
                    # pos restant valorisé à clôture (algébriquement = pos × scale_low)
                    pos = pos * scale_low
                    # cash : produits liquidation aux prix low + cash avant le move du jour
                    cash = cash_open + float(np.nansum(intra_pos)) * (1.0 - scale_low) - c
                    equity = float(np.nansum(pos)) + cash
                    gross = float(np.nansum(np.abs(pos)))
                    margin_calls.append({
                        'date': dt.strftime('%Y-%m-%d'),
                        'equity': round(equity, 2),
                        'leverage_before': round(lev_before, 2),
                        'liquidated': round(liquidated, 2),
                        'trigger': 'intraday_low',
                    })

            # 3b) APPEL DE MARGE sur clôture : equity < maint × exposition brute
            if margin_on and gross > 0 and equity < maint * gross:
                lev_before = gross / equity
                target_gross = max(0.0, equity * post_lev)
                scale = target_gross / gross if gross > 0 else 0.0
                liquidated = float(np.abs(pos - pos * scale).sum())
                c = liquidated * cost
                tx_total += c
                pos = pos * scale
                cash = equity - float(pos.sum()) - c
                equity = float(pos.sum() + cash)
                gross = float(np.abs(pos).sum())
                margin_calls.append({
                    'date': dt.strftime('%Y-%m-%d'),
                    'equity': round(equity, 2),
                    'leverage_before': round(lev_before, 2),
                    'liquidated': round(liquidated, 2),
                    'trigger': 'close',
                })

            # 4) apport DCA
            if dt in dca_days:
                cash += dca_amount
                equity += dca_amount
                invested += dca_amount
                contrib_total += dca_amount
                flow_today += dca_amount
                cashflows.append((dt, -dca_amount))

            # 5) rééquilibrage mensuel
            if dt in apply_vec:
                target = apply_vec[dt] * equity
                turn = float(np.abs(target - pos).sum())
                c = turn * cost
                tx_total += c
                pos = target
                cash = equity - float(pos.sum()) - c
                equity = float(pos.sum() + cash)

            # 6) enregistrements (TWR hors flux du jour)
            twr_val = (equity - flow_today) / equity_prev - 1.0 if equity_prev > 0 else 0.0
            twr_list.append(twr_val)
            eq_list.append(equity)
            end_gross = float(np.abs(pos).sum())
            lev_list.append(end_gross / equity if equity > 1e-9 else 0.0)
            inv_list.append(invested)
            idx_list.append(dt)
            equity_prev = equity

            # 7) arrêt anticipé (optimisation uniquement) : drawdown > seuil
            if max_dd_stop is not None and end_gross > 0:
                if equity > _peak_eq:
                    _peak_eq = equity
                elif _peak_eq > 0 and equity / _peak_eq - 1.0 < max_dd_stop:
                    early_stop = True
                    break

        idx = pd.DatetimeIndex(idx_list)
        twr_ret = pd.Series(twr_list, index=idx)
        equity = pd.Series(eq_list, index=idx)
        leverage = pd.Series(lev_list, index=idx)
        invested_s = pd.Series(inv_list, index=idx)
        twr_index = (1.0 + twr_ret).cumprod() * capital

        final_equity = float(equity.iloc[-1]) if len(equity) else 0.0
        cashflows.append((idx[-1], final_equity))

        lev_active = leverage[leverage > 0.01]
        return {
            'twr_ret': twr_ret,
            'equity': equity,
            'twr_index': twr_index,
            'leverage': leverage,
            'invested': invested_s,
            'dca_days': dca_days,
            'cashflows': cashflows,
            'margin_calls': margin_calls,
            'tx_total': tx_total,
            'fin_total': fin_total,
            'contrib_total': contrib_total,
            'total_invested': invested,
            'final_equity': final_equity,
            'ruined': ruined,
            'early_stop': early_stop,
            'avg_leverage': float(lev_active.mean()) if len(lev_active) else 0.0,
            'max_leverage': float(leverage.max()) if len(leverage) else 0.0,
            'pct_time_levered': float((leverage > 1.0001).mean()) if len(leverage) else 0.0,
            'pct_time_invested': float((leverage > 0.01).mean()) if len(leverage) else 0.0,
        }

    @staticmethod
    def _buy_hold(bench_ret, dates, capital, dca_amount, dca_days, cost):
        """Benchmark acheté-conservé, soumis au **même calendrier de flux** (capital
        initial + DCA) et à un coût d'entrée, pour une comparaison équitable en €."""
        bench_ret = bench_ret.reindex(dates).fillna(0.0)
        val = capital * (1.0 - cost)
        invested = float(capital)
        eq = []
        for i, dt in enumerate(dates):
            if i > 0:
                val *= (1.0 + float(bench_ret.iloc[i]))
            if dt in dca_days:
                val += dca_amount * (1.0 - cost)
                invested += dca_amount
            eq.append((dt, val))
        equity = pd.Series(dict(eq)).sort_index()
        return equity, invested, float(val)

    # ------------------------------------------------------------------
    # 7) Point d'entrée
    # ------------------------------------------------------------------
    def run(self, capital=10000.0, years=5, nb_top=5, vol_scaling=False,
            vol_target_pct=12.0, max_exposure_pct=250.0, portfolio_filter=False,
            portfolio_vol_threshold_pct=20.0, benchmark='SPY', pool_size=None,
            tx_cost_bps=None, margin_rate_pct=None, cash_yield_pct=None,
            dca_amount=0.0, margin_call_enabled=True, maintenance_margin_pct=None,
            post_call_leverage=None, progress=None):
        """Exécute le backtest complet et retourne un dict JSON-sérialisable."""
        params = {
            'nb_top': int(nb_top), 'vol_scaling': bool(vol_scaling),
            'vol_target_pct': float(vol_target_pct), 'max_exposure_pct': float(max_exposure_pct),
            'portfolio_filter': bool(portfolio_filter),
            'portfolio_vol_threshold_pct': float(portfolio_vol_threshold_pct),
        }
        # Hypothèses de réalisme (UI → défauts).
        sim_p = {
            'tx_cost_bps': self.DEFAULT_TX_COST_BPS if tx_cost_bps is None else float(tx_cost_bps),
            'margin_rate_pct': self.DEFAULT_MARGIN_RATE_PCT if margin_rate_pct is None else float(margin_rate_pct),
            'cash_yield_pct': self.DEFAULT_CASH_YIELD_PCT if cash_yield_pct is None else float(cash_yield_pct),
            'dca_amount': float(dca_amount or 0.0),
            'margin_call_enabled': bool(margin_call_enabled),
            'maintenance_margin_pct': self.DEFAULT_MAINT_MARGIN_PCT if maintenance_margin_pct is None else float(maintenance_margin_pct),
            'post_call_leverage': self.DEFAULT_POST_CALL_LEVERAGE if post_call_leverage is None else float(post_call_leverage),
        }
        end = pd.Timestamp.now().normalize()
        start = end - pd.DateOffset(years=int(years))

        # Clamp de la date de début à la profondeur réellement disponible en base.
        # Sans ça, demander 20-30 ans alors que le daily ne remonte qu'à ~2015 ferait
        # rejeter TOUS les tickers par _db_covers (« historique trop court ») → repli
        # réseau capé à 40 tickers, qui jette les données 2015+ déjà présentes.
        clamped_years = False
        earliest = self._earliest_daily_date()
        if earliest is not None:
            earliest_ts = pd.Timestamp(earliest)
            if start < earliest_ts:
                start = earliest_ts
                clamped_years = True

        # Fenêtre data : période + lookback momentum (13 mois) + marge vol (126 j)
        nb_jours = int((end - start).days) + 13 * 31 + 200

        pool = self.build_candidate_pool(pool_size)
        # Filtre point-in-time (IndexMembership) : réduit le biais de survivance
        # en n'autorisant un titre qu'aux dates où il appartenait à un indice.
        membership = self._load_membership(pool)
        close_px, dvol, low_px, fmeta = self.fetch_history(pool, nb_jours, start.date(), progress)
        if close_px.empty:
            raise RuntimeError(
                "Cache de prix vide pour cette période. Lance le pré-remplissage "
                "(cron nocturne ou /api/backtest/prefill) puis réessaie.")

        # Low returns pour le check de marge intraday
        # low_ret[t] = low[t] / close[t-1] - 1 (return intraday au plus-bas)
        low_ret = None
        if low_px is not None and not low_px.empty:
            # On aligne sur close_px pour avoir le même index et les mêmes colonnes
            low_aligned = low_px.reindex(index=close_px.index, columns=close_px.columns)
            low_ret = low_aligned / close_px.shift(1) - 1.0

        # Benchmark (DB d'abord, sinon récupération ; SPY → fallback ^GSPC).
        # Chargé avec ~1 an de lookback avant `start` pour que la SMA200 (régime
        # bull/bear) soit déjà valide dès le début de la courbe d'équité.
        bench_load_start = (start - pd.Timedelta(days=400)).date()
        def _bench(sym):
            df = self._load_db(sym, bench_load_start)
            if not self._db_covers(df, start.date()):
                df, _ = self._fetch_ticker(sym, nb_jours)
            return df
        bench_df = _bench(benchmark)
        if bench_df is None or 'adjClose' not in getattr(bench_df, 'columns', []):
            benchmark = '^GSPC'
            bench_df = _bench(benchmark)
        bench_close = bench_df['adjClose'] if bench_df is not None else pd.Series(dtype=float)

        daily_ret = close_px.pct_change()

        # monthly_px pour le momentum : priorité MonthlyPriceBar (20 ans, yfinance)
        # qui couvre le lookback 13 mois même pour les backtests longs.
        # Le resample daily sert de fallback pour les tickers absents de MonthlyPriceBar.
        since_monthly = (start - pd.DateOffset(months=14)).date()
        monthly_px_db = self._load_monthly_px(pool, since_monthly)
        monthly_px_daily = close_px.resample('ME').last()
        if not monthly_px_db.empty:
            # DB mensuelle comme base (historique long), daily comme complément
            monthly_px = monthly_px_db.combine_first(monthly_px_daily)
        else:
            monthly_px = monthly_px_daily

        weights_df, meta = self.build_weight_matrix(monthly_px, daily_ret, dvol, start, params,
                                                    membership=membership)
        if weights_df.empty:
            raise RuntimeError("Aucun signal sur la période (période trop courte ou données insuffisantes).")

        sim = self._simulate(weights_df, daily_ret, start, end, capital, sim_p, low_ret=low_ret)
        if sim is None or sim['equity'].empty:
            raise RuntimeError("Simulation vide sur la période demandée.")

        dates = sim['equity'].index
        cost = sim_p['tx_cost_bps'] / 10000.0
        # Benchmark : daily returns + parcours buy-and-hold avec les mêmes flux.
        bench_daily = bench_close.pct_change()
        bench_eq, bench_invested, bench_final = self._buy_hold(
            bench_daily, dates, capital, sim_p['dca_amount'], sim['dca_days'], cost)
        bench_ret = bench_daily.reindex(dates).fillna(0.0)

        drawdown = sim['twr_index'] / sim['twr_index'].cummax() - 1.0

        stats = self._stats(sim, bench_ret, capital, meta)
        bench_stats = self._benchmark_stats(bench_ret, bench_eq, capital, bench_invested, bench_final)

        twr_daily = [round(float(x), 8) for x in sim['twr_ret'].dropna().tolist()]

        return {
            'success': True,
            'equity': self._series_to_points(sim['equity']),
            'benchmark_equity': self._series_to_points(bench_eq),
            'invested': self._series_to_points(sim['invested']) if sim_p['dca_amount'] > 0 else [],
            'leverage': self._series_to_points(sim['leverage']) if sim['max_leverage'] > 1.05 else [],
            'drawdown': self._series_to_points(drawdown),
            'regime_segments': self._regime_segments(bench_close, dates),
            'daily_returns': twr_daily,
            'monthly_returns': self._monthly_returns(sim['twr_ret']),
            'yearly_returns': self._yearly_returns(sim['twr_ret'], bench_ret),
            'drawdown_periods': self._drawdown_periods(sim['twr_index']),
            'margin_calls': sim['margin_calls'],
            'stats': stats,
            'benchmark_stats': bench_stats,
            'meta': {
                'benchmark': benchmark,
                'start': dates.min().strftime('%Y-%m-%d'),
                'end': dates.max().strftime('%Y-%m-%d'),
                'capital': capital,
                'pool_size': len(pool),
                'data': fmeta,
                'config': params,
                'assumptions': sim_p,
                'n_rebalances': meta['n_rebalances'],
                'n_universe_changes': meta['n_universe_changes'],
                'n_riskoff_months': meta.get('n_riskoff_months', 0),
                'pit_universe': meta.get('pit_universe', False),
                'warnings': ([
                    "Biais de survivance résiduel : l'univers candidat est constitué des "
                    "tickers existant aujourd'hui (les titres délistés sont absents).",
                    "Les appels de marge sont évalués sur le plus-bas intraday (low yfinance) "
                    "quand disponible, sinon sur le cours de clôture.",
                ] + ([
                    f"⚠️ Période demandée ({years} ans) tronquée : le cache daily ne remonte "
                    f"qu'au {start.strftime('%Y-%m-%d')}. Lance une collecte COMPLÈTE "
                    f"(Config → Données de marché → recollecte complète) pour backtester plus loin."
                ] if clamped_years else []) + ([
                    "⚠️ RUINE : l'effet de levier a été balayé par une chute violente "
                    "(equity tombée à zéro). Le backtest s'arrête à cette date."
                ] if sim['ruined'] else []) + ([
                    f"Cache incomplet : {fmeta['skipped']} ticker(s) pas encore en base "
                    f"(récupérés progressivement par le cron nocturne). Résultat partiel."
                ] if fmeta.get('skipped') else []) + ([
                    "⚠️ Données mensuelles yfinance absentes : le momentum est calculé "
                    "depuis le resampling du daily (limité à 6 ans). Lance la collecte yfinance "
                    "(Config → Données de marché) pour un historique complet jusqu'à 20 ans."
                ] if monthly_px_db.empty else [])),
            },
        }

    # ------------------------------------------------------------------
    # Helpers de sérialisation / stats
    # ------------------------------------------------------------------
    @staticmethod
    def _series_to_points(s):
        if s is None or len(s) == 0:
            return []
        return [{'t': idx.strftime('%Y-%m-%d'), 'v': round(float(v), 4)}
                for idx, v in s.items() if pd.notna(v)]

    @staticmethod
    def _regime_segments(bench_close, dates):
        """
        Segments bull/bear du marché sur la plage de la courbe d'équité.
        Définition alignée sur le live (get_market_regime) : BULL si le benchmark est
        au-dessus de sa SMA200, BEAR sinon. Retourne une liste compacte de segments
        contigus : [{'regime': 'bull'|'bear', 'start': 'YYYY-MM-DD', 'end': ...}].
        """
        if bench_close is None or len(bench_close) == 0 or len(dates) == 0:
            return []
        try:
            sma200 = bench_close.rolling(200, min_periods=200).mean()
            reg = pd.Series(np.where(bench_close > sma200, 'bull', 'bear'),
                            index=bench_close.index)
            reg = reg[sma200.notna()]                       # ignore le warmup SMA200
            d0, d1 = dates.min(), dates.max()
            reg = reg[(reg.index >= d0) & (reg.index <= d1)]
            if reg.empty:
                return []
            # Compresser les jours consécutifs de même régime en segments
            groups = (reg != reg.shift()).cumsum()
            segments = []
            for _, grp in reg.groupby(groups):
                segments.append({
                    'regime': str(grp.iloc[0]),
                    'start': grp.index[0].strftime('%Y-%m-%d'),
                    'end': grp.index[-1].strftime('%Y-%m-%d'),
                })
            return segments
        except Exception as e:
            logger.warning('_regime_segments : %s', e)
            return []

    @staticmethod
    def _monthly_returns(port_ret):
        m = (1 + port_ret).resample('ME').prod() - 1
        return [{'year': idx.year, 'month': idx.month, 'return_pct': round(float(v) * 100, 2)}
                for idx, v in m.items() if pd.notna(v)]

    @staticmethod
    def _yearly_returns(port_ret, bench_ret):
        sy = (1 + port_ret).resample('YE').prod() - 1
        by = (1 + bench_ret).resample('YE').prod() - 1 if bench_ret is not None and not bench_ret.empty else pd.Series(dtype=float)
        by_year = {idx.year: float(v) for idx, v in by.items() if pd.notna(v)}
        out = []
        for idx, v in sy.items():
            if pd.isna(v):
                continue
            b = by_year.get(idx.year)
            out.append({'year': idx.year, 'strategy_pct': round(float(v) * 100, 2),
                        'benchmark_pct': round(b * 100, 2) if b is not None else None})
        return out

    @staticmethod
    def _drawdown_periods(equity_index, top=5):
        """Top-N pires épisodes de drawdown (début, creux, fin, profondeur, durée jours)."""
        if equity_index is None or len(equity_index) < 2:
            return []
        dd = equity_index / equity_index.cummax() - 1.0
        periods, in_dd, start, trough, trough_v = [], False, None, None, 0.0
        for dt, v in dd.items():
            v = float(v)
            if not in_dd and v < -1e-9:
                in_dd, start, trough, trough_v = True, dt, dt, v
            elif in_dd:
                if v < trough_v:
                    trough_v, trough = v, dt
                if v >= -1e-9:
                    periods.append((start, trough, dt, trough_v))
                    in_dd = False
        if in_dd:
            periods.append((start, trough, None, trough_v))
        periods.sort(key=lambda x: x[3])
        out = []
        for st, tr, en, depth in periods[:top]:
            end_days = (en if en is not None else equity_index.index[-1])
            out.append({
                'start': st.strftime('%Y-%m-%d'),
                'valley': tr.strftime('%Y-%m-%d'),
                'end': en.strftime('%Y-%m-%d') if en is not None else None,
                'depth_pct': round(depth * 100, 2),
                'days': int((end_days - st).days),
                'recovered': en is not None,
            })
        return out

    @staticmethod
    def _xirr(cashflows, lo=-0.9999, hi=10.0, tol=1e-6, max_iter=200):
        """Taux de rendement actuariel (MWR) annualisé sur flux datés, par bissection."""
        if not cashflows or len(cashflows) < 2:
            return None
        t0 = cashflows[0][0]
        ts = [((d - t0).days) / 365.0 for d, _ in cashflows]
        amts = [float(a) for _, a in cashflows]
        if not (any(a > 0 for a in amts) and any(a < 0 for a in amts)):
            return None

        def npv(rate):
            return sum(a / (1.0 + rate) ** t for a, t in zip(amts, ts))

        flo, fhi = npv(lo), npv(hi)
        if flo * fhi > 0:
            return None
        for _ in range(max_iter):
            mid = (lo + hi) / 2.0
            fm = npv(mid)
            if abs(fm) < tol:
                return mid
            if flo * fm < 0:
                hi, fhi = mid, fm
            else:
                lo, flo = mid, fm
        return (lo + hi) / 2.0

    def _stats(self, sim, bench_ret, capital, meta):
        qs = _get_qs()
        twr = sim['twr_ret']
        equity = sim['equity']
        twr_index = sim['twr_index']

        def safe(fn, *a, **k):
            # Silencieux par design : quantstats lève des exceptions numériques normales
            # (séries trop courtes, NaN, division par zéro) qu'on traduit en None dans l'UI.
            try:
                v = fn(*a, **k)
                if hasattr(v, 'iloc'):
                    v = v.iloc[0]
                return round(float(v), 4) if v is not None and np.isfinite(v) else None
            except Exception:
                return None

        monthly = (1 + twr).resample('ME').prod() - 1
        pct_pos = float((monthly > 0).mean()) if len(monthly) else None
        twr_total = float(twr_index.iloc[-1] / capital - 1.0)

        # alpha / beta vs benchmark
        alpha = beta = None
        try:
            if bench_ret is not None and not bench_ret.empty:
                aligned = bench_ret.reindex(twr.index).dropna()
                if len(aligned) > 10:
                    g = qs.stats.greeks(twr.reindex(aligned.index), aligned)
                    alpha = round(float(g.get('alpha')), 4)
                    beta = round(float(g.get('beta')), 4)
        except Exception as e:
            logger.debug('Calcul alpha/beta échoué (données insuffisantes) : %s', e)

        wins = twr[twr > 0]
        losses = twr[twr < 0]
        profit_factor = (float(wins.sum()) / abs(float(losses.sum()))
                         if len(losses) and losses.sum() != 0 else None)

        mwr = self._xirr(sim['cashflows'])
        total_costs = sim['tx_total'] + sim['fin_total']

        return {
            # Valeur / rendement
            'final_value': round(sim['final_equity'], 2),
            'total_invested': round(sim['total_invested'], 2),
            'profit': round(sim['final_equity'] - sim['total_invested'], 2),
            'total_return': round(twr_total, 4),            # TWR (perf stratégie)
            'twr_total_return': round(twr_total, 4),
            'money_weighted_return': round(mwr, 4) if mwr is not None else None,
            'cagr': safe(qs.stats.cagr, twr),
            # Risque
            'volatility': safe(qs.stats.volatility, twr),
            'sharpe': safe(qs.stats.sharpe, twr),
            'sortino': safe(qs.stats.sortino, twr),
            'max_drawdown': safe(qs.stats.max_drawdown, twr),
            'calmar': safe(qs.stats.calmar, twr),
            'ulcer_index': safe(qs.stats.ulcer_index, twr),
            'var_95': safe(qs.stats.value_at_risk, twr),
            'cvar_95': safe(qs.stats.conditional_value_at_risk, twr),
            'omega': safe(qs.stats.omega, twr),
            'tail_ratio': safe(qs.stats.tail_ratio, twr),
            'gain_to_pain': safe(qs.stats.gain_to_pain_ratio, twr),
            'skew': safe(qs.stats.skew, twr),
            'kurtosis': safe(qs.stats.kurtosis, twr),
            # Distribution
            'best_day': safe(qs.stats.best, twr),
            'worst_day': safe(qs.stats.worst, twr),
            'best_month': round(float(monthly.max()), 4) if len(monthly) else None,
            'worst_month': round(float(monthly.min()), 4) if len(monthly) else None,
            'win_rate_daily': round(float((twr > 0).mean()), 4) if len(twr) else None,
            'profit_factor': round(profit_factor, 2) if profit_factor is not None else None,
            'pct_positive_months': round(pct_pos, 4) if pct_pos is not None else None,
            'alpha': alpha,
            'beta': beta,
            # Exposition / levier
            'avg_leverage': round(sim['avg_leverage'], 3),
            'max_leverage': round(sim['max_leverage'], 3),
            'pct_time_levered': round(sim['pct_time_levered'], 4),
            'pct_time_invested': round(sim['pct_time_invested'], 4),
            # Coûts / frictions
            'tx_costs': round(sim['tx_total'], 2),
            'financing_costs': round(sim['fin_total'], 2),
            'total_costs': round(total_costs, 2),
            'contributions': round(sim['contrib_total'], 2),
            'n_margin_calls': len(sim['margin_calls']),
            'ruined': sim['ruined'],
            # Activité
            'n_rebalances': meta['n_rebalances'],
            'n_universe_changes': meta['n_universe_changes'],
            'n_riskoff_months': meta.get('n_riskoff_months', 0),
        }

    def monte_carlo(self, daily_returns: list, n_simulations: int = 1000,
                    horizon_days: int = 252, initial_value: float = 10000.0) -> dict:
        """Bootstrap Monte Carlo : rééchantillonnage avec remise des rendements journaliers TWR."""
        arr = np.array(daily_returns, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) < 20:
            return {'error': 'Données insuffisantes (< 20 jours)'}
        n_simulations = max(100, min(n_simulations, 5000))
        horizon_days = max(21, min(horizon_days, 1260))

        rng = np.random.default_rng(42)
        # (n_simulations, horizon_days) : tirage avec remise
        sampled = rng.choice(arr, size=(n_simulations, horizon_days), replace=True)
        # Chemins cumulés
        paths = initial_value * np.cumprod(1.0 + sampled, axis=1)
        init_col = np.full((n_simulations, 1), initial_value)
        paths = np.hstack([init_col, paths])

        pcts = np.percentile(paths, [5, 25, 50, 75, 95], axis=0)
        return {
            'p5':  [round(float(x), 2) for x in pcts[0]],
            'p25': [round(float(x), 2) for x in pcts[1]],
            'p50': [round(float(x), 2) for x in pcts[2]],
            'p75': [round(float(x), 2) for x in pcts[3]],
            'p95': [round(float(x), 2) for x in pcts[4]],
            'n_simulations': n_simulations,
            'horizon_days': horizon_days,
            'initial_value': initial_value,
        }

    def _benchmark_stats(self, bench_ret, bench_eq, capital, bench_invested, bench_final):
        """Stats du benchmark (sur ses rendements purs) + valeur finale buy-and-hold."""
        qs = _get_qs()

        def safe(fn, *a, **k):
            # Silencieux par design : voir commentaire dans _stats.
            try:
                v = fn(*a, **k)
                if hasattr(v, 'iloc'):
                    v = v.iloc[0]
                return round(float(v), 4) if v is not None and np.isfinite(v) else None
            except Exception:
                return None
        br = bench_ret.dropna() if bench_ret is not None else pd.Series(dtype=float)
        if br.empty:
            return {}
        return {
            'final_value': round(float(bench_final), 2),
            'total_invested': round(float(bench_invested), 2),
            'profit': round(float(bench_final - bench_invested), 2),
            'cagr': safe(qs.stats.cagr, br),
            'volatility': safe(qs.stats.volatility, br),
            'sharpe': safe(qs.stats.sharpe, br),
            'sortino': safe(qs.stats.sortino, br),
            'max_drawdown': safe(qs.stats.max_drawdown, br),
            'calmar': safe(qs.stats.calmar, br),
            'total_return': safe(qs.stats.comp, br),
        }
