# -*- coding: utf-8 -*-
"""Routes IBKR, Flex et tableau de bord Performance."""
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

    # Notifier avant la 2FA
    try:
        get_email_service().envoyer_notification_gateway()
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
    Tableau de bord Performance : reconstruit l'évolution du portefeuille en
    buy & hold des positions actuelles (qty × prix historiques persistés), calcule
    les métriques (CAGR, Sharpe, max drawdown, drawdown série, rendements mensuels)
    et compare au S&P 500 (SPY). Paramètre ?range=1W|1M|3M|6M|1Y|3Y|5Y|YTD|ALL.

    Note : approximation buy & hold (pas d'historique de transactions) — la série
    suppose les positions actuelles détenues sur toute la période.
    """
    import pandas as pd
    range_key = (request.args.get('range') or '1Y').upper()

    try:
        if not ibkr_service.ensure_connected():
            return jsonify({'success': False, 'error': 'Reconnexion IBKR impossible'}), 503

        stats = ibkr_service.get_portfolio_stats()
        positions = stats.get('positions', [])
        if not positions:
            return jsonify({'success': True, 'empty': True,
                            'message': 'Aucune position', 'summary': stats})

        # Fenêtre temporelle
        from datetime import date as _date
        if range_key == 'YTD':
            nb_jours = (_date.today() - _date(_date.today().year, 1, 1)).days + 1
        else:
            nb_jours = RANGE_TO_DAYS.get(range_key, 365)

        svc = get_momentum_service()
        cutoff = pd.Timestamp(datetime.now()) - pd.Timedelta(days=nb_jours)

        def _tz_naive(s):
            """Normalise l'index d'une série en tz-naive (mix IBKR tz-aware / DB tz-naive)."""
            if getattr(s.index, 'tz', None) is not None:
                s.index = s.index.tz_localize(None)
            return s

        # Source de la NAV : snapshots Flex réels (prioritaire) sinon reconstruction
        from models import PortfolioSnapshot
        snaps = (PortfolioSnapshot.query
                 .filter(PortfolioSnapshot.date >= cutoff.date())
                 .order_by(PortfolioSnapshot.date.asc()).all())
        nav_source = 'flex'
        if len(snaps) >= 2:
            nav = pd.Series({pd.Timestamp(s.date): s.nav for s in snaps}).sort_index()
        else:
            # 1) Reconstruction buy & hold : Σ qty_i × prix_i(t)
            nav_source = 'reconstruction'
            series = {}
            for p in positions:
                ticker, qty = p['ticker'], (p.get('qty') or 0)
                if abs(qty) < 1e-9:
                    continue
                df, err = svc._fetch_daily_adjusted(ticker, nb_jours) if svc else (None, 'no svc')
                if df is None or df.empty:
                    continue
                series[ticker] = _tz_naive(df['adjClose'] * qty)
            if not series:
                return jsonify({'success': True, 'empty': True,
                                'message': 'Pas de prix historiques disponibles', 'summary': stats})
            nav_df = pd.DataFrame(series).sort_index().ffill().dropna(how='all')
            nav = nav_df.sum(axis=1).dropna()
            nav = nav[nav.index >= cutoff]

        nav = _tz_naive(nav)
        if len(nav) < 2:
            return jsonify({'success': True, 'empty': True,
                            'message': 'Historique insuffisant', 'summary': stats})

        # 2) Benchmark S&P 500 (SPY), rebasé sur la valeur initiale du portefeuille
        bench_series = None
        if svc:
            spy_df, _ = svc._fetch_daily_adjusted('SPY', nb_jours)
            if spy_df is not None and not spy_df.empty:
                spy = _tz_naive(spy_df['adjClose'].copy())
                spy = spy[spy.index >= cutoff]
                if len(spy) >= 2:
                    bench_series = (spy / spy.iloc[0]) * float(nav.iloc[0])

        # 3) Métriques — basées sur le TWR (Time-Weighted Return) qui neutralise
        # les dépôts/retraits. On chaîne les rendements quotidiens en mettant à 0
        # les jours de flux de capitaux (variation > 25% = dépôt/retrait, pas perf).
        raw_ret = nav.pct_change()
        FLOW_THRESHOLD = 0.25
        twr_ret = raw_ret.where(raw_ret.abs() <= FLOW_THRESHOLD, 0.0).fillna(0.0)
        twr_index = (1 + twr_ret).cumprod()            # base 1.0 au départ
        twr_index = twr_index / twr_index.iloc[0]

        daily_ret = twr_ret[twr_ret != 0.0]            # pour vol/sharpe (jours de marché)
        days = max(1, (nav.index[-1] - nav.index[0]).days)
        total_ret = float(twr_index.iloc[-1] - 1)
        cagr = float(twr_index.iloc[-1] ** (365.0 / days) - 1) if days >= 1 else 0.0
        vol_ann = float(daily_ret.std() * (252 ** 0.5)) if len(daily_ret) > 1 else 0.0
        rf = 0.04
        sharpe = float((cagr - rf) / vol_ann) if vol_ann > 1e-9 else 0.0
        # Drawdown sur l'indice TWR (neutralise les flux)
        cummax = twr_index.cummax()
        drawdown = (twr_index - cummax) / cummax
        max_dd = float(drawdown.min())

        # Bench CAGR pour comparaison
        bench_cagr = None
        if bench_series is not None and len(bench_series) >= 2:
            bd = max(1, (bench_series.index[-1] - bench_series.index[0]).days)
            bench_cagr = float((bench_series.iloc[-1] / bench_series.iloc[0]) ** (365.0 / bd) - 1)

        # 4) Rendements mensuels (heatmap) — depuis l'indice TWR
        monthly = twr_index.resample('ME').last().pct_change().dropna()
        monthly_returns = [
            {'year': idx.year, 'month': idx.month, 'return_pct': round(float(v) * 100, 2)}
            for idx, v in monthly.items()
        ]

        # 5) Séries pour les graphes
        def _fmt(s):
            return [{'date': idx.strftime('%Y-%m-%d'), 'value': round(float(v), 2)}
                    for idx, v in s.items()]

        # Courbe de drawdown (en %) depuis l'indice TWR
        drawdown_series = drawdown

        # Dividendes réels sur la période (Flex)
        from models import Dividend
        div_rows = Dividend.query.filter(Dividend.date >= cutoff.date()).all()
        dividends_total = round(sum(d.amount for d in div_rows), 2)
        dividends_by_period = {}
        for d in div_rows:
            key = d.date.strftime('%Y-%m')
            dividends_by_period[key] = round(dividends_by_period.get(key, 0) + d.amount, 2)

        return jsonify({
            'success': True,
            'range': range_key,
            'nav_source': nav_source,
            'kpis': {
                'total_value': stats.get('total_value', 0),
                'total_return_pct': round(total_ret * 100, 2),
                'cagr_pct': round(cagr * 100, 2),
                'bench_cagr_pct': round(bench_cagr * 100, 2) if bench_cagr is not None else None,
                'cagr_vs_bench_pct': round((cagr - bench_cagr) * 100, 2) if bench_cagr is not None else None,
                'sharpe': round(sharpe, 2),
                'vol_annual_pct': round(vol_ann * 100, 2),
                'max_drawdown_pct': round(max_dd * 100, 2),
                'unrealized_pnl': stats.get('total_unrealized_pnl', 0),
                'realized_pnl': stats.get('total_realized_pnl', 0),
                'dividends_total': dividends_total,
            },
            'dividends_by_period': [{'period': k, 'amount': v}
                                    for k, v in sorted(dividends_by_period.items())],
            'timeseries': {
                'portfolio': _fmt(nav),
                'benchmark': _fmt(bench_series) if bench_series is not None else [],
                # Performance TWR en % (neutralise dépôts/retraits) pour la perf relative
                'portfolio_twr_pct': [{'date': idx.strftime('%Y-%m-%d'), 'value': round((float(v) - 1) * 100, 2)}
                                      for idx, v in twr_index.items()],
            },
            'drawdown': [{'date': idx.strftime('%Y-%m-%d'), 'value': round(float(v) * 100, 2)}
                         for idx, v in drawdown.items()],
            'monthly_returns': monthly_returns,
            'positions': positions,
            'summary': stats,
        })
    except Exception as e:
        current_app.logger.exception('Erreur dans perf_dashboard')
        return jsonify({'success': False, 'error': str(e)}), 500


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
    """Statut Flex : configuré ? dernière synchro ? volumes importés."""
    from models import PortfolioSnapshot, Transaction, Dividend
    configured = bool(Settings.get('flex_token_enc') and Settings.get('flex_query_id'))
    return jsonify({
        'configured': configured,
        'last_sync': Settings.get('flex_last_sync'),
        'last_error': Settings.get('flex_last_error'),
        'snapshots': PortfolioSnapshot.query.count(),
        'transactions': Transaction.query.count(),
        'dividends': Dividend.query.count(),
    })


@bp.route('/api/flex/sync', methods=['POST'])
@require_admin
def flex_sync():
    """
    Récupère le rapport Flex et importe NAV / transactions / dividendes en base.
    Données officielles IBKR (exactes).
    """
    from models import db, PortfolioSnapshot, Transaction, Dividend

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

    nav_n = trade_n = div_n = 0

    # Toutes les lectures d'existence d'abord, puis les écritures — et on désactive
    # l'autoflush pour éviter qu'une query déclenche un flush prématuré (cause de
    # l'erreur de contrainte unique vue lors d'un import partiel).
    existing_snap = {s.date: s for s in PortfolioSnapshot.query.all()}
    existing_tx = {(t.date.date() if hasattr(t.date, 'date') else t.date, t.ticker,
                    round(t.quantity, 4), round(t.price, 4))
                   for t in Transaction.query.all()}
    existing_div = {(d.date, d.ticker, round(d.amount, 2)) for d in Dividend.query.all()}

    with db.session.no_autoflush:
        # NAV → PortfolioSnapshot. parsed['nav'] peut contenir plusieurs lignes
        # pour une même date → on déduplique (dernière valeur) avant insertion.
        nav_by_date = {}
        for row in parsed['nav']:
            nav_by_date[row['date']] = row['nav']
        for d, val in nav_by_date.items():
            if d in existing_snap:
                existing_snap[d].nav = val
            else:
                db.session.add(PortfolioSnapshot(date=d, nav=val))
                existing_snap[d] = True  # marquer pour éviter un doublon intra-batch
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

    db.session.commit()
    Settings.set('flex_last_sync', datetime.now().isoformat())
    Settings.set('flex_last_error', '')

    return jsonify({
        'success': True,
        'imported': {'snapshots': nav_n, 'transactions': trade_n, 'dividends': div_n},
        'totals': {
            'snapshots': PortfolioSnapshot.query.count(),
            'transactions': Transaction.query.count(),
            'dividends': Dividend.query.count(),
        },
        'account_id': parsed.get('account_id'),
    })


@bp.route('/api/ibkr/rebalance', methods=['POST'])
@require_admin
def ibkr_rebalance():
    """
    Passe des ordres de rééquilibrage via IB Gateway.
    Body JSON: { targets: [{ticker, target_pct, currency?}], dry_run: bool }
    dry_run=true (défaut) → aperçu sans exécution.
    """
    data = request.get_json() or {}
    targets = data.get('targets', [])
    dry_run = data.get('dry_run', True)

    if not targets:
        return jsonify({'success': False, 'error': 'targets requis'}), 400
    try:
        if not ibkr_service.ensure_connected():
            return jsonify({'success': False, 'error': 'Reconnexion IBKR impossible'}), 503
        result = ibkr_service.place_rebalance_orders(targets, dry_run=dry_run)
        orders = result['orders']
        placed = [o for o in orders if o.get('status') == 'placed']
        failed = [o for o in orders if o.get('status') == 'failed']
        return jsonify({
            'success': True,
            'dry_run': dry_run,
            'orders': orders,
            'count': len(orders),
            'placed_count': len(placed),
            'failed_count': len(failed),
            'total_target_pct': result.get('total_target_pct'),
        })
    except ConnectionError as e:
        return jsonify({'success': False, 'error': str(e)}), 503
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# SURVEILLANCE DU MARCHÉ — moteur d'évènements (anti-spam) & briefings
# =============================================================================

