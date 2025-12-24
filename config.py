# -*- coding: utf-8 -*-
"""
Configuration de l'application Momentum Strategy
================================================
Ce fichier gère la configuration via variables d'environnement.
La clé API et autres secrets sont protégés et jamais exposés dans le code.
"""

import os
from dotenv import load_dotenv

# Charger les variables d'environnement depuis .env (en local)
load_dotenv()


class Config:
    """Configuration de base de l'application"""
    
    # ==========================================================================
    # SECRETS - Chargés depuis les variables d'environnement
    # ==========================================================================
    
    # Clé API Tiingo (OBLIGATOIRE)
    # En production: définie dans les variables d'environnement Render
    # En local: définie dans le fichier .env
    TIINGO_API_KEY = os.environ.get('TIINGO_API_KEY')
    
    # Clé secrète Flask pour les sessions
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # Mot de passe admin pour accéder aux fonctions d'écriture
    # Si non défini, le mode admin est désactivé (lecture seule pour tous)
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
    
    # ==========================================================================
    # CONFIGURATION EMAIL (Resend)
    # ==========================================================================
    
    # Clé API Resend pour l'envoi d'emails
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
    
    # Email de l'expéditeur (domaine vérifié sur Resend ou onboarding@resend.dev)
    EMAIL_FROM = os.environ.get('EMAIL_FROM', 'onboarding@resend.dev')
    
    # Email du destinataire (votre email personnel)
    EMAIL_TO = os.environ.get('EMAIL_TO')
    
    # ==========================================================================
    # BASE DE DONNÉES
    # ==========================================================================
    
    # URL de la base de données PostgreSQL
    # En production: fournie par Render
    # En local: utilise SQLite par défaut
    DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///momentum.db')
    
    # Correction pour SQLAlchemy (Render utilise postgres:// au lieu de postgresql://)
    if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # ==========================================================================
    # PARAMÈTRES PAR DÉFAUT DE LA STRATÉGIE
    # ==========================================================================
    
    # Nombre d'actions à sélectionner par défaut
    DEFAULT_NB_TOP = 5
    
    # Panel d'actions par défaut
    DEFAULT_PANEL = [
        "NVDA", "AVGO", "TSM", "PLTR", "SMCI", "LLY", "CRDO", "MDB", "LRCX", "AMD",
        "MU", "SANM", "VRT", "APH", "EXPE", "ALL", "PLMR", "GAMB", "NEM", "KGC",
        "BHP", "HEI", "ROK", "JBL", "GMED", "DDS", "AEO", "TJX", "MCK", "CI",
        "UNH", "CVX", "XOM", "CCJ", "EQT", "BAC", "MS", "IBKR", "V", "MA",
        "KO", "PEP", "MCD", "WMT", "COST", "LOW", "HD", "CAT", "DE", "LMT", "RTX"
    ]


class DevelopmentConfig(Config):
    """Configuration pour le développement local"""
    DEBUG = True
    

class ProductionConfig(Config):
    """Configuration pour la production (Render)"""
    DEBUG = False


# Sélection de la configuration selon l'environnement
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}


def get_config():
    """Retourne la configuration appropriée selon l'environnement"""
    env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])

