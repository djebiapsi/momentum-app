# -*- coding: utf-8 -*-
"""
Service de Screening d'actions (Optimisé)
=========================================
Génère automatiquement un panel de 50 tickers basé sur des critères quantitatifs :
- MarketCap >= 1B$
- ADV (Average Daily Dollar Volume) >= 5M$
- Score = log(MarketCap) × log(ADV)

OPTIMISATION: Utilise les endpoints bulk de Tiingo pour minimiser les appels API.
- ~12 appels au lieu de ~550
"""

import requests
import math
from datetime import datetime


class ScreenerService:
    """
    Service pour screener et sélectionner les meilleures actions US.
    Utilise les endpoints bulk de Tiingo pour minimiser les appels API.
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
    
    def get_supported_tickers_csv(self):
        """
        Télécharge la liste des tickers US supportés depuis le fichier CSV de Tiingo.
        NE COMPTE PAS comme appel API (fichier statique).
        
        Returns:
            tuple: (list of tickers, error)
        """
        import io
        import zipfile
        
        url = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
        
        try:
            response = requests.get(url, timeout=60)
            
            if response.status_code != 200:
                return None, f"Erreur téléchargement CSV: {response.status_code}"
            
            # Décompresser le ZIP
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                # Lire le fichier CSV
                csv_filename = z.namelist()[0]
                with z.open(csv_filename) as f:
                    content = f.read().decode('utf-8')
            
            # Parser le CSV
            lines = content.strip().split('\n')
            headers = lines[0].split(',')
            
            # Trouver les indices des colonnes
            ticker_idx = headers.index('ticker') if 'ticker' in headers else 0
            exchange_idx = headers.index('exchange') if 'exchange' in headers else None
            asset_type_idx = headers.index('assetType') if 'assetType' in headers else None
            
            us_tickers = []
            valid_exchanges = {'NYSE', 'NASDAQ', 'NYSE ARCA', 'NYSE MKT', 'NASDAQ GLOBAL SELECT', 'AMEX'}
            
            for line in lines[1:]:
                cols = line.split(',')
                if len(cols) <= ticker_idx:
                    continue
                
                ticker = cols[ticker_idx].strip()
                
                # Filtrer par exchange si disponible
                if exchange_idx and len(cols) > exchange_idx:
                    exchange = cols[exchange_idx].strip()
                    if exchange not in valid_exchanges:
                        continue
                
                # Filtrer par asset type si disponible
                if asset_type_idx and len(cols) > asset_type_idx:
                    asset_type = cols[asset_type_idx].strip()
                    if asset_type.upper() not in ['STOCK', 'ETF']:
                        continue
                
                if ticker and len(ticker) <= 5 and ticker.isalpha():
                    us_tickers.append(ticker)
            
            return us_tickers, None
            
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
    
    def get_fundamentals_batch(self, tickers):
        """
        Récupère les données fondamentales pour une liste de tickers.
        Utilise l'endpoint bulk fundamentals de Tiingo.
        
        Args:
            tickers: Liste de tickers (recommandé: max 100-200 par appel)
        
        Returns:
            tuple: (dict {ticker: marketCap}, error)
        """
        if not tickers:
            return {}, None
        
        url = f"{self.base_url}/tiingo/fundamentals/daily"
        
        # Joindre les tickers
        tickers_str = ",".join(tickers)
        
        data, error = self._api_call(url, {"tickers": tickers_str}, timeout=180)
        
        if error:
            return None, error
        
        result = {}
        for item in data:
            ticker = item.get('ticker')
            market_cap = item.get('marketCap')
            if ticker and market_cap:
                result[ticker] = market_cap
        
        return result, None
    
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
        OPTIMISÉ: Utilise les endpoints bulk pour minimiser les appels API.
        
        Étapes:
        1. Récupère la liste des tickers US (1 appel)
        2. Récupère les données IEX bulk - prix et volume (1 appel)
        3. Récupère les fondamentaux par batch (5-10 appels)
        4. Filtre et calcule les scores
        5. Retourne les 50 meilleurs
        
        Total: ~7-12 appels API
        
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
        # ÉTAPE 1: Données IEX bulk - prix et volume (1 appel)
        # On commence par IEX car ça nous donne tous les tickers actifs
        # =================================================================
        report(0, 100, "📊 Récupération des données IEX (prix + volume)...")
        
        iex_data, error = self.get_iex_bulk_data()
        
        if error:
            return self._error_result(f"Erreur IEX: {error}")
        
        if not iex_data:
            return self._error_result("Aucune donnée IEX disponible")
        
        # Les tickers viennent directement de l'IEX
        all_tickers = list(iex_data.keys())
        
        report(15, 100, f"✅ {len(all_tickers)} tickers avec données IEX (1 appel API)")
        
        # =================================================================
        # ÉTAPE 2: Filtrage par ADV (pas d'appel API)
        # =================================================================
        report(20, 100, "📈 Filtrage par ADV >= 5M$...")
        
        # Premier filtre: ADV >= 5M$
        tickers_above_adv = [
            t for t in all_tickers 
            if iex_data[t]['adv'] >= self.min_adv
        ]
        
        report(25, 100, f"✅ {len(tickers_above_adv)} tickers avec ADV >= 5M$")
        
        if len(tickers_above_adv) == 0:
            return self._error_result("Aucun ticker ne respecte le critère ADV >= 5M$")
        
        # =================================================================
        # ÉTAPE 3: Fondamentaux batch - MarketCap (quelques appels)
        # =================================================================
        report(30, 100, "💰 Récupération des capitalisations (batch)...")
        
        # On limite aux tickers avec ADV suffisant pour économiser des appels
        batch_size = 100
        all_market_caps = {}
        
        for i in range(0, len(tickers_above_adv), batch_size):
            batch = tickers_above_adv[i:i + batch_size]
            progress = 35 + int((i / len(tickers_above_adv)) * 30)
            report(progress, 100, f"📥 Batch {i//batch_size + 1}/{(len(tickers_above_adv)//batch_size)+1}...")
            
            market_caps, err = self.get_fundamentals_batch(batch)
            if market_caps:
                all_market_caps.update(market_caps)
        
        report(70, 100, f"✅ MarketCap pour {len(all_market_caps)} tickers ({self.api_calls} appels API total)")
        
        # =================================================================
        # ÉTAPE 4: Filtrage et scoring
        # =================================================================
        report(75, 100, "🎯 Calcul des scores...")
        
        scored_tickers = []
        
        for ticker in tickers_above_adv:
            market_cap = all_market_caps.get(ticker, 0)
            
            # Filtre MarketCap >= 1B$
            if market_cap < self.min_market_cap:
                continue
            
            iex = iex_data[ticker]
            adv = iex['adv']
            
            # Calcul du score
            score = self.calculate_score(market_cap, adv)
            
            scored_tickers.append({
                'ticker': ticker,
                'market_cap': market_cap,
                'market_cap_display': self._format_number(market_cap),
                'price': iex['price'],
                'volume': iex['volume'],
                'volume_display': self._format_number(iex['volume']),
                'adv': adv,
                'adv_display': self._format_number(adv),
                'score': round(score, 2)
            })
        
        report(85, 100, f"📊 {len(scored_tickers)} tickers respectent tous les critères")
        
        if len(scored_tickers) == 0:
            return self._error_result("Aucun ticker ne respecte tous les critères")
        
        # =================================================================
        # ÉTAPE 5: Tri et sélection des 50 meilleurs
        # =================================================================
        report(90, 100, "🏆 Sélection des 50 meilleurs...")
        
        # Trier par score décroissant
        scored_tickers.sort(key=lambda x: x['score'], reverse=True)
        
        # Prendre les 50 premiers
        top_50 = scored_tickers[:self.target_count]
        
        # Ajouter le rang
        for i, t in enumerate(top_50):
            t['rank'] = i + 1
        
        report(100, 100, f"✅ Terminé ! {self.api_calls} appels API utilisés")
        
        return {
            'success': True,
            'tickers': top_50,
            'stats': {
                'total_tickers_us': len(all_tickers),
                'above_adv_threshold': len(tickers_above_adv),
                'above_market_cap': len(scored_tickers),
                'selected': len(top_50),
                'min_score': round(top_50[-1]['score'], 2) if top_50 else 0,
                'max_score': round(top_50[0]['score'], 2) if top_50 else 0,
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
