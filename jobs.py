# -*- coding: utf-8 -*-
"""Tâches planifiées (extrait de app.py) ; app injecté par scheduler.create_scheduler()."""
from datetime import datetime
from models import db, PanelAction
from services import (ibkr_service, get_momentum_service, get_email_service,
                     get_backtest_service, get_price_data_service)
from core import compute_and_save_momentum, run_market_monitor, build_briefing_payload


app = None  # injecté par scheduler.create_scheduler()


def job_rebalance_reminder():
    """
    Cron mensuel (1er du mois). Calcule le momentum, le sauvegarde, et envoie
    l'email « C'est le moment de rééquilibrer ! » avec bouton de téléchargement.
    """
    with app.app_context():
        print(f"[{datetime.now()}] 🔄 Rappel mensuel de rééquilibrage…")
        recommandations, history = compute_and_save_momentum()
        if not recommandations:
            return

        email_svc = get_email_service()
        if email_svc.is_configured():
            result = email_svc.envoyer_rebalance_reminder(recommandations, history.id)
            print(f"{'✅' if result['success'] else '❌'} Email rééquilibrage: {result['message']}")
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


def job_market_monitor():
    """Cron minute (séance US 9h30–16h00 ET, lun–ven) : alertes en temps réel."""
    with app.app_context():
        try:
            if not _is_us_session():
                return  # hors séance → sortie immédiate, le cron 15-min prend le relais
            result = run_market_monitor()
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


DIGEST_RECIPIENTS = [
    'kouatebryan38@gmail.com',
    'callista.chagnard@gmail.com',
]


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

