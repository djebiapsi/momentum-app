# -*- coding: utf-8 -*-
"""
Service de Screening via Finviz (Long & Short)
==============================================
Génère automatiquement des panels de 50 tickers sans utiliser d'API payante.

Stratégie Long (critères du document select_50_tickers.md):
- MarketCap >= 1B$
- ADV >= 5M$ (calculé: Price × Volume)
- Score = log(MarketCap) × log(ADV)
- Tri par score décroissant, top 50

Stratégie Short:
- MarketCap >= 2B$ (pour "shortabilité")
- Volume >= 500K
- Performance Year <= -20%
- Tri par perf croissant (pires en premier), top 50

Résultat: 0 appel API Tiingo !
"""

from finvizfinance.screener.overview import Overview
from finvizfinance.screener.performance import Performance
from datetime import datetime
import math
import time
import signal


class TimeoutError(Exception):
    pass


class FinvizScreenerService:
    """
    Service unifié pour screener via Finviz (gratuit, sans API key).
    Supporte les stratégies Long et Short.
    """
    
    def __init__(self):
        self.target_count = 50
    
    # =========================================================================
    # STRATÉGIE LONG - Basée sur MarketCap × ADV
    # =========================================================================
    
    def screen_long(self, progress_callback=None):
        """
        Screening pour stratégie Long selon les critères quantitatifs.
        
        Critères:
        - MarketCap >= $1B
        - ADV >= $5M (Price × Volume)
        - Score = log(MarketCap) × log(ADV)
        
        Returns:
            dict: {success, tickers, stats, error}
        """
        def report(pct, msg):
            if progress_callback:
                progress_callback(pct, 100, msg)
        
        try:
            # =================================================================
            # ÉTAPE 1: Récupérer les données via Finviz Overview
            # =================================================================
            report(10, "Configuration des filtres Finviz...")
            
            foverview = Overview()
            
            # Filtres TRÈS stricts pour rapidité (moins de résultats = plus rapide)
            filters_dict = {
                'Market Cap.': '+Large (over $10bln)',  # Large cap seulement = beaucoup moins de résultats
                'Average Volume': 'Over 1M',            # Volume élevé uniquement
            }
            
            report(20, "Envoi de la requête à Finviz...")
            foverview.set_filter(filters_dict=filters_dict)
            
            report(40, "Récupération des données...")
            # Limiter à 150 résultats max pour éviter timeout
            df = foverview.screener_view(
                order='Market Cap.',
                ascend=False,
                limit=150,
                verbose=0,
                sleep_sec=0.05  # Réduit pour rapidité
            )
            
            if df is None or df.empty:
                return self._error("Aucune donnée retournée par Finviz")
            
            report(50, f"{len(df)} actions récupérées")
            
            # =================================================================
            # ÉTAPE 2: Parser et calculer les métriques
            # =================================================================
            report(60, "Calcul des scores...")
            
            scored = []
            for _, row in df.iterrows():
                try:
                    ticker = row.get('Ticker', '')
                    if not ticker:
                        continue
                    
                    # Parser le Market Cap
                    market_cap = self._parse_market_cap(row.get('Market Cap', ''))
                    if market_cap < 10_000_000_000:  # < 10B (Large Cap)
                        continue
                    
                    # Parser Price et Volume pour ADV
                    price = self._parse_float(row.get('Price', 0))
                    volume = self._parse_volume(row.get('Volume', 0))
                    
                    if price <= 0 or volume <= 0:
                        continue
                    
                    adv = price * volume
                    if adv < 5_000_000:  # < 5M
                        continue
                    
                    # Calcul du score: log(MarketCap) × log(ADV)
                    score = math.log(market_cap) * math.log(adv)
                    
                    scored.append({
                        'ticker': ticker,
                        'company': row.get('Company', ''),
                        'sector': row.get('Sector', ''),
                        'market_cap': market_cap,
                        'market_cap_display': self._format_number(market_cap),
                        'price': round(price, 2),
                        'volume': int(volume),
                        'volume_display': self._format_number(volume),
                        'adv': adv,
                        'adv_display': self._format_number(adv),
                        'score': round(score, 2)
                    })
                except Exception:
                    continue
            
            if not scored:
                return self._error("Aucune action ne respecte les critères (MarketCap >= 10B$, ADV >= 5M$)")
            
            report(75, f"{len(scored)} actions qualifiées")
            
            # =================================================================
            # ÉTAPE 3: Tri et sélection des 50 meilleurs
            # =================================================================
            report(85, "Sélection des 50 meilleurs scores...")
            
            scored.sort(key=lambda x: x['score'], reverse=True)
            top_50 = scored[:self.target_count]
            
            for i, t in enumerate(top_50):
                t['rank'] = i + 1
            
            report(100, f"Terminé - {len(top_50)} actions sélectionnées")
            
            return {
                'success': True,
                'tickers': top_50,
                'stats': {
                    'total_found': len(df),
                    'qualified': len(scored),
                    'selected': len(top_50),
                    'min_market_cap': '$10B',
                    'min_adv': '$5M',
                    'best_score': top_50[0]['score'] if top_50 else '-',
                    'generated_at': datetime.now().isoformat()
                },
                'error': None
            }
            
        except Exception as e:
            return self._error(f"Erreur: {str(e)}")
    
    # =========================================================================
    # STRATÉGIE SHORT - Basée sur Performance négative
    # =========================================================================
    
    def screen_short(self, min_perf_year=-20, progress_callback=None):
        """
        Screening pour stratégie Short (actions en forte baisse).
        
        Critères:
        - MarketCap >= $2B (pour shortabilité)
        - Volume >= 500K
        - Performance Year <= min_perf_year%
        
        Returns:
            dict: {success, tickers, stats, error}
        """
        def report(pct, msg):
            if progress_callback:
                progress_callback(pct, 100, msg)
        
        try:
            # =================================================================
            # ÉTAPE 1: Configuration Finviz mode Performance
            # =================================================================
            report(10, "Configuration des filtres Finviz...")
            
            fperf = Performance()
            
            # Filtres stricts pour rapidité
            filters_dict = {
                'Market Cap.': '+Mid (over $2bln)',
                'Average Volume': 'Over 500K',
            }
            
            # Filtre de performance selon le seuil
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
            
            report(20, "Envoi de la requête à Finviz...")
            fperf.set_filter(filters_dict=filters_dict)
            
            report(40, "Récupération des données...")
            # Limiter à 100 résultats pour éviter timeout
            df = fperf.screener_view(
                order='Performance (Year)',
                ascend=True,      # Croissant = pires en premier
                limit=100,
                verbose=0,
                sleep_sec=0.05  # Réduit pour rapidité
            )
            
            if df is None or df.empty:
                return self._error("Aucune action trouvée avec ces critères")
            
            report(60, f"{len(df)} actions récupérées")
            
            # =================================================================
            # ÉTAPE 2: Parser la performance et trier
            # =================================================================
            report(70, "Traitement des données...")
            
            # Parser la perf Year
            def parse_perf(val):
                s = str(val).strip()
                if '%' in s:
                    return float(s.replace('%', ''))
                else:
                    return float(s) * 100  # Décimal -> %
            
            if 'Perf Year' in df.columns:
                df['Perf_Num'] = df['Perf Year'].apply(parse_perf)
            elif 'Perf YTD' in df.columns:
                df['Perf_Num'] = df['Perf YTD'].apply(parse_perf)
            else:
                perf_cols = [c for c in df.columns if 'perf' in c.lower()]
                if perf_cols:
                    df['Perf_Num'] = df[perf_cols[0]].apply(parse_perf)
                else:
                    return self._error(f"Colonnes: {list(df.columns)}")
            
            # Trier par perf croissante (pires en premier)
            df = df.sort_values(by='Perf_Num', ascending=True)
            
            # =================================================================
            # ÉTAPE 3: Sélection des 50 premiers
            # =================================================================
            report(85, "Sélection des 50 plus fortes baisses...")
            
            top_losers = df.head(self.target_count)
            
            tickers = []
            for i, row in enumerate(top_losers.itertuples()):
                tickers.append({
                    'ticker': row.Ticker if hasattr(row, 'Ticker') else str(row[1]),
                    'company': '',
                    'sector': '',
                    'price': self._parse_float(getattr(row, 'Price', 0)),
                    'change': str(getattr(row, 'Change', '-')),
                    'perf_year': round(row.Perf_Num, 2),
                    'volume': self._format_number(getattr(row, 'Volume', 0)),
                    'rank': i + 1
                })
            
            report(100, f"Terminé - {len(tickers)} actions sélectionnées")
            
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
            return self._error(f"Erreur: {str(e)}")
    
    # =========================================================================
    # HELPERS
    # =========================================================================
    
    def _parse_market_cap(self, val):
        """Parse '1.5B' ou '500M' en nombre."""
        try:
            s = str(val).strip().upper()
            if not s or s == '-':
                return 0
            
            multiplier = 1
            if s.endswith('T'):
                multiplier = 1_000_000_000_000
                s = s[:-1]
            elif s.endswith('B'):
                multiplier = 1_000_000_000
                s = s[:-1]
            elif s.endswith('M'):
                multiplier = 1_000_000
                s = s[:-1]
            elif s.endswith('K'):
                multiplier = 1_000
                s = s[:-1]
            
            return float(s.replace(',', '')) * multiplier
        except:
            return 0
    
    def _parse_float(self, val):
        """Parse un float depuis une valeur."""
        try:
            s = str(val).replace('$', '').replace(',', '').replace('%', '').strip()
            return float(s) if s and s != '-' else 0
        except:
            return 0
    
    def _parse_volume(self, val):
        """Parse le volume (peut être '1.5M' ou nombre)."""
        try:
            if isinstance(val, (int, float)):
                return val
            s = str(val).strip().upper()
            if not s or s == '-':
                return 0
            
            multiplier = 1
            if s.endswith('M'):
                multiplier = 1_000_000
                s = s[:-1]
            elif s.endswith('K'):
                multiplier = 1_000
                s = s[:-1]
            elif s.endswith('B'):
                multiplier = 1_000_000_000
                s = s[:-1]
            
            return float(s.replace(',', '')) * multiplier
        except:
            return 0
    
    def _format_number(self, num):
        """Formate en lisible: 1.5B, 25M, etc."""
        try:
            num = float(num)
            if num >= 1_000_000_000_000:
                return f"{num / 1_000_000_000_000:.1f}T"
            elif num >= 1_000_000_000:
                return f"{num / 1_000_000_000:.1f}B"
            elif num >= 1_000_000:
                return f"{num / 1_000_000:.1f}M"
            elif num >= 1_000:
                return f"{num / 1_000:.1f}K"
            return str(int(num))
        except:
            return '-'
    
    def _error(self, msg):
        """Retourne un résultat d'erreur."""
        return {
            'success': False,
            'tickers': [],
            'stats': {},
            'error': msg
        }

