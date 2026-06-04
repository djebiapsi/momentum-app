# -*- coding: utf-8 -*-
"""
Service de backtest de la stratégie momentum 12-1
=================================================
Rejoue la stratégie sur l'historique avec **intérêt composé** et la compare à un
benchmark (SPY).

Spécificités (validées avec l'utilisateur) :
  - **Univers dynamique** recalculé tous les 3 mois avec le même critère que le
    screener Long (`ScreenerService` : ADV = prix × volume ≥ 5 M$, classé par
    log(ADV) → top 50), reconstruit *point-in-time* depuis l'historique prix+volume.
  - **Config live** : `nb_top`, vol_scaling, frein anti-krach lus dans les `Settings`
    et appliqués (formules de `momentum_service.generer_recommandations` recalculées
    point-in-time, sans appel API « now »).

Moteur : rééquilibrage mensuel vectorisé (pandas) → série de rendements journaliers ;
métriques pro via **quantstats** (CAGR, Sharpe, Sortino, max drawdown, Calmar,
alpha/beta vs benchmark). Aucune dépendance lourde supplémentaire bloquante.

Limites assumées (affichées dans l'UI) : biais de survivance résiduel (univers
candidat = tickers existant aujourd'hui), screen de liquidité (pas fondamental),
1ʳᵉ exécution lente (fetch Tiingo). Nécessite `TIINGO_API_KEY`.
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
    UNIVERSE_SIZE = 50         # top-N liquidité retenu par trimestre (= screener live)
    MIN_ADV = 5_000_000        # seuil ADV (= screener live)
    ADV_WINDOW = 63            # fenêtre ADV glissant (~1 trimestre de bourse)
    VOL_WINDOW = 126           # fenêtre vol réalisée (= live, 126 j)
    MOM_MIN_MONTHS = 13        # mois nécessaires au momentum 12-1
    VOL_DEFAULT = 0.20         # vol par défaut si données insuffisantes (= live)
    MAX_ONDEMAND_FETCH = 40    # tickers récupérés au réseau par exécution (borne le temps)

    def __init__(self, momentum_service, screener_service):
        self.ms = momentum_service
        self.ss = screener_service
        # Cache mémoire court (dédoublonne les fetches réseau dans une même exécution).
        self._hist_cache = TTLCache(ttl_seconds=12 * 3600)

    # ------------------------------------------------------------------
    # 1) Pool candidat (univers de départ)
    # ------------------------------------------------------------------
    def build_candidate_pool(self, pool_size=None):
        """Top-N tickers US les plus liquides aujourd'hui (ADV ≥ seuil) — pool de départ."""
        pool_size = pool_size or self.POOL_SIZE
        if self.ss is None:
            raise RuntimeError("Screener non configuré (clé Tiingo manquante)")
        data, err = self.ss.get_iex_bulk_data()
        if err or not data:
            raise RuntimeError(f"Univers indisponible : {err or 'aucune donnée IEX'}")
        rows = [
            (t, d['adv']) for t, d in data.items()
            if d.get('adv', 0) >= self.MIN_ADV and ScreenerService._is_valid_us_symbol(t)
        ]
        rows.sort(key=lambda x: x[1], reverse=True)
        return [t for t, _ in rows[:pool_size]]

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

    def _load_db(self, ticker, start_date):
        """Charge adjClose/close/volume depuis MarketPriceBar (≥ start_date). None si indispo."""
        try:
            from models import MarketPriceBar
            rows = (MarketPriceBar.query.filter_by(ticker=ticker.upper())
                    .filter(MarketPriceBar.bar_date >= start_date)
                    .order_by(MarketPriceBar.bar_date).all())
            if not rows:
                return None
            df = pd.DataFrame([{'date': r.bar_date, 'adjClose': r.adj_close,
                                'close': r.close, 'volume': r.volume} for r in rows])
            df['date'] = pd.to_datetime(df['date'])
            return df.set_index('date').sort_index()
        except Exception:
            return None  # hors contexte d'app (tests) ou table absente

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
                except Exception:
                    pass

        result = (out, None if out is not None else (err or 'pas de données'))
        self._hist_cache.set(key, result)
        return result

    def fetch_history(self, tickers, nb_jours, start_date, progress=None, max_fetch=None):
        """
        Construit close_px (prix ajusté) + dvol (volume $, pour l'ADV).
        Lit d'abord le cache DB (instantané, 0 API) ; ne récupère au réseau que les
        tickers manquants, **borné** à `max_fetch` pour rester sous le timeout serveur.
        Returns (close_px, dvol, meta).
        """
        max_fetch = self.MAX_ONDEMAND_FETCH if max_fetch is None else max_fetch
        closes, dollar_vol, missing = {}, {}, []

        def _add(t, df):
            closes[t] = df['adjClose']
            if 'close' in df.columns and 'volume' in df.columns:
                dollar_vol[t] = (df['close'].astype(float) * df['volume'].astype(float))

        # Passe 1 : cache DB (rapide, séquentiel — nécessite le contexte Flask)
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
        meta = {'from_db': len(tickers) - len(missing), 'fetched': fetched,
                'skipped': max(0, len(missing) - fetched)}
        return close_px, dvol, meta

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
    # 3) Univers trimestriel (re-screen ADV point-in-time)
    # ------------------------------------------------------------------
    def quarterly_universe(self, dvol, as_of, size=None):
        """Top-N par ADV glissant (≈63 j) à la date `as_of`, filtré ≥ seuil (= screener live)."""
        size = size or self.UNIVERSE_SIZE
        window = dvol.loc[:as_of].tail(self.ADV_WINDOW)
        if window.empty:
            return []
        adv = window.mean(numeric_only=True).dropna()
        eligible = adv[adv >= self.MIN_ADV]
        if eligible.empty:
            return []
        # Tri par log(ADV) décroissant = ADV décroissant
        return list(eligible.sort_values(ascending=False).head(size).index)

    # ------------------------------------------------------------------
    # 4) Pondération point-in-time (reproduit generer_recommandations)
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

    def compute_weights(self, as_of, universe, monthly_px, daily_ret, params):
        """
        Poids cibles à `as_of` pour `universe` (reproduit la config live) :
          1. momentum 12-1 > 0, garder top `nb_top` ;
          2. base : inverse-volatilité normalisée à 100 % ;
          3. vol_scaling : w_i = σ_target/σ_i (vol 126 j), exposition plafonnée ;
          4. portfolio_filter : facteur f = min(1, σ_seuil/σ_panier) (vol 126 j pondérée).
        Retourne une pd.Series (index = tickers), somme ≤ 1 (ou levier si vol_scaling).
        """
        nb_top = params['nb_top']
        # 1) sélection momentum
        moms = []
        for t in universe:
            if t not in monthly_px.columns:
                continue
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
    def build_weight_matrix(self, monthly_px, daily_ret, dvol, start, params):
        """
        Pour chaque fin de mois ≥ start : (ré)évalue l'univers tous les 3 mois, calcule
        les poids cibles. Retourne (weights_df [dates × tickers], meta dict).
        """
        month_ends = [d for d in monthly_px.index if d >= start]
        weights = {}
        universe = None
        n_universe_changes = 0
        first_month = month_ends[0].to_period('M') if month_ends else None

        for d in month_ends:
            # Re-screen trimestriel : tous les 3 mois (en partant du 1er mois)
            months_since = (d.to_period('M') - first_month).n if first_month else 0
            if universe is None or months_since % 3 == 0:
                new_u = self.quarterly_universe(dvol, d)
                if new_u:
                    if universe is not None and set(new_u) != set(universe):
                        n_universe_changes += 1
                    universe = new_u
            if not universe:
                continue
            w = self.compute_weights(d, universe, monthly_px, daily_ret, params)
            if not w.empty:
                weights[d] = w

        if not weights:
            return pd.DataFrame(), {'n_rebalances': 0, 'n_universe_changes': n_universe_changes}
        wdf = pd.DataFrame(weights).T.reindex(columns=monthly_px.columns).fillna(0.0)
        wdf = wdf.sort_index()
        return wdf, {'n_rebalances': len(weights), 'n_universe_changes': n_universe_changes}

    # ------------------------------------------------------------------
    # 6) Simulation (compounding journalier, sans lookahead)
    # ------------------------------------------------------------------
    @staticmethod
    def simulate(weights_df, daily_ret, start, end):
        """
        Applique les poids mensuels (forward-fill) aux rendements journaliers, décalés
        d'un jour (les poids décidés en fin de mois s'appliquent dès le lendemain → pas
        de lookahead). Retourne la série de rendements journaliers du portefeuille.
        """
        if weights_df.empty:
            return pd.Series(dtype=float)
        daily = daily_ret.loc[start:end]
        cols = [c for c in weights_df.columns if c in daily.columns]
        daily = daily[cols].fillna(0.0)
        w_daily = weights_df[cols].reindex(daily.index, method='ffill').fillna(0.0)
        port_ret = (w_daily.shift(1) * daily).sum(axis=1)
        return port_ret.dropna()

    # ------------------------------------------------------------------
    # 7) Point d'entrée
    # ------------------------------------------------------------------
    def run(self, capital=10000.0, years=5, nb_top=5, vol_scaling=False,
            vol_target_pct=12.0, max_exposure_pct=250.0, portfolio_filter=False,
            portfolio_vol_threshold_pct=20.0, benchmark='SPY', pool_size=None,
            progress=None):
        """Exécute le backtest complet et retourne un dict JSON-sérialisable."""
        params = {
            'nb_top': int(nb_top), 'vol_scaling': bool(vol_scaling),
            'vol_target_pct': float(vol_target_pct), 'max_exposure_pct': float(max_exposure_pct),
            'portfolio_filter': bool(portfolio_filter),
            'portfolio_vol_threshold_pct': float(portfolio_vol_threshold_pct),
        }
        end = pd.Timestamp.now().normalize()
        start = end - pd.DateOffset(years=int(years))
        # Fenêtre data : période + lookback momentum (13 mois) + marge vol (126 j)
        nb_jours = int((end - start).days) + 13 * 31 + 200

        pool = self.build_candidate_pool(pool_size)
        close_px, dvol, fmeta = self.fetch_history(pool, nb_jours, start.date(), progress)
        if close_px.empty:
            raise RuntimeError(
                "Cache de prix vide pour cette période. Lance le pré-remplissage "
                "(cron nocturne ou /api/backtest/prefill) puis réessaie.")

        # Benchmark (DB d'abord, sinon récupération ; SPY → fallback ^GSPC)
        def _bench(sym):
            df = self._load_db(sym, start.date())
            if not self._db_covers(df, start.date()):
                df, _ = self._fetch_ticker(sym, nb_jours)
            return df
        bench_df = _bench(benchmark)
        if bench_df is None or 'adjClose' not in getattr(bench_df, 'columns', []):
            benchmark = '^GSPC'
            bench_df = _bench(benchmark)
        bench_close = bench_df['adjClose'] if bench_df is not None else pd.Series(dtype=float)

        monthly_px = close_px.resample('ME').last()
        daily_ret = close_px.pct_change()

        weights_df, meta = self.build_weight_matrix(monthly_px, daily_ret, dvol, start, params)
        if weights_df.empty:
            raise RuntimeError("Aucun signal sur la période (période trop courte ou données insuffisantes).")

        port_ret = self.simulate(weights_df, daily_ret, start, end)
        if port_ret.empty:
            raise RuntimeError("Simulation vide sur la période demandée.")

        bench_ret = bench_close.pct_change().loc[port_ret.index.min():port_ret.index.max()].dropna()

        equity = (1 + port_ret).cumprod() * capital
        bench_equity = (1 + bench_ret).cumprod() * capital if not bench_ret.empty else pd.Series(dtype=float)

        return {
            'success': True,
            'equity': self._series_to_points(equity),
            'benchmark_equity': self._series_to_points(bench_equity),
            'drawdown': self._series_to_points(equity / equity.cummax() - 1.0),
            'monthly_returns': self._monthly_returns(port_ret),
            'stats': self._stats(port_ret, bench_ret, equity, capital, meta),
            'meta': {
                'benchmark': benchmark,
                'start': port_ret.index.min().strftime('%Y-%m-%d'),
                'end': port_ret.index.max().strftime('%Y-%m-%d'),
                'capital': capital,
                'pool_size': len(pool),
                'data': fmeta,
                'config': params,
                'n_rebalances': meta['n_rebalances'],
                'n_universe_changes': meta['n_universe_changes'],
                'warnings': ([
                    "Biais de survivance résiduel : l'univers candidat est constitué des "
                    "tickers liquides existant aujourd'hui (les titres délistés sont absents).",
                    "Le re-screen reproduit le critère de liquidité (ADV), pas un "
                    "screen fondamental.",
                ] + ([
                    f"Cache incomplet : {fmeta['skipped']} ticker(s) pas encore en base "
                    f"(récupérés progressivement par le cron nocturne). Résultat partiel."
                ] if fmeta.get('skipped') else [])),
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
    def _monthly_returns(port_ret):
        m = (1 + port_ret).resample('ME').prod() - 1
        return [{'year': idx.year, 'month': idx.month, 'return_pct': round(float(v) * 100, 2)}
                for idx, v in m.items() if pd.notna(v)]

    def _stats(self, port_ret, bench_ret, equity, capital, meta):
        qs = _get_qs()

        def safe(fn, *a, **k):
            try:
                v = fn(*a, **k)
                return round(float(v), 4) if v is not None and np.isfinite(v) else None
            except Exception:
                return None
        total_return = float(equity.iloc[-1] / capital - 1.0)
        pos_months = (1 + port_ret).resample('ME').prod() - 1
        pct_pos = float((pos_months > 0).mean()) if len(pos_months) else None
        greeks = {}
        try:
            if bench_ret is not None and not bench_ret.empty:
                aligned = bench_ret.reindex(port_ret.index).dropna()
                if len(aligned) > 10:
                    g = qs.stats.greeks(port_ret.reindex(aligned.index), aligned)
                    greeks = {'alpha': round(float(g.get('alpha')), 4),
                              'beta': round(float(g.get('beta')), 4)}
        except Exception:
            greeks = {}
        return {
            'total_return': round(total_return, 4),
            'final_value': round(float(equity.iloc[-1]), 2),
            'cagr': safe(qs.stats.cagr, port_ret),
            'volatility': safe(qs.stats.volatility, port_ret),
            'sharpe': safe(qs.stats.sharpe, port_ret),
            'sortino': safe(qs.stats.sortino, port_ret),
            'max_drawdown': safe(qs.stats.max_drawdown, port_ret),
            'calmar': safe(qs.stats.calmar, port_ret),
            'best_day': safe(qs.stats.best, port_ret),
            'worst_day': safe(qs.stats.worst, port_ret),
            'pct_positive_months': round(pct_pos, 4) if pct_pos is not None else None,
            'alpha': greeks.get('alpha'),
            'beta': greeks.get('beta'),
            'n_rebalances': meta['n_rebalances'],
            'n_universe_changes': meta['n_universe_changes'],
        }
