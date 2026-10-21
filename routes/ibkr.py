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

    - NAV : Flex uniquement (PortfolioSnapshot), TWR Modified Dietz avec les
      dépôts/retraits rattachés à la première NAV ≥ leur date (perf_service).
    - Benchmark SPY aligné sur la première NAV de la période (même fenêtre).
    - CAGR et Sharpe : annualisés seulement à partir de 3 mois de données.
    - Positions : IBKR live si connecté, sinon positions ouvertes du dernier
      relevé Flex (source et date renvoyées). Sans source : valeurs None, pas 0.
    - P&L réalisé : somme des ventes de la période (cycle de vie des positions).
    """
    import pandas as pd
    import perf_service as ps
    from models import PortfolioSnapshot, Dividend, CashFlow, MarketPriceBar
    range_key = (request.args.get('range') or '1Y').upper()

    try:
        from datetime import date as _date
        if range_key == 'YTD':
            nb_jours = (_date.today() - _date(_date.today().year, 1, 1)).days + 1
        else:
            nb_jours = RANGE_TO_DAYS.get(range_key, 365)
        cutoff = pd.Timestamp(datetime.now()).normalize() - pd.Timedelta(days=nb_jours)

        all_snaps = PortfolioSnapshot.query.order_by(PortfolioSnapshot.date.asc()).all()
        snaps = [s for s in all_snaps if pd.Timestamp(s.date) >= cutoff]
        n_total = len(all_snaps)
        last_date = all_snaps[-1].date.isoformat() if all_snaps else None
        flex_stats = {
            'total_snapshots': n_total, 'snapshots_in_range': len(snaps),
            'last_snapshot_date': last_date,
            'snapshots_with_cash': sum(1 for s in all_snaps if s.cash is not None),
            'last_sync': Settings.get('flex_last_sync') or None,
        }
        if len(snaps) < 2:
            return jsonify({
                'success': True, 'empty': True, 'nav_source': 'flex', 'flex_stats': flex_stats,
                'message': (f'Pas assez de relevés Flex sur la période « {range_key} » '
                            f'({len(snaps)} jour(s) ; {n_total} en base, dernier le {last_date or "—"}). '
                            f'Synchronise Flex ou choisis une période plus longue.'),
            })

        nav = pd.Series({pd.Timestamp(s.date): s.nav for s in snaps}).sort_index()
        start = nav.index[0]

        # TWR : les flux postérieurs à la 1re NAV (celle-ci inclut déjà les apports passés)
        flows = [(cf.date, cf.amount) for cf in
                 CashFlow.query.filter(CashFlow.date > start.date()).all()]
        twr, daily = ps.twr_from_nav(nav, flows)
        m = ps.risk_metrics(twr, daily)

        # Benchmark SPY sur la même fenêtre
        bench_index, bench_total, bench_cagr = None, None, None
        svc = get_momentum_service()
        if svc:
            try:
                spy_df, _ = svc._fetch_daily_adjusted('SPY', nb_jours + 10)
                if spy_df is not None and not spy_df.empty:
                    spy = spy_df['adjClose'].copy()
                    if getattr(spy.index, 'tz', None) is not None:
                        spy.index = spy.index.tz_localize(None)
                    spy = spy[(spy.index >= start) & (spy.index <= nav.index[-1])]   # même fenêtre que le compte
                    if len(spy) >= 2:
                        bench_index = spy / spy.iloc[0]
                        bench_total = float(bench_index.iloc[-1] - 1)
                        bd = max(1, (bench_index.index[-1] - bench_index.index[0]).days)
                        if bd >= ps.MIN_DAYS_ANNUALIZED:
                            bench_cagr = float(bench_index.iloc[-1] ** (365.0 / bd) - 1)
            except Exception:
                pass

        def _fmt_index(series):
            return [{'date': i.strftime('%Y-%m-%d'), 'value': round(float(v) * 100, 4)}
                    for i, v in series.items()]

        daily_returns = [{'date': i.strftime('%Y-%m-%d'), 'return_pct': round(float(v) * 100, 2)}
                         for i, v in twr.pct_change().dropna().items()]
        monthly_returns = [{'year': i.year, 'month': i.month, 'return_pct': round(float(v) * 100, 2)}
                           for i, v in ps.period_returns(twr, 'ME').items()]
        weekly_returns = [{'year': i.isocalendar()[0], 'week': i.isocalendar()[1],
                           'return_pct': round(float(v) * 100, 2), 'date': i.strftime('%Y-%m-%d')}
                          for i, v in ps.period_returns(twr, 'W').items()]

        # Dividendes nets (retenues à la source déduites) sur la période
        div_rows = Dividend.query.filter(Dividend.date >= start.date()).all()
        dividends_by_ticker = {}
        for d in div_rows:
            dividends_by_ticker[d.ticker] = round(dividends_by_ticker.get(d.ticker, 0) + d.amount, 2)
        dividends_total = round(sum(d.amount for d in div_rows), 2)

        cf_rows = CashFlow.query.filter(CashFlow.date >= start.date()).order_by(CashFlow.date).all()
        cf_monthly = {}
        for cf in cf_rows:
            k = cf.date.strftime('%Y-%m')
            cf_monthly[k] = round(cf_monthly.get(k, 0) + cf.amount, 2)

        # Positions : IBKR live, sinon dernier relevé Flex
        positions, positions_value, cash_live = [], None, None
        positions_source, positions_as_of = None, None
        try:
            if ibkr_service.ensure_connected():
                st = ibkr_service.get_portfolio_stats()
                positions = st.get('positions', [])
                positions_value = st.get('total_value', 0.0)
                cash_live = ibkr_service.get_cash_balance()
                positions_source, positions_as_of = 'ibkr', datetime.now().date().isoformat()
        except Exception as e:
            current_app.logger.warning('perf_dashboard: IBKR indisponible: %s', e)
        if positions_source is None:
            actual, src, as_of = ps.actual_positions(None)
            if actual:
                positions = [{'ticker': tk, 'qty': v['qty'], 'market_price': v.get('price'),
                              'market_value': v.get('value'),
                              'avg_cost': (v['cost_basis'] / v['qty']) if v.get('cost_basis') and v['qty'] else None,
                              'unrealized_pnl': v.get('unrealized')} for tk, v in actual.items()]
                positions_value = sum(p['market_value'] or 0 for p in positions)
                positions_source, positions_as_of = src, as_of

        last_snap = snaps[-1]
        cash = cash_live if cash_live is not None else last_snap.cash
        if positions_source == 'ibkr' and cash is not None:
            total_value = positions_value + cash
        else:
            total_value = last_snap.nav
        ref = total_value if total_value and total_value > 0 else None
        for p in positions:
            p['allocation_pct_nav'] = round((p.get('market_value') or 0) / ref * 100, 2) if ref else None

        has_pos = positions_source is not None
        allocs = sorted((p.get('allocation_pct_nav') or 0 for p in positions), reverse=True)
        best = max(positions, key=lambda p: p.get('unrealized_pnl') or 0) if positions else None
        worst = min(positions, key=lambda p: p.get('unrealized_pnl') or 0) if positions else None

        # P&L réalisé sur la période (ventes datées dans la fenêtre)
        realized = None
        try:
            _, _, events, _ = ps.position_lifecycles(ps.load_transactions())
            realized = round(sum(pnl for d, _, pnl in events if d >= start.date()), 2)
        except Exception as e:
            current_app.logger.warning('perf_dashboard: P&L réalisé indisponible: %s', e)

        flex_account_id = Settings.get('flex_account_id', '')
        pct = lambda x: round(x * 100, 2) if x is not None else None
        return jsonify({
            'success': True, 'range': range_key, 'nav_source': 'flex',
            'ibkr_connected': positions_source == 'ibkr',
            'positions_source': positions_source, 'positions_as_of': positions_as_of,
            'flex_account_id': flex_account_id,
            'is_paper': bool(flex_account_id and flex_account_id.upper().startswith('DU')),
            'flex_stats': flex_stats,
            'period': {'start': start.strftime('%Y-%m-%d'), 'end': nav.index[-1].strftime('%Y-%m-%d'),
                       'days': m['days'], 'annualized': m['cagr'] is not None},
            'kpis': {
                'total_value': round(total_value, 2) if total_value is not None else None,
                'positions_value': round(positions_value, 2) if positions_value is not None else None,
                'cash': round(cash, 2) if cash is not None else None,
                'cash_pct': round(cash / ref * 100, 1) if (cash is not None and ref) else None,
                'cash_source': 'ibkr' if cash_live is not None else ('flex' if last_snap.cash is not None else None),
                'total_return_pct': pct(m['total']),
                'bench_total_pct': pct(bench_total),
                'excess_total_pct': pct(m['total'] - bench_total) if bench_total is not None else None,
                'cagr_pct': pct(m['cagr']),
                'bench_cagr_pct': pct(bench_cagr),
                'cagr_vs_bench_pct': pct(m['cagr'] - bench_cagr) if (m['cagr'] is not None and bench_cagr is not None) else None,
                'sharpe': round(m['sharpe'], 2) if m['sharpe'] is not None else None,
                'vol_annual_pct': pct(m['vol']),
                'max_drawdown_pct': pct(m['max_dd']),
                'unrealized_pnl': round(sum(p.get('unrealized_pnl') or 0 for p in positions), 2) if has_pos else None,
                'realized_pnl': realized,
                'positions_count': len(positions) if has_pos else None,
                'winners_count': sum(1 for p in positions if (p.get('unrealized_pnl') or 0) > 0) if has_pos else None,
                'top5_concentration_pct': round(sum(allocs[:5]), 1) if has_pos and ref else None,
                'best_position': {'ticker': best['ticker'], 'pnl': round(best.get('unrealized_pnl') or 0, 2)} if best else None,
                'worst_position': {'ticker': worst['ticker'], 'pnl': round(worst.get('unrealized_pnl') or 0, 2)} if worst else None,
                'dividends_total': dividends_total,
            },
            'timeseries': {'portfolio': _fmt_index(twr),
                           'benchmark': _fmt_index(bench_index) if bench_index is not None else []},
            'drawdown': [{'date': i.strftime('%Y-%m-%d'), 'value': round(float(v) * 100, 2)}
                         for i, v in m['drawdown'].items()],
            'daily_returns': daily_returns,
            'monthly_returns': monthly_returns,
            'weekly_returns': weekly_returns,
            'positions': positions,
            'cash_flows': [cf.to_dict() for cf in cf_rows],
            'cash_flows_monthly': [{'month': k, 'amount': v} for k, v in sorted(cf_monthly.items())],
            'dividends_by_ticker': [{'ticker': t, 'amount': a}
                                    for t, a in sorted(dividends_by_ticker.items(), key=lambda x: -x[1])],
        })
    except Exception as e:
        current_app.logger.exception('Erreur dans perf_dashboard')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/perf/positions-timeline', methods=['GET'])
@require_admin
def perf_positions_timeline():
    """
    Évolution chronologique de la valeur de marché de chaque position
    (graphe en aires empilées). Reconstruit les quantités détenues jour
    par jour à partir de l'historique des transactions, puis les valorise
    avec les prix journaliers (MarketPriceBar).

    Entrées/sorties visibles : une position apparaît quand elle est achetée
    et retombe à zéro quand elle est soldée (ex. TSLA il y a quelques mois).

    Ancrage des quantités :
    - IBKR connecté → H(t) = qté_actuelle - Σtx_postérieures (le plus fiable,
      gère les positions ouvertes avant le début de l'historique Flex) ;
    - sinon heuristique : H0 = max(0, -min(cumul des tx)) pour rester ≥ 0.

    Paramètre ?range=1W|1M|3M|6M|1Y|3Y|5Y|YTD|ALL.
    """
    import pandas as pd
    from datetime import date as _date
    from models import Transaction, MarketPriceBar
    range_key = (request.args.get('range') or '1Y').upper()

    try:
        if range_key == 'YTD':
            nb_jours = (_date.today() - _date(_date.today().year, 1, 1)).days + 1
        else:
            nb_jours = RANGE_TO_DAYS.get(range_key, 365)

        today  = pd.Timestamp(datetime.now()).normalize()
        cutoff = today - pd.Timedelta(days=nb_jours)

        # ── Transactions (hors forex type EUR.USD) ──────────────────────────
        txs = (Transaction.query
               .filter(~Transaction.ticker.like('%.%'))
               .order_by(Transaction.date.asc()).all())
        if not txs:
            return jsonify({'success': True, 'empty': True,
                            'message': 'Aucune transaction. Synchronisez Flex depuis les Paramètres.'})

        tickers = sorted({t.ticker for t in txs})

        # Quantité signée cumulée par ticker (BUY +, SELL −)
        signed = {}
        for tk in tickers:
            ev = {}
            for t in txs:
                if t.ticker != tk:
                    continue
                if t.type == 'ADJ':
                    q = t.quantity or 0.0                       # quantité déjà signée
                else:
                    q = (t.quantity or 0.0) * (1.0 if t.type == 'BUY' else -1.0)
                ts = pd.Timestamp(t.date).normalize()
                ev[ts] = ev.get(ts, 0.0) + q
            signed[tk] = pd.Series(ev, dtype=float).sort_index().cumsum()

        # ── Ancrage sur les quantités actuelles IBKR si disponible ──────────
        # Ancrage : positions réelles (IBKR live, sinon dernier relevé Flex)
        import perf_service as ps
        current_qty = {}
        anchor = 'heuristic'
        actual, src, _ = ps.actual_positions(ibkr_service)
        if actual is not None:
            current_qty = {tk: v['qty'] for tk, v in actual.items()}
            anchor = src

        first_tx = min(s.index[0] for s in signed.values())
        start = first_tx if range_key == 'ALL' else max(cutoff, first_tx)

        # ── Prix journaliers (close brut, repli adj_close) ──────────────────
        price_rows = (MarketPriceBar.query
                      .filter(MarketPriceBar.ticker.in_(tickers),
                              MarketPriceBar.bar_date >= start.date())
                      .with_entities(MarketPriceBar.ticker, MarketPriceBar.bar_date,
                                     MarketPriceBar.close, MarketPriceBar.adj_close)
                      .all())
        px = {}
        for tk, bd, close, adj in price_rows:
            px.setdefault(tk, {})[pd.Timestamp(bd)] = close if close else adj
        px = {tk: pd.Series(d, dtype=float).sort_index() for tk, d in px.items()}

        # Axe temporel : jours de bourse
        idx = pd.bdate_range(start, today)
        if len(idx) == 0:
            return jsonify({'success': True, 'empty': True,
                            'message': 'Plage trop courte.'})

        val = pd.DataFrame(index=idx)
        for tk in tickers:
            cum = signed[tk]
            cum_now = float(cum.iloc[-1])
            if anchor in ('ibkr', 'flex'):
                h0 = current_qty.get(tk, 0.0) - cum_now
            else:
                h0 = max(0.0, -float(cum.min()))
            holding = (h0 + cum.reindex(idx.union(cum.index)).ffill()
                                .reindex(idx).fillna(0.0)).clip(lower=0.0)
            price = (px.get(tk, pd.Series(dtype=float))
                       .reindex(idx.union(px.get(tk, pd.Series(dtype=float)).index))
                       .ffill().reindex(idx))
            v = (holding * price).fillna(0.0)
            if v.abs().sum() > 0:
                val[tk] = v

        if val.empty or val.shape[1] == 0:
            return jsonify({'success': True, 'empty': True,
                            'message': 'Aucune position valorisable sur la période.'})

        # Échantillonnage hebdomadaire pour les longues plages
        if nb_jours > 200:
            val = val.resample('W-FRI').last().dropna(how='all').fillna(0.0)

        # ── Top tickers par valeur de pointe, reste regroupé en « Autres » ──
        peak = val.max().sort_values(ascending=False)
        TOP = 12
        keep = list(peak.index[:TOP])
        rest = [c for c in val.columns if c not in keep]
        # Inutile de créer un groupe « Autres » pour un unique ticker restant
        if len(rest) == 1:
            keep += rest
            rest = []

        # Ordre d'empilage : par valeur actuelle décroissante
        last_row = val.iloc[-1]
        keep.sort(key=lambda c: last_row.get(c, 0.0), reverse=True)

        dates = [d.strftime('%Y-%m-%d') for d in val.index]
        series = [{'ticker': tk,
                   'values': [round(float(x), 2) for x in val[tk].tolist()],
                   'held_now': bool(last_row.get(tk, 0.0) > 1e-6)}
                  for tk in keep]
        if rest:
            others = val[rest].sum(axis=1)
            series.append({'ticker': f'Autres ({len(rest)})',
                           'values': [round(float(x), 2) for x in others.tolist()],
                           'held_now': bool(others.iloc[-1] > 1e-6)})

        return jsonify({
            'success': True,
            'range': range_key,
            'anchor': anchor,
            'dates': dates,
            'series': series,
        })
    except Exception as e:
        current_app.logger.exception('Erreur dans perf_positions_timeline')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/perf/positions-pnl', methods=['GET'])
@require_admin
def perf_positions_pnl():
    """
    Impact de chaque action sur le portefeuille (cycle de vie complet, coût
    moyen, ordre chronologique) — perf_service.position_lifecycles.

    La reconstruction depuis les transactions Flex est RÉCONCILIÉE avec les
    positions réellement détenues (IBKR live, sinon dernier relevé Flex) :
      - reconstruite ouverte mais plus détenue → soldée (`reconciled`
        'missing_exit', P&L incomplet) : c'était le bug des positions fantômes ;
      - détenue sans historique → ouverte, coût inconnu ('missing_entry') ;
      - quantité différente (division non suivie) → quantité du courtier ('qty_mismatch').
    """
    import perf_service as ps
    from models import Dividend, MarketPriceBar

    try:
        txs = ps.load_transactions()
        actual, source, as_of = ps.actual_positions(ibkr_service)
        if not txs and not actual:
            return jsonify({'success': True, 'positions': [],
                            'message': 'Aucune transaction. Synchronise Flex.'})
        divs = {}
        for d in Dividend.query.all():
            divs[d.ticker] = divs.get(d.ticker, 0.0) + (d.amount or 0.0)
        tickers = sorted({t['ticker'] for t in txs} | set(actual or {}))
        prices = {}
        for tk in tickers:
            if actual and tk in actual and actual[tk].get('price'):
                continue
            row = (MarketPriceBar.query.filter(MarketPriceBar.ticker == tk)
                   .order_by(MarketPriceBar.bar_date.desc()).first())
            if row:
                prices[tk] = row.close if row.close else row.adj_close
        positions, totals, _, recon = ps.position_lifecycles(txs, divs, prices, actual)
        return jsonify({'success': True, 'positions': positions, 'totals': totals,
                        'reconciliation': {'source': source, 'as_of': as_of, 'fixes': recon}})
    except Exception as e:
        current_app.logger.exception('Erreur dans perf_positions_pnl')
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
    Importe le relevé Flex (NAV, transactions exécution par exécution, dividendes
    nets, dépôts/retraits, positions ouvertes). La fenêtre du relevé remplace les
    données existantes (cf. perf_service.import_flex). ?force=true contourne les
    garde-fous compte paper / NAV incohérente.
    """
    import perf_service as ps
    data = request.get_json(silent=True) or {}
    code, payload = ps.run_flex_sync(force=bool(data.get('force', False)))
    return jsonify(payload), code


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
    # Régime BEAR : cibles vides autorisées si explicitement demandé → tout liquider.
    go_cash = bool(data.get('allow_cash', False))

    if not targets and not go_cash:
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

