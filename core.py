# -*- coding: utf-8 -*-
"""Logique métier partagée entre routes et jobs (extrait de app.py)."""
import json
from datetime import datetime
from flask import current_app
from models import (db, Settings, PanelAction, RecommendationHistory,
                    RecommendationDetail, MarketEvent)
from services import (ibkr_service, get_momentum_service, get_email_service,
                      get_market_monitor, get_news_service)


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
        else:  # nouvel épisode → créer + alerter une fois
            ev = MarketEvent(
                event_type=b['event_type'], ticker=b['ticker'], severity=b['severity'],
                threshold=b['threshold'], trigger_value=b['value'], peak_value=b['value'],
                message=b['message'], started_at=now, last_checked_at=now,
            )
            db.session.add(ev)
            db.session.flush()
            if configured:
                try:
                    email_svc.envoyer_alerte_marche(ev.to_dict())
                    ev.notified_open = True
                except Exception as e:
                    print(f"❌ Email alerte: {e}")
            opened.append(b)

    # Clôturer les évènements dont la condition n'est plus remplie
    closed = 0
    for key, ev in open_map.items():
        if key not in breach_keys:
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
            'opened': opened, 'closed': closed}


def build_briefing_payload(session):
    """Construit le payload du briefing (régime, VIX, positions, technicals, news)."""
    monitor = get_market_monitor()
    metrics = monitor.collect_metrics()

    # Perf intraday par position (depuis les quotes IBKR)
    intraday_map = {p['ticker']: p for p in (metrics.get('positions') or [])}

    stats, positions = None, []
    try:
        if ibkr_service.ensure_connected():
            s = ibkr_service.get_portfolio_stats()
            stats = {k: s.get(k) for k in ('total_value', 'total_pnl', 'return_pct', 'positions_count')}
            raw_positions = s.get('positions', [])
            # Enrichir chaque position avec la perf intraday et le cours live
            for p in raw_positions:
                t = p.get('ticker', '')
                intra = intraday_map.get(t, {})
                p['intraday_pct'] = intra.get('pct')    # % depuis clôture précédente
                p['last_price']   = intra.get('last')   # cours live
            positions = raw_positions
    except Exception as e:
        print(f"⚠️ Briefing: positions indisponibles ({e})")

    tickers = [p['ticker'] for p in positions if p.get('ticker')]
    news_items, news_summary = [], ''
    try:
        ns = get_news_service()
        news_items = ns.fetch_news(tickers)
        regime = (metrics.get('regime') or {}).get('regime', '?')
        ctx = (f"régime {regime}, VIX {metrics.get('vix')}, "
               f"SPY {metrics.get('spy_intraday_pct')}% intraday, "
               f"QQQ {metrics.get('qqq_intraday_pct')}% intraday")
        news_summary = ns.summarize(news_items, context=ctx, tickers=tickers)
    except Exception as e:
        print(f"⚠️ Briefing: news indisponibles ({e})")

    return {
        'session': session,
        'regime': metrics.get('regime'),
        'vix': metrics.get('vix'), 'vix_pct': metrics.get('vix_pct'),
        'spy': metrics.get('spy'), 'spy_intraday_pct': metrics.get('spy_intraday_pct'),
        'qqq': metrics.get('qqq'), 'qqq_intraday_pct': metrics.get('qqq_intraday_pct'),
        'portfolio_intraday_pct': metrics.get('portfolio_intraday_pct'),
        'technicals': metrics.get('technicals') or {},
        'stats': stats, 'positions': positions,
        'news_summary': news_summary, 'news_items': news_items,
    }


# =============================================================================
# ROUTES - API MARCHÉ (pulse, évènements, seuils, déclencheurs de test)
# =============================================================================

