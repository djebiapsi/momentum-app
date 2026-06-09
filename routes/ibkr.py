# -*- coding: utf-8 -*-
"""Routes IBKR, Flex et tableau de bord Performance."""
import asyncio
import json
from datetime import datetime
from flask import Blueprint, jsonify, request, make_response, current_app
from models import (db, Settings, PanelAction, RecommendationHistory,
                    RecommendationDetail, ShortPanelAction,
                    ShortRecommendationHistory, ShortRecommendationDetail,
                    OptionRecommendation, MarketEvent)
from auth import require_admin
from services import (ibkr_service, get_momentum_service, get_email_service,
                      get_news_service, get_market_monitor, get_screener_service,
                      get_short_screener_service, get_finviz_screener_service,
                      get_options_service)
import re
import flex_service
from ibkr_service import encrypt_credential, decrypt_credential

bp = Blueprint('ibkr', __name__)


@bp.route('/api/ibkr/status', methods=['GET'])
def ibkr_status():
    """Retourne le statut de connexion à IB Gateway + le mode de trading courant."""
    status = ibkr_service.get_status()
    # Le mode est déduit du port socat réel (4003=live, 4004=paper) : reflète la
    # connexion effective, plus fiable qu'une valeur Settings potentiellement périmée.
    mode_from_port = {4003: 'live', 4004: 'paper'}.get(status.get('port'))
    status['trading_mode'] = mode_from_port or Settings.get('ibkr_trading_mode', 'live')
    return jsonify(status)


@bp.route('/api/ibkr/connect', methods=['POST'])
@require_admin
def ibkr_connect():
    """Tente une connexion (ou reconnexion) à IB Gateway."""
    result = ibkr_service.connect()
    return jsonify(result), 200 if result['success'] else 503


@bp.route('/api/ibkr/trading-mode', methods=['POST'])
@require_admin
def ibkr_set_trading_mode():
    """
    Bascule entre live et paper. Recrée le gateway dans le nouveau mode
    (→ 2FA requise) et repointe l'app sur le bon port socat.
    Body JSON: { mode: 'live' | 'paper' }
    """
    data = request.get_json() or {}
    mode = (data.get('mode') or '').lower()
    if mode not in ('live', 'paper'):
        return jsonify({'success': False, 'error': "mode doit être 'live' ou 'paper'"}), 400

    current = Settings.get('ibkr_trading_mode', 'live')
    if mode == current and ibkr_service.get_status()['connected']:
        return jsonify({'success': True, 'mode': mode, 'message': f'Déjà en mode {mode}'})

    # Récupérer les credentials chiffrés
    secret = current_app.config.get('SECRET_KEY', '')
    enc_user = Settings.get('ibkr_username_enc')
    enc_pass = Settings.get('ibkr_password_enc')
    if not enc_user or not enc_pass:
        return jsonify({'success': False,
                        'error': 'Identifiants IBKR absents — saisissez-les d\'abord'}), 400
    try:
        username = decrypt_credential(enc_user, secret)
        password = decrypt_credential(enc_pass, secret)
    except Exception:
        return jsonify({'success': False,
                        'error': 'Déchiffrement des identifiants impossible (SECRET_KEY changé ?)'}), 500

    Settings.set('ibkr_trading_mode', mode)

    # Notifier avant la 2FA (push uniquement — plus rapide qu'un email)
    try:
        import push_service
        push_service.send_push_all(
            title='🔐 IB Gateway — 2FA requis',
            body='Le gateway IBKR redémarre. Approuvez la 2FA sur votre téléphone IBKR dans quelques secondes.',
            url='/',
            tag='ibkr-2fa',
        )
    except Exception:
        pass

    _ibkr_update_env_and_restart(username, password, mode)

    return jsonify({
        'success': True,
        'mode': mode,
        'port': IBKR_SOCAT_PORT.get(mode),
        'message': f'Bascule en {mode} — gateway en redémarrage (~90s), 2FA requise sur votre téléphone',
    })


@bp.route('/api/ibkr/credentials', methods=['POST'])
@require_admin
def ibkr_save_credentials():
    """
    Sauvegarde les identifiants IBKR, met à jour le .env et redémarre IB Gateway.
    Body JSON: { username, password, trading_mode }
    """
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    trading_mode = data.get('trading_mode', 'live')

    if not username or not password:
        return jsonify({'success': False, 'error': 'username et password requis'}), 400

    # Stocker chiffré en base
    secret = current_app.config.get('SECRET_KEY', '')
    Settings.set('ibkr_username_enc', encrypt_credential(username, secret))
    Settings.set('ibkr_password_enc', encrypt_credential(password, secret))
    Settings.set('ibkr_trading_mode', trading_mode)

    # Notifier l'utilisateur avant la 2FA
    try:
        get_email_service().envoyer_notification_gateway()
    except Exception:
        pass

    # Mettre à jour le .env sur le serveur et redémarrer IB Gateway
    _ibkr_update_env_and_restart(username, password, trading_mode)

    return jsonify({'success': True, 'message': 'Identifiants sauvegardés — IB Gateway en cours de démarrage (~90s)'})


# Port SOCAT du gateway (accessible depuis le réseau Docker) selon le mode.
# Live  : API interne 4001 → socat 4003
# Paper : API interne 4002 → socat 4004
IBKR_SOCAT_PORT = {'live': 4003, 'paper': 4004}


def _ibkr_set_env_vars(updates: dict):
    """
    Met à jour des variables dans le .env hôte (monté en /app/.env.host).
    Déduplique les clés en double (garde une seule occurrence) pour éviter les
    incohérences : python-dotenv prend la dernière valeur, ce qui pouvait annuler
    un changement de mode si un doublon subsistait.
    """
    import re
    env_path = '/app/.env.host'
    try:
        with open(env_path, 'r') as f:
            lines = f.read().splitlines()

        # Appliquer les updates et dédupliquer (dernière occurrence gagne)
        result_lines = []
        seen = set()
        applied = set()
        for line in lines:
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', line)
            if not m:
                result_lines.append(line)
                continue
            key = m.group(1)
            if key in seen:
                continue  # doublon → on l'enlève
            seen.add(key)
            if key in updates:
                result_lines.append(f'{key}={updates[key]}')
                applied.add(key)
            else:
                result_lines.append(line)

        # Ajouter les nouvelles clés absentes du fichier
        for key, value in updates.items():
            if key not in applied:
                result_lines.append(f'{key}={value}')

        with open(env_path, 'w') as f:
            f.write('\n'.join(result_lines) + '\n')
        return True
    except Exception as e:
        current_app.logger.warning('_ibkr_set_env_vars: %s', e)
        return False


def _recreate_gateway(username: str, password: str, trading_mode: str):
    """
    Recrée le conteneur IB Gateway via le SDK Docker, en répliquant fidèlement
    les options du docker-compose.yml (healthcheck, auto-restart, autoheal, etc.).
    Lancé en thread car le gateway met ~90s à démarrer + 2FA.
    """
    import threading

    def _restart():
        try:
            import docker as docker_sdk
            client = docker_sdk.from_env()
            net_name = 'momentum-app_internal'
            for c in client.containers.list(all=True, filters={'name': 'ib-gateway'}):
                c.stop()
                c.remove()
            # Créer sans démarrer, puis connecter au réseau avec l'alias DNS
            # 'ib-gateway' (sinon seul le nom du conteneur est résolvable, et l'app
            # qui cherche 'ib-gateway' échoue avec "name resolution"). docker-compose
            # crée cet alias automatiquement, mais le SDK doit le faire explicitement.
            container = client.containers.create(
                'ghcr.io/gnzsnz/ib-gateway:stable',
                name='momentum-app-ib-gateway-1',
                detach=True,
                restart_policy={'Name': 'unless-stopped'},
                labels={'autoheal': 'true'},
                environment={
                    'TWS_USERID': username,
                    'TWS_PASSWORD': password,
                    'TRADING_MODE': trading_mode,
                    'TWS_SETTINGS_PATH': '/home/ibgateway/Jts',
                    'VNC_SERVER_PASSWORD': 'changeme',
                    'TWS_ACCEPT_INCOMING': 'accept',
                    'AUTO_RESTART_TIME': '11:30 PM',
                    'TIME_ZONE': 'America/New_York',
                    'RELOGIN_AFTER_TWOFA_TIMEOUT': 'yes',
                    'TWOFA_TIMEOUT_ACTION': 'restart',
                },
                ports={'5900/tcp': ('127.0.0.1', 5900)},
                healthcheck={
                    'test': ["CMD-SHELL", "bash -c 'echo > /dev/tcp/127.0.0.1/4001' || exit 1"],
                    'interval': 60_000_000_000, 'timeout': 10_000_000_000,
                    'retries': 3, 'start_period': 180_000_000_000,
                },
            )
            network = client.networks.get(net_name)
            network.connect(container, aliases=['ib-gateway'])
            container.start()
            current_app.logger.info('IB Gateway recréé (mode=%s, alias=ib-gateway)', trading_mode)
        except Exception as e:
            current_app.logger.warning('_recreate_gateway: %s', e)

    threading.Thread(target=_restart, daemon=True).start()


def _ibkr_update_env_and_restart(username: str, password: str, trading_mode: str):
    """Met à jour le .env (credentials + mode + port socat) et recrée le gateway."""
    port = IBKR_SOCAT_PORT.get(trading_mode, 4003)
    _ibkr_set_env_vars({
        'IB_USERNAME': username,
        'IB_PASSWORD': password,
        'IB_TRADING_MODE': trading_mode,
        'IB_GATEWAY_PORT': port,
    })
    # Pointer l'app sur le bon port socat et forcer la reconnexion
    ibkr_service.port = port
    ibkr_service.disconnect()
    _recreate_gateway(username, password, trading_mode)


@bp.route('/api/ibkr/positions', methods=['GET'])
@require_admin
def ibkr_positions():
    """Retourne les positions ouvertes depuis IB Gateway."""
    try:
        if not ibkr_service.ensure_connected():
            return jsonify({'success': False, 'error': 'Reconnexion IBKR impossible'}), 503
        positions = ibkr_service.get_positions()
        return jsonify({'success': True, 'positions': positions, 'count': len(positions)})
    except ConnectionError as e:
        return jsonify({'success': False, 'error': str(e)}), 503
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/ibkr/portfolio-stats', methods=['GET'])
@require_admin
def ibkr_portfolio_stats():
    """Stats complètes du portefeuille : positions, P&L, allocation, rendement."""
    try:
        if not ibkr_service.ensure_connected():
            return jsonify({'success': False, 'error': 'Reconnexion IBKR impossible'}), 503
        stats = ibkr_service.get_portfolio_stats()
        return jsonify({'success': True, **stats})
    except ConnectionError as e:
        return jsonify({'success': False, 'error': str(e)}), 503
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


RANGE_TO_DAYS = {
    '1W': 7, '1M': 30, '3M': 90, '6M': 180,
    '1Y': 365, '3Y': 1095, '5Y': 1825, 'ALL': 3650,
}


@bp.route('/api/perf/dashboard', methods=['GET'])
@require_admin
def perf_dashboard():
    """
    Tableau de bord Performance. Paramètre ?range=1W|1M|3M|6M|1Y|3Y|5Y|YTD|ALL.

    Source NAV : Flex UNIQUEMENT (PortfolioSnapshot). Pas de reconstruction.
    Si Flex insuffisant pour la période → message clair, pas de mélange paper/live.
    Positions actuelles (P&L, prix courants) : IBKR si connecté, sinon omises.
    """
    import pandas as pd
    range_key = (request.args.get('range') or '1Y').upper()

    try:
        from datetime import date as _date
        if range_key == 'YTD':
            nb_jours = (_date.today() - _date(_date.today().year, 1, 1)).days + 1
        else:
            nb_jours = RANGE_TO_DAYS.get(range_key, 365)

        cutoff = pd.Timestamp(datetime.now()) - pd.Timedelta(days=nb_jours)

        def _tz_naive(s):
            if getattr(s.index, 'tz', None) is not None:
                s.index = s.index.tz_localize(None)
            return s

        # ── Source NAV : Flex UNIQUEMENT ─────────────────────────────────────
        from models import PortfolioSnapshot, Dividend, CashFlow
        all_snaps = (PortfolioSnapshot.query
                     .order_by(PortfolioSnapshot.date.asc()).all())
        snaps = [s for s in all_snaps if pd.Timestamp(s.date) >= cutoff]

        n_total = len(all_snaps)
        last_date = all_snaps[-1].date.isoformat() if all_snaps else None

        if len(snaps) < 2:
            return jsonify({
                'success': True, 'empty': True,
                'nav_source': 'flex',
                'message': (
                    f'Données Flex insuffisantes pour la période "{range_key}" : '
                    f'{len(snaps)} snapshot(s) sur {nb_jours} jours. '
                    f'Total en base : {n_total}. Dernier : {last_date}. '
                    f'Synchronisez Flex depuis les Paramètres → onglet IBKR.'
                ),
                'flex_stats': {
                    'total_snapshots': n_total,
                    'snapshots_in_range': len(snaps),
                    'last_snapshot_date': last_date,
                },
            })

        nav = pd.Series({pd.Timestamp(s.date): s.nav for s in snaps}).sort_index()
        nav = _tz_naive(nav)

        last_snap = snaps[-1]
        cash_flex  = last_snap.cash    # None si pas encore extrait du XML
        nav_total  = last_snap.nav     # total compte officiel (positions + cash)

        # Diagnostique : quel % des snapshots ont le cash renseigné ?
        snaps_with_cash = sum(1 for s in all_snaps if s.cash is not None)

        # ── Benchmark SPY ────────────────────────────────────────────────────
        svc = get_momentum_service()
        bench_index = None
        bench_cagr  = None
        if svc:
            try:
                spy_df, _ = svc._fetch_daily_adjusted('SPY', nb_jours)
                if spy_df is not None and not spy_df.empty:
                    spy = _tz_naive(spy_df['adjClose'].copy())
                    spy = spy[spy.index >= cutoff]
                    if len(spy) >= 2:
                        bench_index = spy / spy.iloc[0]
                        bd = max(1, (bench_index.index[-1] - bench_index.index[0]).days)
                        bench_cagr = float(bench_index.iloc[-1] ** (365.0 / bd) - 1)
            except Exception:
                pass

        # ── TWR via Modified Dietz : HPR_t = (NAV_t - NAV_{t-1} - CF_t) / NAV_{t-1}
        # Soustrait exactement chaque dépôt/retrait du numérateur, quel que soit
        # son montant — contrairement à l'ancienne heuristique (seuil 20%).
        all_cf = CashFlow.query.filter(
            CashFlow.date >= nav.index[0].date()
        ).order_by(CashFlow.date).all()
        cf_by_date: dict = {}
        for cf in all_cf:
            ts = pd.Timestamp(cf.date)
            cf_by_date[ts] = cf_by_date.get(ts, 0.0) + cf.amount
        cf_series = pd.Series(cf_by_date, dtype=float).reindex(nav.index).fillna(0.0)

        nav_prev  = nav.shift(1)
        hpr       = (nav - nav_prev - cf_series) / nav_prev
        # Garde-fou : exclut les valeurs manifestement aberrantes (erreur de données Flex,
        # ex. snapshot à 0 ou jump × 100 non lié à un dépôt). N'impacte pas les vraies
        # journées exceptionnelles car ±95% en une séance est physiquement impossible.
        hpr       = hpr.fillna(0.0).clip(-0.95, 0.95)

        twr_index = (1 + hpr).cumprod()
        twr_index = twr_index / twr_index.iloc[0]

        daily_ret = hpr.iloc[1:]  # exclure le premier NaN converti en 0
        days      = max(1, (nav.index[-1] - nav.index[0]).days)
        total_ret = float(twr_index.iloc[-1] - 1)
        cagr      = float(twr_index.iloc[-1] ** (365.0 / days) - 1) if days >= 1 else 0.0
        vol_ann   = float(daily_ret.std() * (252 ** 0.5)) if len(daily_ret) > 1 else 0.0
        rf        = 0.04
        sharpe    = float((cagr - rf) / vol_ann) if vol_ann > 1e-9 else 0.0
        cummax    = twr_index.cummax()
        drawdown  = (twr_index - cummax) / cummax
        max_dd    = float(drawdown.min())

        # ── Heatmaps ─────────────────────────────────────────────────────────
        def _fmt_index(s):
            return [{'date': idx.strftime('%Y-%m-%d'), 'value': round(float(v) * 100, 4)}
                    for idx, v in s.items()]

        daily_rets_raw = twr_index.pct_change().dropna()
        daily_returns  = [
            {'date': idx.strftime('%Y-%m-%d'), 'return_pct': round(float(v) * 100, 2)}
            for idx, v in daily_rets_raw.items()
        ]
        monthly = twr_index.resample('ME').last().pct_change().dropna()
        monthly_returns = [
            {'year': idx.year, 'month': idx.month, 'return_pct': round(float(v) * 100, 2)}
            for idx, v in monthly.items()
        ]
        weekly = twr_index.resample('W').last().pct_change().dropna()
        weekly_returns = [
            {'year': idx.isocalendar()[0], 'week': idx.isocalendar()[1],
             'return_pct': round(float(v) * 100, 2), 'date': idx.strftime('%Y-%m-%d')}
            for idx, v in weekly.items()
        ]

        # ── Dividendes ───────────────────────────────────────────────────────
        div_rows = Dividend.query.filter(Dividend.date >= cutoff.date()).all()
        dividends_total = round(sum(d.amount for d in div_rows), 2)
        dividends_by_ticker: dict = {}
        for d in div_rows:
            dividends_by_ticker[d.ticker] = round(dividends_by_ticker.get(d.ticker, 0) + d.amount, 2)

        # ── Flux de capitaux ─────────────────────────────────────────────────
        cf_rows = CashFlow.query.filter(CashFlow.date >= cutoff.date()).order_by(CashFlow.date).all()
        cash_flow_list = [cf.to_dict() for cf in cf_rows]
        cf_monthly: dict = {}
        for cf in cf_rows:
            key = cf.date.strftime('%Y-%m')
            cf_monthly[key] = round((cf_monthly.get(key) or 0) + cf.amount, 2)
        cf_monthly_list = [{'month': k, 'amount': v} for k, v in sorted(cf_monthly.items())]

        # ── Positions + cash live : IBKR si connecté (optionnel) ────────────
        positions       = []
        positions_value = 0.0
        ibkr_connected  = False
        cash_ibkr       = None
        try:
            if ibkr_service.ensure_connected():
                stats_ibkr      = ibkr_service.get_portfolio_stats()
                positions       = stats_ibkr.get('positions', [])
                positions_value = stats_ibkr.get('total_value', 0.0)
                cash_ibkr       = ibkr_service.get_cash_balance()
                ibkr_connected  = True
        except Exception as e:
            current_app.logger.warning('perf_dashboard: positions IBKR non disponibles: %s', e)

        # Cash : priorité IBKR live (compte courant), sinon dernier snapshot Flex
        cash_display = cash_ibkr if cash_ibkr is not None else cash_flex

        # NAV live = positions + cash IBKR si disponible, sinon dernier snapshot Flex
        nav_live = (positions_value + cash_display) if (ibkr_connected and cash_display is not None) else nav_total

        # Recalculer les allocations sur la NAV live (cash inclus)
        ref_nav = nav_live if nav_live and nav_live > 0 else nav_total
        if ref_nav and ref_nav > 0:
            for p in positions:
                mv = p.get('market_value') or 0
                p['allocation_pct_nav'] = round(mv / ref_nav * 100, 2)

        cash_pct_of_nav = round(cash_display / ref_nav * 100, 1) if (cash_display and ref_nav) else None

        top5_alloc  = sum(sorted([p.get('allocation_pct_nav', p.get('allocation_pct', 0))
                                  for p in positions], reverse=True)[:5])
        best_pos    = max(positions, key=lambda p: p.get('unrealized_pnl', 0), default=None) if positions else None
        worst_pos   = min(positions, key=lambda p: p.get('unrealized_pnl', 0), default=None) if positions else None
        winners     = [p for p in positions if (p.get('unrealized_pnl') or 0) > 0]

        flex_account_id = Settings.get('flex_account_id', '')
        is_paper = bool(flex_account_id and flex_account_id.upper().startswith('DU'))

        return jsonify({
            'success': True,
            'range': range_key,
            'nav_source': 'flex',
            'ibkr_connected': ibkr_connected,
            'flex_account_id': flex_account_id,
            'is_paper': is_paper,
            'flex_stats': {
                'total_snapshots': n_total,
                'snapshots_in_range': len(snaps),
                'last_snapshot_date': last_date,
                'snapshots_with_cash': snaps_with_cash,
            },
            'kpis': {
                'total_value':    round(nav_live if nav_live else nav_total, 2),
                'positions_value': round(positions_value, 2),
                'cash':           round(cash_display, 2) if cash_display is not None else None,
                'cash_pct':       cash_pct_of_nav,
                'cash_source':    'ibkr' if cash_ibkr is not None else ('flex' if cash_flex is not None else None),
                'total_return_pct': round(total_ret * 100, 2),
                'cagr_pct':       round(cagr * 100, 2),
                'bench_cagr_pct': round(bench_cagr * 100, 2) if bench_cagr is not None else None,
                'cagr_vs_bench_pct': round((cagr - bench_cagr) * 100, 2) if bench_cagr is not None else None,
                'sharpe':         round(sharpe, 2),
                'vol_annual_pct': round(vol_ann * 100, 2),
                'max_drawdown_pct': round(max_dd * 100, 2),
                'unrealized_pnl': sum(p.get('unrealized_pnl') or 0 for p in positions),
                'realized_pnl':   None,
                'positions_count': len(positions),
                'winners_count':  len(winners),
                'top5_concentration_pct': round(top5_alloc, 1),
                'best_position':  {'ticker': best_pos['ticker'],
                                   'pnl': round(best_pos['unrealized_pnl'], 2)} if best_pos else None,
                'worst_position': {'ticker': worst_pos['ticker'],
                                   'pnl': round(worst_pos['unrealized_pnl'], 2)} if worst_pos else None,
                'dividends_total': dividends_total,
            },
            'timeseries': {
                'portfolio':  _fmt_index(twr_index),
                'benchmark':  _fmt_index(bench_index) if bench_index is not None else [],
            },
            'drawdown': [{'date': idx.strftime('%Y-%m-%d'), 'value': round(float(v) * 100, 2)}
                         for idx, v in drawdown.items()],
            'daily_returns':    daily_returns,
            'monthly_returns':  monthly_returns,
            'weekly_returns':   weekly_returns,
            'positions':        positions,
            'cash_flows':       cash_flow_list,
            'cash_flows_monthly': cf_monthly_list,
            'dividends_by_ticker': [{'ticker': t, 'amount': a}
                                    for t, a in sorted(dividends_by_ticker.items(), key=lambda x: -x[1])],
        })
    except Exception as e:
        current_app.logger.exception('Erreur dans perf_dashboard')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/flex/preview', methods=['GET'])
@require_admin
def flex_preview():
    """
    Récupère et parse un rapport Flex LIVE sans rien sauvegarder.
    Permet de vérifier le format, le contenu et la présence du cash.
    Retourne un résumé détaillé pour le débogage.
    """
    enc_token = Settings.get('flex_token_enc')
    query_id  = Settings.get('flex_query_id')
    if not enc_token or not query_id:
        return jsonify({'success': False, 'error': 'Flex non configuré (token + query_id)'}), 400
    try:
        token = decrypt_credential(enc_token, current_app.config.get('SECRET_KEY', ''))
    except Exception:
        return jsonify({'success': False, 'error': 'Déchiffrement du token impossible'}), 500

    try:
        parsed = flex_service.fetch_and_parse(token, query_id)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Flex fetch : {e}'}), 502

    nav_rows  = parsed.get('nav', [])
    trades    = parsed.get('trades', [])
    dividends = parsed.get('dividends', [])
    cash_flows = parsed.get('cash_flows', [])

    # Résumé des champs cash dans les lignes NAV
    cash_present  = [r for r in nav_rows if r.get('cash') is not None]
    cash_missing  = [r for r in nav_rows if r.get('cash') is None]

    # Trier par date pour les extraits
    nav_sorted = sorted(nav_rows, key=lambda r: r['date'])

    def _fmt(r):
        return {
            'date': r['date'].isoformat() if hasattr(r['date'], 'isoformat') else str(r['date']),
            'nav':  round(r['nav'], 2),
            'cash': round(r['cash'], 2) if r.get('cash') is not None else None,
        }

    account_id = parsed.get('account_id') or ''
    is_paper = bool(account_id.upper().startswith('DU'))

    return jsonify({
        'success':    True,
        'account_id': account_id,
        'is_paper':   is_paper,
        'nav': {
            'count':        len(nav_rows),
            'with_cash':    len(cash_present),
            'without_cash': len(cash_missing),
            'date_range':   [
                nav_sorted[0]['date'].isoformat() if nav_sorted else None,
                nav_sorted[-1]['date'].isoformat() if nav_sorted else None,
            ],
            'first_5':  [_fmt(r) for r in nav_sorted[:5]],
            'last_5':   [_fmt(r) for r in nav_sorted[-5:]],
        },
        'trades':    {'count': len(trades)},
        'dividends': {'count': len(dividends)},
        'cash_flows': {
            'count':   len(cash_flows),
            'sample':  [
                {
                    'date': cf['date'].isoformat() if hasattr(cf['date'], 'isoformat') else str(cf['date']),
                    'amount': cf['amount'],
                    'description': cf.get('description', ''),
                }
                for cf in cash_flows[:10]
            ],
        },
    })


@bp.route('/api/flex/credentials', methods=['POST'])
@require_admin
def flex_save_credentials():
    """Sauvegarde le token + query_id Flex (chiffrés). Body: { token, query_id }"""
    data = request.get_json() or {}
    token = (data.get('token') or '').strip()
    query_id = (data.get('query_id') or '').strip()
    if not token or not query_id:
        return jsonify({'success': False, 'error': 'token et query_id requis'}), 400
    secret = current_app.config.get('SECRET_KEY', '')
    Settings.set('flex_token_enc', encrypt_credential(token, secret))
    Settings.set('flex_query_id', query_id)
    return jsonify({'success': True, 'message': 'Identifiants Flex sauvegardés'})


@bp.route('/api/flex/status', methods=['GET'])
def flex_status():
    """Statut Flex : configuré ? dernière synchro ? volumes importés. Détecte paper vs live."""
    from models import PortfolioSnapshot, Transaction, Dividend, CashFlow
    configured = bool(Settings.get('flex_token_enc') and Settings.get('flex_query_id'))
    account_id = Settings.get('flex_account_id', '')
    is_paper = bool(account_id and account_id.upper().startswith('DU'))
    return jsonify({
        'configured': configured,
        'account_id': account_id,
        'is_paper': is_paper,
        'last_sync': Settings.get('flex_last_sync'),
        'last_error': Settings.get('flex_last_error'),
        'snapshots': PortfolioSnapshot.query.count(),
        'transactions': Transaction.query.count(),
        'dividends': Dividend.query.count(),
        'cash_flows': CashFlow.query.count(),
    })


@bp.route('/api/flex/purge-outliers', methods=['POST'])
@require_admin
def flex_purge_outliers():
    """
    Supprime les snapshots dont le NAV est incohérent avec la médiane (ratio > 10×).
    Utile pour effacer une synchro accidentelle d'un autre compte.
    Body optionnel : {"dry_run": true} pour voir sans supprimer.
    """
    from models import db, PortfolioSnapshot
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get('dry_run', False))

    all_snaps = PortfolioSnapshot.query.order_by(PortfolioSnapshot.date).all()
    if not all_snaps:
        return jsonify({'success': True, 'deleted': 0, 'message': 'Aucun snapshot'})

    navs = sorted([s.nav for s in all_snaps])
    median = navs[len(navs) // 2]
    outliers = [s for s in all_snaps if s.nav > median * 10 or s.nav < median / 10]

    if not dry_run:
        for s in outliers:
            db.session.delete(s)
        db.session.commit()

    return jsonify({
        'success': True,
        'dry_run': dry_run,
        'median_nav': round(median, 2),
        'deleted': len(outliers),
        'outliers': [{'date': s.date.isoformat(), 'nav': round(s.nav, 2)} for s in outliers],
        'remaining': PortfolioSnapshot.query.count() if not dry_run else len(all_snaps) - len(outliers),
    })


@bp.route('/api/flex/sync', methods=['POST'])
@require_admin
def flex_sync():
    """
    Récupère le rapport Flex et importe NAV / transactions / dividendes en base.
    Données officielles IBKR (exactes).
    """
    from models import db, PortfolioSnapshot, Transaction, Dividend, CashFlow

    enc_token = Settings.get('flex_token_enc')
    query_id = Settings.get('flex_query_id')
    if not enc_token or not query_id:
        return jsonify({'success': False, 'error': 'Flex non configuré (token + query_id)'}), 400

    try:
        token = decrypt_credential(enc_token, current_app.config.get('SECRET_KEY', ''))
    except Exception:
        return jsonify({'success': False, 'error': 'Déchiffrement du token impossible'}), 500

    try:
        parsed = flex_service.fetch_and_parse(token, query_id)
    except Exception as e:
        Settings.set('flex_last_error', str(e)[:300])
        return jsonify({'success': False, 'error': f'Flex : {e}'}), 502

    # Garde : détection changement de compte / mélange paper-live
    data = request.get_json(silent=True) or {}
    force = bool(data.get('force', False))
    new_account_id = (parsed.get('account_id') or '').strip()
    stored_account_id = (Settings.get('flex_account_id') or '').strip()
    is_paper_new = new_account_id.upper().startswith('DU')

    # Bloquer si le rapport est manifestement paper (DU...) et qu'on n'est pas en force
    if is_paper_new and not force:
        Settings.set('flex_last_error', f'Compte paper détecté ({new_account_id}). Utilisez force=true pour confirmer.')
        return jsonify({
            'success': False,
            'error': f'Le rapport Flex appartient à un compte PAPER ({new_account_id}). Vérifiez que votre Flex Query pointe sur votre compte LIVE.',
            'account_id': new_account_id,
            'is_paper': True,
            'force_available': True,
        }), 409

    # Avertir si le compte change (sans bloquer, pour permettre une migration)
    account_changed = (stored_account_id and new_account_id and stored_account_id != new_account_id)

    # Détection heuristique : NAV incompatible avec l'historique existant (×10)
    nav_inconsistency = None
    if not force and parsed.get('nav'):
        from models import PortfolioSnapshot as _PS
        existing_navs = [s.nav for s in _PS.query.all()]
        if existing_navs:
            median_existing = sorted(existing_navs)[len(existing_navs) // 2]
            new_navs = [r['nav'] for r in parsed['nav']]
            median_new = sorted(new_navs)[len(new_navs) // 2]
            if median_existing > 0 and (median_new / median_existing > 10 or median_new / median_existing < 0.1):
                nav_inconsistency = {
                    'median_existing': round(median_existing, 2),
                    'median_new': round(median_new, 2),
                    'ratio': round(median_new / median_existing, 2),
                }
                return jsonify({
                    'success': False,
                    'error': (
                        f'Incohérence NAV détectée : médiane existante {median_existing:.0f}$ vs '
                        f'médiane rapport {median_new:.0f}$ (ratio {nav_inconsistency["ratio"]}×). '
                        f'Le rapport semble pointer sur un autre compte. Passez force=true pour importer quand même.'
                    ),
                    'account_id': new_account_id,
                    'nav_inconsistency': nav_inconsistency,
                    'force_available': True,
                }), 409

    nav_n = trade_n = div_n = cf_n = 0

    # Toutes les lectures d'existence d'abord, puis les écritures — et on désactive
    # l'autoflush pour éviter qu'une query déclenche un flush prématuré (cause de
    # l'erreur de contrainte unique vue lors d'un import partiel).
    existing_snap = {s.date: s for s in PortfolioSnapshot.query.all()}
    existing_tx = {(t.date.date() if hasattr(t.date, 'date') else t.date, t.ticker,
                    round(t.quantity, 4), round(t.price, 4))
                   for t in Transaction.query.all()}
    existing_div = {(d.date, d.ticker, round(d.amount, 2)) for d in Dividend.query.all()}
    existing_cf = {(cf.date, round(cf.amount, 2)) for cf in CashFlow.query.all()}

    with db.session.no_autoflush:
        # NAV + cash → PortfolioSnapshot. parsed['nav'] peut contenir plusieurs lignes
        # pour une même date → on déduplique (dernière valeur) avant insertion.
        nav_by_date = {}
        for row in parsed['nav']:
            nav_by_date[row['date']] = row  # garde toute la ligne (nav + cash)
        for d, row in nav_by_date.items():
            if d in existing_snap:
                existing_snap[d].nav = row['nav']
                if row.get('cash') is not None:
                    existing_snap[d].cash = row['cash']
            else:
                db.session.add(PortfolioSnapshot(
                    date=d, nav=row['nav'], cash=row.get('cash')))
                existing_snap[d] = True
                nav_n += 1

        # Transactions (dédup par date+ticker+qty+price)
        for tr in parsed['trades']:
            key = (tr['date'], tr['ticker'], round(tr['quantity'], 4), round(tr['price'], 4))
            if key in existing_tx:
                continue
            existing_tx.add(key)
            db.session.add(Transaction(
                date=datetime.combine(tr['date'], datetime.min.time()),
                ticker=tr['ticker'], type=tr['type'], quantity=tr['quantity'],
                price=tr['price'], amount=tr['amount'], currency=tr['currency'],
            ))
            trade_n += 1

        # Dividendes (dédup par date+ticker+amount)
        for dv in parsed['dividends']:
            key = (dv['date'], dv['ticker'], round(dv['amount'], 2))
            if key in existing_div:
                continue
            existing_div.add(key)
            db.session.add(Dividend(date=dv['date'], ticker=dv['ticker'],
                                    amount=dv['amount'], currency=dv['currency']))
            div_n += 1

        # Flux de capitaux / dépôts / retraits (dédup par date+amount)
        for cf in parsed.get('cash_flows', []):
            key = (cf['date'], round(cf['amount'], 2))
            if key in existing_cf:
                continue
            existing_cf.add(key)
            db.session.add(CashFlow(
                date=cf['date'], amount=cf['amount'],
                description=cf.get('description', ''), currency=cf.get('currency', 'USD'),
            ))
            cf_n += 1

    db.session.commit()
    Settings.set('flex_last_sync', datetime.now().isoformat())
    Settings.set('flex_last_error', '')

    # Persister l'account_id pour détecter paper vs live sans refetch
    account_id = new_account_id
    if account_id:
        Settings.set('flex_account_id', account_id)

    is_paper = account_id.upper().startswith('DU')

    return jsonify({
        'success': True,
        'account_id': account_id,
        'is_paper': is_paper,
        'account_changed': account_changed,
        'imported': {'snapshots': nav_n, 'transactions': trade_n,
                     'dividends': div_n, 'cash_flows': cf_n},
        'totals': {
            'snapshots': PortfolioSnapshot.query.count(),
            'transactions': Transaction.query.count(),
            'dividends': Dividend.query.count(),
            'cash_flows': CashFlow.query.count(),
        },
    })


@bp.route('/api/ibkr/rebalance', methods=['POST'])
@require_admin
def ibkr_rebalance():
    """
    Rééquilibrage via IB Gateway.
    Body JSON: { targets: [{ticker, target_pct, currency?}], dry_run: bool }
    dry_run=true (défaut) → aperçu sans exécution.
    Utilise totalQuantity (actions calculées) — plus fiable que cashQty.
    """
    data = request.get_json() or {}
    targets = data.get('targets', [])
    dry_run = data.get('dry_run', True)

    if not targets:
        return jsonify({'success': False, 'error': 'targets requis'}), 400
    try:
        needed = not dry_run
        if not ibkr_service.ensure_connected(trading_mode=needed):
            return jsonify({'success': False, 'error': 'Reconnexion IBKR impossible'}), 503
        result = ibkr_service.place_rebalance_orders(targets, dry_run=dry_run)
        orders = result['orders']
        placed = [o for o in orders if o.get('status') == 'placed']
        failed = [o for o in orders if o.get('status') == 'failed']
        return jsonify({
            'success': True, 'dry_run': dry_run, 'orders': orders,
            'count': len(orders), 'placed_count': len(placed), 'failed_count': len(failed),
            'total_target_pct': result.get('total_target_pct'),
        })
    except ConnectionError as e:
        return jsonify({'success': False, 'error': str(e)}), 503
    except Exception as e:
        current_app.logger.exception('Erreur rebalance')
        return jsonify({'success': False, 'error': str(e)}), 500




# =============================================================================
# SURVEILLANCE DU MARCHÉ — moteur d'évènements (anti-spam) & briefings
# =============================================================================

