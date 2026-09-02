# -*- coding: utf-8 -*-
"""
Worker backtest Quality-Value isolé — lancé en sous-processus par
/api/qv/backtest/run. GIL séparé de gunicorn (qui reste réactif au poll).
Usage : python3 _qv_bt_worker.py <params_file> <result_file>
"""
import json
import logging
import os
import sys

logging.disable(logging.CRITICAL)

params_file = sys.argv[1]
result_file = sys.argv[2]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

with open(params_file) as f:
    data = json.load(f)

import app as app_module
application = app_module.create_app()

with application.app_context():
    from services import ibkr_service as _ib
    _ib.ensure_connected = lambda: False

    from qv_backtest import run_portfolio_backtest
    result = run_portfolio_backtest(**data)

with open(result_file, 'w') as f:
    json.dump(result, f)

try:
    os.remove(params_file)
except OSError:
    pass
