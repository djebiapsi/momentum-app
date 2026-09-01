# -*- coding: utf-8 -*-
"""
Service de signal SHORT live (scoring multi-facteurs)
=====================================================
Calcule, sur les données live en base (as_of = dernière séance disponible), le
score composite de la stratégie short pour tout l'univers, et renvoie les
candidats classés. Réutilise EXACTEMENT la logique de `short_data`
(source de vérité unique partagée avec le backtest directionnel).

⚠️ Les seuils du score sont des hypothèses à calibrer (cf. `docs/strategie_short_v2.md`
Partie 13). Ce service produit un signal indicatif ; la décision d'instrument
(PUT / PUT SPREAD) ne prend pas encore en compte le skew (données options non
souscrites tant que la validation directionnelle n'est pas franchie).
"""

import logging

import pandas as pd

import short_data as sd
from short_scoring import decide_instrument

logger = logging.getLogger(__name__)


class ShortSignalService:

    def __init__(self, min_bars=260):
        self.min_bars = min_bars

    def compute_signals(self, version='C', min_score=3, top_n=15):
        """
        Calcule le signal short live.

        version  : 'A' momentum seul | 'B' +accruals | 'C' +short interest (défaut).
        min_score: score composite minimum pour figurer dans les candidats.
        top_n    : nombre maximum de candidats renvoyés.

        Renvoie un dict :
          {success, date, version, regime, universe, n_scored, candidates:[...],
           error?}
        Chaque candidat :
          {ticker, score, points, perf_63_5, alpha_sector, death_cross,
           squeeze_risk, uptrend_regime, instrument, size_factor, rank}
        """
        prices = sd.load_daily_prices(min_bars=self.min_bars)
        if not prices or 'SPY' not in prices:
            return {'success': False,
                    'error': "Données de prix insuffisantes (SPY absent) — "
                             "lancer la collecte yfinance."}

        spy = prices['SPY']
        as_of = pd.Timestamp(spy.index[-1])

        membership = sd.load_membership()
        sector_map = sd.load_sector_map()
        accruals = sd.load_accruals() if version in ('B', 'C') else {}
        short_int = sd.load_short_interest() if version == 'C' else {}

        from price_data_service import BENCHMARKS
        bench = set(BENCHMARKS)
        candidates = [t for t in prices if t not in bench]

        scored = sd.score_candidates_asof(
            as_of, candidates, prices, version=version, membership=membership,
            sector_map=sector_map, accruals=accruals, short_int=short_int,
            spy=spy, with_forward=False)

        regime = scored[0]['regime'] if scored else None

        rows = []
        for e in scored:
            res = e['score']
            if res.total < min_score:
                continue
            decision = decide_instrument(
                res.total, e['perf_63_5'], skew_ratio=None,
                squeeze_risk=res.squeeze_risk, uptrend_regime=res.uptrend_regime)
            rows.append({
                'ticker': e['ticker'],
                'score': res.total,
                'points': res.points,
                'perf_63_5': round(e['perf_63_5'], 4),
                'alpha_sector': (round(e['alpha_sector'], 4)
                                 if e['alpha_sector'] is not None else None),
                'death_cross': res.death_cross,
                'squeeze_risk': res.squeeze_risk,
                'uptrend_regime': res.uptrend_regime,
                'instrument': decision['instrument'],
                'size_factor': decision['size_factor'],
            })

        # Classement : score décroissant, puis momentum le plus négatif
        rows.sort(key=lambda r: (-r['score'], r['perf_63_5']))
        rows = rows[:top_n]
        for i, r in enumerate(rows):
            r['rank'] = i + 1

        return {
            'success': True,
            'date': as_of.date().isoformat(),
            'version': version,
            'regime': regime,
            'universe': len(candidates),
            'n_scored': len(scored),
            'candidates': rows,
        }
