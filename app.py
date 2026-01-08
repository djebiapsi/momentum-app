# -*- coding: utf-8 -*-
"""
Application Flask - Momentum Strategy
=====================================
API REST pour l'application de stratégie momentum.
"""

import os
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from functools import wraps
from config import get_config
from models import (
    db, init_db, Settings, 
    PanelAction, RecommendationHistory, RecommendationDetail,
    ShortPanelAction, ShortRecommendationHistory, ShortRecommendationDetail
)
from momentum_service import MomentumService
from email_service import EmailService
from screener_service import ScreenerService
from short_screener_service import ShortScreenerService
from finviz_screener_service import FinvizScreenerService


def require_admin(f):
    """
    Décorateur pour protéger les routes admin.
    Vérifie le header X-Admin-Token ou refuse l'accès.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_password = app.config.get('ADMIN_PASSWORD')
        
        # Si pas de mot de passe configuré, accès libre (mode dev)
        if not admin_password:
            return f(*args, **kwargs)
        
        # Vérifier le token
        token = request.headers.get('X-Admin-Token', '')
        if token != admin_password:
            return jsonify({
                'error': 'Accès refusé - Authentification requise',
                'auth_required': True
            }), 401
        
        return f(*args, **kwargs)
    return decorated_function


# =============================================================================
# INITIALISATION DE L'APPLICATION
# =============================================================================

def create_app():
    """Factory pour créer l'application Flask"""
    
    app = Flask(__name__)
    
    # Charger la configuration
    config_class = get_config()
    app.config.from_object(config_class)
    
    # Activer CORS
    CORS(app)
    
    # Initialiser la base de données
    db.init_app(app)
    init_db(app, config_class.DEFAULT_PANEL)
    
    return app


app = create_app()

# Services (initialisés après app)
momentum_service = None
email_service = None
screener_service = None
short_screener_service = None
finviz_screener_service = None


def get_momentum_service():
    """Récupère ou crée le service momentum"""
    global momentum_service
    if momentum_service is None:
        api_key = app.config.get('TIINGO_API_KEY')
        if api_key:
            momentum_service = MomentumService(api_key)
    return momentum_service


def get_email_service():
    """Récupère ou crée le service email"""
    global email_service
    if email_service is None:
        email_service = EmailService(
            api_key=app.config.get('RESEND_API_KEY'),
            from_email=app.config.get('EMAIL_FROM'),
            to_email=app.config.get('EMAIL_TO')
        )
    return email_service


def get_screener_service():
    """Récupère ou crée le service de screening"""
    global screener_service
    if screener_service is None:
        api_key = app.config.get('TIINGO_API_KEY')
        if api_key:
            screener_service = ScreenerService(api_key)
    return screener_service


def get_short_screener_service():
    """Récupère ou crée le service de screening Short (via Finviz) - LEGACY"""
    global short_screener_service
    if short_screener_service is None:
        short_screener_service = ShortScreenerService()
    return short_screener_service


def get_finviz_screener_service():
    """Récupère ou crée le service Finviz unifié (Long & Short)"""
    global finviz_screener_service
    if finviz_screener_service is None:
        finviz_screener_service = FinvizScreenerService()
    return finviz_screener_service


# =============================================================================
# ROUTES - PAGES
# =============================================================================

@app.route('/')
def index():
    """Page principale de l'application"""
    return render_template('index.html')


# =============================================================================
# ROUTES - API SETTINGS
# =============================================================================

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Récupère les paramètres actuels"""
    nb_top = Settings.get('nb_top', app.config.get('DEFAULT_NB_TOP', 5))
    date_calcul = Settings.get('date_calcul', '')  # Vide = aujourd'hui
    email_to = app.config.get('EMAIL_TO', '')
    email_configured = get_email_service().is_configured()
    
    return jsonify({
        'nb_top': int(nb_top),
        'date_calcul': date_calcul,
        'email_to': email_to,
        'email_configured': email_configured,
        'api_configured': app.config.get('TIINGO_API_KEY') is not None
    })


@app.route('/api/settings', methods=['POST'])
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
    
    return jsonify({'success': True, 'message': 'Paramètres mis à jour'})


# =============================================================================
# ROUTES - API PANEL
# =============================================================================

@app.route('/api/panel', methods=['GET'])
def get_panel():
    """Récupère la liste des actions du panel"""
    actions = PanelAction.query.filter_by(is_active=True).all()
    return jsonify({
        'count': len(actions),
        'actions': [a.to_dict() for a in actions]
    })


@app.route('/api/panel', methods=['POST'])
@require_admin
def add_to_panel():
    """Ajoute une action au panel"""
    data = request.get_json()
    ticker = data.get('ticker', '').upper().strip()
    
    if not ticker:
        return jsonify({'error': 'Ticker requis'}), 400
    
    # Vérifier si déjà présent
    existing = PanelAction.query.filter_by(ticker=ticker).first()
    if existing:
        if existing.is_active:
            return jsonify({'error': f'{ticker} est déjà dans le panel'}), 400
        else:
            # Réactiver
            existing.is_active = True
            db.session.commit()
            return jsonify({'success': True, 'message': f'{ticker} réactivé', 'action': existing.to_dict()})
    
    # Valider le ticker via Tiingo
    service = get_momentum_service()
    if service:
        validation = service.valider_ticker(ticker)
        if not validation['valid']:
            return jsonify({'error': f'Ticker invalide: {validation["error"]}'}), 400
        name = validation['name']
    else:
        name = None
    
    # Ajouter
    action = PanelAction(ticker=ticker, name=name, strategy_type='long')
    db.session.add(action)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'{ticker} ajouté au panel',
        'action': action.to_dict()
    })


@app.route('/api/panel/<ticker>', methods=['DELETE'])
@require_admin
def remove_from_panel(ticker):
    """Retire une action du panel"""
    ticker = ticker.upper()
    action = PanelAction.query.filter_by(ticker=ticker).first()
    
    if not action:
        return jsonify({'error': f'{ticker} non trouvé'}), 404
    
    action.is_active = False
    db.session.commit()
    
    return jsonify({'success': True, 'message': f'{ticker} retiré du panel'})


# =============================================================================
# ROUTES - API MOMENTUM
# =============================================================================

@app.route('/api/calculate', methods=['POST'])
@require_admin
def calculate_momentum():
    """Lance le calcul du momentum et génère les recommandations"""
    
    service = get_momentum_service()
    if not service:
        return jsonify({'error': 'API Tiingo non configurée'}), 500
    
    # Récupérer les paramètres
    nb_top = int(Settings.get('nb_top', app.config.get('DEFAULT_NB_TOP', 5)))
    date_calcul = Settings.get('date_calcul', '')
    
    if not date_calcul:
        date_calcul = None  # Utiliser la date du jour
    
    # Récupérer le panel
    actions = PanelAction.query.filter_by(is_active=True).all()
    panel = [a.ticker for a in actions]
    
    if not panel:
        return jsonify({'error': 'Panel vide - ajoutez des actions'}), 400
    
    # Calculer le momentum
    resultats = service.analyser_panel(panel, date_calcul)
    
    if not resultats['success']:
        return jsonify({
            'error': 'Échec du calcul',
            'erreurs': resultats['erreurs']
        }), 500
    
    # Générer les recommandations
    recommandations = service.generer_recommandations(resultats, nb_top)
    
    # Sauvegarder dans l'historique
    history = RecommendationHistory(
        calculation_date=datetime.strptime(recommandations['date_calcul'], '%Y-%m-%d'),
        nb_top=nb_top
    )
    db.session.add(history)
    db.session.flush()  # Pour obtenir l'ID
    
    for r in recommandations['recommandations']:
        detail = RecommendationDetail(
            history_id=history.id,
            ticker=r['ticker'],
            momentum=r['momentum'],
            signal=r['signal'],
            allocation=r['allocation'],
            rank=r['rank']
        )
        db.session.add(detail)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'history_id': history.id,
        **recommandations
    })


@app.route('/api/calculate-and-notify', methods=['POST'])
@require_admin
def calculate_and_notify():
    """Lance le calcul et envoie une notification email"""
    
    # D'abord calculer
    service = get_momentum_service()
    if not service:
        return jsonify({'error': 'API Tiingo non configurée'}), 500
    
    nb_top = int(Settings.get('nb_top', app.config.get('DEFAULT_NB_TOP', 5)))
    date_calcul = Settings.get('date_calcul', '')
    
    if not date_calcul:
        date_calcul = None
    
    actions = PanelAction.query.filter_by(is_active=True).all()
    panel = [a.ticker for a in actions]
    
    if not panel:
        return jsonify({'error': 'Panel vide'}), 400
    
    resultats = service.analyser_panel(panel, date_calcul)
    
    if not resultats['success']:
        return jsonify({'error': 'Échec du calcul', 'erreurs': resultats['erreurs']}), 500
    
    recommandations = service.generer_recommandations(resultats, nb_top)
    
    # Sauvegarder
    history = RecommendationHistory(
        calculation_date=datetime.strptime(recommandations['date_calcul'], '%Y-%m-%d'),
        nb_top=nb_top
    )
    db.session.add(history)
    db.session.flush()
    
    for r in recommandations['recommandations']:
        detail = RecommendationDetail(
            history_id=history.id,
            ticker=r['ticker'],
            momentum=r['momentum'],
            signal=r['signal'],
            allocation=r['allocation'],
            rank=r['rank']
        )
        db.session.add(detail)
    
    db.session.commit()
    
    # Envoyer l'email
    email_svc = get_email_service()
    email_result = email_svc.envoyer_recommandations(recommandations)
    
    return jsonify({
        'success': True,
        'history_id': history.id,
        'email_sent': email_result['success'],
        'email_message': email_result['message'],
        **recommandations
    })


# =============================================================================
# ROUTES - API HISTORIQUE
# =============================================================================

@app.route('/api/history', methods=['GET'])
def get_history():
    """Récupère l'historique des recommandations"""
    limit = request.args.get('limit', 12, type=int)
    
    history = RecommendationHistory.query\
        .order_by(RecommendationHistory.created_at.desc())\
        .limit(limit)\
        .all()
    
    return jsonify({
        'count': len(history),
        'history': [h.to_dict() for h in history]
    })


@app.route('/api/history/<int:history_id>', methods=['GET'])
def get_history_detail(history_id):
    """Récupère les détails d'une recommandation passée"""
    history = RecommendationHistory.query.get_or_404(history_id)
    return jsonify(history.to_dict())


@app.route('/api/history/latest', methods=['GET'])
def get_latest():
    """Récupère la dernière recommandation"""
    history = RecommendationHistory.query\
        .order_by(RecommendationHistory.created_at.desc())\
        .first()
    
    if not history:
        return jsonify({'message': 'Aucune recommandation disponible'}), 404
    
    return jsonify(history.to_dict())


# =============================================================================
# ROUTES - API EMAIL
# =============================================================================

@app.route('/api/email/test', methods=['POST'])
@require_admin
def send_test_email():
    """Envoie un email de test"""
    email_svc = get_email_service()
    result = email_svc.envoyer_test()
    
    status_code = 200 if result['success'] else 500
    return jsonify(result), status_code


@app.route('/api/email/status', methods=['GET'])
def email_status():
    """Vérifie le statut de la configuration email"""
    email_svc = get_email_service()
    return jsonify({
        'configured': email_svc.is_configured(),
        'to_email': app.config.get('EMAIL_TO', '')
    })


# =============================================================================
# ROUTES - API AUTH (Mode Admin)
# =============================================================================

@app.route('/api/auth/check', methods=['GET'])
def check_auth():
    """
    Vérifie si le mot de passe admin est configuré et si l'utilisateur est authentifié.
    Utilisé au chargement de la page pour savoir quel mode afficher.
    """
    admin_password = app.config.get('ADMIN_PASSWORD')
    
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


@app.route('/api/auth/login', methods=['POST'])
def admin_login():
    """
    Vérifie le mot de passe admin.
    Retourne le token (= mot de passe) si correct, pour le stocker côté client.
    """
    admin_password = app.config.get('ADMIN_PASSWORD')
    
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
# ROUTES - API SCREENER
# =============================================================================

@app.route('/api/screener/generate', methods=['POST'])
@require_admin
def generate_panel():
    """
    Génère automatiquement un panel de 50 tickers basé sur les critères:
    - MarketCap >= 1B$
    - ADV >= 5M$
    - Score = log(MarketCap) × log(ADV)
    """
    screener = get_screener_service()
    if not screener:
        return jsonify({'error': 'API Tiingo non configurée'}), 500
    
    # Lancer le screening (peut prendre du temps)
    result = screener.screen_universe()
    
    if not result['success']:
        return jsonify({
            'success': False,
            'error': result['error'],
            'stats': result.get('stats', {})
        }), 500
    
    return jsonify({
        'success': True,
        'tickers': result['tickers'],
        'stats': result['stats']
    })


@app.route('/api/screener/apply', methods=['POST'])
@require_admin
def apply_generated_panel():
    """
    Applique les tickers générés au panel actuel.
    Remplace tout le panel existant par les nouveaux tickers.
    """
    data = request.get_json()
    tickers = data.get('tickers', [])
    
    if not tickers:
        return jsonify({'error': 'Aucun ticker fourni'}), 400
    
    # Désactiver tous les tickers actuels
    PanelAction.query.update({PanelAction.is_active: False})
    
    # Ajouter ou réactiver les nouveaux tickers
    added = 0
    for ticker_data in tickers:
        ticker = ticker_data.get('ticker', '').upper().strip()
        if not ticker:
            continue
        
        existing = PanelAction.query.filter_by(ticker=ticker).first()
        if existing:
            existing.is_active = True
        else:
            action = PanelAction(
                ticker=ticker,
                name=None,  # On pourrait stocker le nom si disponible
                strategy_type='long'
            )
            db.session.add(action)
        added += 1
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'{added} tickers ajoutés au panel',
        'count': added
    })


# =============================================================================
# ROUTES - API SCREENER FINVIZ (Long - 0 appel API Tiingo)
# =============================================================================

@app.route('/api/screener/finviz/generate', methods=['POST'])
@require_admin
def generate_panel_finviz():
    """
    Génère automatiquement un panel Long de 50 tickers via Finviz.
    Critères conformes à select_50_tickers.md:
    - MarketCap >= 1B$
    - ADV >= 5M$ (Price × Volume)
    - Score = log(MarketCap) × log(ADV)
    
    Avantage: 0 appel API Tiingo !
    """
    screener = get_finviz_screener_service()
    
    result = screener.screen_long()
    
    if not result['success']:
        return jsonify({
            'success': False,
            'error': result['error'],
            'stats': result.get('stats', {})
        }), 500
    
    return jsonify({
        'success': True,
        'tickers': result['tickers'],
        'stats': result['stats']
    })


# =============================================================================
# ROUTES - API SHORT PANEL
# =============================================================================

@app.route('/api/short/panel', methods=['GET'])
def get_short_panel():
    """Récupère la liste des actions du panel Short"""
    actions = ShortPanelAction.query.filter_by(is_active=True).all()
    return jsonify({
        'count': len(actions),
        'actions': [a.to_dict() for a in actions]
    })


@app.route('/api/short/panel', methods=['POST'])
@require_admin
def add_to_short_panel():
    """Ajoute une action au panel Short"""
    data = request.get_json()
    ticker = data.get('ticker', '').upper().strip()
    
    if not ticker:
        return jsonify({'error': 'Ticker requis'}), 400
    
    # Vérifier si déjà présent
    existing = ShortPanelAction.query.filter_by(ticker=ticker).first()
    if existing:
        if existing.is_active:
            return jsonify({'error': f'{ticker} est déjà dans le panel Short'}), 400
        else:
            # Réactiver
            existing.is_active = True
            db.session.commit()
            return jsonify({'success': True, 'message': f'{ticker} réactivé', 'action': existing.to_dict()})
    
    # Valider le ticker via Tiingo (si configuré)
    service = get_momentum_service()
    name = None
    if service:
        validation = service.valider_ticker(ticker)
        if not validation['valid']:
            return jsonify({'error': f'Ticker invalide: {validation["error"]}'}), 400
        name = validation['name']
    
    # Ajouter
    action = ShortPanelAction(ticker=ticker, name=name)
    db.session.add(action)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'{ticker} ajouté au panel Short',
        'action': action.to_dict()
    })


@app.route('/api/short/panel/<ticker>', methods=['DELETE'])
@require_admin
def remove_from_short_panel(ticker):
    """Retire une action du panel Short"""
    ticker = ticker.upper()
    action = ShortPanelAction.query.filter_by(ticker=ticker).first()
    
    if not action:
        return jsonify({'error': f'{ticker} non trouvé'}), 404
    
    action.is_active = False
    db.session.commit()
    
    return jsonify({'success': True, 'message': f'{ticker} retiré du panel Short'})


# =============================================================================
# ROUTES - API SHORT MOMENTUM
# =============================================================================

@app.route('/api/short/calculate', methods=['POST'])
@require_admin
def calculate_short_momentum():
    """Lance le calcul du momentum Short et génère les recommandations"""
    
    service = get_momentum_service()
    if not service:
        return jsonify({'error': 'API Tiingo non configurée'}), 500
    
    # Récupérer les paramètres Short
    nb_top = int(Settings.get('short_nb_top', app.config.get('DEFAULT_NB_TOP', 5)))
    date_calcul = Settings.get('short_date_calcul', '')
    
    if not date_calcul:
        date_calcul = None
    
    # Récupérer le panel Short
    actions = ShortPanelAction.query.filter_by(is_active=True).all()
    panel = [a.ticker for a in actions]
    
    if not panel:
        return jsonify({'error': 'Panel Short vide - ajoutez des actions'}), 400
    
    # Calculer le momentum (même méthode, mais on triera différemment)
    resultats = service.analyser_panel(panel, date_calcul)
    
    if not resultats['success']:
        return jsonify({
            'error': 'Échec du calcul',
            'erreurs': resultats['erreurs']
        }), 500
    
    # Générer les recommandations SHORT (inverser le tri)
    recommandations = generer_recommandations_short(resultats, nb_top)
    
    # Sauvegarder dans l'historique Short
    history = ShortRecommendationHistory(
        calculation_date=datetime.strptime(recommandations['date_calcul'], '%Y-%m-%d'),
        nb_top=nb_top
    )
    db.session.add(history)
    db.session.flush()
    
    for r in recommandations['recommandations']:
        detail = ShortRecommendationDetail(
            history_id=history.id,
            ticker=r['ticker'],
            momentum=r['momentum'],
            signal=r['signal'],
            allocation=r['allocation'],
            rank=r['rank']
        )
        db.session.add(detail)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'history_id': history.id,
        **recommandations
    })


def generer_recommandations_short(resultats_analyse, nb_top):
    """
    Génère les signaux Short (inverse du Long).
    Trie par momentum CROISSANT (les plus fortes baisses en premier).
    """
    if not resultats_analyse['success']:
        return {
            'date_calcul': resultats_analyse['date_calcul'],
            'nb_top': nb_top,
            'recommandations': [],
            'total_shorter': 0,
            'erreurs': resultats_analyse['erreurs']
        }
    
    resultats = resultats_analyse['resultats']
    
    # IMPORTANT: Trier par momentum CROISSANT (les plus bas en premier)
    resultats.sort(key=lambda x: x['momentum'], reverse=False)
    
    # Réattribuer les rangs après le nouveau tri
    for i, r in enumerate(resultats):
        r['rank'] = i + 1
    
    nb_actions = len(resultats)
    nb_selection = min(nb_top, nb_actions)
    
    # Allocation par action sélectionnée
    allocation_par_action = round(100.0 / nb_selection, 2) if nb_selection > 0 else 0
    
    recommandations = []
    
    for i, r in enumerate(resultats):
        if i < nb_selection:
            signal = "Shorter"  # Signal pour vendre à découvert
            allocation = allocation_par_action
        else:
            signal = "Couvrir"  # Signal pour ne pas shorter / couvrir position existante
            allocation = 0.0
        
        recommandations.append({
            'ticker': r['ticker'],
            'momentum': round(r['momentum'], 2),
            'signal': signal,
            'allocation': allocation,
            'rank': r['rank'],
            'details_mensuels': r.get('details_mensuels', [])
        })
    
    return {
        'date_calcul': resultats_analyse['date_calcul'],
        'nb_top': nb_top,
        'recommandations': recommandations,
        'total_shorter': nb_selection,
        'erreurs': resultats_analyse['erreurs']
    }


# =============================================================================
# ROUTES - API SHORT HISTORIQUE
# =============================================================================

@app.route('/api/short/history', methods=['GET'])
def get_short_history():
    """Récupère l'historique des recommandations Short"""
    limit = request.args.get('limit', 12, type=int)
    
    history = ShortRecommendationHistory.query\
        .order_by(ShortRecommendationHistory.created_at.desc())\
        .limit(limit)\
        .all()
    
    return jsonify({
        'count': len(history),
        'history': [h.to_dict() for h in history]
    })


@app.route('/api/short/history/<int:history_id>', methods=['GET'])
def get_short_history_detail(history_id):
    """Récupère les détails d'une recommandation Short passée"""
    history = ShortRecommendationHistory.query.get_or_404(history_id)
    return jsonify(history.to_dict())


@app.route('/api/short/history/latest', methods=['GET'])
def get_short_latest():
    """Récupère la dernière recommandation Short"""
    history = ShortRecommendationHistory.query\
        .order_by(ShortRecommendationHistory.created_at.desc())\
        .first()
    
    if not history:
        return jsonify({'message': 'Aucune recommandation Short disponible'}), 404
    
    return jsonify(history.to_dict())


# =============================================================================
# ROUTES - API SHORT SETTINGS
# =============================================================================

@app.route('/api/short/settings', methods=['GET'])
def get_short_settings():
    """Récupère les paramètres Short"""
    nb_top = Settings.get('short_nb_top', app.config.get('DEFAULT_NB_TOP', 5))
    date_calcul = Settings.get('short_date_calcul', '')
    
    return jsonify({
        'nb_top': int(nb_top),
        'date_calcul': date_calcul
    })


@app.route('/api/short/settings', methods=['POST'])
@require_admin
def update_short_settings():
    """Met à jour les paramètres Short"""
    data = request.get_json()
    
    if 'nb_top' in data:
        nb_top = int(data['nb_top'])
        if 1 <= nb_top <= 50:
            Settings.set('short_nb_top', nb_top)
        else:
            return jsonify({'error': 'nb_top doit être entre 1 et 50'}), 400
    
    if 'date_calcul' in data:
        date_calcul = data['date_calcul']
        if date_calcul:
            try:
                datetime.strptime(date_calcul, '%Y-%m-%d')
            except ValueError:
                return jsonify({'error': 'Format de date invalide (YYYY-MM-DD)'}), 400
        Settings.set('short_date_calcul', date_calcul)
    
    return jsonify({'success': True, 'message': 'Paramètres Short mis à jour'})


# =============================================================================
# ROUTES - API SHORT SCREENER (via Finviz)
# =============================================================================

@app.route('/api/short/screener/generate', methods=['POST'])
@require_admin
def generate_short_panel():
    """
    Génère automatiquement un panel Short de 50 tickers via Finviz.
    Critères: Market Cap >= 2B$, Volume >= 500K, Perf Year <= -20%
    """
    screener = get_finviz_screener_service()
    
    # Récupérer le seuil de performance (optionnel)
    data = request.get_json(silent=True) or {}
    min_perf = data.get('min_perf_year', -20)
    
    result = screener.screen_short(min_perf_year=min_perf)
    
    if not result['success']:
        return jsonify({
            'success': False,
            'error': result['error'],
            'stats': result.get('stats', {})
        }), 500
    
    return jsonify({
        'success': True,
        'tickers': result['tickers'],
        'stats': result['stats']
    })


@app.route('/api/short/screener/apply', methods=['POST'])
@require_admin
def apply_short_panel():
    """
    Applique les tickers générés au panel Short.
    Remplace tout le panel Short existant par les nouveaux tickers.
    """
    data = request.get_json()
    tickers = data.get('tickers', [])
    
    if not tickers:
        return jsonify({'error': 'Aucun ticker fourni'}), 400
    
    # Désactiver tous les tickers Short actuels
    ShortPanelAction.query.update({ShortPanelAction.is_active: False})
    
    # Ajouter ou réactiver les nouveaux tickers
    added = 0
    for ticker_data in tickers:
        ticker = ticker_data.get('ticker', '').upper().strip()
        if not ticker:
            continue
        
        existing = ShortPanelAction.query.filter_by(ticker=ticker).first()
        if existing:
            existing.is_active = True
            existing.name = ticker_data.get('company')
            existing.sector = ticker_data.get('sector')
            existing.perf_year = ticker_data.get('perf_year')
        else:
            action = ShortPanelAction(
                ticker=ticker,
                name=ticker_data.get('company'),
                sector=ticker_data.get('sector'),
                perf_year=ticker_data.get('perf_year')
            )
            db.session.add(action)
        added += 1
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'{added} tickers ajoutés au panel Short',
        'count': added
    })


# =============================================================================
# TÂCHE PLANIFIÉE - MISE À JOUR MENSUELLE
# =============================================================================

def job_mensuel():
    """
    Tâche exécutée le 1er de chaque mois.
    Calcule le momentum et envoie les recommandations par email.
    """
    with app.app_context():
        print(f"[{datetime.now()}] 🚀 Démarrage du calcul mensuel automatique...")
        
        service = get_momentum_service()
        if not service:
            print("❌ API Tiingo non configurée")
            return
        
        nb_top = int(Settings.get('nb_top', app.config.get('DEFAULT_NB_TOP', 5)))
        
        actions = PanelAction.query.filter_by(is_active=True).all()
        panel = [a.ticker for a in actions]
        
        if not panel:
            print("❌ Panel vide")
            return
        
        # Calculer
        resultats = service.analyser_panel(panel, None)
        
        if not resultats['success']:
            print(f"❌ Échec du calcul: {resultats['erreurs']}")
            return
        
        recommandations = service.generer_recommandations(resultats, nb_top)
        
        # Sauvegarder
        history = RecommendationHistory(
            calculation_date=datetime.strptime(recommandations['date_calcul'], '%Y-%m-%d'),
            nb_top=nb_top
        )
        db.session.add(history)
        db.session.flush()
        
        for r in recommandations['recommandations']:
            detail = RecommendationDetail(
                history_id=history.id,
                ticker=r['ticker'],
                momentum=r['momentum'],
                signal=r['signal'],
                allocation=r['allocation'],
                rank=r['rank']
            )
            db.session.add(detail)
        
        db.session.commit()
        print(f"✅ Recommandations sauvegardées (ID: {history.id})")
        
        # Envoyer email
        email_svc = get_email_service()
        if email_svc.is_configured():
            result = email_svc.envoyer_recommandations(recommandations)
            if result['success']:
                print(f"✅ Email envoyé: {result['message']}")
            else:
                print(f"❌ Erreur email: {result['message']}")
        else:
            print("⚠️ Service email non configuré")


# Initialiser le scheduler
scheduler = BackgroundScheduler()

# Planifier le job le 1er de chaque mois à 8h00 UTC
scheduler.add_job(
    job_mensuel,
    CronTrigger(day=1, hour=8, minute=0),
    id='monthly_momentum',
    name='Calcul mensuel du momentum',
    replace_existing=True
)

# Démarrer le scheduler (fonctionne avec gunicorn en production)
scheduler.start()
print("📅 Scheduler démarré - Mise à jour automatique le 1er de chaque mois à 8h00 UTC")


# =============================================================================
# POINT D'ENTRÉE
# =============================================================================

if __name__ == '__main__':
    # Lancer l'application en mode développement
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=app.config.get('DEBUG', False))

