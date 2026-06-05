# -*- coding: utf-8 -*-
"""Routes de collecte de prix historiques (yfinance : SP500 + Nasdaq-100)."""
from flask import Blueprint, jsonify, request, current_app

from auth import require_admin
from services import get_price_data_service

bp = Blueprint('prices', __name__)


@bp.route('/api/prices/collect', methods=['POST'])
@require_admin
def prices_collect():
    """
    Déclenche la collecte de prix en arrière-plan (bouton config).
    Body optionnel : {"full": true} pour forcer une recollecte complète de l'historique.
    """
    data = request.get_json(silent=True) or {}
    full = bool(data.get('full', False))
    svc = get_price_data_service()
    app_obj = current_app._get_current_object()

    started = svc.run_background(app_obj, full=full)
    if not started:
        return jsonify({'success': False, 'message': 'Collecte déjà en cours'}), 409
    return jsonify({'success': True,
                    'message': 'Collecte lancée en arrière-plan' + (' (complète)' if full else '')})


@bp.route('/api/prices/status', methods=['GET'])
def prices_status():
    """État de la collecte + couverture de la base (pour l'UI)."""
    svc = get_price_data_service()
    out = {'state': svc.get_state()}
    try:
        out['coverage'] = svc.coverage()
    except Exception as e:
        out['coverage'] = None
        out['coverage_error'] = str(e)
    return jsonify(out)
