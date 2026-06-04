# -*- coding: utf-8 -*-
"""
Service de surveillance du marché
=================================
Collecte en temps quasi réel quelques métriques clés (VIX, drawdown intraday du
S&P 500, drawdown du portefeuille, chute par position) et les compare à des seuils
configurables. Utilisé par le cron `job_market_monitor` (chaque minute en séance).

La logique de cycle de vie des évènements (création / clôture, anti-spam) vit dans
`app.py` car elle a besoin de la session DB et du service email ; ici on se limite
à *collecter* et *évaluer*.
"""

import logging

logger = logging.getLogger(__name__)


class MarketMonitorService:
    # Seuils par défaut (surchargés via Settings clé 'market_thresholds', JSON)
    DEFAULT_THRESHOLDS = {
        'vix_warn': 25.0,        # VIX élevé (peur)
        'vix_crit': 35.0,        # VIX très élevé (panique)
        'vix_spike_pct': 20.0,   # bond du VIX sur la séance (%)
        'spy_dd_warn': -2.0,     # drawdown intraday SPY (%)
        'spy_dd_crit': -4.0,
        'portfolio_dd_warn': -3.0,   # drawdown intraday portefeuille (%)
        'portfolio_dd_crit': -5.0,
        'position_drop': -7.0,   # chute intraday d'une position (%)
    }

    def __init__(self, ibkr_service, momentum_service):
        self.ibkr = ibkr_service
        self.momentum = momentum_service

    # ------------------------------------------------------------------
    # Seuils
    # ------------------------------------------------------------------
    def get_thresholds(self):
        """Lit les seuils depuis Settings (JSON) avec fallback sur les défauts."""
        import json
        from models import Settings
        raw = Settings.get('market_thresholds')
        thresholds = dict(self.DEFAULT_THRESHOLDS)
        if raw:
            try:
                thresholds.update({k: float(v) for k, v in json.loads(raw).items()
                                   if k in self.DEFAULT_THRESHOLDS})
            except (ValueError, TypeError) as e:
                logger.warning('Seuils invalides en base, fallback défauts (%s)', e)
        return thresholds

    def save_thresholds(self, values: dict):
        """Persiste les seuils (uniquement les clés connues)."""
        import json
        from models import Settings
        clean = {}
        for k, v in (values or {}).items():
            if k in self.DEFAULT_THRESHOLDS:
                try:
                    clean[k] = float(v)
                except (ValueError, TypeError):
                    continue
        merged = dict(self.DEFAULT_THRESHOLDS)
        merged.update(clean)
        Settings.set('market_thresholds', json.dumps(merged))
        return merged

    # ------------------------------------------------------------------
    # Collecte
    # ------------------------------------------------------------------
    def collect_metrics(self):
        """
        Récupère l'état courant du marché et du portefeuille.

        Returns un dict toujours exploitable (clés à None si indisponible) :
        {
            'connected': bool,
            'vix': float|None, 'vix_pct': float|None,
            'spy': float|None, 'spy_intraday_pct': float|None,
            'portfolio_intraday_pct': float|None,
            'positions': [{'ticker','pct','market_value','weight'}],
            'regime': {...} | None,
            'error': str|None,
        }
        """
        out = {
            'connected': False, 'vix': None, 'vix_pct': None,
            'spy': None, 'spy_intraday_pct': None,
            'portfolio_intraday_pct': None, 'positions': [],
            'regime': None, 'error': None,
        }

        # Régime BULL/BEAR (source momentum, cache journalier — pas d'appel lourd)
        try:
            out['regime'] = self.momentum.get_market_regime()
        except Exception as e:
            logger.warning('collect_metrics: régime indisponible (%s)', e)

        if not self.ibkr.ensure_connected():
            out['error'] = 'IBKR non connecté'
            return out
        out['connected'] = True

        # Positions du portefeuille (pour pondérer le drawdown global)
        positions = []
        try:
            stats = self.ibkr.get_portfolio_stats()
            positions = stats.get('positions', [])
        except Exception as e:
            logger.warning('collect_metrics: positions indisponibles (%s)', e)

        port_tickers = [p['ticker'] for p in positions if p.get('ticker')]
        quote_tickers = list(dict.fromkeys(['SPY', 'QQQ'] + port_tickers))

        try:
            quotes = self.ibkr.get_quotes(quote_tickers, include_vix=True)
        except Exception as e:
            out['error'] = f'Cotations indisponibles: {e}'
            return out

        # VIX
        vix = quotes.get('VIX')
        if vix:
            out['vix'] = vix.get('last')
            out['vix_pct'] = vix.get('pct')

        # SPY intraday
        spy = quotes.get('SPY')
        if spy:
            out['spy'] = spy.get('last')
            out['spy_intraday_pct'] = spy.get('pct')

        # QQQ intraday
        qqq = quotes.get('QQQ')
        if qqq:
            out['qqq'] = qqq.get('last')
            out['qqq_intraday_pct'] = qqq.get('pct')

        # Indicateurs techniques SPY/QQQ (momentum 1M/3M, SMA50/200, RSI14)
        try:
            out['technicals'] = self._compute_technicals(quotes)
        except Exception as e:
            logger.warning('collect_metrics: technicals indisponibles (%s)', e)

        # Drawdown du portefeuille (pondéré par la valeur de marché)
        total_mv = sum(abs(p.get('market_value') or 0) for p in positions) or 0.0
        pos_out, weighted = [], 0.0
        for p in positions:
            t = p.get('ticker')
            q = quotes.get(t)
            pct = q.get('pct') if q else None
            last = q.get('last') if q else None
            mv = abs(p.get('market_value') or 0)
            weight = (mv / total_mv) if total_mv else 0.0
            if pct is not None:
                weighted += weight * pct
            pos_out.append({
                'ticker': t,
                'pct': pct,                          # perf intraday
                'last': last,                         # cours actuel
                'market_value': p.get('market_value'),
                'weight': round(weight * 100, 1),
            })
        out['positions'] = pos_out
        if total_mv and pos_out:
            out['portfolio_intraday_pct'] = round(weighted, 2)

        return out

    def _compute_technicals(self, quotes) -> dict:
        """
        Calcule momentum 1M/3M, % vs SMA50/200 et RSI14 pour SPY et QQQ
        depuis l'historique Tiingo (cache DB). Retourne {} si données insuffisantes.
        """
        result = {}
        try:
            import pandas as pd
            import numpy as np
        except ImportError:
            return result

        for sym in ('SPY', 'QQQ'):
            try:
                df, _ = self.momentum._fetch_daily_tiingo(sym, 250)
                if df is None or df.empty or 'adjClose' in df.columns is False:
                    continue
                px = df['adjClose'].dropna().sort_index()
                if len(px) < 20:
                    continue

                n = len(px)
                p_last = float(px.iloc[-1])

                # Momentum 1M (21j) et 3M (63j)
                mom_1m = (p_last / float(px.iloc[max(0, n - 22)]) - 1) * 100 if n >= 22 else None
                mom_3m = (p_last / float(px.iloc[max(0, n - 64)]) - 1) * 100 if n >= 64 else None

                # SMA50 / SMA200
                sma50  = float(px.tail(50).mean())  if n >= 50  else None
                sma200 = float(px.tail(200).mean()) if n >= 200 else None
                vs_sma50  = (p_last / sma50  - 1) * 100 if sma50  else None
                vs_sma200 = (p_last / sma200 - 1) * 100 if sma200 else None

                # RSI 14
                rsi = None
                if n >= 15:
                    delta = px.diff().dropna().tail(14)
                    gains = delta.clip(lower=0).mean()
                    losses = (-delta.clip(upper=0)).mean()
                    if losses > 1e-9:
                        rs = gains / losses
                        rsi = round(float(100 - 100 / (1 + rs)), 1)

                result[sym] = {
                    'last': round(p_last, 2),
                    'mom_1m': round(mom_1m, 2) if mom_1m is not None else None,
                    'mom_3m': round(mom_3m, 2) if mom_3m is not None else None,
                    'vs_sma50':  round(vs_sma50,  2) if vs_sma50  is not None else None,
                    'vs_sma200': round(vs_sma200, 2) if vs_sma200 is not None else None,
                    'rsi14': rsi,
                    'sma50':  round(sma50,  2) if sma50  else None,
                    'sma200': round(sma200, 2) if sma200 else None,
                }
            except Exception as e:
                logger.debug('_compute_technicals %s: %s', sym, e)

        return result

    # ------------------------------------------------------------------
    # Évaluation des seuils
    # ------------------------------------------------------------------
    def evaluate(self, metrics, thresholds=None):
        """
        Compare les métriques aux seuils → liste de dépassements (breaches).

        Chaque breach : {event_type, ticker, severity, value, threshold, message}.
        La clé (event_type, ticker) identifie un épisode pour l'anti-spam côté app.
        """
        t = thresholds or self.get_thresholds()
        breaches = []

        vix = metrics.get('vix')
        if vix is not None:
            if vix >= t['vix_crit']:
                breaches.append(self._b('VIX_HIGH', None, 'critical', vix, t['vix_crit'],
                                        f"VIX à {vix:.1f} (panique, seuil {t['vix_crit']:.0f})"))
            elif vix >= t['vix_warn']:
                breaches.append(self._b('VIX_HIGH', None, 'warning', vix, t['vix_warn'],
                                        f"VIX à {vix:.1f} (peur, seuil {t['vix_warn']:.0f})"))

        vix_pct = metrics.get('vix_pct')
        if vix_pct is not None and vix_pct >= t['vix_spike_pct']:
            breaches.append(self._b('VIX_SPIKE', None, 'critical', vix_pct, t['vix_spike_pct'],
                                    f"Bond du VIX de +{vix_pct:.1f}% sur la séance"))

        spy = metrics.get('spy_intraday_pct')
        if spy is not None:
            if spy <= t['spy_dd_crit']:
                breaches.append(self._b('SPY_DRAWDOWN', None, 'critical', spy, t['spy_dd_crit'],
                                        f"S&P 500 en baisse de {spy:.1f}% (seuil {t['spy_dd_crit']:.0f}%)"))
            elif spy <= t['spy_dd_warn']:
                breaches.append(self._b('SPY_DRAWDOWN', None, 'warning', spy, t['spy_dd_warn'],
                                        f"S&P 500 en baisse de {spy:.1f}% (seuil {t['spy_dd_warn']:.0f}%)"))

        port = metrics.get('portfolio_intraday_pct')
        if port is not None:
            if port <= t['portfolio_dd_crit']:
                breaches.append(self._b('PORTFOLIO_DRAWDOWN', None, 'critical', port, t['portfolio_dd_crit'],
                                        f"Portefeuille en baisse de {port:.1f}% (seuil {t['portfolio_dd_crit']:.0f}%)"))
            elif port <= t['portfolio_dd_warn']:
                breaches.append(self._b('PORTFOLIO_DRAWDOWN', None, 'warning', port, t['portfolio_dd_warn'],
                                        f"Portefeuille en baisse de {port:.1f}% (seuil {t['portfolio_dd_warn']:.0f}%)"))

        for p in metrics.get('positions', []):
            pct = p.get('pct')
            if pct is not None and pct <= t['position_drop']:
                breaches.append(self._b('POSITION_DROP', p['ticker'], 'warning', pct, t['position_drop'],
                                        f"{p['ticker']} chute de {pct:.1f}% (seuil {t['position_drop']:.0f}%)"))

        return breaches

    @staticmethod
    def _b(event_type, ticker, severity, value, threshold, message):
        return {
            'event_type': event_type, 'ticker': ticker, 'severity': severity,
            'value': round(float(value), 2), 'threshold': round(float(threshold), 2),
            'message': message,
        }
