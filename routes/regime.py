# -*- coding: utf-8 -*-
"""Routes du régime de marché (SPY + QQQ, 4 états) : état live, journal, contrôle manuel."""
from flask import Blueprint, jsonify, request

from auth import require_admin
from models import Settings

bp = Blueprint('regime', __name__)


@bp.route('/api/regime', methods=['GET'])
def get_regime():
    """État live des deux indices, exposition des poches, KPI de suivi et journal."""
    from regime_service import live_regime
    from core import regime_journal
    try:
        reg = live_regime()
    except Exception as e:
        return jsonify({'error': f'Régime indisponible : {e}'}), 503
    reg['journal'] = regime_journal(limit=int(request.args.get('journal', 20)))
    return jsonify(reg)


@bp.route('/api/regime/check', methods=['POST'])
@require_admin
def check_regime():
    """Lance le contrôle quotidien maintenant (mêmes alertes que le cron 18h10 ET)."""
    from core import check_regime_and_alert
    data = request.get_json(silent=True) or {}
    res = check_regime_and_alert(notify=bool(data.get('notify', True)))
    reg = res.get('regime') or {}
    return jsonify({
        'success': not res.get('error'),
        'error': res.get('error'),
        'status': reg.get('status'),
        'initialized': res.get('initialized', False),
        'events': [{k: e.get(k) for k in ('kind', 'severity', 'title', 'body', 'orders')}
                   for e in res.get('events', [])],
    })


@bp.route('/api/regime/settings', methods=['GET'])
def regime_settings():
    from regime_service import get_regime_settings
    return jsonify(get_regime_settings())


@bp.route('/api/regime/settings', methods=['POST'])
@require_admin
def update_regime_settings():
    """Met à jour la portée, la vitesse, l'exposition BULL et les alertes du régime."""
    from regime_service import SCOPES, MODES, get_regime_settings
    data = request.get_json(silent=True) or {}
    if 'regime_scope' in data:
        if data['regime_scope'] not in SCOPES:
            return jsonify({'error': f'regime_scope : {", ".join(SCOPES)}'}), 400
        Settings.set('regime_scope', data['regime_scope'])
    if 'regime_mode' in data:
        if data['regime_mode'] not in MODES:
            return jsonify({'error': f'regime_mode : {", ".join(MODES)}'}), 400
        Settings.set('regime_mode', data['regime_mode'])
    if 'regime_bull_exposure' in data:
        try:
            v = float(data['regime_bull_exposure'])
        except (TypeError, ValueError):
            return jsonify({'error': 'regime_bull_exposure invalide'}), 400
        if not 1.0 <= v <= 1.5:
            return jsonify({'error': 'regime_bull_exposure entre 1,0 et 1,5'}), 400
        Settings.set('regime_bull_exposure', v)
    if 'regime_alerts' in data:
        Settings.set('regime_alerts', 'true' if data['regime_alerts'] else 'false')
    return jsonify({'success': True, **get_regime_settings()})
