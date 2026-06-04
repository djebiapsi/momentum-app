# -*- coding: utf-8 -*-
"""Registre des services (singletons) — extrait de app.py.
Les accesseurs lisent la config via current_app (contexte requête/app)."""
from flask import current_app
from config import get_config
from momentum_service import MomentumService
from email_service import EmailService
from screener_service import ScreenerService
from short_screener_service import ShortScreenerService
from finviz_screener_service import FinvizScreenerService
from options_service import OptionsService
from ibkr_service import IBKRService
from news_service import NewsService
from market_monitor_service import MarketMonitorService


# Services (singletons initialisés à la demande)
momentum_service = None
email_service = None
screener_service = None
short_screener_service = None
finviz_screener_service = None
market_monitor_service = None
news_service = None

# Service IBKR — démarre une boucle asyncio dans un thread dédié.
# Config lue depuis la classe Config (import-time, sans contexte d'app).
_cfg = get_config()
ibkr_service = IBKRService(
    host=getattr(_cfg, 'IB_GATEWAY_HOST', 'ib-gateway'),
    port=getattr(_cfg, 'IB_GATEWAY_PORT', 4001),
)


def get_momentum_service():
    """Récupère ou crée le service momentum (Tiingo + IBKR multi-source)"""
    global momentum_service
    if momentum_service is None:
        api_key = current_app.config.get('TIINGO_API_KEY')
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
            api_key=current_app.config.get('RESEND_API_KEY'),
            from_email=current_app.config.get('EMAIL_FROM'),
            to_email=current_app.config.get('EMAIL_TO')
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
        api_key = current_app.config.get('TIINGO_API_KEY')
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


def get_options_service():
    """Retourne une instance du service Options."""
    return OptionsService(risk_free_rate=0.05)


