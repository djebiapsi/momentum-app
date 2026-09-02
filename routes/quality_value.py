# -*- coding: utf-8 -*-
"""Routes de la stratégie long Quality-Value (fondamentale, US/EU/transversal)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from flask import Blueprint, jsonify, request
from models import Settings
from auth import require_admin
from core import compute_and_save_qv, get_qv_market, QV_DEFAULTS
from services import get_fundamental_screen_service

bp = Blueprint('quality_value', __name__)

VALID_MARKETS = ('us', 'eu', 'all')

# Jobs backtest QV (subprocess isolé, comme le backtest momentum)
_qv_jobs: dict = {}
_qv_jobs_lock = threading.Lock()
_QV_JOB_TTL = 1800
_QV_WORKER = os.path.join(os.path.dirname(os.path.dirname(__file__)), '_qv_bt_worker.py')


@bp.route('/api/qv/config', methods=['GET'])
def qv_config():
    """Config courante de la stratégie QV (marché + paramètres)."""
    return jsonify({
        'market': get_qv_market(),
        'quality_weight': float(Settings.get('qv_quality_weight', QV_DEFAULTS['quality_weight'])),
        'top_n': int(Settings.get('qv_top_n', QV_DEFAULTS['top_n'])),
        'max_per_sector': int(Settings.get('qv_max_per_sector', QV_DEFAULTS['max_per_sector'])),
        'markets': VALID_MARKETS,
    })


@bp.route('/api/qv/config', methods=['POST'])
@require_admin
def set_qv_config():
    """Modifie le marché (us/eu/all) et/ou les paramètres de la stratégie QV."""
    data = request.get_json(silent=True) or {}
    if 'market' in data:
        m = str(data['market']).lower()
        if m not in VALID_MARKETS:
            return jsonify({'success': False, 'error': f'market invalide ({m})'}), 400
        Settings.set('qv_market', m)
    for key, cast in (('quality_weight', float), ('top_n', int), ('max_per_sector', int)):
        if key in data:
            try:
                Settings.set(f'qv_{key}', cast(data[key]))
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': f'{key} invalide'}), 400
    return jsonify({'success': True, 'market': get_qv_market()})


@bp.route('/api/qv/portfolio', methods=['GET'])
def qv_portfolio():
    """
    Dernier portefeuille QV calculé (depuis Settings). ?market=us|eu|all pour une
    région précise ; sinon le marché courant.
    """
    market = (request.args.get('market') or get_qv_market()).lower()
    raw = Settings.get(f'qv_latest_portfolio_{market}') or Settings.get('qv_latest_portfolio')
    if not raw:
        return jsonify({'success': False, 'error': 'Aucun portefeuille calculé — '
                        'lancer /api/qv/run'}), 404
    try:
        return jsonify({'success': True, **json.loads(raw)})
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Portefeuille illisible'}), 500


@bp.route('/api/qv/run', methods=['POST'])
@require_admin
def qv_run():
    """
    Déclenche le calcul du portefeuille QV maintenant. Body optionnel {market}.
    Calcule à la volée (live) — ne dépend pas du cron semestriel.
    """
    data = request.get_json(silent=True) or {}
    market = (data.get('market') or get_qv_market()).lower()
    if market not in VALID_MARKETS:
        return jsonify({'success': False, 'error': f'market invalide ({market})'}), 400
    result = compute_and_save_qv(market)
    if not result or not result.get('success'):
        err = (result or {}).get('error', 'échec du calcul')
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({
        'success': True, 'market': market,
        'eligible': result.get('eligible'),
        'sector_breakdown': result.get('sector_breakdown'),
        'holdings': result.get('holdings'),
    })


@bp.route('/api/qv/screen', methods=['GET'])
def qv_screen():
    """Aperçu du classement complet (sans persistance). ?market=&quality_weight=."""
    market = (request.args.get('market') or get_qv_market()).lower()
    qw = float(request.args.get('quality_weight',
                                Settings.get('qv_quality_weight', QV_DEFAULTS['quality_weight'])))
    top_n = int(request.args.get('top_n', QV_DEFAULTS['top_n']))
    res = get_fundamental_screen_service().build_portfolio(
        top_n=top_n, quality_weight=qw, market=market,
        max_per_sector=int(Settings.get('qv_max_per_sector', QV_DEFAULTS['max_per_sector'])))
    status = 200 if res.get('success') else 400
    return jsonify(res), status


@bp.route('/api/qv/evaluate', methods=['GET'])
def qv_evaluate():
    """
    Score Quality-Value d'un ticker. ?ticker=XXX&market=us|eu|all.
    Si absent de la base, récupère ses données via yfinance puis score.
    """
    ticker = (request.args.get('ticker') or '').upper().strip()
    if not ticker:
        return jsonify({'success': False, 'error': 'Paramètre ticker manquant'}), 400
    market = (request.args.get('market') or get_qv_market()).lower()
    if market not in VALID_MARKETS:
        market = 'all'
    res = get_fundamental_screen_service().evaluate_ticker(ticker, market=market)
    return jsonify(res), (200 if res.get('success') else 404)


@bp.route('/api/qv/quality-scores', methods=['GET'])
def qv_quality_scores():
    """Scores qualité (indicatif) pour une liste de tickers. ?tickers=A,B,C&market=."""
    raw = request.args.get('tickers', '')
    tickers = [t.strip().upper() for t in raw.split(',') if t.strip()]
    if not tickers:
        return jsonify({'success': True, 'scores': {}})
    market = (request.args.get('market') or 'all').lower()
    scores = get_fundamental_screen_service().quality_scores(tickers, market=market)
    return jsonify({'success': True, 'scores': scores})


@bp.route('/api/qv/backtest/run', methods=['POST'])
@require_admin
def qv_backtest_run():
    """Lance le backtest portefeuille QV en subprocess (asynchrone). Renvoie un job_id."""
    data = request.get_json(silent=True) or {}
    market = (data.get('market') or get_qv_market()).lower()
    if market not in VALID_MARKETS:
        return jsonify({'error': f'market invalide ({market})'}), 400
    params = {
        'market': market,
        'wq': float(data.get('wq', 0.5)),
        'wv': float(data.get('wv', 0.5)),
        'wm': float(data.get('wm', 0.0)),
        'top_n': int(data.get('top_n', QV_DEFAULTS['top_n'])),
    }
    job_id = str(uuid.uuid4())
    now = time.time()
    with _qv_jobs_lock:
        for k in [k for k, v in _qv_jobs.items() if now - v.get('created_at', 0) > _QV_JOB_TTL]:
            p = _qv_jobs[k].get('proc')
            if p and p.poll() is None:
                p.terminate()
            del _qv_jobs[k]

    params_file = tempfile.mktemp(prefix=f'qvbt_p_{job_id}_', suffix='.json')
    result_file = tempfile.mktemp(prefix=f'qvbt_r_{job_id}_', suffix='.json')
    error_file = tempfile.mktemp(prefix=f'qvbt_e_{job_id}_', suffix='.txt')
    with open(params_file, 'w') as f:
        json.dump(params, f)
    ef = open(error_file, 'w')
    proc = subprocess.Popen([sys.executable, _QV_WORKER, params_file, result_file],
                            stdout=subprocess.DEVNULL, stderr=ef)
    ef.close()
    with _qv_jobs_lock:
        _qv_jobs[job_id] = {'status': 'running', 'proc': proc, 'result_file': result_file,
                            'error_file': error_file, 'created_at': now}
    return jsonify({'job_id': job_id, 'status': 'running'})


@bp.route('/api/qv/backtest/status/<job_id>', methods=['GET'])
@require_admin
def qv_backtest_status(job_id):
    """Poll le statut d'un backtest QV."""
    with _qv_jobs_lock:
        job = _qv_jobs.get(job_id)
    if job is None:
        return jsonify({'status': 'error', 'error': 'Job introuvable (serveur redémarré ?).'})
    if job['status'] == 'running':
        proc = job.get('proc')
        if proc and proc.poll() is not None:
            rf, ef = job.get('result_file', ''), job.get('error_file', '')
            if proc.returncode == 0 and os.path.exists(rf):
                try:
                    with open(rf) as f:
                        result = json.load(f)
                    with _qv_jobs_lock:
                        job['status'] = 'done'
                        job['result'] = result
                    for fp in (rf, ef):
                        try:
                            if fp and os.path.exists(fp):
                                os.remove(fp)
                        except OSError:
                            pass
                except Exception as e:
                    with _qv_jobs_lock:
                        job['status'] = 'error'
                        job['error'] = f'Lecture résultat impossible : {e}'
            else:
                err = ''
                if ef and os.path.exists(ef):
                    try:
                        with open(ef) as f:
                            err = f.read()[-400:]
                    except OSError:
                        pass
                with _qv_jobs_lock:
                    job['status'] = 'error'
                    job['error'] = err or f'Subprocess code {proc.returncode}'
    if job['status'] == 'running':
        return jsonify({'status': 'running'})
    if job['status'] == 'error':
        return jsonify({'status': 'error', 'error': job.get('error', 'Erreur inconnue')})
    return jsonify({'status': 'done', 'result': job['result']})
