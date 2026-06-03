# -*- coding: utf-8 -*-
"""
Application Flask - Momentum Strategy
=====================================
API REST pour l'application de stratégie momentum.
"""

import os
from flask import Flask, render_template, jsonify, request, make_response
import json
from flask_cors import CORS
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from functools import wraps
from config import get_config
from models import (
    db, init_db, Settings, 
    PanelAction, RecommendationHistory, RecommendationDetail,
    ShortPanelAction, ShortRecommendationHistory, ShortRecommendationDetail,
    OptionRecommendation
)
from momentum_service import MomentumService
from email_service import EmailService
from screener_service import ScreenerService
from short_screener_service import ShortScreenerService
from finviz_screener_service import FinvizScreenerService
from options_service import OptionsService, estimate_historical_volatility
from ibkr_service import IBKRService, encrypt_credential, decrypt_credential


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

# Service IBKR — démarre une boucle asyncio dans un thread dédié
ibkr_service = IBKRService(
    host=app.config.get('IB_GATEWAY_HOST', 'ib-gateway'),
    port=app.config.get('IB_GATEWAY_PORT', 4001),
)


def get_momentum_service():
    """Récupère ou crée le service momentum (Tiingo + IBKR multi-source)"""
    global momentum_service
    if momentum_service is None:
        api_key = app.config.get('TIINGO_API_KEY')
        # Créer le service si Tiingo OU IBKR disponible (IBKR peut être source primaire)
        if api_key or ibkr_service is not None:
            momentum_service = MomentumService(api_key, ibkr_service=ibkr_service)
    elif momentum_service.ibkr_service is None and ibkr_service is not None:
        momentum_service.set_ibkr_service(ibkr_service)
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

    vol_scaling = Settings.get('vol_scaling_enabled',
                               str(app.config.get('DEFAULT_VOL_SCALING', False)).lower())
    vol_target = Settings.get('vol_target', app.config.get('DEFAULT_VOL_TARGET', 12))
    max_exposure = Settings.get('max_exposure', app.config.get('DEFAULT_MAX_EXPOSURE', 250))
    portfolio_filter = Settings.get('portfolio_filter_enabled',
                                    str(app.config.get('DEFAULT_PORTFOLIO_FILTER', False)).lower())
    portfolio_vol_threshold = Settings.get('portfolio_vol_threshold',
                                           app.config.get('DEFAULT_PORTFOLIO_VOL_THRESHOLD', 20))

    return jsonify({
        'nb_top': int(nb_top),
        'date_calcul': date_calcul,
        'email_to': email_to,
        'email_configured': email_configured,
        'api_configured': app.config.get('TIINGO_API_KEY') is not None,
        'vol_scaling_enabled': str(vol_scaling).lower() == 'true',
        'vol_target': float(vol_target),
        'max_exposure': float(max_exposure),
        'portfolio_filter_enabled': str(portfolio_filter).lower() == 'true',
        'portfolio_vol_threshold': float(portfolio_vol_threshold)
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


@app.route('/api/panel/clear', methods=['DELETE'])
@require_admin
def clear_panel():
    """Supprime tous les tickers du panel Long"""
    count = PanelAction.query.filter_by(is_active=True).count()
    PanelAction.query.filter_by(is_active=True).update({PanelAction.is_active: False})
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'{count} ticker(s) retiré(s) du panel',
        'count': count
    })


@app.route('/api/panel/export', methods=['GET'])
def export_panel():
    """Exporte le panel Long en JSON"""
    strategy = request.args.get('strategy', 'long')
    
    if strategy == 'short':
        actions = ShortPanelAction.query.filter_by(is_active=True).all()
    else:
        actions = PanelAction.query.filter_by(is_active=True).all()
    
    export_data = {
        'strategy': strategy,
        'exported_at': datetime.now().isoformat(),
        'count': len(actions),
        'tickers': [{'ticker': a.ticker, 'name': a.name} for a in actions]
    }
    
    response = make_response(json.dumps(export_data, indent=2, ensure_ascii=False))
    response.headers['Content-Type'] = 'application/json'
    response.headers['Content-Disposition'] = f'attachment; filename=panel_{strategy}_{datetime.now().strftime("%Y%m%d")}.json'
    return response


@app.route('/api/panel/import', methods=['POST'])
@require_admin
def import_panel():
    """Importe un panel depuis JSON"""
    data = request.get_json(silent=True)
    
    if not data:
        return jsonify({'error': 'Données JSON invalides'}), 400
    
    strategy = data.get('strategy', 'long')
    tickers_data = data.get('tickers', [])
    
    if not tickers_data:
        return jsonify({'error': 'Aucun ticker dans le fichier'}), 400
    
    added = 0
    skipped = 0
    
    for item in tickers_data:
        ticker = item.get('ticker', '').upper().strip() if isinstance(item, dict) else str(item).upper().strip()
        name = item.get('name') if isinstance(item, dict) else None
        
        if not ticker:
            continue
        
        if strategy == 'short':
            existing = ShortPanelAction.query.filter_by(ticker=ticker).first()
            if existing:
                if not existing.is_active:
                    existing.is_active = True
                    added += 1
                else:
                    skipped += 1
            else:
                action = ShortPanelAction(ticker=ticker, name=name)
                db.session.add(action)
                added += 1
        else:
            existing = PanelAction.query.filter_by(ticker=ticker).first()
            if existing:
                if not existing.is_active:
                    existing.is_active = True
                    added += 1
                else:
                    skipped += 1
            else:
                action = PanelAction(ticker=ticker, name=name, strategy_type='long')
                db.session.add(action)
                added += 1
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'{added} tickers importés ({skipped} déjà présents)',
        'added': added,
        'skipped': skipped
    })


# =============================================================================
# ROUTES - API MOMENTUM
# =============================================================================

def _get_vol_scaling_settings():
    """Lit les réglages de volatility scaling (Long) depuis Settings + fallback config."""
    vs = Settings.get('vol_scaling_enabled',
                      str(app.config.get('DEFAULT_VOL_SCALING', False)).lower())
    pf = Settings.get('portfolio_filter_enabled',
                      str(app.config.get('DEFAULT_PORTFOLIO_FILTER', False)).lower())
    return {
        'vol_scaling': str(vs).lower() == 'true',
        'vol_target_pct': float(Settings.get('vol_target', app.config.get('DEFAULT_VOL_TARGET', 12))),
        'max_exposure_pct': float(Settings.get('max_exposure', app.config.get('DEFAULT_MAX_EXPOSURE', 250))),
        'portfolio_filter': str(pf).lower() == 'true',
        'portfolio_vol_threshold_pct': float(Settings.get('portfolio_vol_threshold',
                                            app.config.get('DEFAULT_PORTFOLIO_VOL_THRESHOLD', 20))),
    }


def _run_long_calculation():
    """
    Logique commune : récupère le panel, calcule le momentum, sauvegarde l'historique.
    Retourne (history, recommandations) ou lève une ValueError/RuntimeError.
    """
    service = get_momentum_service()
    if not service:
        raise RuntimeError('API Tiingo non configurée')

    nb_top = int(Settings.get('nb_top', app.config.get('DEFAULT_NB_TOP', 5)))
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


@app.route('/api/calculate', methods=['POST'])
@require_admin
def calculate_momentum():
    """Lance le calcul du momentum et génère les recommandations"""
    try:
        history, recommandations = _run_long_calculation()
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        app.logger.exception('Erreur inattendue dans calculate_momentum')
        return jsonify({'error': f'Erreur inattendue : {type(e).__name__}: {e}'}), 500

    return jsonify({'success': True, 'history_id': history.id, **recommandations})


@app.route('/api/calculate-and-notify', methods=['POST'])
@require_admin
def calculate_and_notify():
    """Lance le calcul et envoie une notification email"""
    try:
        history, recommandations = _run_long_calculation()
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        app.logger.exception('Erreur inattendue dans calculate_and_notify')
        return jsonify({'error': f'Erreur inattendue : {type(e).__name__}: {e}'}), 500

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
# ROUTES - API CACHE
# =============================================================================

@app.route('/api/benchmark', methods=['GET'])
def get_benchmark():
    """Perf d'un indice (défaut SPY) sur une période donnée — pour comparer au portefeuille."""
    start_str = request.args.get('start')
    end_str   = request.args.get('end')
    ticker    = request.args.get('ticker', 'SPY').upper()

    if not start_str or not end_str:
        return jsonify({'error': 'Paramètres start et end requis'}), 400

    svc = get_momentum_service()
    if not svc:
        return jsonify({'error': 'Tiingo non configuré'}), 503

    from datetime import date as date_cls
    try:
        start_date = date_cls.fromisoformat(start_str)
        end_date   = date_cls.fromisoformat(end_str)
    except ValueError:
        return jsonify({'error': 'Format de date invalide (YYYY-MM-DD)'}), 400

    nb_jours = (end_date - start_date).days + 10
    df, err = svc.recuperer_prix_journaliers(ticker, nb_jours=nb_jours)
    if df is None:
        return jsonify({'error': err or f'Données {ticker} indisponibles'}), 503

    df_filtered = df[(df.index.strftime('%Y-%m-%d') >= start_str) &
                     (df.index.strftime('%Y-%m-%d') <= end_str)]
    if len(df_filtered) < 2:
        return jsonify({'error': 'Pas assez de données pour la période'}), 400

    col = 'adjClose' if 'adjClose' in df_filtered.columns else 'close'
    start_price = float(df_filtered[col].iloc[0])
    end_price   = float(df_filtered[col].iloc[-1])
    perf = (end_price - start_price) / start_price * 100

    return jsonify({
        'ticker': ticker,
        'start_date': df_filtered.index[0].strftime('%Y-%m-%d'),
        'end_date':   df_filtered.index[-1].strftime('%Y-%m-%d'),
        'start_price': round(start_price, 2),
        'end_price':   round(end_price, 2),
        'performance_pct': round(perf, 2),
    })


@app.route('/api/market-regime', methods=['GET'])
def get_market_regime():
    """Régime de marché SPY/SMA200 — appelé au chargement du dashboard."""
    service = get_momentum_service()
    if not service:
        return jsonify({'regime': 'UNKNOWN', 'error': 'API Tiingo non configurée'})
    return jsonify(service.get_market_regime())



@app.route('/api/cache/clear', methods=['POST'])
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
        if isinstance(ticker_data, str):
            ticker = ticker_data.upper().strip()
        else:
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
        # Fallback : tenter le screener Tiingo si Finviz échoue (IP datacenter bloquée)
        tiingo_svc = get_screener_service()
        if tiingo_svc:
            try:
                tiingo_result = tiingo_svc.screen_universe()
                if tiingo_result.get('success') and tiingo_result.get('tickers'):
                    result = {'success': True, 'tickers': tiingo_result['tickers'],
                              'stats': {'source': 'Tiingo (fallback Finviz)', **tiingo_result.get('stats', {})}}
                else:
                    return jsonify({'success': False, 'error': result['error'],
                                    'stats': result.get('stats', {})}), 500
            except Exception as e:
                return jsonify({'success': False, 'error': f"Finviz: {result['error']} | Tiingo: {e}",
                                'stats': {}}), 500
        else:
            return jsonify({'success': False, 'error': result['error'],
                            'stats': result.get('stats', {})}), 500

    # screener_objs : liste de dicts {ticker, price, volume_display, adv_display, ...}
    screener_objs = result['tickers']
    screener_symbols = {o['ticker'] if isinstance(o, dict) else o for o in screener_objs}

    # Récupérer les positions du portefeuille IBKR (toujours en concurrence, en tête)
    # ensure_connected reconnecte si la session est tombée → portefeuille toujours inclus
    portfolio_positions = {}
    try:
        if ibkr_service.ensure_connected():
            for p in ibkr_service.get_positions():
                if p.get('ticker'):
                    portfolio_positions[p['ticker']] = p
    except Exception:
        pass

    # Objets du portefeuille absents du screener → enrichir avec les données IBKR
    portfolio_objs = []
    for ticker, p in portfolio_positions.items():
        if ticker in screener_symbols:
            continue
        price = p.get('market_price')
        portfolio_objs.append({
            'ticker': ticker,
            'price': round(price, 2) if price else '-',
            'volume': None,
            'volume_display': 'Portefeuille',
            'adv': None,
            'adv_display': '-',
            'score': None,
            'in_portfolio': True,
        })

    # Marquer aussi les tickers screener déjà en portefeuille
    def _norm(o):
        if not isinstance(o, dict):
            o = {'ticker': o, 'price': '-', 'volume_display': '-', 'adv_display': '-'}
        o = dict(o)
        o['in_portfolio'] = o['ticker'] in portfolio_positions
        return o

    # Fusion : portefeuille (hors screener) en premier, puis screener, limité à 50
    merged = portfolio_objs + [_norm(o) for o in screener_objs]
    merged = merged[:50]
    for i, o in enumerate(merged):
        o['rank'] = i + 1

    stats = result['stats']
    stats['portfolio_tickers_added'] = len(portfolio_objs)

    return jsonify({
        'success': True,
        'tickers': merged,
        'stats': stats,
        'portfolio_tickers': list(portfolio_positions.keys()),
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


@app.route('/api/short/panel/clear', methods=['DELETE'])
@require_admin
def clear_short_panel():
    """Supprime tous les tickers du panel Short"""
    count = ShortPanelAction.query.filter_by(is_active=True).count()
    ShortPanelAction.query.filter_by(is_active=True).update({ShortPanelAction.is_active: False})
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'{count} ticker(s) retiré(s) du panel Short',
        'count': count
    })


# =============================================================================
# ROUTES - API SHORT MOMENTUM
# =============================================================================

@app.route('/api/short/calculate', methods=['POST'])
@require_admin
def calculate_short_momentum():
    """
    Lance le calcul du momentum Short avec la méthode court terme.
    
    Méthode: Score = Perf(63j) - Perf(5j)
    - Capture la tendance baissière sur 3 mois
    - Exclut les 5 derniers jours pour éviter l'overshoot/rebond technique
    """
    
    service = get_momentum_service()
    if not service:
        return jsonify({'error': 'API Tiingo non configurée'}), 500
    
    # Récupérer les paramètres Short
    nb_top = int(Settings.get('short_nb_top', app.config.get('DEFAULT_NB_TOP', 5)))
    date_calcul = Settings.get('short_date_calcul', '')
    
    # Paramètres de la méthode momentum Short
    lookback = int(Settings.get('short_lookback', 63))  # 63 jours = ~3 mois
    skip_recent = int(Settings.get('short_skip_recent', 5))  # Exclure 5 derniers jours
    
    if not date_calcul:
        date_calcul = None
    
    # Récupérer le panel Short
    actions = ShortPanelAction.query.filter_by(is_active=True).all()
    panel = [a.ticker for a in actions]
    
    if not panel:
        return jsonify({'error': 'Panel Short vide - ajoutez des actions'}), 400
    
    # Calculer le momentum Short avec la nouvelle méthode
    resultats = service.analyser_panel_short(panel, lookback, skip_recent, date_calcul)
    
    if not resultats['success']:
        return jsonify({
            'error': 'Échec du calcul',
            'erreurs': resultats['erreurs']
        }), 500
    
    # Générer les recommandations SHORT
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
            momentum=float(r['momentum']),
            signal=r['signal'],
            allocation=float(r['allocation']),
            rank=int(r['rank'])
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
    Génère les signaux Short avec la méthode momentum court terme.
    
    Méthode: Score = Perf(63j) - Perf(5j)
    Trie par momentum CROISSANT (plus négatif = meilleur candidat short).
    """
    if not resultats_analyse['success']:
        return {
            'date_calcul': resultats_analyse['date_calcul'],
            'nb_top': nb_top,
            'recommandations': [],
            'total_shorter': 0,
            'erreurs': resultats_analyse['erreurs'],
            'methode': resultats_analyse.get('methode', {})
        }
    
    resultats = resultats_analyse['resultats']
    
    # Déjà trié par momentum croissant dans analyser_panel_short
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
            'perf_lookback': r.get('perf_lookback', 0),
            'perf_recent': r.get('perf_recent', 0),
            'prix_actuel': r.get('prix_actuel', 0),
            'signal': signal,
            'allocation': allocation,
            'rank': r['rank']
        })
    
    return {
        'date_calcul': resultats_analyse['date_calcul'],
        'nb_top': nb_top,
        'recommandations': recommandations,
        'total_shorter': nb_selection,
        'erreurs': resultats_analyse['erreurs'],
        'methode': resultats_analyse.get('methode', {})
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
    # Les critères sont maintenant intégrés dans screen_short() avec fallback automatique
    result = screener.screen_short()
    
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
        if isinstance(ticker_data, str):
            ticker = ticker_data.upper().strip()
        else:
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
        
        recommandations = service.generer_recommandations(resultats, nb_top,
                                                          **_get_vol_scaling_settings())

        # Sauvegarder
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


# =============================================================================
# ROUTES - API OPTIONS (PUT & PUT SPREAD)
# =============================================================================

def get_options_service():
    """Retourne une instance du service Options."""
    return OptionsService(risk_free_rate=0.05)


@app.route('/api/options/calculate', methods=['POST'])
@require_admin
def calculate_option():
    """
    Calcule un PUT ou PUT SPREAD pour un ticker donné.
    
    Body JSON:
    {
        "ticker": "AAPL",
        "spot_price": 150.0,
        "iv": 0.30,
        "dte": 45,
        "delta_long": -0.30,
        "delta_short": -0.10,
        "type": "spread"  // "put" ou "spread"
    }
    """
    data = request.get_json(silent=True) or {}
    
    spot_price = data.get('spot_price')
    iv = data.get('iv', 0.30)
    dte = data.get('dte', 45)
    delta_long = data.get('delta_long', -0.30)
    delta_short = data.get('delta_short', -0.10)
    option_type = data.get('type', 'spread')
    
    if not spot_price:
        return jsonify({'error': 'spot_price requis'}), 400
    
    service = get_options_service()
    T = dte / 365
    r = service.risk_free_rate
    
    if option_type == 'spread':
        result = service.calculate_put_spread(spot_price, T, r, iv, delta_long, delta_short)
    else:
        result = service.calculate_naked_put(spot_price, T, r, iv, delta_long)
    
    result['ticker'] = data.get('ticker', '')
    result['expiration_date'] = service.get_expiration_date(dte)
    
    return jsonify({
        'success': True,
        'option': result
    })


@app.route('/api/options/recommendation/<ticker>', methods=['GET'])
def get_option_recommendation(ticker):
    """
    Génère une recommandation d'option pour un ticker Short.
    Utilise les données momentum existantes.
    """
    ticker = ticker.upper()
    
    # Récupérer les données du ticker depuis le dernier calcul Short
    latest = ShortRecommendationHistory.query.order_by(
        ShortRecommendationHistory.created_at.desc()
    ).first()
    
    if not latest:
        return jsonify({'error': 'Aucun calcul Short disponible'}), 404
    
    detail = ShortRecommendationDetail.query.filter_by(
        history_id=latest.id, 
        ticker=ticker
    ).first()
    
    if not detail:
        return jsonify({'error': f'{ticker} non trouvé dans les recommandations Short'}), 404
    
    # Récupérer le prix actuel via Tiingo
    service = get_momentum_service()
    if service:
        df, err = service.recuperer_prix_journaliers(ticker, 100)
        if df is not None and len(df) > 0:
            spot_price = float(df['adjClose'].iloc[-1])
            prices = df['adjClose'].tolist()
            iv = estimate_historical_volatility(prices, min(30, len(prices) - 1))
        else:
            return jsonify({'error': f'Impossible de récupérer le prix de {ticker}'}), 500
    else:
        return jsonify({'error': 'API Tiingo non configurée'}), 500
    
    # Calculer les performances (adaptatif selon les données disponibles)
    n = len(df)
    lookback = min(63, n - 6)
    skip = 5
    
    if n >= lookback + skip + 1:
        prix_lookback = float(df['adjClose'].iloc[-(lookback + skip + 1)])
        prix_skip = float(df['adjClose'].iloc[-(skip + 1)])
        prix_0 = float(df['adjClose'].iloc[-1])
        
        perf_63_5 = ((prix_skip - prix_lookback) / prix_lookback) * 100
        perf_5_0 = ((prix_0 - prix_skip) / prix_skip) * 100
        momentum_score = perf_63_5
    else:
        perf_63_5 = 0
        perf_5_0 = 0
        momentum_score = detail.momentum
    
    # Générer la recommandation
    options_service = get_options_service()
    recommendation = options_service.build_option_recommendation(
        ticker=ticker,
        spot_price=spot_price,
        iv=iv,
        momentum_score=momentum_score,
        perf_63_5=perf_63_5,
        perf_5_0=perf_5_0,
        dte_target=45
    )
    
    return jsonify({
        'success': True,
        'recommendation': recommendation
    })


@app.route('/api/options/saved', methods=['GET'])
def get_saved_option_recommendations():
    """
    Récupère les dernières recommandations d'options sauvegardées.
    """
    recommendations = OptionRecommendation.query.order_by(
        OptionRecommendation.rank
    ).all()
    
    if not recommendations:
        return jsonify({
            'success': True,
            'recommendations': [],
            'count': 0,
            'message': 'Aucune recommandation sauvegardée'
        })
    
    # Date du dernier calcul
    calc_date = recommendations[0].calculation_date if recommendations else None
    
    return jsonify({
        'success': True,
        'calculation_date': calc_date.isoformat() if calc_date else None,
        'recommendations': [r.to_dict() for r in recommendations],
        'count': len(recommendations)
    })


@app.route('/api/options/bulk-recommendations', methods=['GET'])
@require_admin
def get_bulk_option_recommendations():
    """
    Génère des recommandations d'options pour tous les tickers Short signalés.
    Sauvegarde les résultats en base de données.
    """
    # Récupérer le dernier calcul Short
    latest = ShortRecommendationHistory.query.order_by(
        ShortRecommendationHistory.created_at.desc()
    ).first()
    
    if not latest:
        return jsonify({'error': 'Aucun calcul Short disponible'}), 404
    
    # Récupérer les tickers avec signal "Shorter"
    details = ShortRecommendationDetail.query.filter_by(
        history_id=latest.id,
        signal='Shorter'
    ).order_by(ShortRecommendationDetail.rank).all()
    
    if not details:
        return jsonify({'error': 'Aucun signal Shorter trouvé'}), 404
    
    service = get_momentum_service()
    options_service = get_options_service()
    
    recommendations = []
    errors = []
    
    # Supprimer les anciennes recommandations
    OptionRecommendation.query.delete()
    
    for detail in details:
        ticker = detail.ticker
        
        try:
            # Récupérer les prix (100 jours calendaires = ~70 jours de trading)
            df, err = service.recuperer_prix_journaliers(ticker, 100)
            if df is None or len(df) < 20:
                errors.append({'ticker': ticker, 'error': err or 'Données insuffisantes'})
                continue
            
            spot_price = float(df['adjClose'].iloc[-1])
            prices = df['adjClose'].tolist()
            iv = estimate_historical_volatility(prices, min(30, len(prices) - 1))
            
            # Performances (adaptatif selon les données disponibles)
            n = len(df)
            lookback = min(63, n - 6)
            skip = 5
            
            if n >= lookback + skip + 1:
                prix_lookback = float(df['adjClose'].iloc[-(lookback + skip + 1)])
                prix_skip = float(df['adjClose'].iloc[-(skip + 1)])
                prix_0 = float(df['adjClose'].iloc[-1])
                
                perf_63_5 = ((prix_skip - prix_lookback) / prix_lookback) * 100
                perf_5_0 = ((prix_0 - prix_skip) / prix_skip) * 100
                momentum_score = perf_63_5
            else:
                perf_63_5 = 0
                perf_5_0 = 0
                momentum_score = detail.momentum
            
            # Recommandation
            rec = options_service.build_option_recommendation(
                ticker=ticker,
                spot_price=spot_price,
                iv=iv,
                momentum_score=momentum_score,
                perf_63_5=perf_63_5,
                perf_5_0=perf_5_0,
                dte_target=45
            )
            rec['rank'] = detail.rank
            recommendations.append(rec)
            
            # Sauvegarder en base de données
            option_rec = OptionRecommendation(
                ticker=ticker,
                calculation_date=latest.calculation_date,
                spot_price=spot_price,
                iv_pct=round(iv * 100, 1),
                momentum_score=round(momentum_score, 2),
                perf_63_5=round(perf_63_5, 2),
                perf_5_0=round(perf_5_0, 2),
                signal=rec.get('signal', ''),
                all_conditions_met=rec.get('all_conditions_met', False),
                recommended_strategy=rec.get('recommended_strategy', ''),
                rank=detail.rank,
                put_strike=rec.get('put', {}).get('strike'),
                put_price=rec.get('put', {}).get('price'),
                put_delta=rec.get('put', {}).get('delta'),
                spread_strike_long=rec.get('put_spread', {}).get('strike_long'),
                spread_strike_short=rec.get('put_spread', {}).get('strike_short'),
                spread_net_debit=rec.get('put_spread', {}).get('net_debit'),
                spread_max_profit=rec.get('put_spread', {}).get('max_profit'),
                spread_breakeven=rec.get('put_spread', {}).get('breakeven'),
                spread_risk_reward=rec.get('put_spread', {}).get('risk_reward_ratio'),
                spread_delta_long=rec.get('put_spread', {}).get('delta_long_actual'),
                spread_delta_short=rec.get('put_spread', {}).get('delta_short_actual'),
                dte=rec.get('put_spread', {}).get('dte'),
                expiration_date=rec.get('put_spread', {}).get('expiration_date', options_service.get_expiration_date(45))
            )
            db.session.add(option_rec)
            
        except Exception as e:
            errors.append({'ticker': ticker, 'error': str(e)})
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'calculation_date': latest.calculation_date.strftime('%Y-%m-%d'),
        'recommendations': recommendations,
        'errors': errors,
        'count': len(recommendations)
    })


@app.route('/api/options/quick-calc', methods=['POST'])
def quick_option_calc():
    """
    Calcul rapide d'option sans authentification.
    Pour le calculateur interactif.
    """
    data = request.get_json(silent=True) or {}
    
    spot_price = data.get('spot_price')
    iv = data.get('iv', 30) / 100  # Convertir de % en décimal
    dte = data.get('dte', 45)
    strike_long = data.get('strike_long')
    strike_short = data.get('strike_short')
    
    if not spot_price:
        return jsonify({'error': 'spot_price requis'}), 400
    
    service = get_options_service()
    T = dte / 365
    r = service.risk_free_rate
    
    result = {
        'spot_price': spot_price,
        'iv_pct': round(iv * 100, 1),
        'dte': dte,
        'expiration_date': service.get_expiration_date(dte)
    }
    
    if strike_long and strike_short:
        # Calcul avec strikes manuels
        price_long = service.put_price(spot_price, strike_long, T, r, iv)
        price_short = service.put_price(spot_price, strike_short, T, r, iv)
        net_debit = price_long - price_short
        max_profit = (strike_long - strike_short) - net_debit
        
        result['type'] = 'PUT_SPREAD'
        result['strike_long'] = strike_long
        result['strike_short'] = strike_short
        result['price_long'] = round(price_long, 2)
        result['price_short'] = round(price_short, 2)
        result['net_debit'] = round(net_debit, 2)
        result['max_profit'] = round(max_profit, 2)
        result['max_loss'] = round(net_debit, 2)
        result['breakeven'] = round(strike_long - net_debit, 2)
        result['risk_reward'] = round(max_profit / net_debit, 2) if net_debit > 0 else 0
        result['delta_long'] = round(service.delta_put(spot_price, strike_long, T, r, iv), 3)
        result['delta_short'] = round(service.delta_put(spot_price, strike_short, T, r, iv), 3)
    else:
        # Auto-calcul avec deltas par défaut
        spread = service.calculate_put_spread(spot_price, T, r, iv)
        result.update(spread)
    
    return jsonify({
        'success': True,
        'result': result
    })


# =============================================================================
# ROUTES - IBKR / INTERACTIVE BROKERS
# =============================================================================

@app.route('/api/ibkr/status', methods=['GET'])
def ibkr_status():
    """Retourne le statut de connexion à IB Gateway + le mode de trading courant."""
    status = ibkr_service.get_status()
    # Le mode est déduit du port socat réel (4003=live, 4004=paper) : reflète la
    # connexion effective, plus fiable qu'une valeur Settings potentiellement périmée.
    mode_from_port = {4003: 'live', 4004: 'paper'}.get(status.get('port'))
    status['trading_mode'] = mode_from_port or Settings.get('ibkr_trading_mode', 'live')
    return jsonify(status)


@app.route('/api/ibkr/connect', methods=['POST'])
@require_admin
def ibkr_connect():
    """Tente une connexion (ou reconnexion) à IB Gateway."""
    result = ibkr_service.connect()
    return jsonify(result), 200 if result['success'] else 503


@app.route('/api/ibkr/trading-mode', methods=['POST'])
@require_admin
def ibkr_set_trading_mode():
    """
    Bascule entre live et paper. Recrée le gateway dans le nouveau mode
    (→ 2FA requise) et repointe l'app sur le bon port socat.
    Body JSON: { mode: 'live' | 'paper' }
    """
    data = request.get_json() or {}
    mode = (data.get('mode') or '').lower()
    if mode not in ('live', 'paper'):
        return jsonify({'success': False, 'error': "mode doit être 'live' ou 'paper'"}), 400

    current = Settings.get('ibkr_trading_mode', 'live')
    if mode == current and ibkr_service.get_status()['connected']:
        return jsonify({'success': True, 'mode': mode, 'message': f'Déjà en mode {mode}'})

    # Récupérer les credentials chiffrés
    secret = app.config.get('SECRET_KEY', '')
    enc_user = Settings.get('ibkr_username_enc')
    enc_pass = Settings.get('ibkr_password_enc')
    if not enc_user or not enc_pass:
        return jsonify({'success': False,
                        'error': 'Identifiants IBKR absents — saisissez-les d\'abord'}), 400
    try:
        username = decrypt_credential(enc_user, secret)
        password = decrypt_credential(enc_pass, secret)
    except Exception:
        return jsonify({'success': False,
                        'error': 'Déchiffrement des identifiants impossible (SECRET_KEY changé ?)'}), 500

    Settings.set('ibkr_trading_mode', mode)

    # Notifier avant la 2FA
    try:
        get_email_service().envoyer_notification_gateway()
    except Exception:
        pass

    _ibkr_update_env_and_restart(username, password, mode)

    return jsonify({
        'success': True,
        'mode': mode,
        'port': IBKR_SOCAT_PORT.get(mode),
        'message': f'Bascule en {mode} — gateway en redémarrage (~90s), 2FA requise sur votre téléphone',
    })


@app.route('/api/ibkr/credentials', methods=['POST'])
@require_admin
def ibkr_save_credentials():
    """
    Sauvegarde les identifiants IBKR, met à jour le .env et redémarre IB Gateway.
    Body JSON: { username, password, trading_mode }
    """
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    trading_mode = data.get('trading_mode', 'live')

    if not username or not password:
        return jsonify({'success': False, 'error': 'username et password requis'}), 400

    # Stocker chiffré en base
    secret = app.config.get('SECRET_KEY', '')
    Settings.set('ibkr_username_enc', encrypt_credential(username, secret))
    Settings.set('ibkr_password_enc', encrypt_credential(password, secret))
    Settings.set('ibkr_trading_mode', trading_mode)

    # Notifier l'utilisateur avant la 2FA
    try:
        get_email_service().envoyer_notification_gateway()
    except Exception:
        pass

    # Mettre à jour le .env sur le serveur et redémarrer IB Gateway
    _ibkr_update_env_and_restart(username, password, trading_mode)

    return jsonify({'success': True, 'message': 'Identifiants sauvegardés — IB Gateway en cours de démarrage (~90s)'})


# Port SOCAT du gateway (accessible depuis le réseau Docker) selon le mode.
# Live  : API interne 4001 → socat 4003
# Paper : API interne 4002 → socat 4004
IBKR_SOCAT_PORT = {'live': 4003, 'paper': 4004}


def _ibkr_set_env_vars(updates: dict):
    """Met à jour des variables dans le .env hôte (monté en /app/.env.host)."""
    import re
    env_path = '/app/.env.host'
    try:
        with open(env_path, 'r') as f:
            content = f.read()

        def set_var(text, key, value):
            pattern = rf'^{key}=.*$'
            replacement = f'{key}={value}'
            if re.search(pattern, text, re.MULTILINE):
                return re.sub(pattern, replacement, text, flags=re.MULTILINE)
            return text + f'\n{key}={value}'

        for key, value in updates.items():
            content = set_var(content, key, value)

        with open(env_path, 'w') as f:
            f.write(content)
        return True
    except Exception as e:
        app.logger.warning('_ibkr_set_env_vars: %s', e)
        return False


def _recreate_gateway(username: str, password: str, trading_mode: str):
    """
    Recrée le conteneur IB Gateway via le SDK Docker, en répliquant fidèlement
    les options du docker-compose.yml (healthcheck, auto-restart, autoheal, etc.).
    Lancé en thread car le gateway met ~90s à démarrer + 2FA.
    """
    import threading

    def _restart():
        try:
            import docker as docker_sdk
            client = docker_sdk.from_env()
            net_name = 'momentum-app_internal'
            for c in client.containers.list(all=True, filters={'name': 'ib-gateway'}):
                c.stop()
                c.remove()
            # Créer sans démarrer, puis connecter au réseau avec l'alias DNS
            # 'ib-gateway' (sinon seul le nom du conteneur est résolvable, et l'app
            # qui cherche 'ib-gateway' échoue avec "name resolution"). docker-compose
            # crée cet alias automatiquement, mais le SDK doit le faire explicitement.
            container = client.containers.create(
                'ghcr.io/gnzsnz/ib-gateway:stable',
                name='momentum-app-ib-gateway-1',
                detach=True,
                restart_policy={'Name': 'unless-stopped'},
                labels={'autoheal': 'true'},
                environment={
                    'TWS_USERID': username,
                    'TWS_PASSWORD': password,
                    'TRADING_MODE': trading_mode,
                    'TWS_SETTINGS_PATH': '/home/ibgateway/Jts',
                    'VNC_SERVER_PASSWORD': 'changeme',
                    'TWS_ACCEPT_INCOMING': 'accept',
                    'AUTO_RESTART_TIME': '11:30 PM',
                    'TIME_ZONE': 'America/New_York',
                    'RELOGIN_AFTER_TWOFA_TIMEOUT': 'yes',
                    'TWOFA_TIMEOUT_ACTION': 'restart',
                },
                ports={'5900/tcp': ('127.0.0.1', 5900)},
                healthcheck={
                    'test': ["CMD-SHELL", "bash -c 'echo > /dev/tcp/127.0.0.1/4001' || exit 1"],
                    'interval': 60_000_000_000, 'timeout': 10_000_000_000,
                    'retries': 3, 'start_period': 180_000_000_000,
                },
            )
            network = client.networks.get(net_name)
            network.connect(container, aliases=['ib-gateway'])
            container.start()
            app.logger.info('IB Gateway recréé (mode=%s, alias=ib-gateway)', trading_mode)
        except Exception as e:
            app.logger.warning('_recreate_gateway: %s', e)

    threading.Thread(target=_restart, daemon=True).start()


def _ibkr_update_env_and_restart(username: str, password: str, trading_mode: str):
    """Met à jour le .env (credentials + mode + port socat) et recrée le gateway."""
    port = IBKR_SOCAT_PORT.get(trading_mode, 4003)
    _ibkr_set_env_vars({
        'IB_USERNAME': username,
        'IB_PASSWORD': password,
        'IB_TRADING_MODE': trading_mode,
        'IB_GATEWAY_PORT': port,
    })
    # Pointer l'app sur le bon port socat et forcer la reconnexion
    ibkr_service.port = port
    ibkr_service.disconnect()
    _recreate_gateway(username, password, trading_mode)


@app.route('/api/ibkr/positions', methods=['GET'])
@require_admin
def ibkr_positions():
    """Retourne les positions ouvertes depuis IB Gateway."""
    try:
        if not ibkr_service.ensure_connected():
            return jsonify({'success': False, 'error': 'Reconnexion IBKR impossible'}), 503
        positions = ibkr_service.get_positions()
        return jsonify({'success': True, 'positions': positions, 'count': len(positions)})
    except ConnectionError as e:
        return jsonify({'success': False, 'error': str(e)}), 503
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ibkr/portfolio-stats', methods=['GET'])
@require_admin
def ibkr_portfolio_stats():
    """Stats complètes du portefeuille : positions, P&L, allocation, rendement."""
    try:
        if not ibkr_service.ensure_connected():
            return jsonify({'success': False, 'error': 'Reconnexion IBKR impossible'}), 503
        stats = ibkr_service.get_portfolio_stats()
        return jsonify({'success': True, **stats})
    except ConnectionError as e:
        return jsonify({'success': False, 'error': str(e)}), 503
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/perf/dashboard', methods=['GET'])
@require_admin
def perf_dashboard():
    """
    Retourne toutes les données nécessaires au tableau de bord Performance v2.0.
    Agrège les snapshots, le benchmark, les positions, transactions et dividendes.
    """
    try:
        from models import PortfolioSnapshot, MarketPriceBar, Transaction, Dividend
        
        # 1. Snapshots (Historique NAV)
        snapshots = PortfolioSnapshot.query.order_by(PortfolioSnapshot.date.asc()).all()
        
        # 2. Benchmark (^GSPC)
        benchmark = MarketPriceBar.query.filter_by(ticker='^GSPC')\
            .order_by(MarketPriceBar.bar_date.asc()).all()
        
        # 3. Positions actuelles (Live IBKR)
        positions = []
        stats = {}
        if ibkr_service.ensure_connected():
            stats = ibkr_service.get_portfolio_stats()
            positions = stats.get('positions', [])
        
        # 4. Transactions
        transactions = Transaction.query.order_by(Transaction.date.desc()).all()
        
        # 5. Dividendes
        dividends = Dividend.query.order_by(Dividend.date.desc()).all()
        
        return jsonify({
            'success': True,
            'metadata': {
                'currency': 'USD',
                'updated_at': datetime.now().isoformat()
            },
            'data_sources': {
                'portfolio_timeseries': [s.to_dict() for s in snapshots],
                'benchmark': [b.to_dict() for b in benchmark],
                'positions': positions,
                'transactions': [t.to_dict() for t in transactions],
                'dividends': [d.to_dict() for d in dividends]
            },
            'summary': {
                'total_value': stats.get('total_value', 0),
                'total_pnl': stats.get('total_pnl', 0),
                'unrealized_pnl': stats.get('total_unrealized_pnl', 0),
                'realized_pnl': stats.get('total_realized_pnl', 0),
                'return_pct': stats.get('return_pct', 0)
            }
        })
    except Exception as e:
        app.logger.exception('Erreur dans perf_dashboard')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ibkr/rebalance', methods=['POST'])
@require_admin
def ibkr_rebalance():
    """
    Passe des ordres de rééquilibrage via IB Gateway.
    Body JSON: { targets: [{ticker, target_pct, currency?}], dry_run: bool }
    dry_run=true (défaut) → aperçu sans exécution.
    """
    data = request.get_json() or {}
    targets = data.get('targets', [])
    dry_run = data.get('dry_run', True)

    if not targets:
        return jsonify({'success': False, 'error': 'targets requis'}), 400
    try:
        if not ibkr_service.ensure_connected():
            return jsonify({'success': False, 'error': 'Reconnexion IBKR impossible'}), 503
        orders = ibkr_service.place_rebalance_orders(targets, dry_run=dry_run)
        return jsonify({
            'success': True,
            'dry_run': dry_run,
            'orders': orders,
            'count': len(orders),
        })
    except ConnectionError as e:
        return jsonify({'success': False, 'error': str(e)}), 503
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# TÂCHE PLANIFIÉE - POSITIONS IBKR (toutes les 2h, heures de marché US)
# =============================================================================

def job_positions_ibkr():
    """Récupère les positions IBKR, envoie un email de suivi et enregistre un snapshot."""
    with app.app_context():
        print(f"[{datetime.now()}] 📊 Envoi email positions IBKR...")
        try:
            if not ibkr_service.ensure_connected():
                print("⚠️ Reconnexion IBKR impossible, email non envoyé")
                return
            
            # Récupérer les stats complètes pour le snapshot
            stats = ibkr_service.get_portfolio_stats()
            positions = stats.get('positions', [])
            
            if not positions:
                print("⚠️ Aucune position ouverte, email non envoyé")
                return
            
            # Enregistrer le snapshot du jour
            from models import PortfolioSnapshot
            from datetime import date
            today = date.today()
            
            # Vérifier si on a déjà un snapshot pour aujourd'hui (ou mettre à jour)
            snapshot = PortfolioSnapshot.query.filter_by(date=today).first()
            if not snapshot:
                snapshot = PortfolioSnapshot(date=today)
                db.session.add(snapshot)
            
            snapshot.nav = stats.get('total_value', 0)
            snapshot.cash = stats.get('total_value', 0) - stats.get('total_cost', 0) # Simplification si on n'a pas le cash direct
            # Pour invested_capital, on essaie de garder la valeur précédente ou d'initialiser
            if snapshot.invested_capital is None:
                prev = PortfolioSnapshot.query.filter(PortfolioSnapshot.date < today).order_by(PortfolioSnapshot.date.desc()).first()
                snapshot.invested_capital = prev.invested_capital if prev else snapshot.nav
            
            db.session.commit()
            print(f"✅ Snapshot enregistré pour {today} (NAV=${snapshot.nav:,.0f})")

            # Envoi de l'email
            email_svc = get_email_service()
            if not email_svc.is_configured():
                print("⚠️ Service email non configuré")
                return
            result = email_svc.envoyer_positions(positions)
            if result['success']:
                print(f"✅ Email positions envoyé ({len(positions)} positions)")
            else:
                print(f"❌ Erreur email positions: {result['message']}")
        except Exception as e:
            print(f"❌ job_positions_ibkr: {e}")


def job_update_benchmarks():
    """Récupère les données historiques du S&P 500 (^GSPC)."""
    with app.app_context():
        print(f"[{datetime.now()}] 📈 Mise à jour du benchmark (^GSPC)...")
        try:
            if not ibkr_service.ensure_connected():
                print("⚠️ Reconnexion IBKR impossible pour le benchmark")
                return
            
            from models import MarketPriceBar
            from datetime import date, timedelta
            
            # On récupère les 2 dernières années pour être sûr d'avoir l'historique nécessaire
            bars = ibkr_service.get_daily_bars('^GSPC', duration='2 Y')
            
            count = 0
            for b in bars:
                bar_date = date.fromisoformat(b['date'])
                existing = MarketPriceBar.query.filter_by(ticker='^GSPC', bar_date=bar_date).first()
                if not existing:
                    new_bar = MarketPriceBar(
                        ticker='^GSPC',
                        bar_date=bar_date,
                        adj_close=b['adj_close'],
                        close=b['close'],
                        source='ibkr'
                    )
                    db.session.add(new_bar)
                    count += 1
            
            db.session.commit()
            print(f"✅ Benchmark ^GSPC mis à jour ({count} nouvelles barres)")
        except Exception as e:
            print(f"❌ job_update_benchmarks: {e}")


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

# Positions IBKR : 9h30, 11h30, 13h30, 15h30 ET (Lun-Ven)
scheduler.add_job(
    job_positions_ibkr,
    CronTrigger(hour='9,11,13,15', minute=30, day_of_week='mon-fri', timezone='America/New_York'),
    id='ibkr_positions',
    name='Positions IBKR toutes les 2h (heures marché US)',
    replace_existing=True
)

# Mise à jour Benchmark : Tous les jours à 22h00 ET
scheduler.add_job(
    job_update_benchmarks,
    CronTrigger(hour=22, minute=0, timezone='America/New_York'),
    id='update_benchmarks',
    name='Mise à jour Benchmark ^GSPC',
    replace_existing=True
)

# Démarrer le scheduler (fonctionne avec gunicorn en production)
scheduler.start()
print("📅 Scheduler démarré - Mise à jour automatique le 1er de chaque mois à 8h00 UTC")
print("📊 Scheduler IBKR - Positions envoyées à 9h30, 11h30, 13h30, 15h30 ET (Lun-Ven)")


# =============================================================================
# POINT D'ENTRÉE
# =============================================================================

if __name__ == '__main__':
    # Lancer l'application en mode développement
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=app.config.get('DEBUG', False))

