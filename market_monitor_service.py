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

    # Circuit breaker quotes IBKR : après N échecs consécutifs, bypass IBKR
    # pendant BYPASS_TICKS ticks (yfinance direct). Se reset à 0 sur succès.
    _CIRCUIT_OPEN_AFTER = 3
    _CIRCUIT_BYPASS_TICKS = 5

    def __init__(self, ibkr_service, momentum_service):
        self.ibkr = ibkr_service
        self.momentum = momentum_service
        self._ibkr_quotes_fail_streak = 0   # échecs consécutifs de get_quotes
        self._ibkr_quotes_bypass_left = 0   # ticks restants en bypass

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
            'using_yfinance_fallback': False,  # True si quotes positions via yfinance (pas IBKR)
        }

        # Régime BULL/BEAR (source momentum, cache journalier — pas d'appel lourd)
        try:
            out['regime'] = self.momentum.get_market_regime()
        except Exception as e:
            logger.warning('collect_metrics: régime indisponible (%s)', e)

        ibkr_connected = self.ibkr.ensure_connected()
        out['connected'] = ibkr_connected
        if not ibkr_connected:
            last_err = getattr(self.ibkr, '_last_error', None) or 'IBKR non connecté'
            out['error'] = last_err
            logger.warning('collect_metrics: IBKR non connecté — %s (port %s)',
                           last_err, getattr(self.ibkr, 'port', '?'))

        # Positions du portefeuille (IBKR uniquement — aucune source alternative fiable)
        positions = []
        if ibkr_connected:
            try:
                stats = self.ibkr.get_portfolio_stats()
                positions = stats.get('positions', [])
            except Exception as e:
                logger.warning('collect_metrics: positions indisponibles (%s)', e)

        port_tickers = [p['ticker'] for p in positions if p.get('ticker')]
        quote_tickers = list(dict.fromkeys(['SPY', 'QQQ'] + port_tickers))

        quotes = {}
        ibkr_quotes_ok = False
        if ibkr_connected:
            if self._ibkr_quotes_bypass_left > 0:
                # Circuit ouvert : on bypasse IBKR quotes sans même essayer
                self._ibkr_quotes_bypass_left -= 1
                logger.info('collect_metrics: circuit breaker actif (%d ticks restants) → yfinance direct',
                            self._ibkr_quotes_bypass_left)
            else:
                try:
                    quotes = self.ibkr.get_quotes(quote_tickers, include_vix=True)
                    ibkr_quotes_ok = bool(quotes.get('SPY') or quotes.get('VIX'))
                    if ibkr_quotes_ok:
                        self._ibkr_quotes_fail_streak = 0  # succès → reset streak
                    else:
                        self._ibkr_quotes_fail_streak += 1
                except Exception as e:
                    self._ibkr_quotes_fail_streak += 1
                    logger.warning('collect_metrics: IBKR quotes indisponibles (%s) → yfinance', e)

                if self._ibkr_quotes_fail_streak >= self._CIRCUIT_OPEN_AFTER:
                    self._ibkr_quotes_bypass_left = self._CIRCUIT_BYPASS_TICKS
                    self._ibkr_quotes_fail_streak = 0
                    logger.warning('collect_metrics: circuit breaker ouvert après %d échecs '
                                   '— %d prochains ticks via yfinance uniquement',
                                   self._CIRCUIT_OPEN_AFTER, self._CIRCUIT_BYPASS_TICKS)

        if not ibkr_quotes_ok:
            out['using_yfinance_fallback'] = True

        # Fallback yfinance pour SPY / QQQ / VIX — actif même si IBKR est down
        if not ibkr_quotes_ok:
            yf_data = self._yfinance_quotes(['SPY', 'QQQ', '^VIX'])
            for sym, mapped in [('SPY', 'SPY'), ('QQQ', 'QQQ'), ('^VIX', 'VIX')]:
                if mapped not in quotes and sym in yf_data:
                    quotes[mapped] = yf_data[sym]
            if yf_data:
                logger.info('collect_metrics: SPY/QQQ/VIX via yfinance (%s)',
                            'IBKR down' if not ibkr_connected else 'quotes IBKR vides')
            elif not quotes:
                logger.warning('collect_metrics: aucune source de quotes disponible')

        # Fallback yfinance pour les positions dont IBKR n'a pas renvoyé de quote
        missing_pos = [t for t in port_tickers if t not in quotes]
        if missing_pos:
            yf_pos = self._yfinance_quotes(missing_pos)
            for sym, data in yf_pos.items():
                if sym not in quotes:
                    quotes[sym] = data
            if yf_pos:
                logger.info('collect_metrics: %d position(s) via yfinance fallback', len(yf_pos))

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

    @staticmethod
    def _yfinance_quotes(symbols: list) -> dict:
        """
        Fallback yfinance : récupère last / prev_close / pct (intraday réel) pour une
        liste de symboles. Utilisé quand IBKR est down ou renvoie des quotes vides.

        Stratégie :
        - fast_info (léger, pas de DL complet) pour last_price et previous_close
        - Fallback history intraday 5m pour last si fast_info indisponible
        - Fallback history daily pour prev_close si fast_info indisponible
        """
        result = {}
        try:
            import yfinance as yf
            for sym in symbols:
                try:
                    ticker = yf.Ticker(sym)
                    last = None
                    prev = None

                    # Tentative fast_info (la plus légère, données temps réel/différé)
                    try:
                        fi = ticker.fast_info
                        lp = fi.last_price
                        pc = fi.previous_close
                        if lp and float(lp) > 0:
                            last = float(lp)
                        if pc and float(pc) > 0:
                            prev = float(pc)
                    except Exception:
                        pass

                    # Fallback : historique intraday 5 min pour le prix courant
                    if last is None:
                        try:
                            intra = ticker.history(period='1d', interval='5m')
                            if not intra.empty:
                                last = float(intra['Close'].iloc[-1])
                        except Exception:
                            pass

                    # Fallback : historique journalier pour prev_close
                    # Pendant la séance daily[-1] = clôture d'hier (barre du jour non fermée)
                    if prev is None:
                        try:
                            daily = ticker.history(period='5d', interval='1d')
                            if not daily.empty:
                                prev = float(daily['Close'].iloc[-1])
                        except Exception:
                            pass

                    if last is None:
                        continue

                    pct = round((last - prev) / prev * 100, 2) if prev and prev > 0 else None
                    result[sym] = {'last': last, 'prev_close': prev, 'pct': pct, 'source': 'yfinance'}
                except Exception as e:
                    logger.debug('_yfinance_quotes %s: %s', sym, e)
        except ImportError:
            pass
        return result

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
        if spy is not None and abs(spy) < 80:
            if spy <= t['spy_dd_crit']:
                breaches.append(self._b('SPY_DRAWDOWN', None, 'critical', spy, t['spy_dd_crit'],
                                        f"S&P 500 en baisse de {spy:.1f}% (seuil {t['spy_dd_crit']:.0f}%)"))
            elif spy <= t['spy_dd_warn']:
                breaches.append(self._b('SPY_DRAWDOWN', None, 'warning', spy, t['spy_dd_warn'],
                                        f"S&P 500 en baisse de {spy:.1f}% (seuil {t['spy_dd_warn']:.0f}%)"))

        port = metrics.get('portfolio_intraday_pct')
        if port is not None and abs(port) < 80:
            if port <= t['portfolio_dd_crit']:
                breaches.append(self._b('PORTFOLIO_DRAWDOWN', None, 'critical', port, t['portfolio_dd_crit'],
                                        f"Portefeuille en baisse de {port:.1f}% (seuil {t['portfolio_dd_crit']:.0f}%)"))
            elif port <= t['portfolio_dd_warn']:
                breaches.append(self._b('PORTFOLIO_DRAWDOWN', None, 'warning', port, t['portfolio_dd_warn'],
                                        f"Portefeuille en baisse de {port:.1f}% (seuil {t['portfolio_dd_warn']:.0f}%)"))

        for p in metrics.get('positions', []):
            pct = p.get('pct')
            # Ignorer les variations impossibles en séance (signe d'une coupure IBKR)
            if pct is not None and abs(pct) < 80 and pct <= t['position_drop']:
                breaches.append(self._b('POSITION_DROP', p['ticker'], 'warning', pct, t['position_drop'],
                                        f"{p['ticker']} chute de {pct:.1f}% (seuil {t['position_drop']:.0f}%)"))

        return breaches

    def _is_valid_intraday_pct(self, pct) -> bool:
        """Retourne False si la variation est physiquement impossible en séance (~±80%)."""
        return pct is not None and abs(pct) < 80

    @staticmethod
    def _b(event_type, ticker, severity, value, threshold, message):
        return {
            'event_type': event_type, 'ticker': ticker, 'severity': severity,
            'value': round(float(value), 2), 'threshold': round(float(threshold), 2),
            'message': message,
        }
