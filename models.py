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
    # VIX_HIGH | VIX_SPIKE | SPY_DRAWDOWN | PORTFOLIO_DRAWDOWN | POSITION_DROP
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

