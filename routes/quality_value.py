# -*- coding: utf-8 -*-
"""Routes de la stratégie long Quality-Value (fondamentale, US/EU/transversal)."""
import json
from flask import Blueprint, jsonify, request
from models import Settings
from auth import require_admin
from core import compute_and_save_qv, get_qv_market, QV_DEFAULTS
from services import get_fundamental_screen_service

bp = Blueprint('quality_value', __name__)

VALID_MARKETS = ('us', 'eu', 'all')


@bp.route('/api/qv/config', methods=['GET'])
def qv_config():
    """Config courante de la stratégie QV (marché + paramètres)."""
    return jsonify({
        'market': get_qv_market(),
        'quality_weight': float(Settings.get('qv_quality_weight', QV_DEFAULTS['quality_weight'])),
        'top_n': int(Settings.get('qv_top_n', QV_DEFAULTS['top_n'])),
        'max_per_sector': int(Settings.get('qv_max_per_sector', QV_DEFAULTS['max_per_sector'])),
        'markets': VALID_MARKETS,
    })


@bp.route('/api/qv/config', methods=['POST'])
@require_admin
def set_qv_config():
    """Modifie le marché (us/eu/all) et/ou les paramètres de la stratégie QV."""
    data = request.get_json(silent=True) or {}
    if 'market' in data:
        m = str(data['market']).lower()
        if m not in VALID_MARKETS:
            return jsonify({'success': False, 'error': f'market invalide ({m})'}), 400
        Settings.set('qv_market', m)
    for key, cast in (('quality_weight', float), ('top_n', int), ('max_per_sector', int)):
        if key in data:
            try:
                Settings.set(f'qv_{key}', cast(data[key]))
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': f'{key} invalide'}), 400
    return jsonify({'success': True, 'market': get_qv_market()})


@bp.route('/api/qv/portfolio', methods=['GET'])
def qv_portfolio():
    """
    Dernier portefeuille QV calculé (depuis Settings). ?market=us|eu|all pour une
    région précise ; sinon le marché courant.
    """
    market = (request.args.get('market') or get_qv_market()).lower()
    raw = Settings.get(f'qv_latest_portfolio_{market}') or Settings.get('qv_latest_portfolio')
    if not raw:
        return jsonify({'success': False, 'error': 'Aucun portefeuille calculé — '
                        'lancer /api/qv/run'}), 404
    try:
        return jsonify({'success': True, **json.loads(raw)})
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Portefeuille illisible'}), 500


@bp.route('/api/qv/run', methods=['POST'])
@require_admin
def qv_run():
    """
    Déclenche le calcul du portefeuille QV maintenant. Body optionnel {market}.
    Calcule à la volée (live) — ne dépend pas du cron semestriel.
    """
    data = request.get_json(silent=True) or {}
    market = (data.get('market') or get_qv_market()).lower()
    if market not in VALID_MARKETS:
        return jsonify({'success': False, 'error': f'market invalide ({market})'}), 400
    result = compute_and_save_qv(market)
    if not result or not result.get('success'):
        err = (result or {}).get('error', 'échec du calcul')
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({
        'success': True, 'market': market,
        'eligible': result.get('eligible'),
        'sector_breakdown': result.get('sector_breakdown'),
        'holdings': result.get('holdings'),
    })


@bp.route('/api/qv/screen', methods=['GET'])
def qv_screen():
    """Aperçu du classement complet (sans persistance). ?market=&quality_weight=."""
    market = (request.args.get('market') or get_qv_market()).lower()
    qw = float(request.args.get('quality_weight',
                                Settings.get('qv_quality_weight', QV_DEFAULTS['quality_weight'])))
    top_n = int(request.args.get('top_n', QV_DEFAULTS['top_n']))
    res = get_fundamental_screen_service().build_portfolio(
        top_n=top_n, quality_weight=qw, market=market,
        max_per_sector=int(Settings.get('qv_max_per_sector', QV_DEFAULTS['max_per_sector'])))
    status = 200 if res.get('success') else 400
    return jsonify(res), status
