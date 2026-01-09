# -*- coding: utf-8 -*-
"""
Service de Calcul d'Options - Put et Put Spread
================================================
Calculateur basé sur Black-Scholes pour construire des positions
Put et Put Spread selon la stratégie momentum short.

Stratégie:
- PUT ou PUT SPREAD
- DTE cible: 30-60 jours
- Delta put long: -0.25 à -0.40
- Delta put short (spread): -0.10
- IV Rank ≤ 60
"""

import math
from datetime import datetime, timedelta
from scipy.stats import norm
import requests


class OptionsService:
    """
    Service de calcul d'options avec Black-Scholes.
    """
    
    def __init__(self, risk_free_rate=0.05):
        """
        Args:
            risk_free_rate: Taux sans risque annuel (défaut: 5%)
        """
        self.risk_free_rate = risk_free_rate
    
    # =========================================================================
    # BLACK-SCHOLES CORE
    # =========================================================================
    
    def _d1(self, S, K, T, r, sigma):
        """Calcule d1 pour Black-Scholes."""
        if T <= 0 or sigma <= 0:
            return 0
        return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    
    def _d2(self, S, K, T, r, sigma):
        """Calcule d2 pour Black-Scholes."""
        return self._d1(S, K, T, r, sigma) - sigma * math.sqrt(T)
    
    def put_price(self, S, K, T, r, sigma):
        """
        Calcule le prix d'un PUT avec Black-Scholes.
        
        Args:
            S: Prix spot de l'action
            K: Strike price
            T: Temps jusqu'à expiration (en années)
            r: Taux sans risque
            sigma: Volatilité implicite (décimale, ex: 0.30 pour 30%)
        
        Returns:
            float: Prix du PUT
        """
        if T <= 0:
            return max(K - S, 0)  # Valeur intrinsèque
        
        d1 = self._d1(S, K, T, r, sigma)
        d2 = self._d2(S, K, T, r, sigma)
        
        put = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        return max(put, 0)
    
    def call_price(self, S, K, T, r, sigma):
        """Calcule le prix d'un CALL avec Black-Scholes."""
        if T <= 0:
            return max(S - K, 0)
        
        d1 = self._d1(S, K, T, r, sigma)
        d2 = self._d2(S, K, T, r, sigma)
        
        call = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        return max(call, 0)
    
    # =========================================================================
    # GREEKS
    # =========================================================================
    
    def delta_put(self, S, K, T, r, sigma):
        """Delta d'un PUT (-1 à 0)."""
        if T <= 0:
            return -1 if S < K else 0
        d1 = self._d1(S, K, T, r, sigma)
        return norm.cdf(d1) - 1
    
    def delta_call(self, S, K, T, r, sigma):
        """Delta d'un CALL (0 à 1)."""
        if T <= 0:
            return 1 if S > K else 0
        d1 = self._d1(S, K, T, r, sigma)
        return norm.cdf(d1)
    
    def gamma(self, S, K, T, r, sigma):
        """Gamma (même pour PUT et CALL)."""
        if T <= 0 or sigma <= 0:
            return 0
        d1 = self._d1(S, K, T, r, sigma)
        return norm.pdf(d1) / (S * sigma * math.sqrt(T))
    
    def theta_put(self, S, K, T, r, sigma):
        """Theta d'un PUT (décroissance temporelle par jour)."""
        if T <= 0:
            return 0
        d1 = self._d1(S, K, T, r, sigma)
        d2 = self._d2(S, K, T, r, sigma)
        
        theta = (-(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
                 + r * K * math.exp(-r * T) * norm.cdf(-d2))
        return theta / 365  # Par jour
    
    def vega(self, S, K, T, r, sigma):
        """Vega (sensibilité à la volatilité, pour 1% de changement)."""
        if T <= 0:
            return 0
        d1 = self._d1(S, K, T, r, sigma)
        return S * math.sqrt(T) * norm.pdf(d1) / 100
    
    # =========================================================================
    # STRIKE FINDER (basé sur Delta)
    # =========================================================================
    
    def find_strike_by_delta(self, S, T, r, sigma, target_delta, option_type='put'):
        """
        Trouve le strike correspondant à un delta cible.
        
        Args:
            S: Prix spot
            T: Temps jusqu'à expiration (années)
            r: Taux sans risque
            sigma: Volatilité implicite
            target_delta: Delta cible (ex: -0.30 pour un PUT OTM)
            option_type: 'put' ou 'call'
        
        Returns:
            float: Strike approximatif
        """
        # Recherche binaire pour trouver le strike
        if option_type == 'put':
            # Pour un PUT, delta est négatif, strike plus bas = delta plus négatif
            low_strike = S * 0.5
            high_strike = S * 1.5
        else:
            low_strike = S * 0.5
            high_strike = S * 1.5
        
        for _ in range(50):  # Iterations max
            mid_strike = (low_strike + high_strike) / 2
            
            if option_type == 'put':
                current_delta = self.delta_put(S, mid_strike, T, r, sigma)
                # PUT delta: plus le strike est bas, plus delta est proche de 0
                if current_delta < target_delta:
                    high_strike = mid_strike
                else:
                    low_strike = mid_strike
            else:
                current_delta = self.delta_call(S, mid_strike, T, r, sigma)
                if current_delta > target_delta:
                    high_strike = mid_strike
                else:
                    low_strike = mid_strike
            
            if abs(current_delta - target_delta) < 0.001:
                break
        
        return round(mid_strike, 2)
    
    # =========================================================================
    # PUT SPREAD CALCULATOR
    # =========================================================================
    
    def calculate_put_spread(self, S, T, r, sigma, delta_long=-0.30, delta_short=-0.10):
        """
        Calcule un PUT SPREAD optimal selon la stratégie.
        
        Args:
            S: Prix spot de l'action
            T: Temps jusqu'à expiration (années)
            r: Taux sans risque
            sigma: Volatilité implicite
            delta_long: Delta cible pour le PUT acheté (défaut: -0.30)
            delta_short: Delta cible pour le PUT vendu (défaut: -0.10)
        
        Returns:
            dict: Détails du spread
        """
        # Trouver les strikes basés sur les deltas
        strike_long = self.find_strike_by_delta(S, T, r, sigma, delta_long, 'put')
        strike_short = self.find_strike_by_delta(S, T, r, sigma, delta_short, 'put')
        
        # S'assurer que strike_long > strike_short (PUT spread correct)
        if strike_long <= strike_short:
            strike_long, strike_short = strike_short + 1, strike_long - 1
        
        # Calculer les prix
        price_long = self.put_price(S, strike_long, T, r, sigma)
        price_short = self.put_price(S, strike_short, T, r, sigma)
        
        # Net debit (ce qu'on paie)
        net_debit = price_long - price_short
        
        # Max profit = différence des strikes - prime payée
        max_profit = (strike_long - strike_short) - net_debit
        
        # Max loss = prime payée
        max_loss = net_debit
        
        # Breakeven = strike long - prime payée
        breakeven = strike_long - net_debit
        
        # Greeks du spread
        delta_spread = self.delta_put(S, strike_long, T, r, sigma) - self.delta_put(S, strike_short, T, r, sigma)
        gamma_spread = self.gamma(S, strike_long, T, r, sigma) - self.gamma(S, strike_short, T, r, sigma)
        theta_spread = self.theta_put(S, strike_long, T, r, sigma) - self.theta_put(S, strike_short, T, r, sigma)
        vega_spread = self.vega(S, strike_long, T, r, sigma) - self.vega(S, strike_short, T, r, sigma)
        
        # Risk/Reward ratio
        risk_reward = max_profit / max_loss if max_loss > 0 else 0
        
        return {
            'type': 'PUT_SPREAD',
            'spot_price': round(S, 2),
            'strike_long': round(strike_long, 2),
            'strike_short': round(strike_short, 2),
            'price_long': round(price_long, 2),
            'price_short': round(price_short, 2),
            'net_debit': round(net_debit, 2),
            'max_profit': round(max_profit, 2),
            'max_loss': round(max_loss, 2),
            'breakeven': round(breakeven, 2),
            'risk_reward_ratio': round(risk_reward, 2),
            'delta_long_actual': round(self.delta_put(S, strike_long, T, r, sigma), 3),
            'delta_short_actual': round(self.delta_put(S, strike_short, T, r, sigma), 3),
            'delta_spread': round(delta_spread, 3),
            'gamma_spread': round(gamma_spread, 4),
            'theta_spread': round(theta_spread, 4),
            'vega_spread': round(vega_spread, 4),
            'dte': round(T * 365),
            'iv_used': round(sigma * 100, 1)
        }
    
    def calculate_naked_put(self, S, T, r, sigma, delta_target=-0.30):
        """
        Calcule un PUT simple (naked put pour le long).
        
        Args:
            S: Prix spot
            T: Temps jusqu'à expiration (années)
            r: Taux sans risque
            sigma: Volatilité implicite
            delta_target: Delta cible
        
        Returns:
            dict: Détails du PUT
        """
        strike = self.find_strike_by_delta(S, T, r, sigma, delta_target, 'put')
        price = self.put_price(S, strike, T, r, sigma)
        
        delta = self.delta_put(S, strike, T, r, sigma)
        gamma = self.gamma(S, strike, T, r, sigma)
        theta = self.theta_put(S, strike, T, r, sigma)
        vega = self.vega(S, strike, T, r, sigma)
        
        # Breakeven = strike - prime
        breakeven = strike - price
        
        # Max profit = strike - prime (si action va à 0)
        max_profit = breakeven
        
        return {
            'type': 'PUT',
            'spot_price': round(S, 2),
            'strike': round(strike, 2),
            'price': round(price, 2),
            'breakeven': round(breakeven, 2),
            'max_profit': round(max_profit, 2),
            'max_loss': round(price, 2),
            'delta': round(delta, 3),
            'gamma': round(gamma, 4),
            'theta': round(theta, 4),
            'vega': round(vega, 4),
            'dte': round(T * 365),
            'iv_used': round(sigma * 100, 1)
        }
    
    # =========================================================================
    # STRATEGY ENGINE - Basé sur stratégie_short.md
    # =========================================================================
    
    def build_option_recommendation(self, ticker, spot_price, iv, momentum_score, 
                                     perf_63_5, perf_5_0, dte_target=45):
        """
        Construit une recommandation d'option selon la stratégie.
        
        Args:
            ticker: Symbole de l'action
            spot_price: Prix actuel
            iv: Volatilité implicite (décimale)
            momentum_score: Score momentum (Perf_63_5 - Perf_5_0)
            perf_63_5: Performance T-63 à T-5
            perf_5_0: Performance T-5 à T
            dte_target: Jours jusqu'à expiration cible
        
        Returns:
            dict: Recommandation complète ou None si conditions non remplies
        """
        T = dte_target / 365
        r = self.risk_free_rate
        
        # Vérifier conditions minimales
        conditions = {
            'perf_63_5_ok': perf_63_5 <= -15,
            'perf_5_0_ok': perf_5_0 <= 5,
            'iv_rank_ok': True,  # À implémenter avec données réelles
        }
        
        all_conditions_met = all(conditions.values())
        
        # Calculer PUT et PUT SPREAD
        put_data = self.calculate_naked_put(spot_price, T, r, iv, delta_target=-0.30)
        spread_data = self.calculate_put_spread(spot_price, T, r, iv, 
                                                 delta_long=-0.30, delta_short=-0.10)
        
        # Recommander PUT SPREAD si spread assez large, sinon PUT simple
        spread_width = spread_data['strike_long'] - spread_data['strike_short']
        recommend_spread = spread_width >= spot_price * 0.05  # Au moins 5% d'écart
        
        recommendation = {
            'ticker': ticker,
            'signal': 'SHORT_MOMENTUM_OPTION' if all_conditions_met else 'WATCH',
            'momentum_score': round(momentum_score, 2),
            'perf_63_5': round(perf_63_5, 2),
            'perf_5_0': round(perf_5_0, 2),
            'spot_price': round(spot_price, 2),
            'iv_pct': round(iv * 100, 1),
            'conditions': conditions,
            'all_conditions_met': all_conditions_met,
            'recommended_strategy': 'PUT_SPREAD' if recommend_spread else 'PUT',
            'put': put_data,
            'put_spread': spread_data,
            'entry_rules': {
                'rsi_range': [40, 55],
                'pullback_max_pct': 50,
                'no_gap_above_pct': 3
            },
            'exit_rules': {
                'take_profit_pct': 80,
                'stop_loss_pct': -50,
                'time_stop_dte': 14
            }
        }
        
        return recommendation
    
    # =========================================================================
    # HELPERS
    # =========================================================================
    
    def dte_to_years(self, dte):
        """Convertit DTE en fraction d'année."""
        return dte / 365
    
    def get_expiration_date(self, dte):
        """Retourne la date d'expiration pour un DTE donné."""
        return (datetime.now() + timedelta(days=dte)).strftime('%Y-%m-%d')
    
    def estimate_iv_from_price(self, S, K, T, r, market_price, option_type='put'):
        """
        Estime la volatilité implicite à partir du prix de marché.
        Utilise Newton-Raphson.
        
        Args:
            S: Prix spot
            K: Strike
            T: Temps en années
            r: Taux sans risque
            market_price: Prix de marché de l'option
            option_type: 'put' ou 'call'
        
        Returns:
            float: Volatilité implicite estimée
        """
        sigma = 0.30  # Estimation initiale
        
        for _ in range(100):
            if option_type == 'put':
                price = self.put_price(S, K, T, r, sigma)
            else:
                price = self.call_price(S, K, T, r, sigma)
            
            vega = self.vega(S, K, T, r, sigma) * 100  # Ajuster pour le calcul
            
            if vega < 0.0001:
                break
            
            diff = market_price - price
            sigma = sigma + diff / vega
            
            if abs(diff) < 0.0001:
                break
            
            # Bornes de sécurité
            sigma = max(0.01, min(sigma, 5.0))
        
        return sigma


# =============================================================================
# ESTIMATION IV (sans API options)
# =============================================================================

def estimate_historical_volatility(prices, window=30):
    """
    Estime la volatilité historique à partir des prix.
    
    Args:
        prices: Liste de prix (du plus ancien au plus récent)
        window: Fenêtre de calcul en jours
    
    Returns:
        float: Volatilité annualisée (décimale)
    """
    if len(prices) < window + 1:
        return 0.30  # Défaut
    
    # Rendements logarithmiques
    returns = []
    for i in range(1, len(prices)):
        if prices[i-1] > 0:
            returns.append(math.log(prices[i] / prices[i-1]))
    
    if len(returns) < window:
        return 0.30
    
    # Volatilité sur la fenêtre
    recent_returns = returns[-window:]
    mean = sum(recent_returns) / len(recent_returns)
    variance = sum((r - mean) ** 2 for r in recent_returns) / len(recent_returns)
    daily_vol = math.sqrt(variance)
    
    # Annualiser
    annual_vol = daily_vol * math.sqrt(252)
    
    return annual_vol

