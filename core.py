# -*- coding: utf-8 -*-
"""Logique métier partagée entre routes et jobs (extrait de app.py)."""
import json
from datetime import datetime
from flask import current_app
from models import (db, Settings, PanelAction, RecommendationHistory,
                    RecommendationDetail, MarketEvent)
from services import (ibkr_service, get_momentum_service, get_email_service,
                      get_market_monitor, get_news_service, get_cached_positions)


def _get_vol_scaling_settings():
    """Lit les réglages de volatility scaling (Long) depuis Settings + fallback config."""
    vs = Settings.get('vol_scaling_enabled',
                      str(current_app.config.get('DEFAULT_VOL_SCALING', False)).lower())
    pf = Settings.get('portfolio_filter_enabled',
                      str(current_app.config.get('DEFAULT_PORTFOLIO_FILTER', False)).lower())
    return {
        'vol_scaling': str(vs).lower() == 'true',
        'vol_target_pct': float(Settings.get('vol_target', current_app.config.get('DEFAULT_VOL_TARGET', 12))),
        'max_exposure_pct': float(Settings.get('max_exposure', current_app.config.get('DEFAULT_MAX_EXPOSURE', 250))),
        'portfolio_filter': str(pf).lower() == 'true',
        'portfolio_vol_threshold_pct': float(Settings.get('portfolio_vol_threshold',
                                            current_app.config.get('DEFAULT_PORTFOLIO_VOL_THRESHOLD', 20))),
    }


def _run_long_calculation():
    """
    Logique commune : récupère le panel, calcule le momentum, sauvegarde l'historique.
    Retourne (history, recommandations) ou lève une ValueError/RuntimeError.
    """
    service = get_momentum_service()
    if not service:
        raise RuntimeError('API Tiingo non configurée')

    nb_top = int(Settings.get('nb_top', current_app.config.get('DEFAULT_NB_TOP', 5)))
    date_calcul = Settings.get('date_calcul', '') or None
    vs = _get_vol_scaling_settings()

    actions = PanelAction.query.filter_by(is_active=True).all()
    panel = [a.ticker for a in actions]

    if not panel:
        raise ValueError('Panel vide - ajoutez des actions')

    resultats = service.analyser_panel(panel, date_calcul)

    if not resultats['success']:
        erreurs = resultats.get('erreurs') or []
        if erreurs:
            detail = '; '.join(f"{e.get('ticker','?')}: {e.get('erreur','?')}" for e in erreurs[:5])
            raise RuntimeError(f"Calcul impossible — données manquantes : {detail}")
        raise RuntimeError("Calcul impossible — aucun résultat (panel vide ou toutes les sources de données indisponibles)")

    recommandations = service.generer_recommandations(resultats, nb_top, **vs)

    regime = recommandations.get('market_regime')
    history = RecommendationHistory(
        calculation_date=datetime.strptime(recommandations['date_calcul'], '%Y-%m-%d'),
        nb_top=nb_top,
        market_regime=json.dumps(regime) if regime else None,
    )
    db.session.add(history)
    db.session.flush()

    for r in recommandations['recommandations']:
        dm = r.get('details_mensuels')
        pr = r.get('perf_recent_1m')
        vol = r.get('vol_annualisee')
        db.session.add(RecommendationDetail(
            history_id=history.id,
            ticker=r['ticker'],
            momentum=float(r['momentum']),
            signal=r['signal'],
            allocation=float(r['allocation']),
            rank=int(r['rank']),
            perf_recent_1m=float(pr) if pr is not None else None,
            vol_annualisee=float(vol) if vol is not None else None,
            details_mensuels=json.dumps(dm) if dm else None,
        ))

    db.session.commit()
    return history, recommandations


def _momentum_csv_response(history):
    """Construit une réponse CSV téléchargeable à partir d'un RecommendationHistory."""
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['ticker', 'rang', 'momentum_pct', 'signal', 'allocation_pct',
                     'perf_1m_pct', 'vol_annualisee_pct'])
    details = sorted(history.details, key=lambda d: (d.rank if d.rank is not None else 999))
    for d in details:
        writer.writerow([
            d.ticker, d.rank, round(d.momentum, 2), d.signal, d.allocation,
            round(d.perf_recent_1m, 2) if d.perf_recent_1m is not None else '',
            round(d.vol_annualisee, 2) if d.vol_annualisee is not None else '',
        ])
    date_str = history.calculation_date.strftime('%Y%m%d')
    response = make_response(buf.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=momentum_{date_str}.csv'
    return response


def compute_and_save_momentum():
    """
    Calcule le momentum 12-1 à partir du panel actif et des variables enregistrées
    (nb_top, vol scaling), persiste un RecommendationHistory + ses détails, et
    retourne (recommandations_dict, history) — ou (None, None) en cas d'échec.

    Réutilisé par le cron mensuel ET les déclenchements manuels.
    """
    service = get_momentum_service()
    if not service:
        print("❌ Service momentum non configuré")
        return None, None

    nb_top = int(Settings.get('nb_top', current_app.config.get('DEFAULT_NB_TOP', 5)))
    actions = PanelAction.query.filter_by(is_active=True).all()
    panel = [a.ticker for a in actions]
    if not panel:
        print("❌ Panel vide")
        return None, None

    resultats = service.analyser_panel(panel, None)
    if not resultats['success']:
        print(f"❌ Échec du calcul: {resultats['erreurs']}")
        return None, None

    recommandations = service.generer_recommandations(resultats, nb_top,
                                                      **_get_vol_scaling_settings())

    regime = recommandations.get('market_regime')
    history = RecommendationHistory(
        calculation_date=datetime.strptime(recommandations['date_calcul'], '%Y-%m-%d'),
        nb_top=nb_top,
        market_regime=json.dumps(regime) if regime else None,
    )
    db.session.add(history)
    db.session.flush()

    for r in recommandations['recommandations']:
        dm = r.get('details_mensuels')
        pr = r.get('perf_recent_1m')
        vol = r.get('vol_annualisee')
        detail = RecommendationDetail(
            history_id=history.id,
            ticker=r['ticker'],
            momentum=float(r['momentum']),
            signal=r['signal'],
            allocation=float(r['allocation']),
            rank=int(r['rank']),
            perf_recent_1m=float(pr) if pr is not None else None,
            vol_annualisee=float(vol) if vol is not None else None,
            details_mensuels=json.dumps(dm) if dm else None,
        )
        db.session.add(detail)

    db.session.commit()
    print(f"✅ Recommandations sauvegardées (ID: {history.id})")
    return recommandations, history


def _more_extreme(value, current, event_type):
    """Détermine si `value` est plus extrême que `current` pour un type d'évènement."""
    if event_type in ('VIX_HIGH', 'VIX_SPIKE'):
        return value > current        # VIX : plus haut = pire
    return value < current            # drawdowns / chutes : plus négatif = pire


def run_market_monitor():
    """
    Collecte les métriques, évalue les seuils et gère le cycle de vie des
    MarketEvent (création / mise à jour / clôture) avec anti-spam :
      - 1 seul évènement ouvert par (type, ticker) → 1 seul email d'ouverture ;
      - clôture (ended_at) + email court quand la condition disparaît.
    Retourne un résumé exploitable par la route de test.
    """
    monitor = get_market_monitor()
    metrics = monitor.collect_metrics()
    ibkr_up = metrics.get('connected', False)
    # Données marché disponibles (yfinance ou IBKR) même si IBKR est down
    market_data_ok = bool(metrics.get('spy') or metrics.get('vix'))
    breaches = monitor.evaluate(metrics)
    now = datetime.utcnow()
    email_svc = get_email_service()
    configured = email_svc.is_configured()

    open_events = MarketEvent.query.filter(MarketEvent.ended_at.is_(None)).all()
    open_map = {(e.event_type, e.ticker): e for e in open_events}
    breach_keys, opened = set(), []

    for b in breaches:
        key = (b['event_type'], b['ticker'])
        breach_keys.add(key)
        ev = open_map.get(key)
        if ev:  # épisode déjà en cours → mise à jour silencieuse
            ev.last_checked_at = now
            if ev.peak_value is None or _more_extreme(b['value'], ev.peak_value, b['event_type']):
                ev.peak_value = b['value']
            if b['severity'] == 'critical' and ev.severity != 'critical':
                ev.severity = 'critical'
        else:  # nouvel épisode → push (pas d'email à l'ouverture)
            ev = MarketEvent(
                event_type=b['event_type'], ticker=b['ticker'], severity=b['severity'],
                threshold=b['threshold'], trigger_value=b['value'], peak_value=b['value'],
                message=b['message'], started_at=now, last_checked_at=now,
            )
            db.session.add(ev)
            db.session.flush()
            try:
                import push_service
                icon = '🚨' if b['severity'] == 'critical' else '⚠️'
                push_service.send_push_all(
                    title=f"{icon} Alerte marché",
                    body=b['message'],
                    url='/',
                    tag=f"market-{b['event_type']}-{b['ticker'] or 'global'}",
                )
                ev.notified_open = True
            except Exception as e:
                print(f"❌ Push alerte: {e}")
            opened.append(b)

    # Clôturer les évènements dont la condition n'est plus remplie.
    # - Alertes VIX/SPY : clôturables dès qu'on a des données marché (yfinance suffit).
    # - Alertes portefeuille/position : nécessitent IBKR (on ne peut pas vérifier sans quotes de positions).
    # Absence de données ≠ résolution → on ne clôture jamais sans source fiable.
    MARKET_ONLY_TYPES = {'VIX_HIGH', 'VIX_SPIKE', 'SPY_DRAWDOWN'}
    closed = 0
    for key, ev in open_map.items():
        if key in breach_keys:
            continue
        can_close = ibkr_up or (market_data_ok and ev.event_type in MARKET_ONLY_TYPES)
        if not can_close:
            ev.last_checked_at = now
            continue
        ev.ended_at = now
        ev.last_checked_at = now
        if configured and not ev.notified_close:
            try:
                email_svc.envoyer_alerte_resolue(ev.to_dict())
            except Exception as e:
                print(f"❌ Email résolu: {e}")
        ev.notified_close = True
        closed += 1

    db.session.commit()
    return {'metrics': metrics, 'breaches': breaches,
            'opened': opened, 'closed': closed,
            'ibkr_up': ibkr_up, 'market_data_ok': market_data_ok}


def _get_briefing_macro_yfinance() -> dict:
    """
    Récupère VIX / SPY / QQQ via yfinance — pas de timeout IBKR, fonctionne
    en pré/post-marché. Retourne les 2 dernières clôtures pour calculer la variation.
    """
    result = {}
    try:
        import yfinance as yf
        for sym, key in [('^VIX', 'vix'), ('SPY', 'spy'), ('QQQ', 'qqq')]:
            try:
                hist = yf.Ticker(sym).history(period='5d', interval='1d')
                if hist.empty:
                    continue
                last = float(hist['Close'].iloc[-1])
                prev = float(hist['Close'].iloc[-2]) if len(hist) >= 2 else None
                pct  = round((last - prev) / prev * 100, 2) if prev else None
                result[key]          = last
                result[f'{key}_pct'] = pct
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug('_get_briefing_macro_yfinance %s: %s', sym, e)
    except ImportError:
        pass
    return result


def build_briefing_payload(session):
    """
    Construit le payload du briefing.
    Sources intentionnellement séparées pour éviter les blocages IBKR :
    - Positions     : IBKR portfolio() direct (ne passe PAS par get_quotes)
    - VIX/SPY/QQQ   : yfinance (fiable pré/post marché, aucun timeout IBKR)
    - Intraday %    : yfinance par ticker de position
    - Régime/technicals : Tiingo via momentum_service (pas d'IBKR)
    - News          : RSS + LLM
    """
    import concurrent.futures as _cf

    # ── 1. Données macro via yfinance ─────────────────────────────────────────
    macro = _get_briefing_macro_yfinance()

    # ── 2. Positions IBKR — appel DIRECT et isolé (sans get_quotes) ──────────
    # portfolio() est un attribut en cache ib_async, pas une requête réseau —
    # mais il peut être bloqué si get_quotes() tourne en même temps sur le socket.
    # On l'isole dans un thread avec timeout court et cache ≤30 min en fallback.
    raw_positions = []
    ibkr_available = False
    try:
        if ibkr_service.ensure_connected():
            def _fetch_positions():
                return ibkr_service.get_portfolio_stats()
            with _cf.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_fetch_positions)
                try:
                    s = fut.result(timeout=12)
                    raw_positions = s.get('positions', [])
                    ibkr_available = True
                    # Mettre à jour le cache de positions
                    import time as _t
                    from services import _positions_cache, _positions_cache_ts, _POSITIONS_CACHE_TTL
                    import services as _svc
                    if raw_positions:
                        _svc._positions_cache = raw_positions
                        _svc._positions_cache_ts = _t.time()
                except _cf.TimeoutError:
                    print("⚠️ Briefing: positions IBKR timeout 12s → cache")
    except Exception as e:
        print(f"⚠️ Briefing: IBKR positions erreur ({e})")

    # Fallback cache si la requête live a échoué
    if not raw_positions:
        raw_positions = get_cached_positions()
        if raw_positions:
            print("⚠️ Briefing: utilise cache positions")

    # ── 3. Intraday % via yfinance pour les tickers du portefeuille ───────────
    tickers_pos = [p['ticker'] for p in raw_positions if p.get('ticker')]
    intraday_yf = {}
    if tickers_pos:
        try:
            import yfinance as yf
            hist_data = yf.download(
                tickers_pos, period='5d', interval='1d',
                group_by='ticker', auto_adjust=True, progress=False
            )
            for t in tickers_pos:
                try:
                    col = hist_data['Close'][t] if len(tickers_pos) > 1 else hist_data['Close']
                    col = col.dropna()
                    if len(col) >= 2:
                        last = float(col.iloc[-1])
                        prev = float(col.iloc[-2])
                        pct  = round((last - prev) / prev * 100, 2) if prev else None
                        intraday_yf[t] = {'last': last, 'pct': pct}
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ Briefing: intraday yfinance ({e})")

    # Enrichir les positions avec intraday
    stats = None
    positions = []
    if raw_positions:
        total_value = sum(p.get('market_value') or 0 for p in raw_positions)
        total_unrl  = sum(p.get('unrealized_pnl') or 0 for p in raw_positions)
        stats = {
            'total_value':     round(total_value, 2),
            'total_pnl':       round(total_unrl, 2),
            'return_pct':      None,
            'positions_count': len(raw_positions),
        }
        for p in raw_positions:
            t = p.get('ticker', '')
            yf_data = intraday_yf.get(t, {})
            p['intraday_pct'] = yf_data.get('pct')
            p['last_price']   = yf_data.get('last')
        positions = raw_positions

    # ── 4. Régime marché + technicals (Tiingo, aucun IBKR) ───────────────────
    regime     = None
    technicals = {}
    try:
        svc = get_momentum_service()
        if svc:
            regime = svc.get_market_regime()
    except Exception:
        pass
    try:
        monitor = get_market_monitor()
        # _compute_technicals utilise seulement les données Tiingo déjà cachées
        fake_quotes = {}  # pas de quotes IBKR, technicals depuis DB
        technicals = monitor._compute_technicals(fake_quotes)
    except Exception:
        pass

    # ── 5. Calcul portfolio intraday pondéré ─────────────────────────────────
    portfolio_intraday_pct = None
    if positions:
        total_mv = sum(abs(p.get('market_value') or 0) for p in positions)
        if total_mv:
            weighted = sum(
                (abs(p.get('market_value') or 0) / total_mv) * (p.get('intraday_pct') or 0)
                for p in positions
            )
            portfolio_intraday_pct = round(weighted, 2)

    # ── 6. News ───────────────────────────────────────────────────────────────
    tickers = [p['ticker'] for p in positions if p.get('ticker')]
    news_items, news_summary = [], ''
    try:
        ns = get_news_service()
        news_items = ns.fetch_news(tickers)
        reg_str = (regime or {}).get('regime', '?') if isinstance(regime, dict) else str(regime or '?')
        ctx = (f"régime {reg_str}, VIX {macro.get('vix')}, "
               f"SPY {macro.get('spy_pct')}% intraday, "
               f"QQQ {macro.get('qqq_pct')}% intraday")
        news_summary = ns.summarize(news_items, context=ctx, tickers=tickers)
    except Exception as e:
        print(f"⚠️ Briefing: news indisponibles ({e})")

    return {
        'session':               session,
        'ibkr_available':        ibkr_available,
        'regime':                regime,
        'vix':                   macro.get('vix'),
        'vix_pct':               macro.get('vix_pct'),
        'spy':                   macro.get('spy'),
        'spy_intraday_pct':      macro.get('spy_pct'),
        'qqq':                   macro.get('qqq'),
        'qqq_intraday_pct':      macro.get('qqq_pct'),
        'portfolio_intraday_pct': portfolio_intraday_pct,
        'technicals':            technicals,
        'stats':                 stats,
        'positions':             positions,
        'news_summary':          news_summary,
        'news_items':            news_items,
    }


# =============================================================================
# ROUTES - API MARCHÉ (pulse, évènements, seuils, déclencheurs de test)
# =============================================================================

