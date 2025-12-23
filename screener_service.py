# -*- coding: utf-8 -*-
"""
Service de Screening d'actions
==============================
Génère automatiquement un panel de 50 tickers basé sur des critères quantitatifs :
- MarketCap >= 1B$
- ADV (Average Daily Volume) >= 5M$
- Score = log(MarketCap) × log(ADV)
"""

import requests
import math
from datetime import datetime, timedelta


class ScreenerService:
    """
    Service pour screener et sélectionner les meilleures actions US.
    Utilise l'API Tiingo pour récupérer les données fondamentales.
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
        self.min_market_cap = 1_000_000_000  # 1 milliard $
        self.min_adv = 5_000_000  # 5 millions $ de volume journalier
        self.target_count = 50  # Nombre de tickers à sélectionner
    
    def get_supported_tickers(self):
        """
        Récupère la liste des tickers US supportés par Tiingo.
        
        Returns:
            list: Liste des tickers avec leurs métadonnées
        """
        url = f"{self.base_url}/tiingo/daily"
        
        try:
            response = requests.get(
                url,
                params={"token": self.api_key},
                headers={"Content-Type": "application/json"},
                timeout=60
            )
            
            if response.status_code == 200:
                tickers = response.json()
                # Filtrer uniquement les actions US (NYSE, NASDAQ)
                us_tickers = [
                    t for t in tickers 
                    if t.get('exchange') in ['NYSE', 'NASDAQ', 'NYSE ARCA', 'NYSE MKT']
                    and t.get('assetType') == 'Stock'
                ]
                return us_tickers, None
            else:
                return None, f"Erreur API: {response.status_code}"
                
        except Exception as e:
            return None, str(e)
    
    def get_fundamentals_batch(self, tickers):
        """
        Récupère les données fondamentales pour une liste de tickers.
        Utilise l'endpoint fundamentals de Tiingo.
        
        Args:
            tickers: Liste de tickers (max ~100 par appel recommandé)
        
        Returns:
            dict: {ticker: {marketCap, ...}, ...}
        """
        url = f"{self.base_url}/tiingo/fundamentals/daily"
        
        try:
            response = requests.get(
                url,
                params={
                    "token": self.api_key,
                    "tickers": ",".join(tickers)
                },
                headers={"Content-Type": "application/json"},
                timeout=120
            )
            
            if response.status_code == 200:
                data = response.json()
                result = {}
                for item in data:
                    ticker = item.get('ticker')
                    if ticker:
                        result[ticker] = {
                            'marketCap': item.get('marketCap', 0) or 0,
                            'enterpriseVal': item.get('enterpriseVal', 0) or 0,
                        }
                return result, None
            else:
                return None, f"Erreur API fundamentals: {response.status_code}"
                
        except Exception as e:
            return None, str(e)
    
    def get_price_and_volume(self, ticker, days=126):
        """
        Récupère les prix et volumes historiques pour calculer l'ADV.
        
        Args:
            ticker: Symbole de l'action
            days: Nombre de jours (126 ≈ 6 mois de trading)
        
        Returns:
            dict: {price, avg_volume, adv}
        """
        url = f"{self.base_url}/tiingo/daily/{ticker}/prices"
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        try:
            response = requests.get(
                url,
                params={
                    "token": self.api_key,
                    "startDate": start_date.strftime("%Y-%m-%d"),
                    "endDate": end_date.strftime("%Y-%m-%d"),
                },
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if len(data) < 20:  # Pas assez de données
                    return None
                
                # Prix moyen et volume moyen
                prices = [d['adjClose'] for d in data if d.get('adjClose')]
                volumes = [d['volume'] for d in data if d.get('volume')]
                
                if not prices or not volumes:
                    return None
                
                avg_price = sum(prices) / len(prices)
                avg_volume = sum(volumes) / len(volumes)
                adv = avg_price * avg_volume  # Average Daily Dollar Volume
                
                return {
                    'price': round(avg_price, 2),
                    'avg_volume': int(avg_volume),
                    'adv': adv
                }
            else:
                return None
                
        except Exception:
            return None
    
    def calculate_score(self, market_cap, adv):
        """
        Calcule le score de sélection.
        
        Score = log(MarketCap) × log(ADV)
        
        Args:
            market_cap: Capitalisation boursière en $
            adv: Average Daily Dollar Volume en $
        
        Returns:
            float: Score
        """
        if market_cap <= 0 or adv <= 0:
            return 0
        
        return math.log(market_cap) * math.log(adv)
    
    def screen_universe(self, progress_callback=None):
        """
        Effectue le screening complet de l'univers US.
        
        Cette méthode:
        1. Récupère la liste des tickers US
        2. Filtre par critères (MarketCap, ADV)
        3. Calcule les scores
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
        def report(current, total, msg):
            if progress_callback:
                progress_callback(current, total, msg)
        
        report(0, 100, "Récupération de la liste des tickers US...")
        
        # Étape 1: Récupérer la liste des tickers
        all_tickers, error = self.get_supported_tickers()
        
        if error:
            return {
                'success': False,
                'tickers': [],
                'stats': {},
                'error': f"Erreur lors de la récupération des tickers: {error}"
            }
        
        if not all_tickers:
            return {
                'success': False,
                'tickers': [],
                'stats': {},
                'error': "Aucun ticker US trouvé"
            }
        
        report(10, 100, f"Analyse de {len(all_tickers)} tickers US...")
        
        # Étape 2: Récupérer les fondamentaux par batch
        ticker_symbols = [t['ticker'] for t in all_tickers]
        
        # Diviser en batches de 100
        batch_size = 100
        all_fundamentals = {}
        
        for i in range(0, len(ticker_symbols), batch_size):
            batch = ticker_symbols[i:i + batch_size]
            progress = 10 + int((i / len(ticker_symbols)) * 40)
            report(progress, 100, f"Récupération des fondamentaux ({i}/{len(ticker_symbols)})...")
            
            fundamentals, err = self.get_fundamentals_batch(batch)
            if fundamentals:
                all_fundamentals.update(fundamentals)
        
        report(50, 100, "Filtrage par capitalisation...")
        
        # Étape 3: Premier filtre par Market Cap
        candidates = []
        for ticker, data in all_fundamentals.items():
            market_cap = data.get('marketCap', 0)
            if market_cap >= self.min_market_cap:
                candidates.append({
                    'ticker': ticker,
                    'market_cap': market_cap
                })
        
        report(55, 100, f"{len(candidates)} tickers avec MarketCap >= 1B$")
        
        if len(candidates) == 0:
            return {
                'success': False,
                'tickers': [],
                'stats': {'total_analyzed': len(all_tickers)},
                'error': "Aucun ticker ne respecte les critères de capitalisation"
            }
        
        # Étape 4: Calculer l'ADV pour les candidats (limiter pour éviter trop d'appels)
        # On prend les 500 plus grandes capitalisations pour limiter les appels API
        candidates.sort(key=lambda x: x['market_cap'], reverse=True)
        top_candidates = candidates[:500]
        
        report(60, 100, f"Calcul de l'ADV pour les {len(top_candidates)} plus grandes caps...")
        
        scored_tickers = []
        for i, candidate in enumerate(top_candidates):
            ticker = candidate['ticker']
            market_cap = candidate['market_cap']
            
            progress = 60 + int((i / len(top_candidates)) * 35)
            if i % 50 == 0:
                report(progress, 100, f"Analyse de {ticker} ({i+1}/{len(top_candidates)})...")
            
            # Récupérer prix et volume
            pv_data = self.get_price_and_volume(ticker)
            
            if pv_data and pv_data['adv'] >= self.min_adv:
                score = self.calculate_score(market_cap, pv_data['adv'])
                scored_tickers.append({
                    'ticker': ticker,
                    'market_cap': market_cap,
                    'market_cap_display': self._format_number(market_cap),
                    'price': pv_data['price'],
                    'avg_volume': pv_data['avg_volume'],
                    'avg_volume_display': self._format_number(pv_data['avg_volume']),
                    'adv': pv_data['adv'],
                    'adv_display': self._format_number(pv_data['adv']),
                    'score': round(score, 2)
                })
        
        report(95, 100, "Tri et sélection des 50 meilleurs...")
        
        # Étape 5: Trier par score et prendre les 50 premiers
        scored_tickers.sort(key=lambda x: x['score'], reverse=True)
        top_50 = scored_tickers[:self.target_count]
        
        # Ajouter le rang
        for i, t in enumerate(top_50):
            t['rank'] = i + 1
        
        report(100, 100, "Terminé !")
        
        return {
            'success': True,
            'tickers': top_50,
            'stats': {
                'total_tickers_us': len(all_tickers),
                'above_market_cap': len(candidates),
                'above_adv': len(scored_tickers),
                'selected': len(top_50),
                'min_score': top_50[-1]['score'] if top_50 else 0,
                'max_score': top_50[0]['score'] if top_50 else 0,
                'generated_at': datetime.now().isoformat()
            },
            'error': None
        }
    
    def _format_number(self, num):
        """Formate un nombre en format lisible (1.5B, 25M, etc.)"""
        if num >= 1_000_000_000_000:
            return f"{num / 1_000_000_000_000:.1f}T"
        elif num >= 1_000_000_000:
            return f"{num / 1_000_000_000:.1f}B"
        elif num >= 1_000_000:
            return f"{num / 1_000_000:.1f}M"
        elif num >= 1_000:
            return f"{num / 1_000:.1f}K"
        else:
            return str(int(num))

