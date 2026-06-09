# -*- coding: utf-8 -*-
"""Endpoints Web Push : abonnement, désinscription, clé VAPID publique, test."""
from flask import Blueprint, jsonify, request
from auth import require_admin
from models import db, PushSubscription
import push_service

bp = Blueprint('push', __name__)


@bp.route('/api/push/vapid-public-key', methods=['GET'])
def push_vapid_key():
    """Retourne la clé publique VAPID pour le `applicationServerKey` du navigateur."""
    key = push_service.get_vapid_public_key()
    if not key:
        return jsonify({'error': 'Clés VAPID non disponibles'}), 503
    return jsonify({'publicKey': key})


@bp.route('/api/push/subscribe', methods=['POST'])
@require_admin
def push_subscribe():
    """Enregistre ou met à jour un abonnement push (envoyé par le SW après permission)."""
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint', '').strip()
    keys     = data.get('keys') or {}
    p256dh   = keys.get('p256dh', '').strip()
    auth     = keys.get('auth', '').strip()
    label    = (data.get('label') or '').strip()[:100]

    if not endpoint or not p256dh or not auth:
        return jsonify({'success': False, 'error': 'endpoint, p256dh et auth requis'}), 400

    existing = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if existing:
        existing.p256dh = p256dh
        existing.auth   = auth
        if label:
            existing.label = label
        db.session.commit()
        return jsonify({'success': True, 'message': 'Abonnement mis à jour'})

    sub = PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth, label=label or None)
    db.session.add(sub)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Abonnement enregistré'})


@bp.route('/api/push/unsubscribe', methods=['POST'])
@require_admin
def push_unsubscribe():
    """Supprime un abonnement push."""
    data     = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint', '').strip()
    if not endpoint:
        return jsonify({'success': False, 'error': 'endpoint requis'}), 400
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub:
        db.session.delete(sub)
        db.session.commit()
    return jsonify({'success': True})


@bp.route('/api/push/status', methods=['GET'])
def push_status():
    """Statut push : clé publique + nombre d'abonnés."""
    key   = push_service.get_vapid_public_key()
    count = PushSubscription.query.count()
    return jsonify({'configured': bool(key), 'public_key': key, 'subscribers': count})


@bp.route('/api/push/test', methods=['POST'])
@require_admin
def push_test():
    """Envoie une notification de test à tous les abonnés."""
    result = push_service.send_push_all(
        title='🧪 Test Momentum App',
        body='Les notifications push fonctionnent correctement.',
        url='/',
        tag='test',
    )
    return jsonify({'success': True, **result})
