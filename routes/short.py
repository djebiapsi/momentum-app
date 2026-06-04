# -*- coding: utf-8 -*-
"""Routes stratégie Short (panel, calcul, historique, screener)."""
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

bp = Blueprint('short', __name__)


@bp.route('/api/short/panel', methods=['GET'])
def get_short_panel():
    """Récupère la liste des actions du panel Short"""
    actions = ShortPanelAction.query.filter_by(is_active=True).all()
    return jsonify({
        'count': len(actions),
        'actions': [a.to_dict() for a in actions]
    })


@bp.route('/api/short/panel', methods=['POST'])
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


@bp.route('/api/short/panel/<ticker>', methods=['DELETE'])
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


@bp.route('/api/short/panel/clear', methods=['DELETE'])
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

@bp.route('/api/short/calculate', methods=['POST'])
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
    nb_top = int(Settings.get('short_nb_top', current_app.config.get('DEFAULT_NB_TOP', 5)))
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

@bp.route('/api/short/history', methods=['GET'])
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


@bp.route('/api/short/history/<int:history_id>', methods=['GET'])
def get_short_history_detail(history_id):
    """Récupère les détails d'une recommandation Short passée"""
    history = ShortRecommendationHistory.query.get_or_404(history_id)
    return jsonify(history.to_dict())


@bp.route('/api/short/history/latest', methods=['GET'])
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

@bp.route('/api/short/settings', methods=['GET'])
def get_short_settings():
    """Récupère les paramètres Short"""
    nb_top = Settings.get('short_nb_top', current_app.config.get('DEFAULT_NB_TOP', 5))
    date_calcul = Settings.get('short_date_calcul', '')
    
    return jsonify({
        'nb_top': int(nb_top),
        'date_calcul': date_calcul
    })


@bp.route('/api/short/settings', methods=['POST'])
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

@bp.route('/api/short/screener/generate', methods=['POST'])
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


@bp.route('/api/short/screener/apply', methods=['POST'])
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

