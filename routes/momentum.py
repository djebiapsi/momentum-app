# -*- coding: utf-8 -*-
"""Routes calcul momentum, historique, benchmark, régime."""
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
from core import _run_long_calculation, _momentum_csv_response

bp = Blueprint('momentum', __name__)


@bp.route('/api/calculate', methods=['POST'])
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
        current_app.logger.exception('Erreur inattendue dans calculate_momentum')
        return jsonify({'error': f'Erreur inattendue : {type(e).__name__}: {e}'}), 500

    return jsonify({'success': True, 'history_id': history.id, **recommandations})


@bp.route('/api/calculate-and-notify', methods=['POST'])
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
        current_app.logger.exception('Erreur inattendue dans calculate_and_notify')
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

@bp.route('/api/history', methods=['GET'])
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


@bp.route('/api/history/<int:history_id>', methods=['GET'])
def get_history_detail(history_id):
    """Récupère les détails d'une recommandation passée"""
    history = RecommendationHistory.query.get_or_404(history_id)
    return jsonify(history.to_dict())


@bp.route('/api/history/latest', methods=['GET'])
def get_latest():
    """Récupère la dernière recommandation"""
    history = RecommendationHistory.query\
        .order_by(RecommendationHistory.created_at.desc())\
        .first()
    
    if not history:
        return jsonify({'message': 'Aucune recommandation disponible'}), 404

    return jsonify(history.to_dict())


@bp.route('/api/history/latest/download', methods=['GET'])
def download_latest_momentum():
    """Télécharge le dernier calcul de momentum au format CSV."""
    history = RecommendationHistory.query\
        .order_by(RecommendationHistory.created_at.desc())\
        .first()
    if not history:
        return jsonify({'message': 'Aucune recommandation disponible'}), 404
    return _momentum_csv_response(history)


@bp.route('/api/history/<int:history_id>/download', methods=['GET'])
def download_momentum(history_id):
    """Télécharge un calcul de momentum précis au format CSV."""
    history = RecommendationHistory.query.get_or_404(history_id)
    return _momentum_csv_response(history)


# =============================================================================
# ROUTES - API EMAIL
# =============================================================================

@bp.route('/api/benchmark', methods=['GET'])
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


@bp.route('/api/market-regime', methods=['GET'])
def get_market_regime():
    """Régime de marché SPY/SMA200 — appelé au chargement du dashboard."""
    service = get_momentum_service()
    if not service:
        return jsonify({'regime': 'UNKNOWN', 'error': 'API Tiingo non configurée'})
    return jsonify(service.get_market_regime())



