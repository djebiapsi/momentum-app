# -*- coding: utf-8 -*-
"""Routes settings / email / auth / cache."""
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

bp = Blueprint('settings', __name__)


@bp.route('/api/settings', methods=['GET'])
def get_settings():
    """Récupère les paramètres actuels"""
    nb_top = Settings.get('nb_top', current_app.config.get('DEFAULT_NB_TOP', 5))
    date_calcul = Settings.get('date_calcul', '')  # Vide = aujourd'hui
    email_to = current_app.config.get('EMAIL_TO', '')
    email_configured = get_email_service().is_configured()

    vol_scaling = Settings.get('vol_scaling_enabled',
                               str(current_app.config.get('DEFAULT_VOL_SCALING', False)).lower())
    vol_target = Settings.get('vol_target', current_app.config.get('DEFAULT_VOL_TARGET', 12))
    max_exposure = Settings.get('max_exposure', current_app.config.get('DEFAULT_MAX_EXPOSURE', 250))
    portfolio_filter = Settings.get('portfolio_filter_enabled',
                                    str(current_app.config.get('DEFAULT_PORTFOLIO_FILTER', False)).lower())
    portfolio_vol_threshold = Settings.get('portfolio_vol_threshold',
                                           current_app.config.get('DEFAULT_PORTFOLIO_VOL_THRESHOLD', 20))

    return jsonify({
        'nb_top': int(nb_top),
        'date_calcul': date_calcul,
        'email_to': email_to,
        'email_configured': email_configured,
        'api_configured': current_app.config.get('TIINGO_API_KEY') is not None,
        'vol_scaling_enabled': str(vol_scaling).lower() == 'true',
        'vol_target': float(vol_target),
        'max_exposure': float(max_exposure),
        'portfolio_filter_enabled': str(portfolio_filter).lower() == 'true',
        'portfolio_vol_threshold': float(portfolio_vol_threshold)
    })


@bp.route('/api/settings', methods=['POST'])
@require_admin
def update_settings():
    """Met à jour les paramètres"""
    data = request.get_json()
    
    if 'nb_top' in data:
        nb_top = int(data['nb_top'])
        if 1 <= nb_top <= 50:
            Settings.set('nb_top', nb_top)
        else:
            return jsonify({'error': 'nb_top doit être entre 1 et 50'}), 400
    
    if 'date_calcul' in data:
        date_calcul = data['date_calcul']
        # Validation du format si non vide
        if date_calcul:
            try:
                datetime.strptime(date_calcul, '%Y-%m-%d')
            except ValueError:
                return jsonify({'error': 'Format de date invalide (YYYY-MM-DD)'}), 400
        Settings.set('date_calcul', date_calcul)

    if 'vol_scaling_enabled' in data:
        Settings.set('vol_scaling_enabled', 'true' if data['vol_scaling_enabled'] else 'false')

    if 'vol_target' in data:
        try:
            vol_target = float(data['vol_target'])
        except (TypeError, ValueError):
            return jsonify({'error': 'vol_target invalide'}), 400
        if 1 <= vol_target <= 100:
            Settings.set('vol_target', vol_target)
        else:
            return jsonify({'error': 'vol_target doit être entre 1 et 100'}), 400

    if 'max_exposure' in data:
        try:
            max_exposure = float(data['max_exposure'])
        except (TypeError, ValueError):
            return jsonify({'error': 'max_exposure invalide'}), 400
        if 100 <= max_exposure <= 500:
            Settings.set('max_exposure', max_exposure)
        else:
            return jsonify({'error': 'max_exposure doit être entre 100 et 500'}), 400

    if 'portfolio_filter_enabled' in data:
        Settings.set('portfolio_filter_enabled', 'true' if data['portfolio_filter_enabled'] else 'false')

    if 'portfolio_vol_threshold' in data:
        try:
            seuil = float(data['portfolio_vol_threshold'])
        except (TypeError, ValueError):
            return jsonify({'error': 'portfolio_vol_threshold invalide'}), 400
        if 5 <= seuil <= 100:
            Settings.set('portfolio_vol_threshold', seuil)
        else:
            return jsonify({'error': 'portfolio_vol_threshold doit être entre 5 et 100'}), 400

    return jsonify({'success': True, 'message': 'Paramètres mis à jour'})


# =============================================================================
# ROUTES - API PANEL
# =============================================================================

@bp.route('/api/email/test', methods=['POST'])
@require_admin
def send_test_email():
    """Envoie un email de test"""
    email_svc = get_email_service()
    result = email_svc.envoyer_test()
    
    status_code = 200 if result['success'] else 500
    return jsonify(result), status_code


@bp.route('/api/email/status', methods=['GET'])
def email_status():
    """Vérifie le statut de la configuration email"""
    email_svc = get_email_service()
    return jsonify({
        'configured': email_svc.is_configured(),
        'to_email': current_app.config.get('EMAIL_TO', '')
    })


# =============================================================================
# ROUTES - API AUTH (Mode Admin)
# =============================================================================

@bp.route('/api/auth/check', methods=['GET'])
def check_auth():
    """
    Vérifie si le mot de passe admin est configuré et si l'utilisateur est authentifié.
    Utilisé au chargement de la page pour savoir quel mode afficher.
    """
    admin_password = current_app.config.get('ADMIN_PASSWORD')
    
    # Si pas de mot de passe configuré, tout le monde a accès (mode dev)
    if not admin_password:
        return jsonify({
            'auth_required': False,
            'is_admin': True,
            'message': 'Pas de mot de passe configuré - accès complet'
        })
    
    # Vérifier le token dans le header
    token = request.headers.get('X-Admin-Token', '')
    is_admin = (token == admin_password)
    
    return jsonify({
        'auth_required': True,
        'is_admin': is_admin
    })


@bp.route('/api/auth/login', methods=['POST'])
def admin_login():
    """
    Vérifie le mot de passe admin.
    Retourne le token (= mot de passe) si correct, pour le stocker côté client.
    """
    admin_password = current_app.config.get('ADMIN_PASSWORD')
    
    if not admin_password:
        return jsonify({
            'success': True,
            'message': 'Pas de mot de passe requis'
        })
    
    data = request.get_json()
    password = data.get('password', '')
    
    if password == admin_password:
        return jsonify({
            'success': True,
            'token': password,  # Le client stockera ce token
            'message': 'Connexion réussie'
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Mot de passe incorrect'
        }), 401


# =============================================================================
# ROUTES - API CACHE
# =============================================================================

@bp.route('/api/cache/clear', methods=['POST'])
@require_admin
def clear_cache():
    """
    Vide le cache en mémoire des services (prix Tiingo, screener Finviz).
    Utile pour forcer un recalcul immédiat avec des données fraîches.
    """
    svc = get_momentum_service()
    if svc:
        svc._monthly_cache.invalidate()
        svc._daily_cache.invalidate()
        svc._ticker_cache.invalidate()

    screener = get_screener_service()
    if screener:
        screener._iex_cache.invalidate()

    finviz = get_finviz_screener_service()
    if finviz:
        finviz._screener_cache.invalidate()

    return jsonify({'success': True, 'message': 'Cache vidé - prochain calcul fera des appels API frais'})


# =============================================================================
# ROUTES - API SCREENER
# =============================================================================

