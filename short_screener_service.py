# -*- coding: utf-8 -*-
"""
Service de Screening Short (via Finviz)
========================================
Génère automatiquement un panel de 50 tickers pour la stratégie Short
basé sur les actions ayant le plus fortement baissé.

Utilise finvizfinance pour scraper les données de Finviz (0 appel API Tiingo).

Critères:
- Market Cap >= $2B (Mid Cap et plus)
- Average Volume >= 500K
- Performance Year <= -20% (forte baisse)
- Tri par Performance Year croissante (les pires en premier)
"""

from finvizfinance.screener.overview import Overview
from finvizfinance.screener.performance import Performance
from datetime import datetime
import time


class ShortScreenerService:
    """
    Service pour screener et sélectionner les actions en forte baisse.
    Utilise Finviz (gratuit, pas d'API key nécessaire).
    """
    
    def __init__(self):
        """
        Initialise le service.
        Pas besoin de clé API - utilise le scraping de Finviz.
        """
        self.target_count = 50  # Nombre de tickers à sélectionner
    
    def screen_losers(self, min_perf_year=-20, progress_callback=None):
        """
        Effectue le screening des actions en forte baisse.
        
        Critères:
        - Market Cap: Mid Cap ($2bln+) ou plus
        - Average Volume: Over 500K
        - Performance Year: <= min_perf_year%
        
        Args:
            min_perf_year: Performance annuelle maximale (ex: -20 = baisse de 20%+)
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
        
        try:
            # =================================================================
            # ÉTAPE 1: Configuration du screener Finviz
            # =================================================================
            report(10, 100, "Configuration des filtres Finviz...")
            
            # Utiliser Performance (pas Overview) pour avoir les colonnes de performance
            fperf = Performance()
            
            # Définir les filtres
            # Les noms correspondent aux labels de Finviz
            filters_dict = {
                'Market Cap.': '+Mid (over $2bln)',  # Mid cap et plus
                'Average Volume': 'Over 500K',       # Volume suffisant
            }
            
            # Ajouter le filtre de performance selon le seuil
            # Format Finviz: 'Year -XX%' (pas 'Year Down -XX%')
            if min_perf_year <= -50:
                filters_dict['Performance'] = 'Year -50%'
            elif min_perf_year <= -30:
                filters_dict['Performance'] = 'Year -30%'
            elif min_perf_year <= -20:
                filters_dict['Performance'] = 'Year -20%'
            elif min_perf_year <= -10:
                filters_dict['Performance'] = 'Year -10%'
            else:
                filters_dict['Performance'] = 'Year Down'
            
            report(20, 100, "Envoi de la requête à Finviz...")
            
            fperf.set_filter(filters_dict=filters_dict)
            
            # =================================================================
            # ÉTAPE 2: Récupération des données
            # =================================================================
            report(40, 100, "Récupération des données...")
            
            # Petite pause pour éviter le rate limiting
            time.sleep(1)
            
            df = fperf.screener_view()
            
            if df is None or df.empty:
                return self._error_result("Aucune action trouvée avec ces critères")
            
            report(60, 100, f"{len(df)} actions récupérées")
            
            # =================================================================
            # ÉTAPE 3: Nettoyage et tri des données
            # =================================================================
            report(70, 100, "Traitement des données...")
            
            # Colonnes mode Performance: Ticker, Perf Week, Perf Month, Perf Quart, 
            # Perf Half, Perf Year, Perf YTD, Volatility W, Volatility M, Recom, 
            # Avg Volume, Rel Volume, Price, Change, Volume
            
            # Convertir la performance annuelle en float (en %)
            # Finviz retourne les perfs en décimal (0.25 = 25%) ou en string "25%"
            def parse_perf(val):
                s = str(val).strip()
                if '%' in s:
                    return float(s.replace('%', ''))
                else:
                    # C'est en décimal, convertir en %
                    return float(s) * 100
            
            if 'Perf Year' in df.columns:
                df['Perf_Year_Num'] = df['Perf Year'].apply(parse_perf)
            elif 'Perf YTD' in df.columns:
                df['Perf_Year_Num'] = df['Perf YTD'].apply(parse_perf)
            else:
                # Chercher une colonne de performance
                perf_cols = [c for c in df.columns if 'perf' in c.lower() and 'year' in c.lower()]
                if not perf_cols:
                    perf_cols = [c for c in df.columns if 'perf' in c.lower()]
                if perf_cols:
                    df['Perf_Year_Num'] = df[perf_cols[0]].apply(parse_perf)
                else:
                    return self._error_result(f"Colonnes disponibles: {list(df.columns)}")
            
            # Trier par performance croissante (les plus fortes baisses en premier)
            df = df.sort_values(by='Perf_Year_Num', ascending=True)
            
            # =================================================================
            # ÉTAPE 4: Sélection des 50 premiers
            # =================================================================
            report(85, 100, "Sélection des 50 plus fortes baisses...")
            
            top_losers = df.head(self.target_count)
            
            # Construire la liste de résultats
            # Mode Performance: Ticker, Perf Week, Perf Month, Perf Quart, Perf Half, 
            # Perf Year, Perf YTD, Volatility W, Volatility M, Recom, Avg Volume, 
            # Rel Volume, Price, Change, Volume
            tickers = []
            for i, row in enumerate(top_losers.itertuples()):
                ticker_data = {
                    'ticker': row.Ticker if hasattr(row, 'Ticker') else str(row[1]),
                    'company': '',  # Non disponible en mode Performance
                    'sector': '',   # Non disponible en mode Performance  
                    'market_cap': '',
                    'price': self._parse_price(row),
                    'change': self._parse_change(row),
                    'perf_year': round(row.Perf_Year_Num, 2),
                    'volume': self._format_volume(row),
                    'rank': i + 1
                }
                tickers.append(ticker_data)
            
            report(100, 100, f"Terminé - {len(tickers)} actions sélectionnées")
            
            return {
                'success': True,
                'tickers': tickers,
                'stats': {
                    'total_found': len(df),
                    'selected': len(tickers),
                    'worst_perf': f"{tickers[0]['perf_year']}%" if tickers else '-',
                    'best_perf': f"{tickers[-1]['perf_year']}%" if tickers else '-',
                    'min_perf_filter': f"{min_perf_year}%",
                    'generated_at': datetime.now().isoformat()
                },
                'error': None
            }
            
        except Exception as e:
            return self._error_result(f"Erreur: {str(e)}")
    
    def _parse_price(self, row):
        """Parse le prix depuis la row Finviz"""
        try:
            if hasattr(row, 'Price'):
                return float(str(row.Price).replace('$', '').replace(',', ''))
            return 0.0
        except:
            return 0.0
    
    def _parse_change(self, row):
        """Parse le changement depuis la row Finviz"""
        try:
            if hasattr(row, 'Change'):
                return str(row.Change)
            return '-'
        except:
            return '-'
    
    def _format_volume(self, row):
        """Formate le volume depuis la row Finviz"""
        try:
            if hasattr(row, 'Volume'):
                vol = row.Volume
                if isinstance(vol, str):
                    return vol
                elif vol >= 1000000:
                    return f"{vol / 1000000:.1f}M"
                elif vol >= 1000:
                    return f"{vol / 1000:.1f}K"
                return str(int(vol))
            return '-'
        except:
            return '-'
    
    def _error_result(self, error_msg):
        """Retourne un résultat d'erreur formaté."""
        return {
            'success': False,
            'tickers': [],
            'stats': {},
            'error': error_msg
        }

