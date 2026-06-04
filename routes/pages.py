# -*- coding: utf-8 -*-
"""Route page principale."""
import json
from datetime import datetime
from flask import Blueprint, jsonify, request, make_response, current_app, render_template
from models import (db, Settings, PanelAction, RecommendationHistory,
                    RecommendationDetail, ShortPanelAction,
                    ShortRecommendationHistory, ShortRecommendationDetail,
                    OptionRecommendation, MarketEvent)
from auth import require_admin
from services import (ibkr_service, get_momentum_service, get_email_service,
                      get_news_service, get_market_monitor, get_screener_service,
                      get_short_screener_service, get_finviz_screener_service,
                      get_options_service)

bp = Blueprint('pages', __name__)


@bp.route('/')
def index():
    """Page principale de l'application"""
    return render_template('index.html')


# =============================================================================
# ROUTES - API SETTINGS
# =============================================================================

