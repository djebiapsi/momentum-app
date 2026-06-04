# -*- coding: utf-8 -*-
"""Routes panel Long + screener Long."""
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

bp = Blueprint('panel', __name__)


@bp.route('/api/panel', methods=['GET'])
def get_panel():
    """Récupère la liste des actions du panel"""
    actions = PanelAction.query.filter_by(is_active=True).all()
    return jsonify({
        'count': len(actions),
        'actions': [a.to_dict() for a in actions]
    })


@bp.route('/api/panel', methods=['POST'])
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


@bp.route('/api/panel/<ticker>', methods=['DELETE'])
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


@bp.route('/api/panel/clear', methods=['DELETE'])
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


@bp.route('/api/panel/export', methods=['GET'])
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


@bp.route('/api/panel/import', methods=['POST'])
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

@bp.route('/api/screener/generate', methods=['POST'])
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


@bp.route('/api/screener/apply', methods=['POST'])
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

@bp.route('/api/screener/finviz/generate', methods=['POST'])
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

