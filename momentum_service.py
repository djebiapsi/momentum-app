# -*- coding: utf-8 -*-
"""
Service de calcul du Momentum
=============================
Contient toute la logique métier pour calculer le momentum 12-1.
Adapté du script strategy.py original.
"""

import math
import requests
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from cache_utils import TTLCache


class MomentumService:
    """
    Service pour calculer le momentum des actions.
    """

    def __init__(self, api_key):
        """
        Initialise le service avec la clé API Tiingo.

        Args:
            api_key: Clé API Tiingo
        """
        self.api_key = api_key
        self.base_url = "https://api.tiingo.com/tiingo/daily"

        # Cache en mémoire pour éviter les appels Tiingo redondants (~50 req/h)
        self._monthly_cache = TTLCache(ttl_seconds=6 * 3600)   # Prix mensuels : 6h
        self._daily_cache   = TTLCache(ttl_seconds=4 * 3600)   # Prix journaliers : 4h
        self._ticker_cache  = TTLCache(ttl_seconds=24 * 3600)  # Validation ticker : 24h
    
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
    
    def recuperer_prix_tiingo(self, ticker, date_debut, date_fin):
        """
        Récupère les prix historiques ajustés depuis l'API Tiingo.
        Résultat mis en cache 6h (les prix mensuels ne changent pas dans la journée).

        Args:
            ticker: Symbole de l'action (str)
            date_debut: Date de début au format "YYYY-MM-DD"
            date_fin: Date de fin au format "YYYY-MM-DD"

        Returns:
            DataFrame pandas avec les prix ou None en cas d'erreur
        """
        cache_key = f"{ticker}_{date_debut}_{date_fin}"
        cached, hit = self._monthly_cache.get(cache_key)
        if hit:
            return cached

        url = f"{self.base_url}/{ticker}/prices"

        params = {
            "startDate": date_debut,
            "endDate": date_fin,
            "token": self.api_key,
            "resampleFreq": "monthly"
        }

        headers = {"Content-Type": "application/json"}

        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)

            if response.status_code == 200:
                data = response.json()

                if len(data) == 0:
                    return None, f"Aucune donnée disponible"

                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)

                result = (df, None)
                self._monthly_cache.set(cache_key, result)
                return result

            elif response.status_code == 404:
                return None, f"Ticker non trouvé sur Tiingo"
            elif response.status_code == 401:
                return None, f"Erreur d'authentification API"
            else:
                return None, f"Erreur API: Code {response.status_code}"

        except requests.exceptions.Timeout:
            return None, "Timeout de la requête"
        except requests.exceptions.RequestException as e:
            return None, f"Erreur de connexion: {str(e)}"
    
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
    
    def generer_recommandations(self, resultats_analyse, nb_top):
        """
        Génère les signaux d'investissement et calcule les allocations.
        
        Args:
            resultats_analyse: Résultat de analyser_panel()
            nb_top: Nombre d'actions à sélectionner pour investir
        
        Returns:
            dict: {
                'date_calcul': str,
                'nb_top': int,
                'recommandations': list,
                'total_investir': int,
                'erreurs': list
            }
        """
        if not resultats_analyse['success']:
            return {
                'date_calcul': resultats_analyse['date_calcul'],
                'nb_top': nb_top,
                'recommandations': [],
                'total_investir': 0,
                'erreurs': resultats_analyse['erreurs'],
                'market_regime': resultats_analyse.get('market_regime')
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

        # Calculer les vols uniquement pour les titres "Investir" (top N, momentum > 0)
        investir_resultats = [r for r in resultats[:nb_selection] if r['momentum'] > 0]
        vols = {
            r['ticker']: _vol_annualisee(r.get('details_mensuels', []))
            for r in investir_resultats
        }
        inv_vol_weights = {t: 1.0 / v for t, v in vols.items()}
        total_weight = sum(inv_vol_weights.values())

        # Allocations normalisées à 100%, arrondies — ajuster la dernière pour sommer pile à 100
        if total_weight > 0 and investir_resultats:
            raw_allocs = {
                t: round(w / total_weight * 100, 2)
                for t, w in inv_vol_weights.items()
            }
            # Correction d'arrondi sur le premier ticker (impact max ±0.0x%)
            rounding_error = round(100.0 - sum(raw_allocs.values()), 2)
            first_ticker = next(iter(raw_allocs))
            raw_allocs[first_ticker] = round(raw_allocs[first_ticker] + rounding_error, 2)
            allocations_invvol = raw_allocs
        else:
            allocations_invvol = {}

        recommandations = []

        for i, r in enumerate(resultats):
            if i < nb_selection:
                if r['momentum'] > 0:
                    signal = "Investir"
                    allocation = allocations_invvol.get(r['ticker'], 0.0)
                    vol = round(vols.get(r['ticker'], VOL_DEFAULT) * 100, 1)
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
                'signal': signal,
                'allocation': allocation,
                'rank': r['rank'],
                'details_mensuels': r.get('details_mensuels', [])
            })

        total_investir = sum(1 for r in recommandations if r['signal'] == 'Investir')

        return {
            'date_calcul': resultats_analyse['date_calcul'],
            'nb_top': nb_top,
            'recommandations': recommandations,
            'total_investir': total_investir,
            'erreurs': resultats_analyse['erreurs'],
            'market_regime': resultats_analyse.get('market_regime')
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

        date_fin = datetime.now()
        date_debut = date_fin - relativedelta(days=nb_jours + 30)

        url = f"{self.base_url}/{ticker}/prices"

        params = {
            "startDate": date_debut.strftime("%Y-%m-%d"),
            "endDate": date_fin.strftime("%Y-%m-%d"),
            "token": self.api_key,
            "resampleFreq": "daily"
        }

        try:
            response = requests.get(url, params=params, headers={"Content-Type": "application/json"}, timeout=30)

            if response.status_code == 200:
                data = response.json()

                if len(data) == 0:
                    return None, "Aucune donnée disponible"

                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df = df.sort_index()

                result = (df, None)
                self._daily_cache.set(cache_key, result)
                return result

            elif response.status_code == 404:
                return None, "Ticker non trouvé"
            else:
                return None, f"Erreur API: {response.status_code}"

        except Exception as e:
            return None, str(e)
    
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

