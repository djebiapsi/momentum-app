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
    OptionRecommendation, MarketEvent
)
from momentum_service import MomentumService
from email_service import EmailService
from screener_service import ScreenerService
from short_screener_service import ShortScreenerService
from finviz_screener_service import FinvizScreenerService
from options_service import OptionsService, estimate_historical_volatility
from ibkr_service import IBKRService, encrypt_credential, decrypt_credential
from market_monitor_service import MarketMonitorService
from news_service import NewsService
import flex_service
import functools


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
market_monitor_service = None
news_service = None

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


def get_news_service():
    """Récupère ou crée le service news (RSS + résumé via API LLM compatible OpenAI)."""
    global news_service
    if news_service is None:
        # Les identifiants LLM (LLM_API_KEY / LLM_BASE_URL / LLM_MODEL) sont lus
        # depuis l'environnement par NewsService.
        news_service = NewsService()
    return news_service


def get_market_monitor():
    """Récupère ou crée le service de surveillance du marché."""
    global market_monitor_service
    if market_monitor_service is None:
        market_monitor_service = MarketMonitorService(
            ibkr_service=ibkr_service,
            momentum_service=get_momentum_service(),
        )
    return market_monitor_service


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


@app.route('/api/history/latest/download', methods=['GET'])
def download_latest_momentum():
    """Télécharge le dernier calcul de momentum au format CSV."""
    history = RecommendationHistory.query\
        .order_by(RecommendationHistory.created_at.desc())\
        .first()
    if not history:
        return jsonify({'message': 'Aucune recommandation disponible'}), 404
    return _momentum_csv_response(history)


@app.route('/api/history/<int:history_id>/download', methods=['GET'])
def download_momentum(history_id):
    """Télécharge un calcul de momentum précis au format CSV."""
    history = RecommendationHistory.query.get_or_404(history_id)
    return _momentum_csv_response(history)


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

    nb_top = int(Settings.get('nb_top', app.config.get('DEFAULT_NB_TOP', 5)))
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


def job_rebalance_reminder():
    """
    Cron mensuel (1er du mois). Calcule le momentum, le sauvegarde, et envoie
    l'email « C'est le moment de rééquilibrer ! » avec bouton de téléchargement.
    """
    with app.app_context():
        print(f"[{datetime.now()}] 🔄 Rappel mensuel de rééquilibrage…")
        recommandations, history = compute_and_save_momentum()
        if not recommandations:
            return

        email_svc = get_email_service()
        if email_svc.is_configured():
            result = email_svc.envoyer_rebalance_reminder(recommandations, history.id)
            print(f"{'✅' if result['success'] else '❌'} Email rééquilibrage: {result['message']}")
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
    """
    Met à jour des variables dans le .env hôte (monté en /app/.env.host).
    Déduplique les clés en double (garde une seule occurrence) pour éviter les
    incohérences : python-dotenv prend la dernière valeur, ce qui pouvait annuler
    un changement de mode si un doublon subsistait.
    """
    import re
    env_path = '/app/.env.host'
    try:
        with open(env_path, 'r') as f:
            lines = f.read().splitlines()

        # Appliquer les updates et dédupliquer (dernière occurrence gagne)
        result_lines = []
        seen = set()
        applied = set()
        for line in lines:
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', line)
            if not m:
                result_lines.append(line)
                continue
            key = m.group(1)
            if key in seen:
                continue  # doublon → on l'enlève
            seen.add(key)
            if key in updates:
                result_lines.append(f'{key}={updates[key]}')
                applied.add(key)
            else:
                result_lines.append(line)

        # Ajouter les nouvelles clés absentes du fichier
        for key, value in updates.items():
            if key not in applied:
                result_lines.append(f'{key}={value}')

        with open(env_path, 'w') as f:
            f.write('\n'.join(result_lines) + '\n')
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


RANGE_TO_DAYS = {
    '1W': 7, '1M': 30, '3M': 90, '6M': 180,
    '1Y': 365, '3Y': 1095, '5Y': 1825, 'ALL': 3650,
}


@app.route('/api/perf/dashboard', methods=['GET'])
@require_admin
def perf_dashboard():
    """
    Tableau de bord Performance : reconstruit l'évolution du portefeuille en
    buy & hold des positions actuelles (qty × prix historiques persistés), calcule
    les métriques (CAGR, Sharpe, max drawdown, drawdown série, rendements mensuels)
    et compare au S&P 500 (SPY). Paramètre ?range=1W|1M|3M|6M|1Y|3Y|5Y|YTD|ALL.

    Note : approximation buy & hold (pas d'historique de transactions) — la série
    suppose les positions actuelles détenues sur toute la période.
    """
    import pandas as pd
    range_key = (request.args.get('range') or '1Y').upper()

    try:
        if not ibkr_service.ensure_connected():
            return jsonify({'success': False, 'error': 'Reconnexion IBKR impossible'}), 503

        stats = ibkr_service.get_portfolio_stats()
        positions = stats.get('positions', [])
        if not positions:
            return jsonify({'success': True, 'empty': True,
                            'message': 'Aucune position', 'summary': stats})

        # Fenêtre temporelle
        from datetime import date as _date
        if range_key == 'YTD':
            nb_jours = (_date.today() - _date(_date.today().year, 1, 1)).days + 1
        else:
            nb_jours = RANGE_TO_DAYS.get(range_key, 365)

        svc = get_momentum_service()
        cutoff = pd.Timestamp(datetime.now()) - pd.Timedelta(days=nb_jours)

        def _tz_naive(s):
            """Normalise l'index d'une série en tz-naive (mix IBKR tz-aware / DB tz-naive)."""
            if getattr(s.index, 'tz', None) is not None:
                s.index = s.index.tz_localize(None)
            return s

        # Source de la NAV : snapshots Flex réels (prioritaire) sinon reconstruction
        from models import PortfolioSnapshot
        snaps = (PortfolioSnapshot.query
                 .filter(PortfolioSnapshot.date >= cutoff.date())
                 .order_by(PortfolioSnapshot.date.asc()).all())
        nav_source = 'flex'
        if len(snaps) >= 2:
            nav = pd.Series({pd.Timestamp(s.date): s.nav for s in snaps}).sort_index()
        else:
            # 1) Reconstruction buy & hold : Σ qty_i × prix_i(t)
            nav_source = 'reconstruction'
            series = {}
            for p in positions:
                ticker, qty = p['ticker'], (p.get('qty') or 0)
                if abs(qty) < 1e-9:
                    continue
                df, err = svc._fetch_daily_adjusted(ticker, nb_jours) if svc else (None, 'no svc')
                if df is None or df.empty:
                    continue
                series[ticker] = _tz_naive(df['adjClose'] * qty)
            if not series:
                return jsonify({'success': True, 'empty': True,
                                'message': 'Pas de prix historiques disponibles', 'summary': stats})
            nav_df = pd.DataFrame(series).sort_index().ffill().dropna(how='all')
            nav = nav_df.sum(axis=1).dropna()
            nav = nav[nav.index >= cutoff]

        nav = _tz_naive(nav)
        if len(nav) < 2:
            return jsonify({'success': True, 'empty': True,
                            'message': 'Historique insuffisant', 'summary': stats})

        # 2) Benchmark S&P 500 (SPY), rebasé sur la valeur initiale du portefeuille
        bench_series = None
        if svc:
            spy_df, _ = svc._fetch_daily_adjusted('SPY', nb_jours)
            if spy_df is not None and not spy_df.empty:
                spy = _tz_naive(spy_df['adjClose'].copy())
                spy = spy[spy.index >= cutoff]
                if len(spy) >= 2:
                    bench_series = (spy / spy.iloc[0]) * float(nav.iloc[0])

        # 3) Métriques — basées sur le TWR (Time-Weighted Return) qui neutralise
        # les dépôts/retraits. On chaîne les rendements quotidiens en mettant à 0
        # les jours de flux de capitaux (variation > 25% = dépôt/retrait, pas perf).
        raw_ret = nav.pct_change()
        FLOW_THRESHOLD = 0.25
        twr_ret = raw_ret.where(raw_ret.abs() <= FLOW_THRESHOLD, 0.0).fillna(0.0)
        twr_index = (1 + twr_ret).cumprod()            # base 1.0 au départ
        twr_index = twr_index / twr_index.iloc[0]

        daily_ret = twr_ret[twr_ret != 0.0]            # pour vol/sharpe (jours de marché)
        days = max(1, (nav.index[-1] - nav.index[0]).days)
        total_ret = float(twr_index.iloc[-1] - 1)
        cagr = float(twr_index.iloc[-1] ** (365.0 / days) - 1) if days >= 1 else 0.0
        vol_ann = float(daily_ret.std() * (252 ** 0.5)) if len(daily_ret) > 1 else 0.0
        rf = 0.04
        sharpe = float((cagr - rf) / vol_ann) if vol_ann > 1e-9 else 0.0
        # Drawdown sur l'indice TWR (neutralise les flux)
        cummax = twr_index.cummax()
        drawdown = (twr_index - cummax) / cummax
        max_dd = float(drawdown.min())

        # Bench CAGR pour comparaison
        bench_cagr = None
        if bench_series is not None and len(bench_series) >= 2:
            bd = max(1, (bench_series.index[-1] - bench_series.index[0]).days)
            bench_cagr = float((bench_series.iloc[-1] / bench_series.iloc[0]) ** (365.0 / bd) - 1)

        # 4) Rendements mensuels (heatmap) — depuis l'indice TWR
        monthly = twr_index.resample('ME').last().pct_change().dropna()
        monthly_returns = [
            {'year': idx.year, 'month': idx.month, 'return_pct': round(float(v) * 100, 2)}
            for idx, v in monthly.items()
        ]

        # 5) Séries pour les graphes
        def _fmt(s):
            return [{'date': idx.strftime('%Y-%m-%d'), 'value': round(float(v), 2)}
                    for idx, v in s.items()]

        # Courbe de drawdown (en %) depuis l'indice TWR
        drawdown_series = drawdown

        # Dividendes réels sur la période (Flex)
        from models import Dividend
        div_rows = Dividend.query.filter(Dividend.date >= cutoff.date()).all()
        dividends_total = round(sum(d.amount for d in div_rows), 2)
        dividends_by_period = {}
        for d in div_rows:
            key = d.date.strftime('%Y-%m')
            dividends_by_period[key] = round(dividends_by_period.get(key, 0) + d.amount, 2)

        return jsonify({
            'success': True,
            'range': range_key,
            'nav_source': nav_source,
            'kpis': {
                'total_value': stats.get('total_value', 0),
                'total_return_pct': round(total_ret * 100, 2),
                'cagr_pct': round(cagr * 100, 2),
                'bench_cagr_pct': round(bench_cagr * 100, 2) if bench_cagr is not None else None,
                'cagr_vs_bench_pct': round((cagr - bench_cagr) * 100, 2) if bench_cagr is not None else None,
                'sharpe': round(sharpe, 2),
                'vol_annual_pct': round(vol_ann * 100, 2),
                'max_drawdown_pct': round(max_dd * 100, 2),
                'unrealized_pnl': stats.get('total_unrealized_pnl', 0),
                'realized_pnl': stats.get('total_realized_pnl', 0),
                'dividends_total': dividends_total,
            },
            'dividends_by_period': [{'period': k, 'amount': v}
                                    for k, v in sorted(dividends_by_period.items())],
            'timeseries': {
                'portfolio': _fmt(nav),
                'benchmark': _fmt(bench_series) if bench_series is not None else [],
                # Performance TWR en % (neutralise dépôts/retraits) pour la perf relative
                'portfolio_twr_pct': [{'date': idx.strftime('%Y-%m-%d'), 'value': round((float(v) - 1) * 100, 2)}
                                      for idx, v in twr_index.items()],
            },
            'drawdown': [{'date': idx.strftime('%Y-%m-%d'), 'value': round(float(v) * 100, 2)}
                         for idx, v in drawdown.items()],
            'monthly_returns': monthly_returns,
            'positions': positions,
            'summary': stats,
        })
    except Exception as e:
        app.logger.exception('Erreur dans perf_dashboard')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/flex/credentials', methods=['POST'])
@require_admin
def flex_save_credentials():
    """Sauvegarde le token + query_id Flex (chiffrés). Body: { token, query_id }"""
    data = request.get_json() or {}
    token = (data.get('token') or '').strip()
    query_id = (data.get('query_id') or '').strip()
    if not token or not query_id:
        return jsonify({'success': False, 'error': 'token et query_id requis'}), 400
    secret = app.config.get('SECRET_KEY', '')
    Settings.set('flex_token_enc', encrypt_credential(token, secret))
    Settings.set('flex_query_id', query_id)
    return jsonify({'success': True, 'message': 'Identifiants Flex sauvegardés'})


@app.route('/api/flex/status', methods=['GET'])
def flex_status():
    """Statut Flex : configuré ? dernière synchro ? volumes importés."""
    from models import PortfolioSnapshot, Transaction, Dividend
    configured = bool(Settings.get('flex_token_enc') and Settings.get('flex_query_id'))
    return jsonify({
        'configured': configured,
        'last_sync': Settings.get('flex_last_sync'),
        'last_error': Settings.get('flex_last_error'),
        'snapshots': PortfolioSnapshot.query.count(),
        'transactions': Transaction.query.count(),
        'dividends': Dividend.query.count(),
    })


@app.route('/api/flex/sync', methods=['POST'])
@require_admin
def flex_sync():
    """
    Récupère le rapport Flex et importe NAV / transactions / dividendes en base.
    Données officielles IBKR (exactes).
    """
    from models import db, PortfolioSnapshot, Transaction, Dividend

    enc_token = Settings.get('flex_token_enc')
    query_id = Settings.get('flex_query_id')
    if not enc_token or not query_id:
        return jsonify({'success': False, 'error': 'Flex non configuré (token + query_id)'}), 400

    try:
        token = decrypt_credential(enc_token, app.config.get('SECRET_KEY', ''))
    except Exception:
        return jsonify({'success': False, 'error': 'Déchiffrement du token impossible'}), 500

    try:
        parsed = flex_service.fetch_and_parse(token, query_id)
    except Exception as e:
        Settings.set('flex_last_error', str(e)[:300])
        return jsonify({'success': False, 'error': f'Flex : {e}'}), 502

    nav_n = trade_n = div_n = 0

    # Toutes les lectures d'existence d'abord, puis les écritures — et on désactive
    # l'autoflush pour éviter qu'une query déclenche un flush prématuré (cause de
    # l'erreur de contrainte unique vue lors d'un import partiel).
    existing_snap = {s.date: s for s in PortfolioSnapshot.query.all()}
    existing_tx = {(t.date.date() if hasattr(t.date, 'date') else t.date, t.ticker,
                    round(t.quantity, 4), round(t.price, 4))
                   for t in Transaction.query.all()}
    existing_div = {(d.date, d.ticker, round(d.amount, 2)) for d in Dividend.query.all()}

    with db.session.no_autoflush:
        # NAV → PortfolioSnapshot. parsed['nav'] peut contenir plusieurs lignes
        # pour une même date → on déduplique (dernière valeur) avant insertion.
        nav_by_date = {}
        for row in parsed['nav']:
            nav_by_date[row['date']] = row['nav']
        for d, val in nav_by_date.items():
            if d in existing_snap:
                existing_snap[d].nav = val
            else:
                db.session.add(PortfolioSnapshot(date=d, nav=val))
                existing_snap[d] = True  # marquer pour éviter un doublon intra-batch
                nav_n += 1

        # Transactions (dédup par date+ticker+qty+price)
        for tr in parsed['trades']:
            key = (tr['date'], tr['ticker'], round(tr['quantity'], 4), round(tr['price'], 4))
            if key in existing_tx:
                continue
            existing_tx.add(key)
            db.session.add(Transaction(
                date=datetime.combine(tr['date'], datetime.min.time()),
                ticker=tr['ticker'], type=tr['type'], quantity=tr['quantity'],
                price=tr['price'], amount=tr['amount'], currency=tr['currency'],
            ))
            trade_n += 1

        # Dividendes (dédup par date+ticker+amount)
        for dv in parsed['dividends']:
            key = (dv['date'], dv['ticker'], round(dv['amount'], 2))
            if key in existing_div:
                continue
            existing_div.add(key)
            db.session.add(Dividend(date=dv['date'], ticker=dv['ticker'],
                                    amount=dv['amount'], currency=dv['currency']))
            div_n += 1

    db.session.commit()
    Settings.set('flex_last_sync', datetime.now().isoformat())
    Settings.set('flex_last_error', '')

    return jsonify({
        'success': True,
        'imported': {'snapshots': nav_n, 'transactions': trade_n, 'dividends': div_n},
        'totals': {
            'snapshots': PortfolioSnapshot.query.count(),
            'transactions': Transaction.query.count(),
            'dividends': Dividend.query.count(),
        },
        'account_id': parsed.get('account_id'),
    })


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
        result = ibkr_service.place_rebalance_orders(targets, dry_run=dry_run)
        orders = result['orders']
        placed = [o for o in orders if o.get('status') == 'placed']
        failed = [o for o in orders if o.get('status') == 'failed']
        return jsonify({
            'success': True,
            'dry_run': dry_run,
            'orders': orders,
            'count': len(orders),
            'placed_count': len(placed),
            'failed_count': len(failed),
            'total_target_pct': result.get('total_target_pct'),
        })
    except ConnectionError as e:
        return jsonify({'success': False, 'error': str(e)}), 503
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# SURVEILLANCE DU MARCHÉ — moteur d'évènements (anti-spam) & briefings
# =============================================================================

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
    """Construit le payload du briefing (régime, VIX, positions, news résumées)."""
    monitor = get_market_monitor()
    metrics = monitor.collect_metrics()

    stats, positions = None, []
    try:
        if ibkr_service.ensure_connected():
            s = ibkr_service.get_portfolio_stats()
            stats = {k: s.get(k) for k in ('total_value', 'total_pnl', 'return_pct', 'positions_count')}
            positions = s.get('positions', [])
    except Exception as e:
        print(f"⚠️ Briefing: positions indisponibles ({e})")

    tickers = [p['ticker'] for p in positions if p.get('ticker')]
    news_items, news_summary = [], ''
    try:
        ns = get_news_service()
        news_items = ns.fetch_news(tickers)
        regime = (metrics.get('regime') or {}).get('regime', '?')
        ctx = f"régime {regime}, VIX {metrics.get('vix')}"
        news_summary = ns.summarize(news_items, context=ctx, tickers=tickers)
    except Exception as e:
        print(f"⚠️ Briefing: news indisponibles ({e})")

    return {
        'session': session,
        'regime': metrics.get('regime'),
        'vix': metrics.get('vix'), 'vix_pct': metrics.get('vix_pct'),
        'stats': stats, 'positions': positions,
        'news_summary': news_summary, 'news_items': news_items,
    }


# =============================================================================
# ROUTES - API MARCHÉ (pulse, évènements, seuils, déclencheurs de test)
# =============================================================================

@app.route('/api/market/pulse', methods=['GET'])
def market_pulse():
    """État courant du marché et du portefeuille (pour le bandeau live)."""
    try:
        metrics = get_market_monitor().collect_metrics()
        return jsonify(metrics)
    except Exception as e:
        return jsonify({'error': str(e), 'connected': False}), 500


@app.route('/api/market/events', methods=['GET'])
def market_events():
    """Liste des évènements de marché (status=open|all)."""
    status = request.args.get('status', 'all')
    q = MarketEvent.query
    if status == 'open':
        q = q.filter(MarketEvent.ended_at.is_(None))
    events = q.order_by(MarketEvent.started_at.desc()).limit(100).all()
    return jsonify({'count': len(events), 'events': [e.to_dict() for e in events]})


@app.route('/api/market/thresholds', methods=['GET', 'POST'])
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


@app.route('/api/market/monitor/run', methods=['POST'])
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


@app.route('/api/briefing/send', methods=['POST'])
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


# =============================================================================
# TÂCHES PLANIFIÉES
# =============================================================================

def job_market_monitor():
    """Cron minute (séance US) : surveille le marché et gère les alertes."""
    with app.app_context():
        try:
            from zoneinfo import ZoneInfo
            from datetime import time as dtime
            now_et = datetime.now(ZoneInfo('America/New_York'))
            if not (dtime(9, 30) <= now_et.time() < dtime(16, 0)):
                return  # hors séance régulière
            result = run_market_monitor()
            if result['opened'] or result['closed']:
                print(f"[{datetime.now()}] 🔔 Monitor: {len(result['opened'])} ouverte(s), "
                      f"{result['closed']} clôturée(s)")
        except Exception as e:
            print(f"❌ job_market_monitor: {e}")


def job_briefing(session='open'):
    """Cron briefing (ouverture / mi-séance / clôture)."""
    with app.app_context():
        print(f"[{datetime.now()}] 📨 Briefing '{session}'…")
        try:
            payload = build_briefing_payload(session)
            email_svc = get_email_service()
            if email_svc.is_configured():
                res = email_svc.envoyer_briefing(payload)
                print(f"{'✅' if res['success'] else '❌'} Briefing {session}: {res['message']}")
            else:
                print("⚠️ Service email non configuré")
        except Exception as e:
            print(f"❌ job_briefing: {e}")


def job_refresh_prices():
    """Cron nuit : rafraîchit le cache de prix (benchmark ^GSPC + panel)."""
    with app.app_context():
        print(f"[{datetime.now()}] 📈 Rafraîchissement du cache de prix…")
        try:
            if not ibkr_service.ensure_connected():
                print("⚠️ IBKR indisponible — cache non rafraîchi")
                return
            from models import MarketPriceBar
            from datetime import date

            bars = ibkr_service.get_daily_bars('^GSPC', duration='2 Y')
            count = 0
            for b in bars:
                bar_date = date.fromisoformat(b['date'])
                if not MarketPriceBar.query.filter_by(ticker='^GSPC', bar_date=bar_date).first():
                    db.session.add(MarketPriceBar(
                        ticker='^GSPC', bar_date=bar_date,
                        adj_close=b['adj_close'], close=b['close'], source='ibkr'))
                    count += 1
            db.session.commit()
            print(f"✅ Benchmark ^GSPC: {count} nouvelles barres")

            # Réchauffe le cache de prix du panel (persiste les barres côté service)
            service = get_momentum_service()
            actions = PanelAction.query.filter_by(is_active=True).all()
            panel = [a.ticker for a in actions]
            if service and panel:
                try:
                    service.analyser_panel(panel, None)
                    print(f"✅ Cache panel réchauffé ({len(panel)} tickers)")
                except Exception as e:
                    print(f"⚠️ Réchauffe panel: {e}")
        except Exception as e:
            print(f"❌ job_refresh_prices: {e}")


# Initialiser le scheduler (1 worker gunicorn + threads → pas de double-firing)
scheduler = BackgroundScheduler(job_defaults={
    'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 60,
})
ET = 'America/New_York'

# 1) Surveillance marché : chaque minute en séance (garde interne 9h30–16h00)
scheduler.add_job(
    job_market_monitor,
    CronTrigger(day_of_week='mon-fri', hour='9-16', minute='*', timezone=ET),
    id='market_monitor', name='Surveillance marché (minute)', replace_existing=True,
)

# 2) Briefings : ouverture 9h35, mi-séance 12h30, clôture 16h05 ET
scheduler.add_job(
    functools.partial(job_briefing, 'open'),
    CronTrigger(day_of_week='mon-fri', hour=9, minute=35, timezone=ET),
    id='briefing_open', name="Briefing d'ouverture", replace_existing=True,
)
scheduler.add_job(
    functools.partial(job_briefing, 'mid'),
    CronTrigger(day_of_week='mon-fri', hour=12, minute=30, timezone=ET),
    id='briefing_mid', name='Briefing mi-séance', replace_existing=True,
)
scheduler.add_job(
    functools.partial(job_briefing, 'close'),
    CronTrigger(day_of_week='mon-fri', hour=16, minute=5, timezone=ET),
    id='briefing_close', name='Briefing de clôture', replace_existing=True,
)

# 3) Rappel mensuel de rééquilibrage : 1er du mois à 8h00 ET
scheduler.add_job(
    job_rebalance_reminder,
    CronTrigger(day=1, hour=8, minute=0, timezone=ET),
    id='rebalance_reminder', name='Rappel mensuel de rééquilibrage', replace_existing=True,
)

# 4) Rafraîchissement du cache de prix : chaque soir 22h00 ET (lun-ven)
scheduler.add_job(
    job_refresh_prices,
    CronTrigger(day_of_week='mon-fri', hour=22, minute=0, timezone=ET),
    id='refresh_prices', name='Rafraîchissement cache de prix', replace_existing=True,
)

scheduler.start()
print("[scheduler] demarre - 4 crons actifs :")
print("  - Surveillance marche : chaque minute (9h30-16h00 ET, lun-ven)")
print("  - Briefings : 9h35 / 12h30 / 16h05 ET (lun-ven)")
print("  - Rappel reequilibrage : 1er du mois 8h00 ET")
print("  - Cache de prix : 22h00 ET (lun-ven)")


# =============================================================================
# POINT D'ENTRÉE
# =============================================================================

if __name__ == '__main__':
    # Lancer l'application en mode développement
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=app.config.get('DEBUG', False))

