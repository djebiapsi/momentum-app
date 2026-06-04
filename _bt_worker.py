# -*- coding: utf-8 -*-
"""
Worker backtest isolé — lancé en sous-processus par la route /api/backtest/run.
Tourne dans son propre interpréteur Python : GIL complètement séparé de gunicorn,
qui reste libre de répondre aux requêtes poll.
Usage : python3 _bt_worker.py <params_file> <result_file>
"""
import json
import logging
import os
import sys

logging.disable(logging.CRITICAL)   # silence le scheduler au démarrage

params_file = sys.argv[1]
result_file = sys.argv[2]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

with open(params_file) as f:
    data = json.load(f)

import app as app_module
application = app_module.create_app()

with application.app_context():
    from services import get_backtest_service
    svc = get_backtest_service()

    nb_top = data.pop('nb_top')
    vol_keys = ['vol_scaling', 'vol_target_pct', 'max_exposure_pct',
                'portfolio_filter', 'portfolio_vol_threshold_pct']
    vs = {k: data.pop(k) for k in vol_keys if k in data}

    result = svc.run(nb_top=nb_top, **data, **vs)

with open(result_file, 'w') as f:
    json.dump(result, f)

os.remove(params_file)
