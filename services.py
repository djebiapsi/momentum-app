# -*- coding: utf-8 -*-
"""Registre des services (singletons) — extrait de app.py.
Les accesseurs lisent la config via current_app (contexte requête/app)."""
import logging
logger = logging.getLogger(__name__)
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
from backtest_service import BacktestService
from price_data_service import PriceDataService
from fundamentals_collector import FundamentalsCollector
from finra_collector import FinraCollector
from edgar_collector import EdgarCollector
from short_signal_service import ShortSignalService
from fundamental_screen_service import FundamentalScreenService


# Services (singletons initialisés à la demande)
momentum_service = None

# Cache des dernières positions connues (évite les timeouts du briefing)
_positions_cache: list = []
_positions_cache_ts: float = 0.0
_POSITIONS_CACHE_TTL = 1800  # 30 min
email_service = None
screener_service = None
short_screener_service = None
finviz_screener_service = None
market_monitor_service = None
news_service = None
backtest_service = None
price_data_service = None
fundamentals_collector = None
finra_collector = None
edgar_collector = None
short_signal_service = None
fundamental_screen_service = None

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


def get_cached_positions() -> list:
    """
    Retourne les dernières positions IBKR connues.
    Tente d'abord une requête live ; si timeout/erreur, retourne le cache (≤30 min).
    Permet au briefing d'avoir des données même si les quotes TWS tardent.
    """
    import time
    global _positions_cache, _positions_cache_ts
    try:
        if ibkr_service.ensure_connected():
            stats = ibkr_service.get_portfolio_stats()
            positions = stats.get('positions', [])
            if positions:
                _positions_cache = positions
                _positions_cache_ts = time.time()
            return positions
    except Exception as e:
        logger.warning('get_cached_positions: live failed (%s)', e)

    age = time.time() - _positions_cache_ts
    if _positions_cache and age < _POSITIONS_CACHE_TTL:
        logger.info('get_cached_positions: utilise cache (âge=%.0fs)', age)
        return _positions_cache
    return []


def get_options_service():
    """Retourne une instance du service Options."""
    return OptionsService(risk_free_rate=0.05)


def get_backtest_service():
    """Récupère ou crée le service de backtest (momentum + screener Tiingo)."""
    global backtest_service
    if backtest_service is None:
        backtest_service = BacktestService(
            momentum_service=get_momentum_service(),
            screener_service=get_screener_service(),
        )
    return backtest_service


def get_price_data_service():
    """Récupère ou crée le service de collecte de prix yfinance (SP500/NDX100)."""
    global price_data_service
    if price_data_service is None:
        price_data_service = PriceDataService(email_service=get_email_service())
    return price_data_service


def get_fundamentals_collector():
    """Récupère ou crée le collecteur de fondamentaux yfinance (stratégie short)."""
    global fundamentals_collector
    if fundamentals_collector is None:
        fundamentals_collector = FundamentalsCollector(email_service=get_email_service())
    return fundamentals_collector


def get_finra_collector():
    """Récupère ou crée le collecteur de short interest FINRA (stratégie short)."""
    global finra_collector
    if finra_collector is None:
        finra_collector = FinraCollector()
    return finra_collector


def get_edgar_collector():
    """Récupère ou crée le collecteur de fondamentaux longs SEC EDGAR."""
    global edgar_collector
    if edgar_collector is None:
        edgar_collector = EdgarCollector(email_service=get_email_service())
    return edgar_collector


def get_short_signal_service():
    """Récupère ou crée le service de signal short live (scoring multi-facteurs)."""
    global short_signal_service
    if short_signal_service is None:
        short_signal_service = ShortSignalService()
    return short_signal_service


def get_fundamental_screen_service():
    """Récupère ou crée le service de screen fondamental Quality-Value (long)."""
    global fundamental_screen_service
    if fundamental_screen_service is None:
        fundamental_screen_service = FundamentalScreenService()
    return fundamental_screen_service


