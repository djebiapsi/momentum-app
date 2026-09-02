# -*- coding: utf-8 -*-
"""Tâches planifiées (extrait de app.py) ; app injecté par scheduler.create_scheduler()."""
from datetime import datetime
from models import db, PanelAction
from services import (ibkr_service, get_momentum_service, get_email_service,
                     get_backtest_service, get_price_data_service, get_news_service,
                     get_fundamentals_collector, get_finra_collector,
                     get_edgar_collector)
from core import (compute_and_save_momentum, compute_and_save_short_signals,
                  compute_and_save_qv, get_qv_market,
                  run_market_monitor, build_briefing_payload)


app = None  # injecté par scheduler.create_scheduler()

# Suivi de la déconnexion IBKR (module-level pour persister entre appels du cron)
_ibkr_down_since = None
_ibkr_down_notified = False   # évite les notifications répétées pour la même coupure


def job_rebalance_reminder():
    """
    Cron mensuel (1er du mois). Calcule le momentum, le sauvegarde, envoie
    l'email de rééquilibrage ET une notification push.
    """
    with app.app_context():
        print(f"[{datetime.now()}] 🔄 Rappel mensuel de rééquilibrage…")
        recommandations, history = compute_and_save_momentum()
        if not recommandations:
            return

        # Push notification
        try:
            import push_service
            nb_top = len([r for r in recommandations.get('recommandations', [])
                          if r.get('signal') == 'Investir'])
            push_service.send_push_all(
                title='🔄 Rééquilibrage mensuel',
                body=f"C'est le 1er du mois — il est temps de rééquilibrer ton portefeuille ({nb_top} positions recommandées).",
                url='/',
                tag='rebalance',
            )
        except Exception as e:
            print(f"⚠️ Push rééquilibrage: {e}")

        email_svc = get_email_service()
        if email_svc.is_configured():
            result = email_svc.envoyer_rebalance_reminder(recommandations, history.id)
            print(f"{'✅' if result['success'] else '❌'} Email rééquilibrage: {result['message']}")
        else:
            print("⚠️ Service email non configuré")


def job_short_signal_monthly():
    """
    Cron mensuel (1er du mois). Calcule le signal short multi-facteurs, le
    sauvegarde, envoie l'email de signal ET une notification push.
    ⚠️ Stratégie en cours de validation — signal indicatif.
    """
    with app.app_context():
        print(f"[{datetime.now()}] 🩳 Signal short mensuel…")
        signal_data, history = compute_and_save_short_signals()
        if not signal_data or not signal_data.get('success'):
            return

        candidates = signal_data.get('candidates', [])
        actionable = [c for c in candidates if c.get('size_factor', 0) > 0]

        # Push notification
        try:
            import push_service
            if candidates:
                body = (f"{len(candidates)} candidats short détectés "
                        f"({len(actionable)} actionnables) — régime "
                        f"{(signal_data.get('regime') or '—').upper()}.")
            else:
                body = "Aucun candidat short ce mois-ci (score insuffisant)."
            push_service.send_push_all(
                title='🩳 Signal short mensuel',
                body=body,
                url='/',
                tag='short_signal',
            )
        except Exception as e:
            print(f"⚠️ Push signal short: {e}")

        email_svc = get_email_service()
        if email_svc.is_configured():
            result = email_svc.envoyer_short_signal(signal_data)
            print(f"{'✅' if result['success'] else '❌'} Email signal short: {result['message']}")
        else:
            print("⚠️ Service email non configuré")


# =============================================================================
# ROUTES - API OPTIONS (PUT & PUT SPREAD)
# =============================================================================

def _is_us_session():
    """True si on est en séance régulière US (9h30–16h00 ET, lun–ven)."""
    from zoneinfo import ZoneInfo
    from datetime import time as dtime
    now_et = datetime.now(ZoneInfo('America/New_York'))
    return now_et.weekday() < 5 and dtime(9, 30) <= now_et.time() < dtime(16, 0)


def _handle_ibkr_connectivity(ibkr_up: bool):
    """
    Suit la connectivité IBKR entre les appels du cron.
    - La push notification de coupure est gérée par run_market_monitor (MarketEvent IBKR_DOWN).
    - Ici : déclenchement de l'auto-restart après 5 min de coupure continue.
    """
    global _ibkr_down_since, _ibkr_down_notified
    if ibkr_up:
        if _ibkr_down_since is not None:
            print(f"[{datetime.now()}] ✅ IBKR reconnecté (était down depuis {_ibkr_down_since})")
        _ibkr_down_since = None
        _ibkr_down_notified = False
        return

    # IBKR est down
    if _ibkr_down_since is None:
        _ibkr_down_since = datetime.now()
        print(f"[{datetime.now()}] ⚠️ IBKR down — suivi démarré")
        return

    elapsed = (datetime.now() - _ibkr_down_since).total_seconds()
    if elapsed >= 300 and not _ibkr_down_notified:
        _ibkr_down_notified = True
        minutes = int(elapsed // 60)
        print(f"[{datetime.now()}] 🔄 IBKR down depuis {minutes} min — tentative auto-restart")

        # Tentative de redémarrage automatique (push géré par MarketEvent à l'ouverture)
        try:
            _auto_restart_gateway()
        except Exception as e:
            print(f"⚠️ Auto-restart gateway: {e}")


def _auto_restart_gateway():
    """
    Tente de redémarrer le conteneur IB Gateway en récupérant les credentials
    depuis Settings (déchiffrés avec la clé courante).
    """
    from models import Settings
    from ibkr_service import decrypt_credential, _make_fernet
    from flask import current_app

    secret = current_app.config.get('SECRET_KEY', '')
    enc_user = Settings.get('ibkr_username_enc')
    enc_pass = Settings.get('ibkr_password_enc')
    mode     = Settings.get('ibkr_trading_mode', 'live')

    if not enc_user or not enc_pass:
        print("⚠️ Auto-restart: credentials IBKR absents")
        return

    try:
        username = decrypt_credential(enc_user, secret)
        password = decrypt_credential(enc_pass, secret)
    except Exception as e:
        print(f"⚠️ Auto-restart: déchiffrement impossible ({e})")
        return

    print(f"[{datetime.now()}] 🔄 Redémarrage IB Gateway (mode={mode})…")
    try:
        import docker as docker_sdk
        client = docker_sdk.from_env()
        net_name = 'momentum-app_internal'
        for c in client.containers.list(all=True, filters={'name': 'ib-gateway'}):
            try:
                c.stop(timeout=10)
                c.remove()
            except Exception:
                pass
        container = client.containers.create(
            'ghcr.io/gnzsnz/ib-gateway:stable',
            name='momentum-app-ib-gateway-1',
            detach=True,
            restart_policy={'Name': 'unless-stopped'},
            labels={'autoheal': 'true'},
            environment={
                'TWS_USERID': username, 'TWS_PASSWORD': password,
                'TRADING_MODE': mode,
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
        print(f"✅ IB Gateway redémarré (mode={mode}) — 2FA requise dans ~90s")

        # Notification push du redémarrage
        try:
            import push_service
            push_service.send_push_all(
                title='🔄 Gateway redémarré — 2FA requise',
                body='IB Gateway vient d\'être redémarré automatiquement. Approuvez la 2FA sur votre téléphone IBKR.',
                url='/',
                tag='ibkr-restart',
            )
        except Exception:
            pass
    except Exception as e:
        print(f"❌ Redémarrage gateway échoué: {e}")


def job_market_monitor():
    """Cron minute (séance US 9h30–16h00 ET, lun–ven) : alertes en temps réel."""
    with app.app_context():
        try:
            if not _is_us_session():
                return  # hors séance → sortie immédiate, le cron 15-min prend le relais
            result = run_market_monitor()
            _handle_ibkr_connectivity(result.get('ibkr_up', True))
            if result['opened'] or result['closed']:
                print(f"[{datetime.now()}] 🔔 Monitor: {len(result['opened'])} ouverte(s), "
                      f"{result['closed']} clôturée(s)")
        except Exception as e:
            print(f"❌ job_market_monitor: {e}")


def job_market_monitor_offhours():
    """
    Cron 15-min (hors séance US, 7j/7) : surveille VIX, futures et évolutions
    nocturnes/week-end. Skip automatique pendant la séance pour ne pas doubler
    avec job_market_monitor.
    """
    with app.app_context():
        try:
            if _is_us_session():
                return  # séance active → déjà couvert par le cron minute
            result = run_market_monitor()
            _handle_ibkr_connectivity(result.get('ibkr_up', True))
            if result['opened'] or result['closed']:
                print(f"[{datetime.now()}] 🌙 Monitor (hors-séance): "
                      f"{len(result['opened'])} ouverte(s), {result['closed']} clôturée(s)")
        except Exception as e:
            print(f"❌ job_market_monitor_offhours: {e}")


def job_briefing(session='open'):
    """Cron briefing (ouverture / mi-séance / clôture)."""
    with app.app_context():
        print(f"[{datetime.now()}] 📨 Briefing '{session}'…")
        try:
            payload = build_briefing_payload(session)
            email_svc = get_email_service()
            if email_svc.is_configured():
                res = email_svc.envoyer_briefing(payload)
                print(f"{'✅' if res['success'] else '❌'} Briefing {session}: {res['message']}")
            else:
                print("⚠️ Service email non configuré")
        except Exception as e:
            print(f"❌ job_briefing: {e}")


def job_collect_prices():
    """
    Cron nuit (00h Europe/Paris) : collecte yfinance de l'historique de prix des
    constituants S&P 500 + Nasdaq-100 (mensuel 20 ans + daily 6 ans, incrémental).
    Revérifie la composition des indices ~1×/mois et alerte par email si ≥ 25 %
    des tickers échouent. Tourne en arrière-plan (thread du service).
    """
    with app.app_context():
        print(f"[{datetime.now()}] 🌙 Collecte de prix yfinance (SP500 + NDX100)…")
        try:
            svc = get_price_data_service()
            started = svc.run_background(app, full=False)
            if not started:
                print("⚠️ Collecte déjà en cours — cron ignoré")
        except Exception as e:
            print(f"❌ job_collect_prices: {e}")


def job_collect_fundamentals():
    """
    Cron nuit : collecte des données fondamentales yfinance (états financiers +
    info marché) pour la stratégie short. Incrémental (n'insère que les nouvelles
    périodes comptables). Tourne en arrière-plan.
    """
    with app.app_context():
        print(f"[{datetime.now()}] 📊 Collecte fondamentaux yfinance (short)…")
        try:
            svc = get_fundamentals_collector()
            started = svc.run_background(app, full=False, with_info=True)
            if not started:
                print("⚠️ Collecte fondamentaux déjà en cours — cron ignoré")
        except Exception as e:
            print(f"❌ job_collect_fundamentals: {e}")


def job_collect_finra():
    """
    Cron bi-mensuel : collecte du short interest FINRA (fichiers consolidés
    gratuits). Récupère les publications récentes manquantes. Tourne en
    arrière-plan.
    """
    with app.app_context():
        print(f"[{datetime.now()}] 🩳 Collecte short interest FINRA…")
        try:
            svc = get_finra_collector()
            started = svc.run_background(app, months_back=2)
            if not started:
                print("⚠️ Collecte FINRA déjà en cours — cron ignoré")
        except Exception as e:
            print(f"❌ job_collect_finra: {e}")


def job_qv_rebalance():
    """
    Cron semestriel (15 jan / 15 juil). Calcule le portefeuille Quality-Value pour
    le marché sélectionné (Settings 'qv_market' : us/eu/all), le sauvegarde, envoie
    l'email de portefeuille ET une notification push.
    """
    with app.app_context():
        market = get_qv_market()
        print(f"[{datetime.now()}] 💎 Rééquilibrage Quality-Value ({market})…")
        result = compute_and_save_qv(market)
        if not result or not result.get('success'):
            return

        holdings = result.get('holdings', [])
        try:
            import push_service
            push_service.send_push_all(
                title='💎 Rééquilibrage Quality-Value',
                body=f"Portefeuille {market.upper()} mis à jour — {len(holdings)} titres.",
                url='/', tag='qv_rebalance',
            )
        except Exception as e:
            print(f"⚠️ Push QV: {e}")

        email_svc = get_email_service()
        if email_svc.is_configured():
            res = email_svc.envoyer_qv_portfolio(result)
            print(f"{'✅' if res['success'] else '❌'} Email QV: {res['message']}")
        else:
            print("⚠️ Service email non configuré")


def job_collect_edgar():
    """
    Cron mensuel : rafraîchit les fondamentaux longs SEC EDGAR (nouveaux 10-K).
    Historique complet + date de dépôt réelle (anti-look-ahead). Arrière-plan.
    """
    with app.app_context():
        print(f"[{datetime.now()}] 🏛️ Collecte fondamentaux SEC EDGAR…")
        try:
            svc = get_edgar_collector()
            started = svc.run_background(app)
            if not started:
                print("⚠️ Collecte EDGAR déjà en cours — cron ignoré")
        except Exception as e:
            print(f"❌ job_collect_edgar: {e}")


DIGEST_RECIPIENTS = [
    'kouatebryan38@gmail.com',
    'callista.chagnard@gmail.com',
]

TECH_DIGEST_RECIPIENT = ['kouatebryan38@gmail.com']


def job_digest_actualites():
    """
    Digest d'actualités bi-quotidien (10h et 20h Europe/Paris).
    Agrège ~50 articles via flux RSS monde, génère un résumé LLM en 5 thèmes
    (géopolitique, économie, écologie, politique française, événements majeurs)
    et envoie à DIGEST_RECIPIENTS.
    """
    with app.app_context():
        from datetime import datetime
        edition = 'matin' if datetime.now().hour < 14 else 'soir'
        print(f"[{datetime.now()}] 🗞️ Digest actualités ({edition})…")
        try:
            news_svc = get_news_service()
            items = news_svc.fetch_digest_news(max_per_feed=5)
            if not items:
                print("⚠️ Digest: aucun article récupéré — envoi annulé")
                return
            summary = news_svc.summarize_digest(items)
            email_svc = get_email_service()
            if not email_svc.is_configured():
                print("⚠️ Service email non configuré — digest annulé")
                return
            res = email_svc.envoyer_digest_actualites(summary, items, DIGEST_RECIPIENTS)
            print(f"{'✅' if res['success'] else '❌'} Digest {edition}: {res['message']}")
        except Exception as e:
            print(f"❌ job_digest_actualites: {e}")


def job_screener_reminder():
    """
    Cron trimestriel (dernier jour de Q1/Q2/Q3/Q4).
    Notification push : « C'est le moment de mettre à jour le screener. »
    """
    with app.app_context():
        print(f"[{datetime.now()}] 📊 Rappel trimestriel screener…")
        try:
            import push_service
            from datetime import date
            today = date.today()
            quarters = {3: 'Q1', 6: 'Q2', 9: 'Q3', 12: 'Q4'}
            quarter = quarters.get(today.month, '')
            push_service.send_push_all(
                title='📊 Mise à jour screener',
                body=f"Fin de {quarter} — c'est le moment de relancer le screener et de mettre à jour le panel d'actions.",
                url='/',
                tag='screener-reminder',
            )
            print(f"✅ Push screener {quarter} envoyé")
        except Exception as e:
            print(f"❌ job_screener_reminder: {e}")


def job_digest_tech():
    """
    Digest Tech & IA quotidien (10h Europe/Paris uniquement).
    Agrège les flux TECH_IA_FEEDS, génère un résumé LLM en 4 thèmes
    (IA/ML, Data Eng, Software Eng, Tech culture) et envoie à TECH_DIGEST_RECIPIENT.
    """
    with app.app_context():
        print(f"[{datetime.now()}] 🤖 Digest Tech & IA…")
        try:
            news_svc = get_news_service()
            items = news_svc.fetch_tech_digest_news(max_per_feed=3)
            if not items:
                print("⚠️ Digest Tech: aucun article récupéré — envoi annulé")
                return
            summary = news_svc.summarize_tech_digest(items)
            email_svc = get_email_service()
            if not email_svc.is_configured():
                print("⚠️ Service email non configuré — digest tech annulé")
                return
            res = email_svc.envoyer_digest_tech(summary, items, TECH_DIGEST_RECIPIENT)
            print(f"{'✅' if res['success'] else '❌'} Digest Tech: {res['message']}")
        except Exception as e:
            print(f"❌ job_digest_tech: {e}")


def job_refresh_prices():
    """Cron nuit : rafraîchit le cache de prix (benchmark ^GSPC + panel)."""
    with app.app_context():
        print(f"[{datetime.now()}] 📈 Rafraîchissement du cache de prix…")
        try:
            if not ibkr_service.ensure_connected():
                print("⚠️ IBKR indisponible — cache non rafraîchi")
                return
            from models import MarketPriceBar
            from datetime import date

            bars = ibkr_service.get_daily_bars('^GSPC', duration='2 Y')
            count = 0
            for b in bars:
                bar_date = date.fromisoformat(b['date'])
                if not MarketPriceBar.query.filter_by(ticker='^GSPC', bar_date=bar_date).first():
                    db.session.add(MarketPriceBar(
                        ticker='^GSPC', bar_date=bar_date,
                        adj_close=b['adj_close'], close=b['close'], source='ibkr'))
                    count += 1
            db.session.commit()
            print(f"✅ Benchmark ^GSPC: {count} nouvelles barres")

            # Réchauffe le cache de prix du panel (persiste les barres côté service)
            service = get_momentum_service()
            actions = PanelAction.query.filter_by(is_active=True).all()
            panel = [a.ticker for a in actions]
            if service and panel:
                try:
                    service.analyser_panel(panel, None)
                    print(f"✅ Cache panel réchauffé ({len(panel)} tickers)")
                except Exception as e:
                    print(f"⚠️ Réchauffe panel: {e}")

            # Pré-remplissage du cache de prix pour le backtest (pool candidat, avec
            # volume), borné par nuit pour respecter le pacing IBKR. Étalé sur plusieurs
            # nuits jusqu'à couverture complète.
            try:
                bt = get_backtest_service()
                res = bt.prefill_pool(years=10, max_fetch=50)
                print(f"✅ Pré-remplissage backtest : {res['fetched']} ticker(s) sur {res['pool']}")
            except Exception as e:
                print(f"⚠️ Pré-remplissage backtest: {e}")
        except Exception as e:
            print(f"❌ job_refresh_prices: {e}")

