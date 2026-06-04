# -*- coding: utf-8 -*-
"""Routes backtest : lance le backtest momentum et renvoie courbe d'équité + stats."""
import threading
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
        'years_options': [3, 5, 10],
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


@bp.route('/api/backtest/run', methods=['POST'])
@require_admin
def backtest_run():
    """
    Lance le backtest sur l'historique (univers re-screené tous les 3 mois, config live).
    Body optionnel : {capital, years, benchmark, pool_size}. nb_top et couches de risque
    sont lus dans les Settings (config live).
    """
    data = request.get_json(silent=True) or {}
    try:
        capital = float(data.get('capital', DEFAULT_CAPITAL))
        years = int(data.get('years', DEFAULT_YEARS))
        benchmark = (data.get('benchmark') or 'SPY').upper()
        pool_size = int(data.get('pool_size', DEFAULT_POOL))
        # Hypothèses de réalisme (optionnelles ; None → défauts du service)
        def _optf(key):
            v = data.get(key)
            return float(v) if v is not None and v != '' else None
        tx_cost_bps = _optf('tx_cost_bps')
        margin_rate_pct = _optf('margin_rate_pct')
        cash_yield_pct = _optf('cash_yield_pct')
        maintenance_margin_pct = _optf('maintenance_margin_pct')
        post_call_leverage = _optf('post_call_leverage')
        dca_amount = float(data.get('dca_amount', 0) or 0)
        margin_call_enabled = bool(data.get('margin_call_enabled', True))
    except (TypeError, ValueError):
        return jsonify({'error': 'Paramètres invalides'}), 400

    if capital <= 0 or years < 1 or years > 30:
        return jsonify({'error': 'capital > 0 et 1 ≤ années ≤ 30'}), 400
    if dca_amount < 0 or dca_amount > 1_000_000:
        return jsonify({'error': 'apport DCA invalide (0 ≤ DCA ≤ 1 000 000)'}), 400
    for label, val in (('coût', tx_cost_bps), ('taux de marge', margin_rate_pct),
                       ('rendement cash', cash_yield_pct)):
        if val is not None and (val < 0 or val > 100):
            return jsonify({'error': f'{label} hors bornes (0–100)'}), 400

    nb_top, vs = _live_config()
    svc = get_backtest_service()
    try:
        result = svc.run(capital=capital, years=years, nb_top=nb_top,
                         benchmark=benchmark, pool_size=pool_size,
                         tx_cost_bps=tx_cost_bps, margin_rate_pct=margin_rate_pct,
                         cash_yield_pct=cash_yield_pct,
                         maintenance_margin_pct=maintenance_margin_pct,
                         post_call_leverage=post_call_leverage,
                         dca_amount=dca_amount, margin_call_enabled=margin_call_enabled,
                         **vs)
        return jsonify(result)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        current_app.logger.exception('Erreur backtest')
        return jsonify({'error': f'Erreur inattendue : {type(e).__name__}: {e}'}), 500


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
