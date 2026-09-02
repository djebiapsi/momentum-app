# -*- coding: utf-8 -*-
"""
Application Flask - Momentum Strategy
=====================================
Point d'entrée : assemble l'application (config, DB, CORS), enregistre les
blueprints et démarre le scheduler. La logique est répartie dans des modules
dédiés :
  - auth.py        : décorateur d'authentification admin
  - services.py    : registre des services (singletons)
  - core.py        : logique métier partagée (routes + jobs)
  - routes/        : blueprints API par domaine
  - jobs.py        : tâches planifiées
  - scheduler.py   : construction et démarrage de l'APScheduler

`app` reste exposé au niveau module → `gunicorn app:app` inchangé.
"""

import os
from flask import Flask
from flask_cors import CORS
from config import get_config
from models import db, init_db

from routes.pages import bp as pages_bp
from routes.settings import bp as settings_bp
from routes.panel import bp as panel_bp
from routes.momentum import bp as momentum_bp
from routes.short import bp as short_bp
from routes.options import bp as options_bp
from routes.ibkr import bp as ibkr_bp
from routes.market import bp as market_bp
from routes.backtest import bp as backtest_bp
from routes.prices import bp as prices_bp
from routes.push import bp as push_bp
from routes.quality_value import bp as quality_value_bp
from scheduler import create_scheduler


def create_app():
    """Factory : crée l'application Flask et enregistre les blueprints."""
    app = Flask(__name__)

    # Configuration
    config_class = get_config()
    app.config.from_object(config_class)

    # CORS
    CORS(app)

    # Base de données
    db.init_app(app)
    init_db(app, config_class.DEFAULT_PANEL)

    # Blueprints
    app.register_blueprint(pages_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(panel_bp)
    app.register_blueprint(momentum_bp)
    app.register_blueprint(short_bp)
    app.register_blueprint(options_bp)
    app.register_blueprint(ibkr_bp)
    app.register_blueprint(market_bp)
    app.register_blueprint(backtest_bp)
    app.register_blueprint(prices_bp)
    app.register_blueprint(push_bp)
    app.register_blueprint(quality_value_bp)

    return app


app = create_app()

# Scheduler (1 worker gunicorn + threads → pas de double-firing)
scheduler = create_scheduler(app)


# =============================================================================
# POINT D'ENTRÉE
# =============================================================================

if __name__ == '__main__':
    # Lancer l'application en mode développement
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=app.config.get('DEBUG', False))
