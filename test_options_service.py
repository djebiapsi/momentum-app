# -*- coding: utf-8 -*-
"""
Tests unitaires pour OptionsService
===================================
Valide les calculs Black-Scholes, Greeks, et la logique de stratégie.
"""

import unittest
import math
from datetime import datetime, timedelta
from options_service import OptionsService, estimate_historical_volatility


class TestOptionsService(unittest.TestCase):
    """Tests pour la classe OptionsService."""
    
    def setUp(self):
        """Initialise le service avant chaque test."""
        self.service = OptionsService(risk_free_rate=0.05)
        # Paramètres de test standards
        self.S = 100.0  # Prix spot
        self.K = 95.0   # Strike
        self.T = 45 / 365  # 45 jours en années
        self.r = 0.05   # Taux sans risque 5%
        self.sigma = 0.30  # Volatilité 30%
    
    # =========================================================================
    # TESTS BLACK-SCHOLES CORE
    # =========================================================================
    
    def test_d1_calculation(self):
        """Test le calcul de d1."""
        d1 = self.service._d1(self.S, self.K, self.T, self.r, self.sigma)
        self.assertIsInstance(d1, float)
        # Pour un PUT OTM (S > K), d1 devrait être positif
        self.assertGreater(d1, 0)
    
    def test_d1_edge_cases(self):
        """Test d1 avec cas limites."""
        # T = 0
        d1_zero = self.service._d1(self.S, self.K, 0, self.r, self.sigma)
        self.assertEqual(d1_zero, 0)
        
        # sigma = 0
        d1_no_vol = self.service._d1(self.S, self.K, self.T, self.r, 0)
        self.assertEqual(d1_no_vol, 0)
    
    def test_d2_calculation(self):
        """Test le calcul de d2."""
        d1 = self.service._d1(self.S, self.K, self.T, self.r, self.sigma)
        d2 = self.service._d2(self.S, self.K, self.T, self.r, self.sigma)
        # d2 devrait être inférieur à d1
        self.assertLess(d2, d1)
        # Différence attendue
        expected_diff = self.sigma * math.sqrt(self.T)
        self.assertAlmostEqual(d1 - d2, expected_diff, places=5)
    
    def test_put_price_atm(self):
        """Test prix PUT à la monnaie (ATM)."""
        # Strike = Spot
        K_atm = self.S
        price = self.service.put_price(self.S, K_atm, self.T, self.r, self.sigma)
        self.assertGreater(price, 0)
        # Prix devrait être raisonnable (pas négatif, pas infini)
        self.assertLess(price, self.S)
    
    def test_put_price_itm(self):
        """Test prix PUT dans la monnaie (ITM)."""
        # Strike > Spot (PUT ITM)
        K_itm = 110.0
        price = self.service.put_price(self.S, K_itm, self.T, self.r, self.sigma)
        self.assertGreater(price, 0)
        # Prix minimum = valeur intrinsèque
        intrinsic = max(K_itm - self.S, 0)
        self.assertGreaterEqual(price, intrinsic)
    
    def test_put_price_otm(self):
        """Test prix PUT hors de la monnaie (OTM)."""
        # Strike < Spot (PUT OTM)
        K_otm = 80.0
        price = self.service.put_price(self.S, K_otm, self.T, self.r, self.sigma)
        self.assertGreaterEqual(price, 0)
        # Prix devrait être faible mais positif
        self.assertLess(price, self.S)
    
    def test_put_price_expired(self):
        """Test prix PUT à expiration (T=0)."""
        price = self.service.put_price(self.S, self.K, 0, self.r, self.sigma)
        # À expiration, prix = valeur intrinsèque
        intrinsic = max(self.K - self.S, 0)
        self.assertEqual(price, intrinsic)
    
    def test_call_price_basic(self):
        """Test prix CALL de base."""
        price = self.service.call_price(self.S, self.K, self.T, self.r, self.sigma)
        self.assertGreater(price, 0)
        self.assertLess(price, self.S)
    
    def test_put_call_parity(self):
        """Test la parité put-call (approximative)."""
        # Put-Call Parity: C - P = S - K*e^(-rT)
        call_price = self.service.call_price(self.S, self.K, self.T, self.r, self.sigma)
        put_price = self.service.put_price(self.S, self.K, self.T, self.r, self.sigma)
        
        lhs = call_price - put_price
        rhs = self.S - self.K * math.exp(-self.r * self.T)
        
        # Devrait être proche (tolérance pour arrondis)
        self.assertAlmostEqual(lhs, rhs, places=2)
    
    # =========================================================================
    # TESTS GREEKS
    # =========================================================================
    
    def test_delta_put_range(self):
        """Test que delta PUT est dans la plage [-1, 0]."""
        delta = self.service.delta_put(self.S, self.K, self.T, self.r, self.sigma)
        self.assertGreaterEqual(delta, -1)
        self.assertLessEqual(delta, 0)
    
    def test_delta_put_itm(self):
        """Test delta PUT ITM (proche de -1)."""
        K_itm = 110.0  # Strike > Spot
        delta = self.service.delta_put(self.S, K_itm, self.T, self.r, self.sigma)
        # PUT ITM devrait avoir delta proche de -1
        self.assertLess(delta, -0.5)
    
    def test_delta_put_otm(self):
        """Test delta PUT OTM (proche de 0)."""
        K_otm = 80.0  # Strike < Spot
        delta = self.service.delta_put(self.S, K_otm, self.T, self.r, self.sigma)
        # PUT OTM devrait avoir delta proche de 0
        self.assertGreater(delta, -0.5)
    
    def test_delta_call_range(self):
        """Test que delta CALL est dans la plage [0, 1]."""
        delta = self.service.delta_call(self.S, self.K, self.T, self.r, self.sigma)
        self.assertGreaterEqual(delta, 0)
        self.assertLessEqual(delta, 1)
    
    def test_gamma_positive(self):
        """Test que gamma est toujours positif."""
        gamma = self.service.gamma(self.S, self.K, self.T, self.r, self.sigma)
        self.assertGreaterEqual(gamma, 0)
    
    def test_theta_put_negative(self):
        """Test que theta PUT est négatif (décroissance temporelle)."""
        theta = self.service.theta_put(self.S, self.K, self.T, self.r, self.sigma)
        # Theta devrait être négatif (perte de valeur avec le temps)
        self.assertLessEqual(theta, 0)
    
    def test_vega_positive(self):
        """Test que vega est positif."""
        vega = self.service.vega(self.S, self.K, self.T, self.r, self.sigma)
        self.assertGreaterEqual(vega, 0)
    
    def test_greeks_edge_cases(self):
        """Test Greeks avec cas limites."""
        # T = 0
        delta_expired = self.service.delta_put(self.S, self.K, 0, self.r, self.sigma)
        self.assertIn(delta_expired, [-1, 0])  # -1 si ITM, 0 si OTM
        
        gamma_expired = self.service.gamma(self.S, self.K, 0, self.r, self.sigma)
        self.assertEqual(gamma_expired, 0)
        
        theta_expired = self.service.theta_put(self.S, self.K, 0, self.r, self.sigma)
        self.assertEqual(theta_expired, 0)
    
    # =========================================================================
    # TESTS STRIKE FINDER
    # =========================================================================
    
    def test_find_strike_by_delta_put(self):
        """Test recherche de strike par delta pour PUT."""
        target_delta = -0.30
        strike = self.service.find_strike_by_delta(
            self.S, self.T, self.r, self.sigma, target_delta, 'put'
        )
        
        # Vérifier que le strike trouvé donne le delta cible
        actual_delta = self.service.delta_put(self.S, strike, self.T, self.r, self.sigma)
        self.assertAlmostEqual(actual_delta, target_delta, places=2)
    
    def test_find_strike_by_delta_call(self):
        """Test recherche de strike par delta pour CALL."""
        target_delta = 0.30
        strike = self.service.find_strike_by_delta(
            self.S, self.T, self.r, self.sigma, target_delta, 'call'
        )
        
        # Vérifier que le strike trouvé donne le delta cible
        actual_delta = self.service.delta_call(self.S, strike, self.T, self.r, self.sigma)
        self.assertAlmostEqual(actual_delta, target_delta, places=2)
    
    def test_find_strike_delta_extreme(self):
        """Test avec delta extrême."""
        # Delta très proche de 0
        strike = self.service.find_strike_by_delta(
            self.S, self.T, self.r, self.sigma, -0.05, 'put'
        )
        self.assertIsInstance(strike, float)
        self.assertGreater(strike, 0)
    
    # =========================================================================
    # TESTS PUT SPREAD
    # =========================================================================
    
    def test_calculate_put_spread_structure(self):
        """Test structure d'un PUT SPREAD."""
        spread = self.service.calculate_put_spread(
            self.S, self.T, self.r, self.sigma,
            delta_long=-0.30, delta_short=-0.10
        )
        
        # Vérifier la structure
        self.assertEqual(spread['type'], 'PUT_SPREAD')
        self.assertGreater(spread['strike_long'], spread['strike_short'])
        self.assertGreater(spread['price_long'], spread['price_short'])
        self.assertGreater(spread['net_debit'], 0)
        self.assertGreater(spread['max_profit'], 0)
        self.assertGreater(spread['max_loss'], 0)
    
    def test_put_spread_breakeven(self):
        """Test calcul du breakeven."""
        spread = self.service.calculate_put_spread(
            self.S, self.T, self.r, self.sigma
        )
        
        # Breakeven = strike_long - net_debit
        expected_be = spread['strike_long'] - spread['net_debit']
        self.assertAlmostEqual(spread['breakeven'], expected_be, places=2)
    
    def test_put_spread_risk_reward(self):
        """Test ratio risk/reward."""
        spread = self.service.calculate_put_spread(
            self.S, self.T, self.r, self.sigma
        )
        
        # Risk/reward devrait être raisonnable
        self.assertGreater(spread['risk_reward_ratio'], 0)
        self.assertLess(spread['risk_reward_ratio'], 10)  # Pas de ratio extrême
    
    def test_put_spread_greeks(self):
        """Test Greeks du spread."""
        spread = self.service.calculate_put_spread(
            self.S, self.T, self.r, self.sigma
        )
        
        # Delta spread devrait être négatif (position short)
        self.assertLess(spread['delta_spread'], 0)
        
        # Vérifier que les champs existent
        self.assertIn('gamma_spread', spread)
        self.assertIn('theta_spread', spread)
        self.assertIn('vega_spread', spread)
    
    # =========================================================================
    # TESTS NAKED PUT
    # =========================================================================
    
    def test_calculate_naked_put_structure(self):
        """Test structure d'un PUT simple."""
        put = self.service.calculate_naked_put(
            self.S, self.T, self.r, self.sigma, delta_target=-0.30
        )
        
        # Vérifier la structure
        self.assertEqual(put['type'], 'PUT')
        self.assertGreater(put['strike'], 0)
        self.assertGreater(put['price'], 0)
        self.assertGreater(put['max_profit'], 0)
        self.assertGreater(put['max_loss'], 0)
    
    def test_naked_put_breakeven(self):
        """Test breakeven PUT."""
        put = self.service.calculate_naked_put(
            self.S, self.T, self.r, self.sigma
        )
        
        # Breakeven = strike - price
        expected_be = put['strike'] - put['price']
        self.assertAlmostEqual(put['breakeven'], expected_be, places=2)
    
    def test_naked_put_greeks(self):
        """Test Greeks du PUT."""
        put = self.service.calculate_naked_put(
            self.S, self.T, self.r, self.sigma
        )
        
        # Delta devrait être dans [-1, 0]
        self.assertGreaterEqual(put['delta'], -1)
        self.assertLessEqual(put['delta'], 0)
        
        # Vérifier présence des autres Greeks
        self.assertIn('gamma', put)
        self.assertIn('theta', put)
        self.assertIn('vega', put)
    
    # =========================================================================
    # TESTS STRATEGY ENGINE
    # =========================================================================
    
    def test_build_recommendation_valid_entry(self):
        """Test recommandation avec conditions d'entrée valides."""
        rec = self.service.build_option_recommendation(
            ticker='AAPL',
            spot_price=100.0,
            iv=0.30,
            momentum_score=-20.0,
            perf_63_5=-20.0,  # Major downtrend
            perf_5_0=2.0,      # Pas de short squeeze
            dte_target=45,
            iv_rank=50.0       # IV Rank OK
        )
        
        # Vérifier structure
        self.assertEqual(rec['ticker'], 'AAPL')
        self.assertIn('signal', rec)
        self.assertIn('entry_conditions_met', rec)
        self.assertIn('recommended_strategy', rec)
        self.assertIn('put', rec)
        self.assertIn('put_spread', rec)
        
        # Conditions devraient être remplies
        self.assertTrue(rec['conditions']['hard_conditions']['major_downtrend'])
        self.assertTrue(rec['entry_conditions_met'])
    
    def test_build_recommendation_invalid_hard_condition(self):
        """Test avec condition hard non remplie."""
        rec = self.service.build_option_recommendation(
            ticker='AAPL',
            spot_price=100.0,
            iv=0.30,
            momentum_score=-10.0,
            perf_63_5=-10.0,  # Pas de major downtrend (< -15)
            perf_5_0=2.0,
            dte_target=45,
            iv_rank=50.0
        )
        
        # Conditions ne devraient pas être remplies
        self.assertFalse(rec['conditions']['hard_conditions']['major_downtrend'])
        self.assertFalse(rec['entry_conditions_met'])
        self.assertEqual(rec['signal'], 'WATCH')
    
    def test_build_recommendation_soft_conditions_scoring(self):
        """Test scoring des conditions soft."""
        # Cas 1: Toutes les conditions soft OK
        rec1 = self.service.build_option_recommendation(
            ticker='AAPL',
            spot_price=100.0,
            iv=0.30,
            momentum_score=-20.0,
            perf_63_5=-20.0,
            perf_5_0=2.0,      # OK
            dte_target=45,
            iv_rank=50.0       # OK
        )
        self.assertGreaterEqual(rec1['conditions']['soft_score'], 0.66)
        
        # Cas 2: Une seule condition soft OK (score < 0.66)
        rec2 = self.service.build_option_recommendation(
            ticker='AAPL',
            spot_price=100.0,
            iv=0.30,
            momentum_score=-20.0,
            perf_63_5=-20.0,
            perf_5_0=10.0,     # Échoue (> 5)
            dte_target=45,
            iv_rank=50.0       # OK
        )
        self.assertLess(rec2['conditions']['soft_score'], 0.66)
    
    def test_build_recommendation_iv_rank_missing(self):
        """Test avec IV Rank manquant."""
        rec = self.service.build_option_recommendation(
            ticker='AAPL',
            spot_price=100.0,
            iv=0.30,
            momentum_score=-20.0,
            perf_63_5=-20.0,
            perf_5_0=2.0,
            dte_target=45,
            iv_rank=None  # Non fourni
        )
        
        # IV Rank devrait être None dans la réponse
        self.assertIsNone(rec['iv_rank'])
        # Condition soft devrait échouer (iv_rank_value = 100 par défaut)
        self.assertFalse(rec['conditions']['soft_conditions']['iv_rank_ok'])
    
    def test_build_recommendation_strategy_selection(self):
        """Test sélection PUT vs PUT SPREAD."""
        # Cas 1: IV élevée → PUT SPREAD
        rec1 = self.service.build_option_recommendation(
            ticker='AAPL',
            spot_price=100.0,
            iv=0.50,  # IV élevée
            momentum_score=-20.0,
            perf_63_5=-20.0,
            perf_5_0=-5.0,
            dte_target=45,
            iv_rank=50.0
        )
        self.assertEqual(rec1['recommended_strategy'], 'PUT_SPREAD')
        
        # Cas 2: Momentum récent faible → PUT SPREAD
        rec2 = self.service.build_option_recommendation(
            ticker='AAPL',
            spot_price=100.0,
            iv=0.30,
            momentum_score=-20.0,
            perf_63_5=-20.0,
            perf_5_0=-1.0,  # > -2
            dte_target=45,
            iv_rank=50.0
        )
        self.assertEqual(rec2['recommended_strategy'], 'PUT_SPREAD')
        
        # Cas 3: Conditions favorables → PUT simple
        rec3 = self.service.build_option_recommendation(
            ticker='AAPL',
            spot_price=100.0,
            iv=0.30,  # IV normale
            momentum_score=-20.0,
            perf_63_5=-20.0,
            perf_5_0=-5.0,  # < -2
            dte_target=45,
            iv_rank=50.0
        )
        self.assertEqual(rec3['recommended_strategy'], 'PUT')
    
    def test_build_recommendation_all_fields(self):
        """Test que tous les champs sont présents."""
        rec = self.service.build_option_recommendation(
            ticker='AAPL',
            spot_price=100.0,
            iv=0.30,
            momentum_score=-20.0,
            perf_63_5=-20.0,
            perf_5_0=2.0,
            dte_target=45,
            iv_rank=50.0
        )
        
        required_fields = [
            'ticker', 'signal', 'momentum_score', 'perf_63_5', 'perf_5_0',
            'spot_price', 'iv_pct', 'iv_rank', 'conditions', 'entry_conditions_met',
            'recommended_strategy', 'put', 'put_spread', 'entry_rules', 'exit_rules'
        ]
        
        for field in required_fields:
            self.assertIn(field, rec, f"Champ manquant: {field}")
    
    # =========================================================================
    # TESTS HELPERS
    # =========================================================================
    
    def test_dte_to_years(self):
        """Test conversion DTE en années."""
        dte = 45
        years = self.service.dte_to_years(dte)
        self.assertAlmostEqual(years, 45 / 365, places=5)
    
    def test_get_expiration_date(self):
        """Test calcul date d'expiration."""
        dte = 45
        exp_date = self.service.get_expiration_date(dte)
        
        # Vérifier format
        self.assertIsInstance(exp_date, str)
        datetime.strptime(exp_date, '%Y-%m-%d')  # Devrait parser sans erreur
        
        # Vérifier que c'est dans le futur
        exp_dt = datetime.strptime(exp_date, '%Y-%m-%d')
        today = datetime.now()
        self.assertGreater(exp_dt, today)
    
    def test_estimate_iv_from_price_put(self):
        """Test estimation IV depuis prix de marché (PUT)."""
        # Prix de marché connu
        market_price = 5.0
        iv_estimated = self.service.estimate_iv_from_price(
            self.S, self.K, self.T, self.r, market_price, 'put'
        )
        
        # IV devrait être dans une plage raisonnable
        self.assertGreater(iv_estimated, 0.01)
        self.assertLess(iv_estimated, 5.0)
        
        # Vérifier que le prix calculé avec cette IV est proche
        calculated_price = self.service.put_price(
            self.S, self.K, self.T, self.r, iv_estimated
        )
        self.assertAlmostEqual(calculated_price, market_price, places=1)
    
    def test_estimate_iv_from_price_call(self):
        """Test estimation IV depuis prix de marché (CALL)."""
        market_price = 8.0
        iv_estimated = self.service.estimate_iv_from_price(
            self.S, self.K, self.T, self.r, market_price, 'call'
        )
        
        self.assertGreater(iv_estimated, 0.01)
        self.assertLess(iv_estimated, 5.0)


class TestHistoricalVolatility(unittest.TestCase):
    """Tests pour estimate_historical_volatility."""
    
    def test_estimate_historical_volatility_basic(self):
        """Test calcul volatilité historique basique."""
        # Générer des prix avec volatilité connue
        prices = [100.0, 101.0, 99.0, 102.0, 98.0, 103.0, 97.0]
        vol = estimate_historical_volatility(prices, window=5)
        
        self.assertGreater(vol, 0)
        self.assertIsInstance(vol, float)
    
    def test_estimate_historical_volatility_insufficient_data(self):
        """Test avec données insuffisantes."""
        prices = [100.0, 101.0]  # Pas assez de données
        vol = estimate_historical_volatility(prices, window=30)
        
        # Devrait retourner valeur par défaut
        self.assertEqual(vol, 0.30)
    
    def test_estimate_historical_volatility_empty(self):
        """Test avec liste vide."""
        prices = []
        vol = estimate_historical_volatility(prices)
        self.assertEqual(vol, 0.30)
    
    def test_estimate_historical_volatility_constant_price(self):
        """Test avec prix constants (volatilité = 0)."""
        prices = [100.0] * 50
        vol = estimate_historical_volatility(prices, window=30)
        
        # Volatilité devrait être proche de 0
        self.assertGreaterEqual(vol, 0)
        self.assertLess(vol, 0.01)


class TestOptionsServiceIntegration(unittest.TestCase):
    """Tests d'intégration pour scénarios complets."""
    
    def setUp(self):
        """Initialise le service."""
        self.service = OptionsService(risk_free_rate=0.05)
    
    def test_complete_workflow(self):
        """Test workflow complet: recommandation → calculs → validation."""
        # Scénario réaliste
        rec = self.service.build_option_recommendation(
            ticker='TSLA',
            spot_price=250.0,
            iv=0.40,
            momentum_score=-25.0,
            perf_63_5=-25.0,
            perf_5_0=3.0,
            dte_target=45,
            iv_rank=45.0
        )
        
        # Vérifier cohérence
        self.assertEqual(rec['ticker'], 'TSLA')
        self.assertEqual(rec['spot_price'], 250.0)
        self.assertEqual(rec['iv_pct'], 40.0)
        
        # Vérifier que PUT et PUT SPREAD sont calculés
        self.assertIn('strike', rec['put'])
        self.assertIn('strike_long', rec['put_spread'])
        
        # Vérifier que les prix sont cohérents
        self.assertGreater(rec['put']['price'], 0)
        self.assertGreater(rec['put_spread']['net_debit'], 0)
    
    def test_edge_case_extreme_volatility(self):
        """Test avec volatilité extrême."""
        rec = self.service.build_option_recommendation(
            ticker='TEST',
            spot_price=100.0,
            iv=1.0,  # Volatilité 100%
            momentum_score=-20.0,
            perf_63_5=-20.0,
            perf_5_0=2.0,
            dte_target=45,
            iv_rank=50.0
        )
        
        # Devrait toujours fonctionner
        self.assertIn('put', rec)
        self.assertIn('put_spread', rec)
        # Prix devraient être élevés avec IV élevée
        self.assertGreater(rec['put']['price'], 0)
    
    def test_edge_case_very_short_dte(self):
        """Test avec DTE très court."""
        rec = self.service.build_option_recommendation(
            ticker='TEST',
            spot_price=100.0,
            iv=0.30,
            momentum_score=-20.0,
            perf_63_5=-20.0,
            perf_5_0=2.0,
            dte_target=7,  # 7 jours seulement
            iv_rank=50.0
        )
        
        # Devrait fonctionner
        self.assertIn('put', rec)
        # Theta devrait être plus élevé (décroissance plus rapide)
        self.assertLess(rec['put']['theta'], 0)


if __name__ == '__main__':
    # Configuration pour tests détaillés
    unittest.main(verbosity=2)

