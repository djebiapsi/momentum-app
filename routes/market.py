# -*- coding: utf-8 -*-
"""Routes marché (pulse, évènements, seuils, briefing)."""
import json
from datetime import datetime
from flask import Blueprint, jsonify, request, make_response, current_app
from models import (db, Settings, PanelAction, RecommendationHistory,
                    RecommendationDetail, ShortPanelAction,
                    ShortRecommendationHistory, ShortRecommendationDetail,
                    OptionRecommendation, MarketEvent)
from auth import require_admin
from services import (ibkr_service, get_momentum_service, get_email_service,
                      get_news_service, get_market_monitor, get_screener_service,
                      get_short_screener_service, get_finviz_screener_service,
                      get_options_service)
from core import run_market_monitor, build_briefing_payload

bp = Blueprint('market', __name__)


@bp.route('/api/market/pulse', methods=['GET'])
def market_pulse():
    """État courant du marché et du portefeuille (pour le bandeau live)."""
    try:
        metrics = get_market_monitor().collect_metrics()
        return jsonify(metrics)
    except Exception as e:
        return jsonify({'error': str(e), 'connected': False}), 500


@bp.route('/api/market/events', methods=['GET'])
def market_events():
    """Liste des évènements de marché (status=open|all)."""
    status = request.args.get('status', 'all')
    q = MarketEvent.query
    if status == 'open':
        q = q.filter(MarketEvent.ended_at.is_(None))
    events = q.order_by(MarketEvent.started_at.desc()).limit(100).all()
    return jsonify({'count': len(events), 'events': [e.to_dict() for e in events]})


@bp.route('/api/market/thresholds', methods=['GET', 'POST'])
def market_thresholds():
    """Lit (GET) ou met à jour (POST, admin) les seuils d'alerte."""
    monitor = get_market_monitor()
    if request.method == 'GET':
        return jsonify({'thresholds': monitor.get_thresholds(),
                        'defaults': monitor.DEFAULT_THRESHOLDS})
    # POST → protégé admin
    decorated = require_admin(lambda: None)
    auth = decorated()
    if auth is not None:  # require_admin a renvoyé une 401
        return auth
    data = request.get_json(silent=True) or {}
    merged = monitor.save_thresholds(data.get('thresholds', data))
    return jsonify({'success': True, 'thresholds': merged})


@bp.route('/api/market/monitor/run', methods=['POST'])
@require_admin
def market_monitor_run():
    """Exécute le moniteur une fois (test manuel)."""
    try:
        result = run_market_monitor()
        return jsonify({
            'success': True,
            'breaches': result['breaches'],
            'opened': len(result['opened']),
            'closed': result['closed'],
            'metrics': result['metrics'],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/briefing/send', methods=['POST'])
@require_admin
def briefing_send():
    """Envoie un briefing à la demande (test). Body: {session: open|mid|close}."""
    data = request.get_json(silent=True) or {}
    session = data.get('session', 'open')
    try:
        payload = build_briefing_payload(session)
        email_svc = get_email_service()
        if not email_svc.is_configured():
            return jsonify({'success': False, 'message': 'Email non configuré'}), 400
        result = email_svc.envoyer_briefing(payload)
        return jsonify(result), (200 if result['success'] else 500)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/digest/send', methods=['POST'])
@require_admin
def digest_send():
    """
    Envoie le digest d'actualités immédiatement (bouton manuel).
    Body optionnel : { recipients: ['a@b.com', ...] }
    Par défaut envoie à la liste DIGEST_RECIPIENTS des jobs.
    """
    from jobs import DIGEST_RECIPIENTS
    data = request.get_json(silent=True) or {}
    recipients = data.get('recipients') or DIGEST_RECIPIENTS

    try:
        news_svc = get_news_service()
        current_app.logger.info('digest_send: récupération des articles…')
        items = news_svc.fetch_digest_news(max_per_feed=5)
        if not items:
            return jsonify({'success': False,
                            'message': 'Aucun article récupéré (vérifiez la connexion réseau)'}), 502

        current_app.logger.info('digest_send: %d articles, génération du résumé…', len(items))
        summary = news_svc.summarize_digest(items)

        email_svc = get_email_service()
        if not email_svc.is_configured():
            return jsonify({'success': False, 'message': 'Service email non configuré'}), 400

        result = email_svc.envoyer_digest_actualites(summary, items, recipients)
        return jsonify({**result, 'articles': len(items), 'recipients': recipients}), \
               (200 if result['success'] else 500)
    except Exception as e:
        current_app.logger.exception('digest_send: erreur')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/digest/tech/send', methods=['POST'])
@require_admin
def digest_tech_send():
    """Envoie le digest Tech & IA immédiatement (bouton manuel)."""
    from jobs import TECH_DIGEST_RECIPIENT
    data       = request.get_json(silent=True) or {}
    recipients = data.get('recipients') or TECH_DIGEST_RECIPIENT

    try:
        news_svc = get_news_service()
        current_app.logger.info('digest_tech_send: récupération des articles…')
        items = news_svc.fetch_tech_digest_news(max_per_feed=3)
        if not items:
            return jsonify({'success': False,
                            'message': 'Aucun article tech récupéré (vérifiez la connexion réseau)'}), 502

        current_app.logger.info('digest_tech_send: %d articles, génération du résumé…', len(items))
        summary = news_svc.summarize_tech_digest(items)

        email_svc = get_email_service()
        if not email_svc.is_configured():
            return jsonify({'success': False, 'message': 'Service email non configuré'}), 400

        result = email_svc.envoyer_digest_tech(summary, items, recipients)
        return jsonify({**result, 'articles': len(items), 'recipients': recipients}), \
               (200 if result['success'] else 500)
    except Exception as e:
        current_app.logger.exception('digest_tech_send: erreur')
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# TÂCHES PLANIFIÉES
# =============================================================================

