# -*- coding: utf-8 -*-
"""
Service de Screening d'actions (Optimisé)
=========================================
Génère automatiquement un panel de 50 tickers basé sur des critères quantitatifs.

COMPROMIS: Le MarketCap n'est pas disponible en compte Tiingo gratuit.
On utilise l'ADV (Average Daily Dollar Volume) comme proxy de taille/liquidité.

Critères:
- ADV >= 5M$ (élimine les petites caps illiquides)
- Score = log(ADV) (les plus liquides = généralement les plus grandes caps)

Résultat: 1 seul appel API !
"""

import requests
import math
from datetime import datetime


class ScreenerService:
    """
    Service pour screener et sélectionner les meilleures actions US.
    Utilise l'endpoint IEX bulk de Tiingo (1 seul appel API).
    """
    
    def __init__(self, api_key):
        """
        Initialise le service avec la clé API Tiingo.
        
        Args:
            api_key: Clé API Tiingo
        """
        self.api_key = api_key
        self.base_url = "https://api.tiingo.com"
        
        # Critères de filtrage
        self.min_adv = 5000000  # 5 millions $ de volume journalier
        self.target_count = 50  # Nombre de tickers à sélectionner
        
        # Compteur d'appels API
        self.api_calls = 0
    
    def _api_call(self, url, params, timeout=60):
        """
        Effectue un appel API et compte les requêtes.
        
        Args:
            url: URL de l'endpoint
            params: Paramètres de la requête
            timeout: Timeout en secondes
        
        Returns:
            tuple: (data, error)
        """
        self.api_calls += 1
        params['token'] = self.api_key
        
        try:
            response = requests.get(
                url,
                params=params,
                headers={"Content-Type": "application/json"},
                timeout=timeout
            )
            
            if response.status_code == 200:
                return response.json(), None
            else:
                return None, f"Erreur API (code {response.status_code})"
                
        except requests.exceptions.Timeout:
            return None, "Timeout de la requête"
        except Exception as e:
            return None, str(e)
    
    def get_iex_bulk_data(self):
        """
        Récupère les données IEX (prix et volume) pour TOUS les tickers en 1 appel.
        
        Returns:
            tuple: (dict {ticker: {price, volume, adv}}, error)
        """
        url = f"{self.base_url}/iex"
        data, error = self._api_call(url, {}, timeout=120)
        
        if error:
            return None, error
        
        result = {}
        for item in data:
            ticker = item.get('ticker')
            if not ticker:
                continue
            
            # Utiliser prevClose ou tngoLast comme prix
            price = item.get('prevClose') or item.get('tngoLast') or item.get('last') or 0
            volume = item.get('volume') or 0
            
            if price > 0 and volume > 0:
                adv = price * volume
                result[ticker] = {
                    'price': round(price, 2),
                    'volume': int(volume),
                    'adv': adv
                }
        
        return result, None
    
    def calculate_score(self, adv):
        """
        Calcule le score de sélection basé sur l'ADV.
        
        Score = log(ADV)
        
        Plus l'ADV est élevé, plus le score est élevé.
        Les actions très liquides sont généralement des grandes caps.
        
        Args:
            adv: Average Daily Dollar Volume en $
        
        Returns:
            float: Score
        """
        if adv <= 0:
            return 0
        
        return math.log(adv)
    
    def screen_universe(self, progress_callback=None):
        """
        Effectue le screening complet de l'univers US.
        ULTRA-OPTIMISÉ: 1 seul appel API !
        
        Étapes:
        1. Récupère les données IEX bulk (prix + volume) - 1 appel API
        2. Filtre par ADV >= 5M$
        3. Calcule Score = log(ADV)
        4. Retourne les 50 meilleurs
        
        Args:
            progress_callback: Fonction appelée avec (current, total, message)
        
        Returns:
            dict: {
                'success': bool,
                'tickers': list of dict,
                'stats': dict,
                'error': str or None
            }
        """
        self.api_calls = 0  # Reset compteur
        
        def report(current, total, msg):
            if progress_callback:
                progress_callback(current, total, msg)
        
        # =================================================================
        # ÉTAPE 1: Données IEX bulk - prix et volume (1 appel API)
        # =================================================================
        report(0, 100, "📊 Récupération des données IEX (prix + volume)...")
        
        iex_data, error = self.get_iex_bulk_data()
        
        if error:
            return self._error_result(f"Erreur IEX: {error}")
        
        if not iex_data:
            return self._error_result("Aucune donnée IEX disponible")
        
        all_tickers = list(iex_data.keys())
        report(30, 100, f"✅ {len(all_tickers)} tickers récupérés (1 appel API)")
        
        # =================================================================
        # ÉTAPE 2: Filtrage par ADV >= 5M$ (0 appel API)
        # =================================================================
        report(40, 100, "📈 Filtrage par ADV >= 5M$...")
        
        tickers_above_adv = [
            t for t in all_tickers 
            if iex_data[t]['adv'] >= self.min_adv
        ]
        
        report(50, 100, f"✅ {len(tickers_above_adv)} tickers avec ADV >= 5M$")
        
        if len(tickers_above_adv) == 0:
            return self._error_result("Aucun ticker ne respecte le critère ADV >= 5M$")
        
        # =================================================================
        # ÉTAPE 3: Calcul des scores (0 appel API)
        # =================================================================
        report(60, 100, "🎯 Calcul des scores (log(ADV))...")
        
        scored_tickers = []
        
        for ticker in tickers_above_adv:
            iex = iex_data[ticker]
            adv = iex['adv']
            
            # Calcul du score = log(ADV)
            score = self.calculate_score(adv)
            
            scored_tickers.append({
                'ticker': ticker,
                'price': iex['price'],
                'volume': iex['volume'],
                'volume_display': self._format_number(iex['volume']),
                'adv': adv,
                'adv_display': self._format_number(adv),
                'score': round(score, 2)
            })
        
        report(75, 100, f"📊 {len(scored_tickers)} tickers scorés")
        
        # =================================================================
        # ÉTAPE 4: Tri et sélection des 50 meilleurs
        # =================================================================
        report(85, 100, "🏆 Sélection des 50 meilleurs...")
        
        # Trier par score décroissant (= ADV décroissant)
        scored_tickers.sort(key=lambda x: x['score'], reverse=True)
        
        # Prendre les 50 premiers
        top_50 = scored_tickers[:self.target_count]
        
        # Ajouter le rang
        for i, t in enumerate(top_50):
            t['rank'] = i + 1
        
        report(100, 100, f"✅ Terminé ! {self.api_calls} appel(s) API utilisé(s)")
        
        return {
            'success': True,
            'tickers': top_50,
            'stats': {
                'total_tickers': len(all_tickers),
                'above_adv_threshold': len(tickers_above_adv),
                'selected': len(top_50),
                'min_adv': self._format_number(top_50[-1]['adv']) if top_50 else '-',
                'max_adv': self._format_number(top_50[0]['adv']) if top_50 else '-',
                'api_calls_used': self.api_calls,
                'generated_at': datetime.now().isoformat()
            },
            'error': None
        }
    
    def _error_result(self, error_msg):
        """Retourne un résultat d'erreur formaté."""
        return {
            'success': False,
            'tickers': [],
            'stats': {'api_calls_used': self.api_calls},
            'error': error_msg
        }
    
    def _format_number(self, num):
        """Formate un nombre en format lisible (1.5B, 25M, etc.)"""
        if num >= 1000000000000:
            return f"{num / 1000000000000:.1f}T"
        elif num >= 1000000000:
            return f"{num / 1000000000:.1f}B"
        elif num >= 1000000:
            return f"{num / 1000000:.1f}M"
        elif num >= 1000:
            return f"{num / 1000:.1f}K"
        else:
            return str(int(num))
