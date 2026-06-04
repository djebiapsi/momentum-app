# -*- coding: utf-8 -*-
"""Routes options (PUT & PUT SPREAD)."""
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
from options_service import estimate_historical_volatility

bp = Blueprint('options', __name__)


@bp.route('/api/options/calculate', methods=['POST'])
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


@bp.route('/api/options/recommendation/<ticker>', methods=['GET'])
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


@bp.route('/api/options/saved', methods=['GET'])
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


@bp.route('/api/options/bulk-recommendations', methods=['GET'])
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


@bp.route('/api/options/quick-calc', methods=['POST'])
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

