# -*- coding: utf-8 -*-
"""
Modèles de base de données
==========================
Définit les tables pour stocker les configurations et l'historique.
"""

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Settings(db.Model):
    """
    Table des paramètres de l'application.
    Stocke la configuration modifiable par l'utilisateur.
    """
    __tablename__ = 'settings'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @classmethod
    def get(cls, key, default=None):
        """Récupère une valeur de configuration"""
        setting = cls.query.filter_by(key=key).first()
        return setting.value if setting else default
    
    @classmethod
    def set(cls, key, value):
        """Définit une valeur de configuration"""
        setting = cls.query.filter_by(key=key).first()
        if setting:
            setting.value = str(value)
        else:
            setting = cls(key=key, value=str(value))
            db.session.add(setting)
        db.session.commit()


class PanelAction(db.Model):
    """
    Table du panel d'actions à suivre.
    Chaque ligne représente un ticker à analyser.
    """
    __tablename__ = 'panel_actions'
    
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(100))  # Nom de l'entreprise (optionnel)
    strategy_type = db.Column(db.String(10), default='long', server_default='long', nullable=False)  # 'long' par défaut
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'ticker': self.ticker,
            'name': self.name,
            'strategy_type': self.strategy_type,
            'added_at': self.added_at.isoformat() if self.added_at else None,
            'is_active': self.is_active
        }


class RecommendationHistory(db.Model):
    """
    Table de l'historique des recommandations.
    Stocke chaque mise à jour mensuelle complète.
    """
    __tablename__ = 'recommendation_history'
    
    id = db.Column(db.Integer, primary_key=True)
    calculation_date = db.Column(db.DateTime, nullable=False)  # Date du calcul
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    nb_top = db.Column(db.Integer, default=5)  # Nombre de top actions à ce moment
    market_regime = db.Column(db.Text)  # Régime de marché (JSON) au moment du calcul

    # Relation avec les détails
    details = db.relationship('RecommendationDetail', backref='history', lazy=True,
                              cascade='all, delete-orphan')

    def to_dict(self):
        import json
        regime = None
        if self.market_regime:
            try:
                regime = json.loads(self.market_regime)
            except Exception:
                regime = None
        return {
            'id': self.id,
            'calculation_date': self.calculation_date.strftime('%Y-%m-%d'),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'nb_top': self.nb_top,
            'market_regime': regime,
            'details': [d.to_dict() for d in self.details]
        }


class RecommendationDetail(db.Model):
    """
    Table des détails de chaque recommandation.
    Une ligne par action pour chaque calcul.
    """
    __tablename__ = 'recommendation_details'
    
    id = db.Column(db.Integer, primary_key=True)
    history_id = db.Column(db.Integer, db.ForeignKey('recommendation_history.id'), nullable=False)
    ticker = db.Column(db.String(10), nullable=False)
    momentum = db.Column(db.Float, nullable=False)
    signal = db.Column(db.String(20), nullable=False)  # Investir, Sortir, Cash
    allocation = db.Column(db.Float, default=0.0)
    rank = db.Column(db.Integer)  # Position dans le classement
    perf_recent_1m = db.Column(db.Float)       # Perf du mois exclu (mean-reversion)
    vol_annualisee = db.Column(db.Float)       # Volatilité annualisée (%)
    details_mensuels = db.Column(db.Text)      # Historique mensuel (JSON)

    def to_dict(self):
        import json
        dm = None
        if self.details_mensuels:
            try:
                dm = json.loads(self.details_mensuels)
            except Exception:
                dm = None
        return {
            'ticker': self.ticker,
            'momentum': round(self.momentum, 2),
            'signal': self.signal,
            'allocation': self.allocation,
            'rank': self.rank,
            'perf_recent_1m': self.perf_recent_1m,
            'vol_annualisee': self.vol_annualisee,
            'details_mensuels': dm,
        }


# =============================================================================
# MODÈLES POUR STRATÉGIE SHORT
# =============================================================================

class ShortPanelAction(db.Model):
    """
    Table du panel d'actions pour la stratégie Short.
    Séparé du panel Long pour une gestion indépendante.
    """
    __tablename__ = 'short_panel_actions'
    
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(100))  # Nom de l'entreprise (optionnel)
    sector = db.Column(db.String(50))  # Secteur
    perf_year = db.Column(db.Float)   # Performance annuelle au moment de l'ajout
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'ticker': self.ticker,
            'name': self.name,
            'sector': self.sector,
            'perf_year': self.perf_year,
            'added_at': self.added_at.isoformat() if self.added_at else None,
            'is_active': self.is_active
        }


class ShortRecommendationHistory(db.Model):
    """
    Table de l'historique des recommandations Short.
    Stocke chaque mise à jour mensuelle pour la stratégie Short.
    """
    __tablename__ = 'short_recommendation_history'
    
    id = db.Column(db.Integer, primary_key=True)
    calculation_date = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    nb_top = db.Column(db.Integer, default=5)  # Nombre de top actions Short
    
    # Relation avec les détails
    details = db.relationship('ShortRecommendationDetail', backref='history', lazy=True,
                              cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'calculation_date': self.calculation_date.strftime('%Y-%m-%d'),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'nb_top': self.nb_top,
            'details': [d.to_dict() for d in self.details]
        }


class ShortRecommendationDetail(db.Model):
    """
    Table des détails de chaque recommandation Short.
    Une ligne par action pour chaque calcul Short.
    """
    __tablename__ = 'short_recommendation_details'
    
    id = db.Column(db.Integer, primary_key=True)
    history_id = db.Column(db.Integer, db.ForeignKey('short_recommendation_history.id'), nullable=False)
    ticker = db.Column(db.String(10), nullable=False)
    momentum = db.Column(db.Float, nullable=False)  # Momentum (négatif pour Short)
    signal = db.Column(db.String(20), nullable=False)  # Shorter, Couvrir
    allocation = db.Column(db.Float, default=0.0)
    rank = db.Column(db.Integer)  # Position dans le classement (1 = plus forte baisse)
    
    def to_dict(self):
        return {
            'ticker': self.ticker,
            'momentum': round(self.momentum, 2),
            'signal': self.signal,
            'allocation': self.allocation,
            'rank': self.rank
        }


class OptionRecommendation(db.Model):
    """
    Table des recommandations d'options (PUT/PUT SPREAD).
    Stocke les dernières recommandations calculées.
    """
    __tablename__ = 'option_recommendations'
    
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(10), nullable=False)
    calculation_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Données du sous-jacent
    spot_price = db.Column(db.Float)
    iv_pct = db.Column(db.Float)
    
    # Momentum
    momentum_score = db.Column(db.Float)
    perf_63_5 = db.Column(db.Float)
    perf_5_0 = db.Column(db.Float)
    
    # Signal
    signal = db.Column(db.String(50))
    all_conditions_met = db.Column(db.Boolean, default=False)
    recommended_strategy = db.Column(db.String(20))
    rank = db.Column(db.Integer)
    
    # PUT simple
    put_strike = db.Column(db.Float)
    put_price = db.Column(db.Float)
    put_delta = db.Column(db.Float)
    
    # PUT SPREAD
    spread_strike_long = db.Column(db.Float)
    spread_strike_short = db.Column(db.Float)
    spread_net_debit = db.Column(db.Float)
    spread_max_profit = db.Column(db.Float)
    spread_breakeven = db.Column(db.Float)
    spread_risk_reward = db.Column(db.Float)
    spread_delta_long = db.Column(db.Float)
    spread_delta_short = db.Column(db.Float)
    
    # Expiration
    dte = db.Column(db.Integer)
    expiration_date = db.Column(db.String(20))
    
    def to_dict(self):
        return {
            'id': self.id,
            'ticker': self.ticker,
            'calculation_date': self.calculation_date.isoformat() if self.calculation_date else None,
            'spot_price': self.spot_price,
            'iv_pct': self.iv_pct,
            'momentum_score': self.momentum_score,
            'perf_63_5': self.perf_63_5,
            'perf_5_0': self.perf_5_0,
            'signal': self.signal,
            'all_conditions_met': self.all_conditions_met,
            'recommended_strategy': self.recommended_strategy,
            'rank': self.rank,
            'put': {
                'strike': self.put_strike,
                'price': self.put_price,
                'delta': self.put_delta
            },
            'put_spread': {
                'strike_long': self.spread_strike_long,
                'strike_short': self.spread_strike_short,
                'net_debit': self.spread_net_debit,
                'max_profit': self.spread_max_profit,
                'breakeven': self.spread_breakeven,
                'risk_reward_ratio': self.spread_risk_reward,
                'delta_long_actual': self.spread_delta_long,
                'delta_short_actual': self.spread_delta_short
            },
            'dte': self.dte,
            'expiration_date': self.expiration_date
        }


class PortfolioSnapshot(db.Model):
    """
    Instantané quotidien de la valeur du portefeuille.
    Permet de tracer l'évolution de la NAV et du capital investi.
    """
    __tablename__ = 'portfolio_snapshots'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False, index=True)
    nav = db.Column(db.Float, nullable=False)
    cash = db.Column(db.Float)
    invested_capital = db.Column(db.Float)  # Dépôts cumulés - Retraits
    
    def to_dict(self):
        return {
            'date': self.date.isoformat(),
            'nav': self.nav,
            'cash': self.cash,
            'invested_capital': self.invested_capital
        }


class Transaction(db.Model):
    """
    Historique des transactions (achats, ventes).
    Essentiel pour le calcul du P&L réalisé et de la contribution par position.
    """
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False, index=True)
    ticker = db.Column(db.String(12), nullable=False, index=True)
    type = db.Column(db.String(10), nullable=False)  # 'BUY', 'SELL'
    quantity = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    amount = db.Column(db.Float)  # Montant total (qty * price + frais)
    currency = db.Column(db.String(3), default='USD')
    
    def to_dict(self):
        return {
            'date': self.date.isoformat(),
            'ticker': self.ticker,
            'type': self.type,
            'quantity': self.quantity,
            'price': self.price,
            'amount': self.amount,
            'currency': self.currency
        }


class Dividend(db.Model):
    """
    Historique des dividendes perçus.
    """
    __tablename__ = 'dividends'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    ticker = db.Column(db.String(12), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD')
    
    def to_dict(self):
        return {
            'date': self.date.isoformat(),
            'ticker': self.ticker,
            'amount': self.amount,
            'currency': self.currency
        }


class MarketPriceBar(db.Model):
    """
    Historique des prix de marché (barres journalières ajustées).
    Persiste les données récupérées via IBKR ou Tiingo pour éviter les
    appels API redondants et garder un historique fiable.

    On stocke les barres JOURNALIÈRES ajustées (ADJUSTED_LAST). Le momentum
    mensuel 12-1 est calculé en resamplant ces barres côté service.
    """
    __tablename__ = 'market_price_bars'

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(12), nullable=False, index=True)
    bar_date = db.Column(db.Date, nullable=False)
    adj_close = db.Column(db.Float, nullable=False)
    close = db.Column(db.Float)
    volume = db.Column(db.Float)  # volume journalier (pour reconstruire l'ADV au backtest)
    low = db.Column(db.Float)    # plus-bas intraday (pour appels de marge réalistes)
    high = db.Column(db.Float)   # plus-haut intraday
    source = db.Column(db.String(10), nullable=False)  # 'ibkr' | 'tiingo' | 'yfinance'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('ticker', 'bar_date', name='uq_ticker_bar_date'),
        db.Index('ix_ticker_date', 'ticker', 'bar_date'),
    )

    def to_dict(self):
        return {
            'ticker': self.ticker,
            'date': self.bar_date.isoformat() if self.bar_date else None,
            'adj_close': self.adj_close,
            'close': self.close,
            'volume': self.volume,
            'source': self.source,
        }


class MonthlyPriceBar(db.Model):
    """
    Historique des prix MENSUELS ajustés (jusqu'à ~20 ans).
    Alimentée par la collecte yfinance nocturne. Sert de base longue au calcul
    du momentum 12-1 (le daily ne couvre que ~6 ans).

    Une barre = dernière séance du mois (yfinance interval='1mo'). bar_date est
    normalisée au 1er du mois pour un upsert stable.
    """
    __tablename__ = 'monthly_price_bars'

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(12), nullable=False, index=True)
    bar_date = db.Column(db.Date, nullable=False)  # 1er du mois (clé de mois)
    adj_close = db.Column(db.Float, nullable=False)
    close = db.Column(db.Float)
    volume = db.Column(db.Float)
    source = db.Column(db.String(10), nullable=False, default='yfinance')  # 'yfinance'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('ticker', 'bar_date', name='uq_monthly_ticker_bar_date'),
        db.Index('ix_monthly_ticker_date', 'ticker', 'bar_date'),
    )

    def to_dict(self):
        return {
            'ticker': self.ticker,
            'date': self.bar_date.isoformat() if self.bar_date else None,
            'adj_close': self.adj_close,
            'close': self.close,
            'volume': self.volume,
            'source': self.source,
        }


class IndexConstituent(db.Model):
    """
    Composition des indices (S&P 500 / Nasdaq-100) pour la collecte de prix.

    Rafraîchie ~1×/mois (scraping Wikipédia avec repli codé en dur). is_active=False
    marque un titre sorti de l'indice (on garde l'historique de prix déjà collecté).
    """
    __tablename__ = 'index_constituents'

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(12), nullable=False)
    index_name = db.Column(db.String(12), nullable=False)  # 'SP500' | 'NDX100'
    name = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow)  # dernier scrape où vu

    __table_args__ = (
        db.UniqueConstraint('ticker', 'index_name', name='uq_constituent_ticker_index'),
    )

    def to_dict(self):
        return {
            'ticker': self.ticker,
            'index_name': self.index_name,
            'name': self.name,
            'is_active': self.is_active,
        }


class IndexMembership(db.Model):
    """
    Historique point-in-time d'appartenance aux indices (S&P 500 / Nasdaq-100).

    Une ligne = un intervalle de présence [start_date, end_date) d'un ticker dans
    un indice. start_date NULL = membre depuis avant le début de l'historique
    Wikipédia ; end_date NULL = toujours membre aujourd'hui. Un ticker peut avoir
    plusieurs intervalles (entrées/sorties multiples de l'indice).

    Reconstruite (delete + insert, idempotent) par
    PriceDataService.rebuild_membership_history() depuis la table « Selected
    changes » de Wikipédia. Lue par le backtest pour filtrer l'univers
    point-in-time et réduire le biais de survivance.
    """
    __tablename__ = 'index_memberships'

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(12), nullable=False, index=True)
    index_name = db.Column(db.String(12), nullable=False)  # 'SP500' | 'NDX100'
    start_date = db.Column(db.Date, nullable=True)   # NULL = avant l'historique connu
    end_date = db.Column(db.Date, nullable=True)     # NULL = toujours membre
    source = db.Column(db.String(20), default='wikipedia')  # 'wikipedia' | 'current'

    def to_dict(self):
        return {
            'ticker': self.ticker,
            'index_name': self.index_name,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'source': self.source,
        }


class CashFlow(db.Model):
    """
    Flux de capitaux (dépôts / retraits) extraits du rapport Flex IBKR.
    Permet de visualiser l'historique des apports sur le graphique de performance.
    """
    __tablename__ = 'cash_flows'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)  # positif = dépôt, négatif = retrait
    description = db.Column(db.String(200))
    currency = db.Column(db.String(3), default='USD')

    __table_args__ = (
        db.Index('ix_cash_flow_date', 'date'),
    )

    def to_dict(self):
        return {
            'date': self.date.isoformat(),
            'amount': self.amount,
            'description': self.description,
            'currency': self.currency,
        }


class MarketEvent(db.Model):
    """
    Évènement de marché détecté par le moniteur (cron chaque minute).

    Un évènement représente UN épisode continu où une métrique dépasse son seuil
    (ex: VIX > 35, drawdown SPY < -4%). Il a un instant de début (started_at) et
    de fin (ended_at, NULL tant que la condition reste vraie). Cette structure
    évite le spam : tant que l'épisode dure, on ne crée pas de doublon.
    """
    __tablename__ = 'market_events'

    id = db.Column(db.Integer, primary_key=True)
    # VIX_HIGH | VIX_SPIKE | SPY_DRAWDOWN | PORTFOLIO_DRAWDOWN | POSITION_DROP | IBKR_DOWN
    event_type = db.Column(db.String(32), nullable=False, index=True)
    ticker = db.Column(db.String(12), nullable=True)  # pour POSITION_DROP
    severity = db.Column(db.String(10), nullable=False, default='warning')  # warning | critical
    threshold = db.Column(db.Float)        # seuil franchi
    trigger_value = db.Column(db.Float)    # valeur à l'ouverture
    peak_value = db.Column(db.Float)       # valeur la plus extrême vue pendant l'épisode
    message = db.Column(db.Text)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ended_at = db.Column(db.DateTime, nullable=True, index=True)  # NULL = épisode en cours
    last_checked_at = db.Column(db.DateTime, default=datetime.utcnow)
    notified_open = db.Column(db.Boolean, default=False)
    notified_close = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.Index('ix_event_open', 'event_type', 'ticker', 'ended_at'),
    )

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    def to_dict(self):
        duration_min = None
        end = self.ended_at or datetime.utcnow()
        if self.started_at:
            duration_min = round((end - self.started_at).total_seconds() / 60.0, 1)
        return {
            'id': self.id,
            'event_type': self.event_type,
            'ticker': self.ticker,
            'severity': self.severity,
            'threshold': self.threshold,
            'trigger_value': self.trigger_value,
            'peak_value': self.peak_value,
            'message': self.message,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'ended_at': self.ended_at.isoformat() if self.ended_at else None,
            'is_open': self.is_open,
            'duration_min': duration_min,
        }


class FundamentalSnapshot(db.Model):
    """
    États financiers trimestriels et annuels par ticker (source : yfinance).

    Une ligne = une période de reporting (Q ou A) pour un ticker.
    Fréquence de collecte : nocturne incrémentale — une nouvelle ligne est insérée
    uniquement quand yfinance expose une période absente en base (détection par
    comparaison des dates de colonnes des DataFrames quarterly_*).

    Les champs typés couvrent les grandeurs nécessaires à la stratégie short
    (accruals, free cash flow, dette, marges). Les champs raw_* conservent
    l'intégralité des lignes des DataFrames yfinance sérialisées en JSON, pour
    tout usage futur sans re-collecte.
    """
    __tablename__ = 'fundamental_snapshots'

    id           = db.Column(db.Integer, primary_key=True)
    ticker       = db.Column(db.String(12), nullable=False, index=True)
    period_date  = db.Column(db.Date, nullable=False)        # fin de période (ex: 2024-09-30)
    period_type  = db.Column(db.String(1), nullable=False)   # 'Q' trimestriel | 'A' annuel
    report_date  = db.Column(db.Date)                        # date de dépôt SEC (filed) — anti-look-ahead exact (EDGAR)
    collected_at = db.Column(db.DateTime, default=datetime.utcnow)
    source       = db.Column(db.String(20), default='yfinance')  # 'yfinance' | 'edgar'

    # ── Compte de résultat (Income Statement) ──────────────────────────────
    total_revenue     = db.Column(db.Float)
    gross_profit      = db.Column(db.Float)
    operating_income  = db.Column(db.Float)
    ebitda            = db.Column(db.Float)
    net_income        = db.Column(db.Float)
    eps_diluted       = db.Column(db.Float)

    # ── Bilan (Balance Sheet) ───────────────────────────────────────────────
    total_assets        = db.Column(db.Float)
    total_liabilities   = db.Column(db.Float)
    total_equity        = db.Column(db.Float)
    cash_and_equivalents = db.Column(db.Float)
    total_debt          = db.Column(db.Float)
    current_assets      = db.Column(db.Float)
    current_liabilities = db.Column(db.Float)
    inventory           = db.Column(db.Float)
    accounts_receivable = db.Column(db.Float)

    # ── Flux de trésorerie (Cash Flow Statement) ────────────────────────────
    operating_cash_flow  = db.Column(db.Float)
    investing_cash_flow  = db.Column(db.Float)
    financing_cash_flow  = db.Column(db.Float)
    capital_expenditure  = db.Column(db.Float)
    free_cash_flow       = db.Column(db.Float)

    # ── Ratios dérivés (calculés à la collecte, pour éviter le recalcul) ───
    accruals_ratio = db.Column(db.Float)   # (net_income - operating_cash_flow) / total_assets
    current_ratio  = db.Column(db.Float)   # current_assets / current_liabilities
    debt_to_equity = db.Column(db.Float)   # total_debt / total_equity
    fcf_margin     = db.Column(db.Float)   # free_cash_flow / total_revenue

    # ── Données brutes complètes (JSON) ────────────────────────────────────
    raw_income_stmt   = db.Column(db.Text)   # ligne du DataFrame quarterly_income_stmt
    raw_balance_sheet = db.Column(db.Text)   # ligne du DataFrame quarterly_balance_sheet
    raw_cashflow      = db.Column(db.Text)   # ligne du DataFrame quarterly_cashflow

    __table_args__ = (
        db.UniqueConstraint('ticker', 'period_date', 'period_type',
                            name='uq_fundamental_ticker_period'),
        db.Index('ix_fundamental_ticker_period', 'ticker', 'period_date'),
    )

    def to_dict(self):
        return {
            'ticker':              self.ticker,
            'period_date':         self.period_date.isoformat() if self.period_date else None,
            'period_type':         self.period_type,
            'collected_at':        self.collected_at.isoformat() if self.collected_at else None,
            'total_revenue':       self.total_revenue,
            'gross_profit':        self.gross_profit,
            'operating_income':    self.operating_income,
            'ebitda':              self.ebitda,
            'net_income':          self.net_income,
            'eps_diluted':         self.eps_diluted,
            'total_assets':        self.total_assets,
            'total_liabilities':   self.total_liabilities,
            'total_equity':        self.total_equity,
            'cash_and_equivalents': self.cash_and_equivalents,
            'total_debt':          self.total_debt,
            'current_assets':      self.current_assets,
            'current_liabilities': self.current_liabilities,
            'operating_cash_flow': self.operating_cash_flow,
            'free_cash_flow':      self.free_cash_flow,
            'accruals_ratio':      self.accruals_ratio,
            'current_ratio':       self.current_ratio,
            'debt_to_equity':      self.debt_to_equity,
            'fcf_margin':          self.fcf_margin,
        }


class TickerInfoSnapshot(db.Model):
    """
    Ratios de marché et données descriptives par ticker (source : yfinance .info).

    Contrairement aux états financiers (FundamentalSnapshot, mis à jour aux
    earnings), ces données reflètent des valeurs courantes (market cap, PE trailing,
    short ratio, etc.) et sont rafraîchies mensuellement par le job nocturne.
    Une ligne par (ticker, date de collecte) — l'historique est conservé.

    Le champ raw_info stocke le dict complet retourné par yf.Ticker(t).info en
    JSON, garantissant qu'aucune donnée n'est perdue même si de nouveaux champs
    apparaissent dans une future version de yfinance.
    """
    __tablename__ = 'ticker_info_snapshots'

    id           = db.Column(db.Integer, primary_key=True)
    ticker       = db.Column(db.String(12), nullable=False, index=True)
    collected_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    source       = db.Column(db.String(20), default='yfinance')

    # ── Valorisation ────────────────────────────────────────────────────────
    market_cap        = db.Column(db.Float)
    enterprise_value  = db.Column(db.Float)
    trailing_pe       = db.Column(db.Float)
    forward_pe        = db.Column(db.Float)
    price_to_book     = db.Column(db.Float)
    price_to_sales    = db.Column(db.Float)
    ev_to_ebitda      = db.Column(db.Float)

    # ── Marges ──────────────────────────────────────────────────────────────
    gross_margins     = db.Column(db.Float)
    operating_margins = db.Column(db.Float)
    profit_margins    = db.Column(db.Float)

    # ── Croissance ──────────────────────────────────────────────────────────
    revenue_growth    = db.Column(db.Float)
    earnings_growth   = db.Column(db.Float)

    # ── Qualité financière ──────────────────────────────────────────────────
    return_on_equity  = db.Column(db.Float)
    return_on_assets  = db.Column(db.Float)
    debt_to_equity    = db.Column(db.Float)
    current_ratio     = db.Column(db.Float)
    quick_ratio       = db.Column(db.Float)

    # ── Short Interest (disponible dans .info, complément à FINRA) ──────────
    short_ratio           = db.Column(db.Float)   # jours pour couvrir (yfinance)
    short_percent_float   = db.Column(db.Float)   # % du float vendu à découvert

    # ── Dividendes ──────────────────────────────────────────────────────────
    dividend_yield   = db.Column(db.Float)
    payout_ratio     = db.Column(db.Float)

    # ── Risque ──────────────────────────────────────────────────────────────
    beta = db.Column(db.Float)

    # ── Identité ────────────────────────────────────────────────────────────
    sector              = db.Column(db.String(50))
    industry            = db.Column(db.String(100))
    full_time_employees = db.Column(db.Integer)
    country             = db.Column(db.String(50))

    # ── Données brutes complètes ─────────────────────────────────────────────
    raw_info = db.Column(db.Text)   # JSON complet de yf.Ticker(t).info

    __table_args__ = (
        db.Index('ix_ticker_info_ticker_date', 'ticker', 'collected_at'),
    )

    def to_dict(self):
        return {
            'ticker':               self.ticker,
            'collected_at':         self.collected_at.isoformat() if self.collected_at else None,
            'market_cap':           self.market_cap,
            'trailing_pe':          self.trailing_pe,
            'forward_pe':           self.forward_pe,
            'price_to_book':        self.price_to_book,
            'gross_margins':        self.gross_margins,
            'operating_margins':    self.operating_margins,
            'profit_margins':       self.profit_margins,
            'revenue_growth':       self.revenue_growth,
            'earnings_growth':      self.earnings_growth,
            'return_on_equity':     self.return_on_equity,
            'return_on_assets':     self.return_on_assets,
            'debt_to_equity':       self.debt_to_equity,
            'current_ratio':        self.current_ratio,
            'short_ratio':          self.short_ratio,
            'short_percent_float':  self.short_percent_float,
            'beta':                 self.beta,
            'sector':               self.sector,
            'industry':             self.industry,
        }


class ShortInterestSnapshot(db.Model):
    """
    Données de short interest par ticker issues de FINRA (source primaire testée).

    FINRA publie deux fois par mois (autour du 15 et de la fin du mois) un fichier
    CSV exhaustif couvrant tous les titres cotés sur les marchés US. Ce fichier est
    téléchargeable gratuitement sans API ni abonnement.

    Une ligne = un ticker pour une date de publication. L'historique est conservé
    intégralement pour permettre l'analyse de tendance (SIR croissant vs décroissant)
    et le backtesting du signal short interest.

    Le champ raw_data conserve la ligne CSV brute sérialisée en JSON au cas où des
    champs supplémentaires seraient exploités ultérieurement.
    """
    __tablename__ = 'short_interest_snapshots'

    id              = db.Column(db.Integer, primary_key=True)
    ticker          = db.Column(db.String(12), nullable=False, index=True)
    settlement_date = db.Column(db.Date, nullable=False)   # date de règlement FINRA
    report_date     = db.Column(db.Date)                   # date de publication du rapport
    collected_at    = db.Column(db.DateTime, default=datetime.utcnow)
    source          = db.Column(db.String(20), default='FINRA')

    # ── Champs FINRA bruts ──────────────────────────────────────────────────
    short_interest          = db.Column(db.Float)   # nb d'actions vendues à découvert
    avg_daily_volume        = db.Column(db.Float)   # volume moyen journalier FINRA
    days_to_cover           = db.Column(db.Float)   # short_interest / avg_daily_volume (SIR)
    previous_short_interest = db.Column(db.Float)   # valeur de la publication précédente
    change_from_previous    = db.Column(db.Float)   # variation absolue vs publication précédente
    change_pct              = db.Column(db.Float)   # variation en % vs publication précédente

    # ── Signaux dérivés (calculés à la collecte) ────────────────────────────
    sir_trend    = db.Column(db.String(10))    # 'up' | 'down' | 'stable'
    squeeze_risk = db.Column(db.Boolean)       # True si days_to_cover > 20

    # ── Ligne brute complète (CSV FINRA → JSON) ─────────────────────────────
    raw_data = db.Column(db.Text)

    __table_args__ = (
        db.UniqueConstraint('ticker', 'settlement_date',
                            name='uq_short_interest_ticker_date'),
        db.Index('ix_short_interest_ticker_date', 'ticker', 'settlement_date'),
    )

    def to_dict(self):
        return {
            'ticker':                   self.ticker,
            'settlement_date':          self.settlement_date.isoformat() if self.settlement_date else None,
            'report_date':              self.report_date.isoformat() if self.report_date else None,
            'short_interest':           self.short_interest,
            'avg_daily_volume':         self.avg_daily_volume,
            'days_to_cover':            self.days_to_cover,
            'previous_short_interest':  self.previous_short_interest,
            'change_pct':               self.change_pct,
            'sir_trend':                self.sir_trend,
            'squeeze_risk':             self.squeeze_risk,
            'source':                   self.source,
        }


class PushSubscription(db.Model):
    """Abonnement Web Push d'un appareil (PWA iOS / Chrome / Firefox)."""
    __tablename__ = 'push_subscriptions'

    id         = db.Column(db.Integer, primary_key=True)
    endpoint   = db.Column(db.Text, unique=True, nullable=False)
    p256dh     = db.Column(db.Text, nullable=False)
    auth       = db.Column(db.Text, nullable=False)
    label      = db.Column(db.String(100))   # ex: "iPhone Bryan"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_info(self):
        return {'endpoint': self.endpoint, 'keys': {'p256dh': self.p256dh, 'auth': self.auth}}


def init_db(app, default_panel):
    """
    Initialise la base de données et charge les valeurs par défaut.

    Args:
        app: Instance Flask
        default_panel: Liste des tickers par défaut
    """
    with app.app_context():
        db.create_all()
        
        # Migration: Ajouter la colonne strategy_type si elle n'existe pas
        _migrate_add_strategy_type(app)

        # Migration: colonnes enrichies pour persister tout le détail du calcul
        _migrate_add_columns(app, 'recommendation_details', {
            'perf_recent_1m':   'FLOAT',
            'vol_annualisee':   'FLOAT',
            'details_mensuels': 'TEXT',
        })
        _migrate_add_columns(app, 'recommendation_history', {
            'market_regime': 'TEXT',
        })
        # Migration: volume + low + high des barres de prix
        _migrate_add_columns(app, 'market_price_bars', {
            'volume': 'FLOAT',
            'low':    'FLOAT',
            'high':   'FLOAT',
        })
        # Migration: cash dans les snapshots portfolio
        _migrate_add_columns(app, 'portfolio_snapshots', {
            'cash':             'FLOAT',
            'invested_capital': 'FLOAT',
        })
        # Migration: date de dépôt SEC (filed) pour les fondamentaux EDGAR
        _migrate_add_columns(app, 'fundamental_snapshots', {
            'report_date': 'DATE',
        })
        
        # Initialiser le panel Long par défaut si vide
        if PanelAction.query.count() == 0:
            for ticker in default_panel:
                action = PanelAction(ticker=ticker.upper(), strategy_type='long')
                db.session.add(action)
            db.session.commit()
            print(f"✅ Panel Long initialisé avec {len(default_panel)} actions")


def _migrate_add_columns(app, table_name, columns: dict):
    """
    Ajoute des colonnes manquantes à une table existante (PostgreSQL & SQLite).
    columns: { 'nom_colonne': 'TYPE_SQL' }
    """
    from sqlalchemy import text, inspect
    try:
        inspector = inspect(db.engine)
        existing = {col['name'] for col in inspector.get_columns(table_name)}
        for name, sql_type in columns.items():
            if name not in existing:
                db.session.execute(text(
                    f'ALTER TABLE {table_name} ADD COLUMN {name} {sql_type}'
                ))
                print(f"[migration] {table_name}.{name} ajoutee")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[migration] {table_name} echouee: {e}")


def _migrate_add_strategy_type(app):
    """
    Migration pour ajouter la colonne strategy_type à panel_actions.
    Compatible PostgreSQL et SQLite.
    """
    from sqlalchemy import text, inspect
    
    inspector = inspect(db.engine)
    columns = [col['name'] for col in inspector.get_columns('panel_actions')]
    
    if 'strategy_type' not in columns:
        print("🔄 Migration: Ajout de la colonne strategy_type...")
        
        # Détecte si c'est PostgreSQL ou SQLite
        dialect = db.engine.dialect.name
        
        if dialect == 'postgresql':
            # PostgreSQL: ajouter colonne avec valeur par défaut
            db.session.execute(text(
                "ALTER TABLE panel_actions ADD COLUMN strategy_type VARCHAR(10) DEFAULT 'long' NOT NULL"
            ))
        else:
            # SQLite: syntaxe légèrement différente
            db.session.execute(text(
                "ALTER TABLE panel_actions ADD COLUMN strategy_type VARCHAR(10) DEFAULT 'long'"
            ))
            # Mettre à jour les valeurs NULL existantes
            db.session.execute(text(
                "UPDATE panel_actions SET strategy_type = 'long' WHERE strategy_type IS NULL"
            ))
        
        db.session.commit()
        print("✅ Migration terminée: colonne strategy_type ajoutée")

