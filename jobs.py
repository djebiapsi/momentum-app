# -*- coding: utf-8 -*-
"""Tâches planifiées (extrait de app.py) ; app injecté par scheduler.create_scheduler()."""
from datetime import datetime
from models import db, PanelAction
from services import ibkr_service, get_momentum_service, get_email_service, get_backtest_service
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

def job_market_monitor():
    """Cron minute (séance US) : surveille le marché et gère les alertes."""
    with app.app_context():
        try:
            from zoneinfo import ZoneInfo
            from datetime import time as dtime
            now_et = datetime.now(ZoneInfo('America/New_York'))
            if not (dtime(9, 30) <= now_et.time() < dtime(16, 0)):
                return  # hors séance régulière
            result = run_market_monitor()
            if result['opened'] or result['closed']:
                print(f"[{datetime.now()}] 🔔 Monitor: {len(result['opened'])} ouverte(s), "
                      f"{result['closed']} clôturée(s)")
        except Exception as e:
            print(f"❌ job_market_monitor: {e}")


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

