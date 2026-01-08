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
from models import db, init_db, Settings, PanelAction, RecommendationHistory, RecommendationDetail
from momentum_service import MomentumService
from email_service import EmailService
from screener_service import ScreenerService


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
    strategy_type = request.args.get('strategy_type', 'long')
    actions = PanelAction.query.filter_by(is_active=True, strategy_type=strategy_type).all()
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
    strategy_type = data.get('strategy_type', 'long')
    
    if not ticker:
        return jsonify({'error': 'Ticker requis'}), 400
    
    # Vérifier si déjà présent pour cette stratégie
    existing = PanelAction.query.filter_by(ticker=ticker, strategy_type=strategy_type).first()
    if existing:
        if existing.is_active:
            return jsonify({'error': f'{ticker} est déjà dans le panel {strategy_type}'}), 400
        else:
            # Réactiver
            existing.is_active = True
            db.session.commit()
            return jsonify({'success': True, 'message': f'{ticker} réactivé dans {strategy_type}', 'action': existing.to_dict()})
    
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
    action = PanelAction(ticker=ticker, name=name, strategy_type=strategy_type)
    db.session.add(action)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'{ticker} ajouté au panel {strategy_type}',
        'action': action.to_dict()
    })


@app.route('/api/panel/<ticker>', methods=['DELETE'])
@require_admin
def remove_from_panel(ticker):
    """Retire une action du panel"""
    strategy_type = request.args.get('strategy_type', 'long')
    ticker = ticker.upper()
    action = PanelAction.query.filter_by(ticker=ticker, strategy_type=strategy_type).first()
    
    if not action:
        return jsonify({'error': f'{ticker} non trouvé dans {strategy_type}'}), 404
    
    action.is_active = False
    db.session.commit()
    
    return jsonify({'success': True, 'message': f'{ticker} retiré du panel {strategy_type}'})


# =============================================================================
# ROUTES - API MOMENTUM
# =============================================================================

@app.route('/api/calculate', methods=['POST'])
@require_admin
def calculate_momentum():
    """Lance le calcul du momentum et génère les recommandations"""
    data = request.get_json() or {}
    strategy_type = data.get('strategy_type', 'long')
    
    service = get_momentum_service()
    if not service:
        return jsonify({'error': 'API Tiingo non configurée'}), 500
    
    # Récupérer les paramètres
    nb_top = int(Settings.get('nb_top', app.config.get('DEFAULT_NB_TOP', 5)))
    date_calcul = Settings.get('date_calcul', '')
    
    if not date_calcul:
        date_calcul = None  # Utiliser la date du jour
    
    # Récupérer le panel
    actions = PanelAction.query.filter_by(is_active=True, strategy_type=strategy_type).all()
    panel = [a.ticker for a in actions]
    
    if not panel:
        return jsonify({'error': f'Panel {strategy_type} vide - ajoutez des actions'}), 400
    
    # Calculer le momentum
    resultats = service.analyser_panel(panel, date_calcul, strategy_type=strategy_type)
    
    if not resultats['success']:
        return jsonify({
            'error': 'Échec du calcul',
            'erreurs': resultats['erreurs']
        }), 500
    
    # Générer les recommandations
    recommandations = service.generer_recommandations(resultats, nb_top, strategy_type=strategy_type)
    
    # Sauvegarder dans l'historique
    history = RecommendationHistory(
        strategy_type=strategy_type,
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
        'strategy_type': strategy_type,
        **recommandations
    })


@app.route('/api/calculate-and-notify', methods=['POST'])
@require_admin
def calculate_and_notify():
    """Lance le calcul et envoie une notification email"""
    data = request.get_json() or {}
    strategy_type = data.get('strategy_type', 'long')
    
    # D'abord calculer
    service = get_momentum_service()
    if not service:
        return jsonify({'error': 'API Tiingo non configurée'}), 500
    
    nb_top = int(Settings.get('nb_top', app.config.get('DEFAULT_NB_TOP', 5)))
    date_calcul = Settings.get('date_calcul', '')
    
    if not date_calcul:
        date_calcul = None
    
    actions = PanelAction.query.filter_by(is_active=True, strategy_type=strategy_type).all()
    panel = [a.ticker for a in actions]
    
    if not panel:
        return jsonify({'error': f'Panel {strategy_type} vide'}), 400
    
    resultats = service.analyser_panel(panel, date_calcul, strategy_type=strategy_type)
    
    if not resultats['success']:
        return jsonify({'error': 'Échec du calcul', 'erreurs': resultats['erreurs']}), 500
    
    recommandations = service.generer_recommandations(resultats, nb_top, strategy_type=strategy_type)
    
    # Sauvegarder
    history = RecommendationHistory(
        strategy_type=strategy_type,
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
    # On pourrait ajouter le type de stratégie dans le sujet de l'email
    email_result = email_svc.envoyer_recommandations(recommandations)
    
    return jsonify({
        'success': True,
        'history_id': history.id,
        'strategy_type': strategy_type,
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
    """Récupère la dernière recommandation pour une stratégie"""
    strategy_type = request.args.get('strategy_type', 'long')
    history = RecommendationHistory.query\
        .filter_by(strategy_type=strategy_type)\
        .order_by(RecommendationHistory.created_at.desc())\
        .first()
    
    if not history:
        return jsonify({'message': f'Aucune recommandation disponible pour {strategy_type}'}), 404
    
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
    Génère automatiquement un panel de 50 tickers basé sur les critères.
    Long: MarketCap/ADV (via Tiingo)
    Short: Losers (via Finviz)
    """
    data = request.get_json() or {}
    strategy_type = data.get('strategy_type', 'long')
    
    screener = get_screener_service()
    if not screener:
        return jsonify({'error': 'Service de screening indisponible'}), 500
    
    # Lancer le screening selon le type
    if strategy_type == 'short':
        result = screener.screen_losers_finviz()
    else:
        result = screener.screen_universe()
    
    if not result['success']:
        return jsonify({
            'success': False,
            'error': result['error'],
            'stats': result.get('stats', {})
        }), 500
    
    return jsonify({
        'success': True,
        'strategy_type': strategy_type,
        'tickers': result['tickers'],
        'stats': result['stats']
    })


@app.route('/api/screener/apply', methods=['POST'])
@require_admin
def apply_generated_panel():
    """
    Applique les tickers générés au panel actuel pour une stratégie donnée.
    Remplace tout le panel existant de cette stratégie par les nouveaux tickers.
    """
    data = request.get_json()
    tickers = data.get('tickers', [])
    strategy_type = data.get('strategy_type', 'long')
    
    if not tickers:
        return jsonify({'error': 'Aucun ticker fourni'}), 400
    
    # Désactiver tous les tickers actuels de cette stratégie
    PanelAction.query.filter_by(strategy_type=strategy_type).update({PanelAction.is_active: False})
    
    # Ajouter ou réactiver les nouveaux tickers
    added = 0
    for ticker_data in tickers:
        ticker = ticker_data.get('ticker', '').upper().strip()
        if not ticker:
            continue
        
        existing = PanelAction.query.filter_by(ticker=ticker, strategy_type=strategy_type).first()
        if existing:
            existing.is_active = True
        else:
            action = PanelAction(
                ticker=ticker,
                name=ticker_data.get('name'), # Utiliser le nom si disponible (ex: Finviz)
                strategy_type=strategy_type
            )
            db.session.add(action)
        added += 1
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'{added} tickers ajoutés au panel {strategy_type}',
        'count': added,
        'strategy_type': strategy_type
    })


# =============================================================================
# TÂCHE PLANIFIÉE - MISE À JOUR MENSUELLE
# =============================================================================

def job_mensuel():
    """
    Tâche exécutée le 1er de chaque mois.
    Calcule le momentum pour les deux stratégies (Long et Short)
    et envoie les recommandations par email.
    """
    with app.app_context():
        print(f"[{datetime.now()}] 🚀 Démarrage du calcul mensuel automatique...")
        
        service = get_momentum_service()
        if not service:
            print("❌ API Tiingo non configurée")
            return
        
        nb_top = int(Settings.get('nb_top', app.config.get('DEFAULT_NB_TOP', 5)))
        
        for strategy_type in ['long', 'short']:
            print(f"📊 Traitement de la stratégie: {strategy_type.upper()}")
            
            actions = PanelAction.query.filter_by(is_active=True, strategy_type=strategy_type).all()
            panel = [a.ticker for a in actions]
            
            if not panel:
                print(f"⚠️ Panel {strategy_type} vide, passage à la suite")
                continue
            
            # Calculer
            resultats = service.analyser_panel(panel, None, strategy_type=strategy_type)
            
            if not resultats['success']:
                print(f"❌ Échec du calcul {strategy_type}: {resultats['erreurs']}")
                continue
            
            recommandations = service.generer_recommandations(resultats, nb_top, strategy_type=strategy_type)
            
            # Sauvegarder
            history = RecommendationHistory(
                strategy_type=strategy_type,
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
            print(f"✅ Recommandations {strategy_type} sauvegardées (ID: {history.id})")
            
            # Envoyer email pour chaque stratégie
            email_svc = get_email_service()
            if email_svc.is_configured():
                # On ajoute le type de stratégie dans le sujet via un préfixe ou autre si besoin
                result = email_svc.envoyer_recommandations(recommandations)
                if result['success']:
                    print(f"✅ Email {strategy_type} envoyé: {result['message']}")
                else:
                    print(f"❌ Erreur email {strategy_type}: {result['message']}")
            else:
                print(f"⚠️ Service email non configuré pour {strategy_type}")


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

