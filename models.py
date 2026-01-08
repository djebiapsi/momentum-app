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
    
    # Relation avec les détails
    details = db.relationship('RecommendationDetail', backref='history', lazy=True,
                              cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'calculation_date': self.calculation_date.strftime('%Y-%m-%d'),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'nb_top': self.nb_top,
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
    
    def to_dict(self):
        return {
            'ticker': self.ticker,
            'momentum': round(self.momentum, 2),
            'signal': self.signal,
            'allocation': self.allocation,
            'rank': self.rank
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
        
        # Initialiser le panel Long par défaut si vide
        if PanelAction.query.count() == 0:
            for ticker in default_panel:
                action = PanelAction(ticker=ticker.upper(), strategy_type='long')
                db.session.add(action)
            db.session.commit()
            print(f"✅ Panel Long initialisé avec {len(default_panel)} actions")


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

