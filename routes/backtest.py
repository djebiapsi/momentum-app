# -*- coding: utf-8 -*-
"""Routes backtest : lance le backtest momentum et renvoie courbe d'équité + stats."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from flask import Blueprint, jsonify, request, current_app

from models import Settings
from auth import require_admin
from services import get_backtest_service
from core import _get_vol_scaling_settings
from backtest_service import BacktestService

bp = Blueprint('backtest', __name__)

DEFAULT_YEARS = 5
DEFAULT_CAPITAL = 10000.0
DEFAULT_POOL = 120  # borne le coût data (≈ temps de la 1re exécution)

# Stockage en mémoire des jobs (TTL 30 min).
# Le calcul tourne dans un SUBPROCESS isolé (GIL séparé) → gunicorn reste réactif.
_jobs: dict = {}
_jobs_lock = threading.Lock()
_JOB_TTL = 1800  # secondes

_WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), '_bt_worker.py')


def _live_config():
    """Lit la config live : nb_top + couches de risque (Settings), réutilise core."""
    nb_top = int(Settings.get('nb_top', current_app.config.get('DEFAULT_NB_TOP', 5)))
    vs = _get_vol_scaling_settings()
    return nb_top, vs


@bp.route('/api/backtest/defaults', methods=['GET'])
def backtest_defaults():
    """Valeurs par défaut + résumé de la config live, pour pré-remplir l'UI."""
    nb_top, vs = _live_config()
    return jsonify({
        'capital': DEFAULT_CAPITAL,
        'years': DEFAULT_YEARS,
        'years_options': [1, 3, 5, 10],
        'nb_top': nb_top,
        'config': vs,
        'assumptions': {
            'tx_cost_bps': BacktestService.DEFAULT_TX_COST_BPS,
            'margin_rate_pct': BacktestService.DEFAULT_MARGIN_RATE_PCT,
            'cash_yield_pct': BacktestService.DEFAULT_CASH_YIELD_PCT,
            'maintenance_margin_pct': BacktestService.DEFAULT_MAINT_MARGIN_PCT,
            'post_call_leverage': BacktestService.DEFAULT_POST_CALL_LEVERAGE,
            'dca_amount': 0.0,
            'margin_call_enabled': True,
        },
        'tiingo_configured': current_app.config.get('TIINGO_API_KEY') is not None,
    })


def _parse_run_params(data):
    """Parse et valide les paramètres communs de /run et /run-async. Lève ValueError si invalide."""
    def _optf(key):
        v = data.get(key)
        return float(v) if v is not None and v != '' else None

    capital = float(data.get('capital', DEFAULT_CAPITAL))
    years = int(data.get('years', DEFAULT_YEARS))
    benchmark = (data.get('benchmark') or 'SPY').upper()
    pool_size = int(data.get('pool_size', DEFAULT_POOL))
    tx_cost_bps = _optf('tx_cost_bps')
    margin_rate_pct = _optf('margin_rate_pct')
    cash_yield_pct = _optf('cash_yield_pct')
    maintenance_margin_pct = _optf('maintenance_margin_pct')
    post_call_leverage = _optf('post_call_leverage')
    dca_amount = float(data.get('dca_amount', 0) or 0)
    margin_call_enabled = bool(data.get('margin_call_enabled', True))

    if capital <= 0 or years < 1 or years > 30:
        raise ValueError('capital > 0 et 1 ≤ années ≤ 30')
    if dca_amount < 0 or dca_amount > 1_000_000:
        raise ValueError('apport DCA invalide (0 ≤ DCA ≤ 1 000 000)')
    for label, val in (('coût', tx_cost_bps), ('taux de marge', margin_rate_pct),
                       ('rendement cash', cash_yield_pct)):
        if val is not None and (val < 0 or val > 100):
            raise ValueError(f'{label} hors bornes (0–100)')

    return dict(capital=capital, years=years, benchmark=benchmark, pool_size=pool_size,
                tx_cost_bps=tx_cost_bps, margin_rate_pct=margin_rate_pct,
                cash_yield_pct=cash_yield_pct, maintenance_margin_pct=maintenance_margin_pct,
                post_call_leverage=post_call_leverage,
                dca_amount=dca_amount, margin_call_enabled=margin_call_enabled)


@bp.route('/api/backtest/run', methods=['POST'])
@require_admin
def backtest_run():
    """
    Lance le backtest de manière ASYNCHRONE pour ne pas bloquer gunicorn.
    Retourne immédiatement un job_id ; le frontend poll /api/backtest/status/<job_id>.
    """
    data = request.get_json(silent=True) or {}
    try:
        params = _parse_run_params(data)
    except (TypeError, ValueError) as e:
        return jsonify({'error': str(e)}), 400

    nb_top, vs = _live_config()
    job_id = str(uuid.uuid4())
    now = time.time()

    # Nettoyage des vieux jobs (> TTL) + terminer les subprocess orphelins
    with _jobs_lock:
        stale = [k for k, v in _jobs.items() if now - v.get('created_at', 0) > _JOB_TTL]
        for k in stale:
            proc = _jobs[k].get('proc')
            if proc and proc.poll() is None:
                proc.terminate()
            del _jobs[k]

    # Écrire les paramètres dans un fichier temporaire
    params_file = tempfile.mktemp(prefix=f'bt_params_{job_id}_', suffix='.json')
    result_file = tempfile.mktemp(prefix=f'bt_result_{job_id}_', suffix='.json')
    error_file  = tempfile.mktemp(prefix=f'bt_error_{job_id}_',  suffix='.txt')

    with open(params_file, 'w') as f:
        json.dump({'nb_top': nb_top, **params, **vs}, f)

    # Lancer le subprocess — GIL totalement séparé de gunicorn
    ef = open(error_file, 'w')
    proc = subprocess.Popen(
        [sys.executable, _WORKER_SCRIPT, params_file, result_file],
        stdout=subprocess.DEVNULL, stderr=ef,
    )
    ef.close()

    with _jobs_lock:
        _jobs[job_id] = {
            'status': 'running', 'proc': proc,
            'result_file': result_file, 'error_file': error_file,
            'created_at': now,
        }

    return jsonify({'job_id': job_id, 'status': 'running'})


@bp.route('/api/backtest/status/<job_id>', methods=['GET'])
@require_admin
def backtest_status(job_id):
    """Poll le statut d'un job backtest (vérifie le subprocess à chaque appel)."""
    with _jobs_lock:
        job = _jobs.get(job_id)

    if job is None:
        return jsonify({'status': 'error',
                        'error': 'Job introuvable (serveur redémarré ?). Relancez le backtest.'})

    # Vérifier si le subprocess s'est terminé
    if job['status'] == 'running':
        proc = job.get('proc')
        if proc and proc.poll() is not None:
            result_file = job.get('result_file', '')
            error_file  = job.get('error_file', '')
            if proc.returncode == 0 and os.path.exists(result_file):
                try:
                    with open(result_file) as f:
                        result = json.load(f)
                    with _jobs_lock:
                        job['status'] = 'done'
                        job['result'] = result
                    for fp in [result_file, error_file]:
                        try:
                            if fp and os.path.exists(fp): os.remove(fp)
                        except Exception:
                            pass
                except Exception as e:
                    with _jobs_lock:
                        job['status'] = 'error'
                        job['error'] = f'Lecture du résultat impossible : {e}'
            else:
                err = ''
                if error_file and os.path.exists(error_file):
                    try:
                        with open(error_file) as f:
                            err = f.read()[-400:]
                    except Exception:
                        pass
                with _jobs_lock:
                    job['status'] = 'error'
                    job['error'] = err or f'Subprocess terminé avec code {proc.returncode}'

    if job['status'] == 'running':
        return jsonify({'status': 'running'})
    if job['status'] == 'error':
        return jsonify({'status': 'error', 'error': job.get('error', 'Erreur inconnue')})
    return jsonify({'status': 'done', 'result': job['result']})


@bp.route('/api/backtest/prefill', methods=['POST'])
@require_admin
def backtest_prefill():
    """
    Pré-remplit le cache de prix (avec volume) pour le pool candidat, en arrière-plan
    (respecte le pacing IBKR). Bootstrap manuel sans attendre le cron nocturne.
    Body optionnel : {years, max_fetch}.
    """
    data = request.get_json(silent=True) or {}
    years = int(data.get('years', 10))
    max_fetch = int(data.get('max_fetch', 60))
    app_obj = current_app._get_current_object()
    svc = get_backtest_service()

    def _run():
        with app_obj.app_context():
            try:
                res = svc.prefill_pool(years=years, max_fetch=max_fetch)
                app_obj.logger.info('Prefill backtest terminé: %s', res)
            except Exception as e:
                app_obj.logger.warning('Prefill backtest échec: %s', e)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'success': True,
                    'message': f'Pré-remplissage lancé en arrière-plan (max {max_fetch} tickers).'})
