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
    ticker = db.Column(db.String(10), nullable=False)
    strategy_type = db.Column(db.String(10), default='long', nullable=False) # 'long' ou 'short'
    name = db.Column(db.String(100))  # Nom de l'entreprise (optionnel)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    __table_args__ = (db.UniqueConstraint('ticker', 'strategy_type', name='_ticker_strategy_uc'),)
    
    def to_dict(self):
        return {
            'id': self.id,
            'ticker': self.ticker,
            'strategy_type': self.strategy_type,
            'name': self.name,
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
    strategy_type = db.Column(db.String(10), default='long', nullable=False) # 'long' ou 'short'
    calculation_date = db.Column(db.DateTime, nullable=False)  # Date du calcul
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    nb_top = db.Column(db.Integer, default=5)  # Nombre de top actions à ce moment
    
    # Relation avec les détails
    details = db.relationship('RecommendationDetail', backref='history', lazy=True,
                              cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'strategy_type': self.strategy_type,
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


def init_db(app, default_panel):
    """
    Initialise la base de données et charge les valeurs par défaut.
    
    Args:
        app: Instance Flask
        default_panel: Liste des tickers par défaut
    """
    with app.app_context():
        db.create_all()
        
        # Initialiser le panel par défaut si vide
        if PanelAction.query.count() == 0:
            for ticker in default_panel:
                action = PanelAction(ticker=ticker.upper(), strategy_type='long')
                db.session.add(action)
            db.session.commit()
            print(f"✅ Panel LONG initialisé avec {len(default_panel)} actions")

