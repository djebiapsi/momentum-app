# -*- coding: utf-8 -*-
"""
Screen fondamental QUALITY-VALUE (portefeuille long, buy-and-hold)
==================================================================
« Bonnes entreprises à prix correct » : combine deux dimensions documentées,
robustes sur le long terme :

  QUALITÉ (Novy-Marx, Asness Quality-Minus-Junk) — rentabilité, génération de
    cash, qualité des bénéfices (accruals bas), solidité du bilan.
  VALEUR (Fama-French, value premium) — cherté raisonnable via rendements
    (FCF yield, earnings yield, book/sales yield, EBITDA/EV).

Méthode : pour chaque métrique, rang PERCENTILE cross-sectionnel (0-100, orienté
« plus haut = mieux »). Score Qualité = moyenne des percentiles qualité dispo ;
Score Valeur = moyenne des percentiles valeur. Composite = pondération des deux.
Robuste aux valeurs extrêmes (rangs, pas de valeurs brutes) et aux données
manquantes (moyenne des métriques disponibles).

⚠️ Screen LIVE : `TickerInfoSnapshot` n'a qu'un instantané courant (ratios de
valorisation non historisés) → conçu pour construire un portefeuille actuel, pas
pour un backtest profond. Les facteurs sont académiquement établis ; la validation
repose sur cette base, pas sur un backtest long (impossible avec l'historique
fondamental yfinance de ~2-3 ans).
"""

import logging

logger = logging.getLogger(__name__)

# Métriques QUALITÉ : (clé, orientation) — 'high' = plus haut meilleur, 'low' = plus bas meilleur
QUALITY_METRICS = [
    ('gp_to_assets',      'high'),   # Novy-Marx : gross profit / total assets
    ('return_on_equity',  'high'),
    ('return_on_assets',  'high'),
    ('gross_margins',     'high'),
    ('operating_margins', 'high'),
    ('fcf_margin',        'high'),
    ('accruals_ratio',    'low'),    # accruals bas = bénéfices de qualité (Sloan)
    ('debt_to_equity',    'low'),
    ('current_ratio',     'high'),
]
# Métriques VALEUR : rendements (plus haut = moins cher = mieux)
VALUE_METRICS = [
    ('fcf_yield',      'high'),   # FCF / market cap
    ('earnings_yield', 'high'),   # 1 / PE
    ('book_yield',     'high'),   # 1 / P/B
    ('sales_yield',    'high'),   # 1 / P/S
    ('ebitda_ev',      'high'),   # 1 / (EV/EBITDA)
]


class FundamentalScreenService:

    def __init__(self, min_market_cap=2e9, min_quality_metrics=4, min_value_metrics=3):
        self.min_market_cap = min_market_cap
        self.min_quality_metrics = min_quality_metrics
        self.min_value_metrics = min_value_metrics
        # Cache TTL des chargements DB lourds (info/fondamentaux) — réutilisé par
        # screen / evaluate / quality_scores (ex: dashboard momentum).
        self._cache = {}
        self._cache_ttl = 600  # secondes

    def _cached(self, key, fn):
        import time as _t
        hit = self._cache.get(key)
        if hit and (_t.time() - hit[0] < self._cache_ttl):
            return hit[1]
        val = fn()
        self._cache[key] = (_t.time(), val)
        return val

    def invalidate_cache(self):
        """Vide le cache (à appeler après une collecte de fondamentaux)."""
        self._cache.clear()

    # =====================================================================
    # CHARGEMENT DES DONNÉES (dernier instantané par ticker)
    # =====================================================================
    def _latest_info(self):
        """{ticker: TickerInfoSnapshot} le plus récent par ticker."""
        from models import db, TickerInfoSnapshot
        from sqlalchemy import func
        sub = (db.session.query(TickerInfoSnapshot.ticker,
                                func.max(TickerInfoSnapshot.collected_at).label('mx'))
               .group_by(TickerInfoSnapshot.ticker).subquery())
        rows = (db.session.query(TickerInfoSnapshot)
                .join(sub, (TickerInfoSnapshot.ticker == sub.c.ticker)
                      & (TickerInfoSnapshot.collected_at == sub.c.mx)).all())
        return {r.ticker: r for r in rows}

    def _ttm_fundamentals(self):
        """
        Par ticker, agrège les fondamentaux en TTM :
          - flux (revenue, gross_profit, fcf, net_income) = somme des 4 derniers trimestres
            (repli : dernière valeur annuelle 'A' si trimestriels insuffisants)
          - stock (total_assets, accruals_ratio, ...) = dernière valeur connue
        Renvoie {ticker: dict de grandeurs}.
        """
        from models import FundamentalSnapshot
        rows = (FundamentalSnapshot.query
                .order_by(FundamentalSnapshot.ticker,
                          FundamentalSnapshot.period_date.desc()).all())
        by_ticker = {}
        for r in rows:
            by_ticker.setdefault(r.ticker, []).append(r)

        out = {}
        for ticker, snaps in by_ticker.items():
            q = [s for s in snaps if s.period_type == 'Q']
            a = [s for s in snaps if s.period_type == 'A']
            latest = snaps[0]  # plus récent tous types confondus

            def _ttm(attr):
                vals = [getattr(s, attr) for s in q[:4] if getattr(s, attr) is not None]
                if len(vals) >= 4:
                    return sum(vals)
                if a:  # repli annuel
                    av = getattr(a[0], attr)
                    if av is not None:
                        return av
                return sum(vals) if vals else None

            total_assets = next((s.total_assets for s in snaps if s.total_assets is not None), None)
            out[ticker] = {
                'revenue':      _ttm('total_revenue'),
                'gross_profit': _ttm('gross_profit'),
                'fcf':          _ttm('free_cash_flow'),
                'net_income':   _ttm('net_income'),
                'total_assets': total_assets,
                'accruals_ratio': next((s.accruals_ratio for s in snaps
                                        if s.accruals_ratio is not None), None),
            }
        return out

    # =====================================================================
    # CALCUL DES MÉTRIQUES BRUTES PAR TICKER
    # =====================================================================
    def _raw_metrics(self, info, fund):
        """Assemble les métriques brutes d'un ticker (dict). None si non calculable."""
        m = {}
        mc = info.market_cap if info else None

        # --- Qualité ---
        gp = fund.get('gross_profit')
        ta = fund.get('total_assets')
        m['gp_to_assets'] = (gp / ta) if (gp is not None and ta not in (None, 0)) else None
        m['return_on_equity'] = info.return_on_equity if info else None
        m['return_on_assets'] = info.return_on_assets if info else None
        m['gross_margins'] = info.gross_margins if info else None
        m['operating_margins'] = info.operating_margins if info else None
        rev = fund.get('revenue')
        fcf = fund.get('fcf')
        m['fcf_margin'] = (fcf / rev) if (fcf is not None and rev not in (None, 0)) else None
        m['accruals_ratio'] = fund.get('accruals_ratio')
        m['debt_to_equity'] = info.debt_to_equity if info else None
        m['current_ratio'] = info.current_ratio if info else None

        # --- Valeur (rendements) ---
        m['fcf_yield'] = (fcf / mc) if (fcf is not None and mc not in (None, 0)) else None
        pe = info.trailing_pe if info else None
        m['earnings_yield'] = (1.0 / pe) if (pe is not None and pe > 0) else None
        pb = info.price_to_book if info else None
        m['book_yield'] = (1.0 / pb) if (pb is not None and pb > 0) else None
        ps = info.price_to_sales if info else None
        m['sales_yield'] = (1.0 / ps) if (ps is not None and ps > 0) else None
        ev_ebitda = info.ev_to_ebitda if info else None
        m['ebitda_ev'] = (1.0 / ev_ebitda) if (ev_ebitda is not None and ev_ebitda > 0) else None
        return m

    # =====================================================================
    # RANGS PERCENTILES CROSS-SECTIONNELS
    # =====================================================================
    @staticmethod
    def _percentile_ranks(values_by_ticker, orientation):
        """
        {ticker: valeur} → {ticker: percentile 0-100}. `orientation` 'high'/'low'
        oriente pour que 100 = meilleur. None ignorés.
        """
        pairs = [(t, v) for t, v in values_by_ticker.items() if v is not None]
        if len(pairs) < 5:
            return {}
        pairs.sort(key=lambda x: x[1])  # croissant
        n = len(pairs)
        ranks = {}
        for i, (t, _) in enumerate(pairs):
            pct = 100.0 * i / (n - 1)          # 0..100 selon rang croissant
            ranks[t] = pct if orientation == 'high' else (100.0 - pct)
        return ranks

    # =====================================================================
    # SCREEN
    # =====================================================================
    def screen(self, top_n=20, quality_weight=0.5, market='all'):
        """
        Calcule le portefeuille Quality-Value. quality_weight ∈ [0,1] pondère
        Qualité vs Valeur dans le composite. market ∈ {'us','eu','all'} restreint
        l'univers par région (via IndexConstituent). Renvoie un dict {success,
        universe, eligible, market, holdings:[...]}.
        """
        info_map = self._latest_info()
        fund_map = self._ttm_fundamentals()
        tickers = set(info_map) | set(fund_map)

        # Filtre région (PEA = 'eu', S&P = 'us', transversal = 'all')
        if market in ('us', 'eu'):
            from eu_universe import universe_for_market
            region = universe_for_market(market)
            if region:
                tickers = tickers & region

        if not tickers:
            return {'success': False, 'error': "Aucune donnée fondamentale pour ce "
                    "marché (lancer les collectes prix + fondamentaux)."}

        # 1) métriques brutes + éligibilité
        raw = {}
        for t in tickers:
            info = info_map.get(t)
            fund = fund_map.get(t, {})
            mc = info.market_cap if info else None
            if mc is None or mc < self.min_market_cap:
                continue
            metrics = self._raw_metrics(info, fund)
            nq = sum(1 for k, _ in QUALITY_METRICS if metrics.get(k) is not None)
            nv = sum(1 for k, _ in VALUE_METRICS if metrics.get(k) is not None)
            if nq < self.min_quality_metrics or nv < self.min_value_metrics:
                continue
            raw[t] = {'metrics': metrics, 'market_cap': mc,
                      'sector': (info.sector if info else None)}

        if len(raw) < 10:
            return {'success': False, 'error': f"Univers éligible trop petit ({len(raw)})."}

        # 2) rangs percentiles par métrique
        pct = {}  # {metric_key: {ticker: percentile}}
        for key, orient in QUALITY_METRICS + VALUE_METRICS:
            vals = {t: raw[t]['metrics'].get(key) for t in raw}
            pct[key] = self._percentile_ranks(vals, orient)

        # 3) scores Qualité / Valeur / Composite
        holdings = []
        for t in raw:
            q_scores = [pct[k].get(t) for k, _ in QUALITY_METRICS if pct[k].get(t) is not None]
            v_scores = [pct[k].get(t) for k, _ in VALUE_METRICS if pct[k].get(t) is not None]
            if not q_scores or not v_scores:
                continue
            quality = sum(q_scores) / len(q_scores)
            value = sum(v_scores) / len(v_scores)
            composite = quality_weight * quality + (1.0 - quality_weight) * value
            holdings.append({
                'ticker': t,
                'composite': round(composite, 1),
                'quality': round(quality, 1),
                'value': round(value, 1),
                'sector': raw[t]['sector'],
                'market_cap': raw[t]['market_cap'],
                'metrics': {k: raw[t]['metrics'].get(k) for k, _ in QUALITY_METRICS + VALUE_METRICS},
            })

        holdings.sort(key=lambda h: -h['composite'])
        for i, h in enumerate(holdings):
            h['rank'] = i + 1

        return {
            'success': True,
            'universe': len(tickers),
            'eligible': len(raw),
            'market': market,
            'quality_weight': quality_weight,
            'top_n': top_n,
            'holdings': holdings[:top_n],
            'all_ranked': holdings,   # complet (pour allocation/diagnostic)
        }

    # =====================================================================
    # CONSTRUCTION DE PORTEFEUILLE (diversification + allocation)
    # =====================================================================
    def build_portfolio(self, top_n=20, quality_weight=0.5, max_per_sector=3,
                        weighting='equal', market='all'):
        """
        Screen + diversification sectorielle + allocation.

        max_per_sector : nb max de titres par secteur GICS (évite la surpondération
                         value sur finance/énergie).
        weighting      : 'equal' (équipondéré) ou 'composite' (∝ score composite).
        market         : 'us' | 'eu' | 'all' (région de l'univers).

        Renvoie {success, holdings:[... + allocation], sector_breakdown, ...}.
        """
        base = self.screen(top_n=10_000, quality_weight=quality_weight, market=market)
        if not base.get('success'):
            return base

        # Sélection avec plafond par secteur, dans l'ordre du composite
        selected, per_sector = [], {}
        for h in base['all_ranked']:
            sec = h.get('sector') or 'Unknown'
            if per_sector.get(sec, 0) >= max_per_sector:
                continue
            per_sector[sec] = per_sector.get(sec, 0) + 1
            selected.append(h)
            if len(selected) >= top_n:
                break

        # Allocation
        if weighting == 'composite':
            total = sum(h['composite'] for h in selected) or 1.0
            for h in selected:
                h['allocation'] = round(100.0 * h['composite'] / total, 2)
        else:  # equal
            w = round(100.0 / len(selected), 2) if selected else 0.0
            for h in selected:
                h['allocation'] = w

        for i, h in enumerate(selected):
            h['rank'] = i + 1

        breakdown = {}
        for h in selected:
            sec = h.get('sector') or 'Unknown'
            breakdown[sec] = round(breakdown.get(sec, 0.0) + h['allocation'], 2)

        return {
            'success': True,
            'universe': base['universe'],
            'eligible': base['eligible'],
            'market': market,
            'quality_weight': quality_weight,
            'max_per_sector': max_per_sector,
            'weighting': weighting,
            'holdings': selected,
            'sector_breakdown': dict(sorted(breakdown.items(),
                                            key=lambda x: -x[1])),
        }

    # =====================================================================
    # UNIVERS ÉLIGIBLE RÉUTILISABLE (screen / éval ticker / scores qualité)
    # =====================================================================
    def _eligible_raw(self, market='all'):
        """{ticker: {metrics, market_cap, sector}} de l'univers éligible + les maps info/fund."""
        info_map = self._cached('info', self._latest_info)
        fund_map = self._cached('fund', self._ttm_fundamentals)
        tickers = set(info_map) | set(fund_map)
        if market in ('us', 'eu'):
            from eu_universe import universe_for_market
            region = universe_for_market(market)
            if region:
                tickers = tickers & region
        raw = {}
        for t in tickers:
            info = info_map.get(t)
            fund = fund_map.get(t, {})
            mc = info.market_cap if info else None
            if mc is None or mc < self.min_market_cap:
                continue
            metrics = self._raw_metrics(info, fund)
            nq = sum(1 for k, _ in QUALITY_METRICS if metrics.get(k) is not None)
            nv = sum(1 for k, _ in VALUE_METRICS if metrics.get(k) is not None)
            if nq < self.min_quality_metrics or nv < self.min_value_metrics:
                continue
            raw[t] = {'metrics': metrics, 'market_cap': mc,
                      'sector': (info.sector if info else None)}
        return raw, info_map, fund_map

    @staticmethod
    def _pct_of(value, population, orientation):
        """Percentile 0-100 d'une valeur dans une population (100 = meilleur selon orientation)."""
        vals = [v for v in population if v is not None]
        if value is None or len(vals) < 5:
            return None
        below = sum(1 for v in vals if v <= value)
        p = 100.0 * below / len(vals)
        return p if orientation == 'high' else (100.0 - p)

    def _universe_composites(self, raw, quality_weight=0.5):
        """{ticker: (quality, value, composite)} pour l'univers éligible."""
        pct = {}
        for key, orient in QUALITY_METRICS + VALUE_METRICS:
            pct[key] = self._percentile_ranks({t: raw[t]['metrics'].get(key) for t in raw}, orient)
        out = {}
        for t in raw:
            qs = [pct[k].get(t) for k, _ in QUALITY_METRICS if pct[k].get(t) is not None]
            vs = [pct[k].get(t) for k, _ in VALUE_METRICS if pct[k].get(t) is not None]
            if not qs or not vs:
                continue
            q = sum(qs) / len(qs)
            v = sum(vs) / len(vs)
            out[t] = (q, v, quality_weight * q + (1 - quality_weight) * v)
        return out

    # =====================================================================
    # ÉVALUATION D'UN TICKER (en base, sinon récupéré en direct)
    # =====================================================================
    def evaluate_ticker(self, ticker, market='all', quality_weight=0.5):
        """
        Score Quality-Value d'un ticker, classé vs l'univers. Si absent de la base,
        récupère ses données via yfinance (fondamentaux + info), les stocke, puis score.
        """
        ticker = (ticker or '').upper().strip()
        if not ticker:
            return {'success': False, 'error': 'Ticker vide'}

        raw, info_map, fund_map = self._eligible_raw(market)
        fetched = False
        info = info_map.get(ticker)
        fund = fund_map.get(ticker, {})

        # Absent de la base → récupération live yfinance + stockage
        if info is None and not fund:
            try:
                from services import get_fundamentals_collector
                fc = get_fundamentals_collector()
                fc.collect_statements([ticker], full=False)
                fc.collect_info([ticker])
                self.invalidate_cache()  # nouvelles données → recharger frais
                info = self._latest_info().get(ticker)
                fund = self._ttm_fundamentals().get(ticker, {})
                fetched = True
            except Exception as e:
                return {'success': False, 'error': f'Récupération yfinance échouée : {e}'}

        if info is None and not fund:
            return {'success': False, 'error': f'Aucune donnée disponible pour {ticker}.'}

        metrics = self._raw_metrics(info, fund)
        detail, q_pcts, v_pcts = {}, [], []
        for key, orient in QUALITY_METRICS:
            pop = [raw[t]['metrics'].get(key) for t in raw]
            p = self._pct_of(metrics.get(key), pop, orient)
            detail[key] = {'value': metrics.get(key), 'pct': (round(p, 1) if p is not None else None),
                           'group': 'quality'}
            if p is not None:
                q_pcts.append(p)
        for key, orient in VALUE_METRICS:
            pop = [raw[t]['metrics'].get(key) for t in raw]
            p = self._pct_of(metrics.get(key), pop, orient)
            detail[key] = {'value': metrics.get(key), 'pct': (round(p, 1) if p is not None else None),
                           'group': 'value'}
            if p is not None:
                v_pcts.append(p)

        if not q_pcts or not v_pcts:
            return {'success': False,
                    'error': f'Métriques insuffisantes pour {ticker} (données incomplètes).'}

        quality = sum(q_pcts) / len(q_pcts)
        value = sum(v_pcts) / len(v_pcts)
        composite = quality_weight * quality + (1 - quality_weight) * value

        # Percentile du composite vs univers
        comps = self._universe_composites(raw, quality_weight)
        comp_vals = [c for _, _, c in comps.values()]
        comp_pct = self._pct_of(composite, comp_vals, 'high') if comp_vals else None

        return {
            'success': True,
            'ticker': ticker,
            'market': market,
            'fetched_live': fetched,
            'in_universe': ticker in raw,
            'sector': info.sector if info else None,
            'market_cap': info.market_cap if info else None,
            'quality': round(quality, 1),
            'value': round(value, 1),
            'composite': round(composite, 1),
            'composite_percentile': (round(comp_pct, 1) if comp_pct is not None else None),
            'universe_size': len(raw),
            'metrics': detail,
        }

    # =====================================================================
    # SCORES QUALITÉ (indicatif — pour le dashboard momentum)
    # =====================================================================
    def quality_scores(self, tickers, market='all'):
        """{ticker: score_qualité 0-100} pour les tickers demandés (None si non éligible)."""
        raw, _, _ = self._eligible_raw(market)
        pct = {}
        for key, orient in QUALITY_METRICS:
            pct[key] = self._percentile_ranks({t: raw[t]['metrics'].get(key) for t in raw}, orient)
        out = {}
        for t in tickers:
            tk = (t or '').upper().strip()
            if tk not in raw:
                out[tk] = None
                continue
            qs = [pct[k].get(tk) for k, _ in QUALITY_METRICS if pct[k].get(tk) is not None]
            out[tk] = round(sum(qs) / len(qs), 1) if qs else None
        return out
