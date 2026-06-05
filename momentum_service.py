# -*- coding: utf-8 -*-
"""
Service de calcul du Momentum
=============================
Contient toute la logique métier pour calculer le momentum 12-1.
Adapté du script strategy.py original.
"""

import math
import logging
import requests
import pandas as pd
from datetime import datetime, date as date_cls
from dateutil.relativedelta import relativedelta
from cache_utils import TTLCache

logger = logging.getLogger(__name__)


class MomentumService:
    """
    Service pour calculer le momentum des actions.

    Récupération des prix multi-source avec persistance :
      1. Cache en base de données (MarketPriceBar)
      2. IBKR (reqHistoricalData, ADJUSTED_LAST) si connecté
      3. Tiingo (fallback)
    Les barres récupérées sont persistées en base.
    """

    def __init__(self, api_key, ibkr_service=None):
        """
        Initialise le service avec la clé API Tiingo.

        Args:
            api_key: Clé API Tiingo
            ibkr_service: instance IBKRService optionnelle (source primaire)
        """
        self.api_key = api_key
        self.base_url = "https://api.tiingo.com/tiingo/daily"
        self.ibkr_service = ibkr_service

        # Cache en mémoire pour éviter les appels redondants
        self._monthly_cache = TTLCache(ttl_seconds=6 * 3600)   # Prix mensuels : 6h
        self._daily_cache   = TTLCache(ttl_seconds=4 * 3600)   # Prix journaliers : 4h
        self._ticker_cache  = TTLCache(ttl_seconds=24 * 3600)  # Validation ticker : 24h

    def set_ibkr_service(self, ibkr_service):
        """Injecte le service IBKR après initialisation."""
        self.ibkr_service = ibkr_service

    # =========================================================================
    # PERSISTANCE & RÉCUPÉRATION MULTI-SOURCE
    # =========================================================================

    def _load_bars_from_db(self, ticker, date_debut=None):
        """
        Charge les barres journalières depuis la base.
        Returns: DataFrame indexé par date avec colonne 'adjClose', ou None.
        """
        try:
            from models import MarketPriceBar
            q = MarketPriceBar.query.filter_by(ticker=ticker.upper())
            if date_debut:
                q = q.filter(MarketPriceBar.bar_date >= date_debut)
            rows = q.order_by(MarketPriceBar.bar_date).all()
            if not rows:
                return None
            df = pd.DataFrame([
                {'date': r.bar_date, 'adjClose': r.adj_close,
                 'close': r.close, 'volume': r.volume}
                for r in rows
            ])
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            return df.sort_index()
        except Exception as e:
            logger.warning('Lecture DB échouée pour %s: %s', ticker, e)
            return None

    def _db_last_date(self, ticker):
        """Date de la barre la plus récente en base pour ce ticker, ou None."""
        try:
            from models import MarketPriceBar
            row = (MarketPriceBar.query
                   .filter_by(ticker=ticker.upper())
                   .order_by(MarketPriceBar.bar_date.desc())
                   .first())
            return row.bar_date if row else None
        except Exception:
            return None

    def _save_bars_to_db(self, ticker, bars, source):
        """
        Persiste/met à jour les barres en base (upsert sur ticker+date).
        bars: liste de dicts {'date': 'YYYY-MM-DD', 'adj_close': float, 'close': float}
        """
        try:
            from models import db, MarketPriceBar
            ticker = ticker.upper()
            existing = {
                r.bar_date.isoformat(): r
                for r in MarketPriceBar.query.filter_by(ticker=ticker).all()
            }
            for b in bars:
                d = b['date'][:10]
                bd = date_cls.fromisoformat(d)
                adj = float(b['adj_close'])
                cl = float(b.get('close') or b['adj_close'])
                vol = b.get('volume')
                vol = float(vol) if vol is not None else None
                if d in existing:
                    existing[d].adj_close = adj
                    existing[d].close = cl
                    if vol is not None:
                        existing[d].volume = vol
                    existing[d].source = source
                else:
                    db.session.add(MarketPriceBar(
                        ticker=ticker, bar_date=bd,
                        adj_close=adj, close=cl, volume=vol, source=source,
                    ))
            db.session.commit()
        except Exception as e:
            logger.warning('Écriture DB échouée pour %s: %s', ticker, e)
            try:
                from models import db
                db.session.rollback()
            except Exception:
                pass

    def _fetch_daily_adjusted(self, ticker, nb_jours):
        """
        Récupère les barres journalières ajustées avec la cascade :
          1. Base de données (si fraîche)
          2. IBKR (ADJUSTED_LAST) si connecté
          3. Tiingo (fallback)
        Persiste les nouvelles barres. Returns: (DataFrame, error_str).
        """
        ticker = ticker.upper().strip()
        date_debut = (datetime.now() - relativedelta(days=nb_jours + 45)).date()

        # 1) Cache DB — frais si la dernière barre date de < 4 jours (week-ends/fériés)
        last_date = self._db_last_date(ticker)
        if last_date and (date_cls.today() - last_date).days <= 4:
            df = self._load_bars_from_db(ticker, date_debut)
            if df is not None and len(df) >= 13:
                return df, None

        # 2) IBKR
        if self.ibkr_service is not None:
            try:
                if self.ibkr_service.ensure_connected():
                    duration = self._jours_to_ib_duration(nb_jours + 45)
                    bars = self.ibkr_service.get_daily_bars(ticker, duration=duration)
                    if bars:
                        self._save_bars_to_db(ticker, bars, source='ibkr')
                        df = self._bars_to_df(bars, date_debut)
                        if df is not None and len(df) >= 13:
                            return df, None
            except Exception as e:
                logger.info('IBKR indisponible pour %s (%s) — fallback Tiingo', ticker, e)

        # 3) Tiingo (fallback)
        df_tiingo, err = self._fetch_daily_tiingo(ticker, nb_jours)
        if df_tiingo is not None:
            bars = [
                {'date': idx.strftime('%Y-%m-%d'),
                 'adj_close': float(row['adjClose']),
                 'close': float(row.get('close', row['adjClose'])),
                 'volume': float(row['volume']) if row.get('volume') is not None else None}
                for idx, row in df_tiingo.iterrows()
            ]
            self._save_bars_to_db(ticker, bars, source='tiingo')
            return df_tiingo[['adjClose']], None

        # 4) Dernier recours : DB même si périmée
        df = self._load_bars_from_db(ticker, date_debut)
        if df is not None and len(df) >= 13:
            return df, None

        return None, err or 'Aucune source de données disponible'

    @staticmethod
    def _jours_to_ib_duration(nb_jours):
        """Convertit un nombre de jours en durée IBKR ('N D' ou 'N Y')."""
        if nb_jours <= 365:
            return f'{max(30, nb_jours)} D'
        annees = math.ceil(nb_jours / 365)
        return f'{annees} Y'

    @staticmethod
    def _bars_to_df(bars, date_debut=None):
        """Convertit une liste de barres en DataFrame indexé par date."""
        if not bars:
            return None
        df = pd.DataFrame([{'date': b['date'], 'adjClose': b['adj_close']} for b in bars])
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df = df.sort_index()
        if date_debut:
            df = df[df.index >= pd.Timestamp(date_debut)]
        return df

    def _fetch_daily_tiingo(self, ticker, nb_jours):
        """Récupère les prix journaliers bruts depuis Tiingo. Returns: (df, error)."""
        if not self.api_key:
            return None, 'Tiingo non configuré'
        date_fin = datetime.now()
        date_debut = date_fin - relativedelta(days=nb_jours + 45)
        url = f"{self.base_url}/{ticker}/prices"
        params = {
            "startDate": date_debut.strftime("%Y-%m-%d"),
            "endDate": date_fin.strftime("%Y-%m-%d"),
            "token": self.api_key,
            "resampleFreq": "daily",
        }
        try:
            response = requests.get(url, params=params,
                                    headers={"Content-Type": "application/json"}, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if len(data) == 0:
                    return None, "Aucune donnée disponible"
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                return df.sort_index(), None
            elif response.status_code == 404:
                return None, "Ticker non trouvé"
            else:
                return None, f"Erreur API: {response.status_code}"
        except Exception as e:
            return None, str(e)
    
    def calculer_periode_analyse(self, date_calcul):
        """
        Calcule les dates de début et fin pour récupérer les données.
        On a besoin de 13 mois de données pour calculer le momentum 12-1.
        
        Args:
            date_calcul: Date de fin du calcul (datetime ou string YYYY-MM-DD)
        
        Returns:
            tuple: (date_debut, date_fin) au format string "YYYY-MM-DD"
        """
        if isinstance(date_calcul, str):
            date_calcul = datetime.strptime(date_calcul, "%Y-%m-%d")

        # date_fin = dernier jour du mois PRÉCÉDENT (mois en cours ignoré car incomplet,
        # ET le mois T-1 devient le "mois exclu" dans calculer_momentum_12_1 → vrai 12-1)
        premier_du_mois = date_calcul.replace(day=1)
        date_fin = premier_du_mois - relativedelta(days=1)  # ex: 9 avril → 31 mars

        # 13 mois avant date_fin pour avoir assez de barres mensuelles complètes
        date_debut = date_fin - relativedelta(months=13)

        return date_debut.strftime("%Y-%m-%d"), date_fin.strftime("%Y-%m-%d")
    
    def _load_monthly_db(self, ticker, date_debut, date_fin):
        """
        Charge les barres mensuelles depuis MonthlyPriceBar (yfinance).
        Renvoie un DataFrame avec 'adjClose' ou None.
        """
        try:
            from models import MonthlyPriceBar
            rows = (MonthlyPriceBar.query.filter_by(ticker=ticker.upper())
                    .filter(MonthlyPriceBar.bar_date >= date_cls.fromisoformat(date_debut[:10]))
                    .filter(MonthlyPriceBar.bar_date <= date_cls.fromisoformat(date_fin[:10]))
                    .order_by(MonthlyPriceBar.bar_date).all())
            if not rows:
                return None
            df = pd.DataFrame([{'date': r.bar_date, 'adjClose': r.adj_close} for r in rows])
            df['date'] = pd.to_datetime(df['date'])
            return df.set_index('date').sort_index()
        except Exception:
            return None

    def recuperer_prix_tiingo(self, ticker, date_debut, date_fin):
        """
        Récupère les prix mensuels ajustés.

        Cascade :
          1. MonthlyPriceBar (yfinance, 20 ans) — source primaire
          2. MarketPriceBar daily → resample mensuel (IBKR/Tiingo)
          3. Tiingo mensuel direct (fallback réseau)

        Args:
            ticker: Symbole de l'action (str)
            date_debut: Date de début au format "YYYY-MM-DD"
            date_fin: Date de fin au format "YYYY-MM-DD"

        Returns:
            tuple (DataFrame mensuel avec 'adjClose', error_str)
        """
        cache_key = f"{ticker}_{date_debut}_{date_fin}"
        cached, hit = self._monthly_cache.get(cache_key)
        if hit:
            return cached

        ts_fin = pd.Timestamp(date_fin) + pd.offsets.MonthEnd(0)

        # 1) MonthlyPriceBar yfinance (prioritaire — données longues et fiables)
        df_monthly = self._load_monthly_db(ticker, date_debut, date_fin)
        if df_monthly is not None and len(df_monthly) >= 13:
            try:
                df_monthly = df_monthly[df_monthly.index <= ts_fin]
            except Exception:
                pass
            if len(df_monthly) >= 13:
                result = (df_monthly, None)
                self._monthly_cache.set(cache_key, result)
                return result

        # 2) Cache daily → resample mensuel (IBKR / Tiingo / MarketPriceBar)
        try:
            dd = datetime.strptime(date_debut, "%Y-%m-%d")
        except (ValueError, TypeError):
            dd = datetime.now() - relativedelta(months=14)
        nb_jours = (datetime.now() - dd).days + 30

        df_daily, err = self._fetch_daily_adjusted(ticker, nb_jours)
        if df_daily is not None and not df_daily.empty:
            df_monthly = df_daily[['adjClose']].resample('ME').last().dropna()
            try:
                df_monthly = df_monthly[df_monthly.index <= ts_fin]
            except Exception:
                pass
            if len(df_monthly) >= 13:
                result = (df_monthly, None)
                self._monthly_cache.set(cache_key, result)
                return result

        return None, err or "Données mensuelles insuffisantes"
    
    def calculer_momentum_12_1(self, df_prix):
        """
        Calcule le momentum 12-1 (rendement sur 12 mois, excluant le dernier mois).

        La stratégie momentum 12-1 classique utilise le rendement des 12 derniers mois
        en excluant le mois le plus récent (pour éviter l'effet de retour à court terme).

        Args:
            df_prix: DataFrame avec les prix mensuels (doit contenir 'adjClose')

        Returns:
            tuple: (momentum, details_mensuels, perf_recent_1m) ou (None, None, None)
                - momentum: float, rendement 12-1 en pourcentage
                - details_mensuels: list of dict avec prix et rendement par mois
                - perf_recent_1m: float, perf du mois exclu (T-1 à T) — signal mean-reversion
        """
        if df_prix is None or len(df_prix) < 13:
            return None, None, None

        # Trier par date croissante
        df_prix = df_prix.sort_index()

        # Prix ajusté il y a 12 mois (le plus ancien)
        prix_debut = df_prix['adjClose'].iloc[-13]

        # Prix ajusté il y a 1 mois (on exclut le mois le plus récent)
        prix_fin = df_prix['adjClose'].iloc[-2]

        # Prix actuel (mois le plus récent, exclu du momentum)
        prix_actuel = df_prix['adjClose'].iloc[-1]

        if prix_debut <= 0:
            return None, None, None

        momentum = ((prix_fin - prix_debut) / prix_debut) * 100

        # Perf du mois exclu : signal d'alerte mean-reversion
        perf_recent_1m = round(((prix_actuel - prix_fin) / prix_fin) * 100, 2) if prix_fin > 0 else 0.0

        # Calculer les détails mensuels (du mois -13 au mois -2)
        details_mensuels = []
        for i in range(-13, -1):
            date = df_prix.index[i]
            prix = df_prix['adjClose'].iloc[i]

            if i > -13:
                prix_precedent = df_prix['adjClose'].iloc[i - 1]
                rendement_mensuel = ((prix - prix_precedent) / prix_precedent) * 100 if prix_precedent > 0 else 0
            else:
                rendement_mensuel = 0

            rendement_cumule = ((prix - prix_debut) / prix_debut) * 100

            details_mensuels.append({
                'mois': date.strftime('%Y-%m'),
                'prix': round(prix, 2),
                'rendement_mensuel': round(rendement_mensuel, 2),
                'rendement_cumule': round(rendement_cumule, 2)
            })

        return momentum, details_mensuels, perf_recent_1m
    
    def analyser_panel(self, panel_tickers, date_calcul=None):
        """
        Analyse l'ensemble du panel d'actions et calcule le momentum de chacune.
        
        Args:
            panel_tickers: Liste des tickers à analyser
            date_calcul: Date du calcul (datetime ou string, None = aujourd'hui)
        
        Returns:
            dict: {
                'success': bool,
                'date_calcul': str,
                'resultats': list of dict,
                'erreurs': list of dict
            }
        """
        if date_calcul is None:
            date_calcul = datetime.now()
        elif isinstance(date_calcul, str):
            date_calcul = datetime.strptime(date_calcul, "%Y-%m-%d")
        
        date_debut, date_fin = self.calculer_periode_analyse(date_calcul)
        
        resultats = []
        erreurs = []
        
        for ticker in panel_tickers:
            ticker = ticker.upper().strip()
            
            # Récupération des prix
            df_prix, erreur = self.recuperer_prix_tiingo(ticker, date_debut, date_fin)
            
            if erreur:
                erreurs.append({'ticker': ticker, 'erreur': erreur})
                continue
            
            # Calcul du momentum avec détails mensuels
            momentum, details_mensuels, perf_recent_1m = self.calculer_momentum_12_1(df_prix)

            if momentum is not None:
                resultats.append({
                    'ticker': ticker,
                    'momentum': momentum,
                    'perf_recent_1m': perf_recent_1m,
                    'details_mensuels': details_mensuels
                })
            else:
                erreurs.append({'ticker': ticker, 'erreur': 'Données insuffisantes pour le calcul'})
        
        # Tri par momentum décroissant
        resultats.sort(key=lambda x: x['momentum'], reverse=True)

        # Ajout du rang
        for i, r in enumerate(resultats):
            r['rank'] = i + 1

        # Régime de marché SPY/SMA200 — 1 appel Tiingo, caché 4h
        market_regime = self.get_market_regime()

        return {
            'success': len(resultats) > 0,
            'date_calcul': date_calcul.strftime("%Y-%m-%d"),
            'resultats': resultats,
            'erreurs': erreurs,
            'market_regime': market_regime
        }
    
    def _vol_realisee_126j(self, ticker):
        """
        Volatilité réalisée annualisée sur ~126 sessions (6 mois), méthode du papier
        Barroso & Santa-Clara (2014), éq. 5 :
            σ̂²_t = 21 × Σ_{j=0}^{125} r²_{t-1-j} / 126   (variance mensuelle)
        annualisée ensuite (× 12). En pratique : variance journalière moyenne × 252.

        Utilise les prix journaliers (cascade DB → IBKR → Tiingo, déjà cachés).

        Args:
            ticker: Symbole de l'action

        Returns:
            float: volatilité annualisée en décimal (ex. 0.30 pour 30%), ou None.
        """
        df, err = self.recuperer_prix_journaliers(ticker, nb_jours=200)
        if df is None or err or 'adjClose' not in df:
            return None

        closes = df.sort_index()['adjClose'].values
        if len(closes) < 21:
            return None

        # Rendements simples journaliers
        rets = closes[1:] / closes[:-1] - 1.0
        # Ne garder que les 126 dernières sessions (éq. 5)
        rets = rets[-126:]
        if len(rets) < 20:
            return None

        daily_var = float((rets ** 2).sum() / len(rets))
        annual_vol = math.sqrt(daily_var * 252)
        return annual_vol if annual_vol > 1e-6 else None

    def _vol_portefeuille_126j(self, poids):
        """
        Volatilité réalisée annualisée du PANIER pondéré sur ~126 sessions (Layer 2,
        cœur de Barroso & Santa-Clara 2014, éq. 5). Contrairement à la vol par actif,
        elle intègre les corrélations entre titres → capte la nervosité de marché.

        Args:
            poids: dict {ticker: poids_relatif} (sera normalisé pour sommer à 1)

        Returns:
            float: volatilité annualisée du panier en décimal, ou None.
        """
        poids = {t: w for t, w in poids.items() if w and w > 0}
        total = sum(poids.values())
        if not poids or total <= 0:
            return None
        poids = {t: w / total for t, w in poids.items()}

        # Récupérer les prix journaliers de chaque titre, alignés sur les dates communes
        series = {}
        for t in poids:
            df, err = self.recuperer_prix_journaliers(t, nb_jours=200)
            if df is None or err or 'adjClose' not in df:
                continue
            series[t] = df.sort_index()['adjClose']

        if not series:
            return None

        prices = pd.DataFrame(series).dropna()
        if len(prices) < 21:
            return None

        # Rendements journaliers, puis rendement du panier = Σ poids_i × r_i
        rets = prices.pct_change().dropna()
        # Renormaliser les poids sur les titres effectivement disponibles
        dispo = {t: poids[t] for t in rets.columns if t in poids}
        s = sum(dispo.values())
        if s <= 0:
            return None
        dispo = {t: w / s for t, w in dispo.items()}

        port_rets = sum(rets[t] * w for t, w in dispo.items())
        port_rets = port_rets.values[-126:]
        if len(port_rets) < 20:
            return None

        daily_var = float((port_rets ** 2).sum() / len(port_rets))
        annual_vol = math.sqrt(daily_var * 252)
        return annual_vol if annual_vol > 1e-6 else None

    def generer_recommandations(self, resultats_analyse, nb_top,
                                vol_scaling=False, vol_target_pct=12.0,
                                max_exposure_pct=250.0,
                                portfolio_filter=False,
                                portfolio_vol_threshold_pct=20.0):
        """
        Génère les signaux d'investissement et calcule les allocations.

        Deux couches de gestion du risque, combinables :

        Layer 1 — répartition par actif :
          - vol_scaling=False (défaut) : pondération inverse-volatilité normalisée à 100%.
          - vol_scaling=True : « volatility scaling » par actif (Barroso & Santa-Clara 2014,
            footnote 13). Poids_i = σ_target / σ_asset_i (vol récente 126j de chaque titre).
            L'exposition brute peut dépasser 100% (levier), plafonnée à max_exposure_pct.

        Layer 2 — filtre portefeuille « anti-krach » (portfolio_filter=True, éq. 5-6) :
          Applique un facteur global f = min(1, σ_seuil / σ̂_panier) aux allocations.
          σ̂_panier = vol réalisée 126j du panier pondéré (intègre les corrélations).
          f ≤ 1 : ne réduit l'exposition (désamorce le levier) qu'en cas de turbulence.

        Args:
            resultats_analyse: Résultat de analyser_panel()
            nb_top: Nombre d'actions à sélectionner pour investir
            vol_scaling: active la mise à l'échelle par volatilité (Long)
            vol_target_pct: volatilité cible annualisée en % (ex. 12)
            max_exposure_pct: plafond d'exposition brute en % (ex. 250)
            portfolio_filter: active le frein anti-krach au niveau du panier
            portfolio_vol_threshold_pct: vol annualisée seuil du panier en % (ex. 20)

        Returns:
            dict avec recommandations + métriques (vol_scaling, exposition_brute,
            portfolio_filter, portfolio_vol, portfolio_factor).
        """
        if not resultats_analyse['success']:
            return {
                'date_calcul': resultats_analyse['date_calcul'],
                'nb_top': nb_top,
                'recommandations': [],
                'total_investir': 0,
                'erreurs': resultats_analyse['erreurs'],
                'market_regime': resultats_analyse.get('market_regime'),
                'vol_scaling': bool(vol_scaling),
                'exposition_brute': 0.0,
                'portfolio_filter': bool(portfolio_filter),
                'portfolio_vol': None,
                'portfolio_threshold_pct': portfolio_vol_threshold_pct if portfolio_filter else None,
                'portfolio_factor': None
            }
        
        resultats = resultats_analyse['resultats']
        nb_actions = len(resultats)
        nb_selection = min(nb_top, nb_actions)

        # ------------------------------------------------------------------
        # Pondération inverse-volatilité
        # Principe : allouer moins aux titres volatils, plus aux défensifs.
        # Vol estimée sur les rendements mensuels déjà fetchés (pas d'appel API).
        # Formule : vol_mensuelle = std(rendements_mensuels) × √12 (annualisée)
        #           weight_i = 1 / vol_i
        #           allocation_i = weight_i / Σ(weights) × 100
        # ------------------------------------------------------------------
        VOL_DEFAULT = 0.20  # 20% fallback si données insuffisantes

        def _vol_annualisee(details_mensuels):
            """Volatilité annualisée depuis les rendements mensuels (décimaux)."""
            returns = [
                d['rendement_mensuel'] / 100
                for d in details_mensuels
                if d.get('rendement_mensuel', 0) != 0
            ]
            if len(returns) < 3:
                return VOL_DEFAULT
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / len(returns)
            monthly_vol = math.sqrt(variance)
            annual_vol = monthly_vol * math.sqrt(12)
            return annual_vol if annual_vol > 1e-6 else VOL_DEFAULT

        # Titres "Investir" : top N avec momentum > 0
        investir_resultats = [r for r in resultats[:nb_selection] if r['momentum'] > 0]

        # ------------------------------------------------------------------
        # Choix de la volatilité utilisée par titre :
        #   - vol_scaling : vol réalisée 126j (éq. 5 du papier), fallback mensuel puis défaut
        #   - sinon       : vol mensuelle annualisée (rendements déjà fetchés)
        # ------------------------------------------------------------------
        def _vol_titre(r):
            if vol_scaling:
                v = self._vol_realisee_126j(r['ticker'])
                if v is not None:
                    return v
            return _vol_annualisee(r.get('details_mensuels', []))

        vols = {r['ticker']: _vol_titre(r) for r in investir_resultats}

        multipliers = {}

        if vol_scaling:
            # --------------------------------------------------------------
            # Volatility scaling par actif (Barroso & Santa-Clara 2014, fn 13)
            #   poids_i = σ_target / σ_asset_i  →  allocation_i (%) = 100 × poids_i
            #   plafond strict sur l'exposition brute : Σ allocation_i ≤ max_exposure_pct
            #   (si Σ < plafond, le reste demeure en cash)
            # --------------------------------------------------------------
            vt = vol_target_pct / 100.0
            allocations = {}
            for t, v in vols.items():
                mult = vt / v if v and v > 1e-6 else 0.0
                multipliers[t] = mult
                allocations[t] = mult * 100.0  # en %

            brut = sum(allocations.values())
            if brut > max_exposure_pct and brut > 0:
                facteur = max_exposure_pct / brut
                allocations = {t: a * facteur for t, a in allocations.items()}
                multipliers = {t: m * facteur for t, m in multipliers.items()}

            allocations = {t: round(a, 2) for t, a in allocations.items()}
        else:
            # Pondération inverse-volatilité normalisée à 100% (comportement historique)
            inv_vol_weights = {t: 1.0 / v for t, v in vols.items() if v and v > 1e-6}
            total_weight = sum(inv_vol_weights.values())
            if total_weight > 0 and investir_resultats:
                allocations = {
                    t: round(w / total_weight * 100, 2)
                    for t, w in inv_vol_weights.items()
                }
                # Correction d'arrondi sur le premier ticker (impact max ±0.0x%)
                rounding_error = round(100.0 - sum(allocations.values()), 2)
                first_ticker = next(iter(allocations))
                allocations[first_ticker] = round(allocations[first_ticker] + rounding_error, 2)
            else:
                allocations = {}

        # ------------------------------------------------------------------
        # Layer 2 — Filtre portefeuille « anti-krach » (Barroso & Santa-Clara, éq. 5-6)
        #   f = min(1, σ_seuil / σ̂_panier) appliqué à toutes les allocations.
        #   σ̂_panier capte les corrélations → désamorce le levier en turbulence.
        # ------------------------------------------------------------------
        portfolio_vol = None
        portfolio_factor = 1.0
        if portfolio_filter and allocations:
            portfolio_vol = self._vol_portefeuille_126j(allocations)
            if portfolio_vol and portfolio_vol > 1e-6:
                seuil = portfolio_vol_threshold_pct / 100.0
                portfolio_factor = min(1.0, seuil / portfolio_vol)
                if portfolio_factor < 1.0:
                    allocations = {t: round(a * portfolio_factor, 2)
                                   for t, a in allocations.items()}
                    multipliers = {t: m * portfolio_factor for t, m in multipliers.items()}

        recommandations = []

        for i, r in enumerate(resultats):
            multiplier = None
            if i < nb_selection:
                if r['momentum'] > 0:
                    signal = "Investir"
                    allocation = allocations.get(r['ticker'], 0.0)
                    vol = round(vols.get(r['ticker'], VOL_DEFAULT) * 100, 1)
                    if vol_scaling:
                        multiplier = round(multipliers.get(r['ticker'], 0.0), 3)
                else:
                    # Momentum négatif dans le top N → rester en cash
                    signal = "Cash"
                    allocation = 0.0
                    vol = round(_vol_annualisee(r.get('details_mensuels', [])) * 100, 1)
            else:
                signal = "Sortir"
                allocation = 0.0
                vol = None

            recommandations.append({
                'ticker': r['ticker'],
                'momentum': round(r['momentum'], 2),
                'perf_recent_1m': r.get('perf_recent_1m'),
                'vol_annualisee': vol,   # % annualisé — visible dans l'UI
                'multiplier': multiplier,  # σ_target / σ_asset (mode vol_scaling)
                'signal': signal,
                'allocation': allocation,
                'rank': r['rank'],
                'details_mensuels': r.get('details_mensuels', [])
            })

        total_investir = sum(1 for r in recommandations if r['signal'] == 'Investir')
        exposition_brute = round(sum(r['allocation'] for r in recommandations), 2)

        return {
            'date_calcul': resultats_analyse['date_calcul'],
            'nb_top': nb_top,
            'recommandations': recommandations,
            'total_investir': total_investir,
            'erreurs': resultats_analyse['erreurs'],
            'market_regime': resultats_analyse.get('market_regime'),
            'vol_scaling': bool(vol_scaling),
            'vol_target_pct': vol_target_pct if vol_scaling else None,
            'max_exposure_pct': max_exposure_pct if vol_scaling else None,
            'exposition_brute': exposition_brute,
            'portfolio_filter': bool(portfolio_filter),
            'portfolio_vol': round(portfolio_vol * 100, 1) if portfolio_vol else None,
            'portfolio_threshold_pct': portfolio_vol_threshold_pct if portfolio_filter else None,
            'portfolio_factor': round(portfolio_factor, 3) if portfolio_filter else None
        }
    
    def valider_ticker(self, ticker):
        """
        Vérifie si un ticker existe sur Tiingo.
        Résultat mis en cache 24h (un ticker ne disparaît pas dans la journée).

        Args:
            ticker: Symbole à vérifier

        Returns:
            dict: {'valid': bool, 'name': str or None, 'error': str or None}
        """
        cache_key = ticker.upper()
        cached, hit = self._ticker_cache.get(cache_key)
        if hit:
            return cached

        url = f"{self.base_url}/{ticker}"

        try:
            response = requests.get(
                url,
                params={"token": self.api_key},
                headers={"Content-Type": "application/json"},
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                result = {
                    'valid': True,
                    'name': data.get('name', ''),
                    'error': None
                }
                self._ticker_cache.set(cache_key, result)
                return result
            elif response.status_code == 404:
                return {
                    'valid': False,
                    'name': None,
                    'error': 'Ticker non trouvé'
                }
            else:
                return {
                    'valid': False,
                    'name': None,
                    'error': f'Erreur API: {response.status_code}'
                }

        except Exception as e:
            return {
                'valid': False,
                'name': None,
                'error': str(e)
            }
    
    def get_market_regime(self):
        """
        Détecte le régime de marché via SPY vs sa SMA200.

        Si SPY < SMA200 → marché baissier : le momentum 12-1 est moins fiable
        et les drawdowns sont historiquement beaucoup plus sévères.
        (Référence : Faber 2007, Antonacci 2012)

        Utilise le cache journalier (4h TTL) — 1 appel Tiingo uniquement.

        Returns:
            dict: {
                'regime': 'BULL' | 'BEAR',
                'spy_price': float,
                'sma200': float,
                'pct_vs_sma200': float,  # écart en %
                'error': str or None
            }
        """
        df, erreur = self.recuperer_prix_journaliers('SPY', nb_jours=300)

        if erreur or df is None or len(df) < 200:
            return {'regime': 'UNKNOWN', 'spy_price': None, 'sma200': None,
                    'pct_vs_sma200': None, 'error': erreur or 'Données insuffisantes'}

        closes = df['adjClose'].values
        sma200 = round(float(closes[-200:].mean()), 2)
        spy_price = round(float(closes[-1]), 2)
        pct_vs_sma200 = round(((spy_price - sma200) / sma200) * 100, 2)
        regime = 'BULL' if spy_price > sma200 else 'BEAR'

        return {
            'regime': regime,
            'spy_price': spy_price,
            'sma200': sma200,
            'pct_vs_sma200': pct_vs_sma200,
            'error': None
        }

    # =========================================================================
    # MÉTHODES POUR STRATÉGIE SHORT ET OPTIONS
    # =========================================================================
    
    def recuperer_prix_journaliers(self, ticker, nb_jours=100):
        """
        Récupère les prix journaliers pour le calcul du momentum Short et Options.
        Résultat mis en cache 4h (les données EOD ne changent qu'une fois par jour).

        Args:
            ticker: Symbole de l'action
            nb_jours: Nombre de jours calendaires à récupérer (défaut: 100)

        Returns:
            DataFrame pandas avec les prix journaliers ou (None, erreur)
        """
        today = datetime.now().strftime('%Y-%m-%d')
        cache_key = f"{ticker}_{nb_jours}_{today}"
        cached, hit = self._daily_cache.get(cache_key)
        if hit:
            return cached

        # Cascade multi-source : DB → IBKR → Tiingo (avec persistance)
        df, err = self._fetch_daily_adjusted(ticker, nb_jours)
        if df is None or df.empty:
            return None, err or "Aucune donnée disponible"

        result = (df, None)
        self._daily_cache.set(cache_key, result)
        return result
    
    def analyser_panel_short(self, panel_tickers, lookback=63, skip_recent=5, date_calcul=None):
        """
        Analyse le panel Short avec la méthode momentum court terme.
        
        Formule: Momentum = Perf(T-lookback à T-skip)
        """
        if date_calcul is None:
            date_calcul = datetime.now()
        elif isinstance(date_calcul, str):
            date_calcul = datetime.strptime(date_calcul, "%Y-%m-%d")
        
        resultats = []
        erreurs = []
        
        for ticker in panel_tickers:
            ticker = ticker.upper().strip()
            
            df_prix, erreur = self.recuperer_prix_journaliers(ticker, lookback + 30)
            
            if erreur:
                erreurs.append({'ticker': ticker, 'erreur': erreur})
                continue
            
            if df_prix is None or len(df_prix) < lookback + skip_recent + 1:
                erreurs.append({'ticker': ticker, 'erreur': 'Données insuffisantes'})
                continue
            
            df = df_prix.sort_index()
            
            prix_lookback = df['adjClose'].iloc[-(lookback + skip_recent + 1)]
            prix_skip = df['adjClose'].iloc[-(skip_recent + 1)]
            prix_actuel = df['adjClose'].iloc[-1]
            
            if prix_lookback <= 0 or prix_skip <= 0:
                erreurs.append({'ticker': ticker, 'erreur': 'Prix invalide'})
                continue
            
            perf_lookback = ((prix_skip - prix_lookback) / prix_lookback) * 100
            perf_recent = ((prix_actuel - prix_skip) / prix_skip) * 100
            momentum = perf_lookback
            
            resultats.append({
                'ticker': ticker,
                'momentum': round(momentum, 2),
                'perf_lookback': round(perf_lookback, 2),
                'perf_recent': round(perf_recent, 2),
                'prix_actuel': round(prix_actuel, 2)
            })
        
        resultats.sort(key=lambda x: x['momentum'])
        
        for i, r in enumerate(resultats):
            r['rank'] = i + 1
        
        return {
            'success': len(resultats) > 0,
            'date_calcul': date_calcul.strftime("%Y-%m-%d"),
            'resultats': resultats,
            'erreurs': erreurs,
            'methode': {
                'nom': 'Momentum Short Court Terme',
                'formule': f'Perf(T-{lookback} à T-{skip_recent})',
                'lookback': lookback,
                'skip_recent': skip_recent
            }
        }

