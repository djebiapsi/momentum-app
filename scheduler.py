# -*- coding: utf-8 -*-
"""Construction et démarrage de l'APScheduler (extrait de app.py)."""
import functools
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import jobs
from jobs import (job_market_monitor, job_market_monitor_offhours, job_briefing,
                  job_rebalance_reminder, job_refresh_prices, job_collect_prices,
                  job_digest_actualites, job_digest_tech, job_screener_reminder,
                  job_collect_fundamentals, job_collect_finra, job_short_signal_monthly,
                  job_collect_edgar, job_qv_rebalance)


def create_scheduler(app):
    """Injecte l'app dans les jobs, construit le scheduler et le démarre."""
    jobs.app = app
    scheduler = BackgroundScheduler(job_defaults={
        'coalesce': True,
        'max_instances': 1,
        'misfire_grace_time': 60,   # défaut pour le monitor (1 min de tolérance)
    })
    ET = 'America/New_York'

    scheduler.add_job(
        job_market_monitor,
        CronTrigger(day_of_week='mon-fri', hour='9-16', minute='*', timezone=ET),
        id='market_monitor', name='Surveillance marché (minute, séance US)', replace_existing=True,
    )
    scheduler.add_job(
        job_market_monitor_offhours,
        CronTrigger(minute='*/15', timezone=ET),
        id='market_monitor_offhours', name='Surveillance marché (15-min, hors-séance)',
        replace_existing=True,
    )

    # 2) Briefings : pre-ouverture 9h15, mi-séance 12h30, clôture 16h05 ET
    scheduler.add_job(
        functools.partial(job_briefing, 'open'),
        CronTrigger(day_of_week='mon-fri', hour=9, minute=15, timezone=ET),
        id='briefing_open', name="Briefing d'ouverture", replace_existing=True,
    )
    scheduler.add_job(
        functools.partial(job_briefing, 'mid'),
        CronTrigger(day_of_week='mon-fri', hour=12, minute=30, timezone=ET),
        id='briefing_mid', name='Briefing mi-séance', replace_existing=True,
    )
    scheduler.add_job(
        functools.partial(job_briefing, 'close'),
        CronTrigger(day_of_week='mon-fri', hour=16, minute=5, timezone=ET),
        id='briefing_close', name='Briefing de clôture', replace_existing=True,
    )

    # 3) Rappel mensuel de rééquilibrage : 1er du mois à 8h00 ET
    scheduler.add_job(
        job_rebalance_reminder,
        CronTrigger(day=1, hour=8, minute=0, timezone=ET),
        id='rebalance_reminder', name='Rappel mensuel de rééquilibrage', replace_existing=True,
    )

    # 3b) Signal short mensuel : 1er du mois à 9h00 ET (après le rappel long)
    scheduler.add_job(
        job_short_signal_monthly,
        CronTrigger(day=1, hour=9, minute=0, timezone=ET),
        id='short_signal_monthly', name='Signal short mensuel (multi-facteurs)',
        replace_existing=True,
    )

    # 4) Rafraîchissement du cache de prix : chaque soir 22h00 ET (lun-ven)
    scheduler.add_job(
        job_refresh_prices,
        CronTrigger(day_of_week='mon-fri', hour=22, minute=0, timezone=ET),
        id='refresh_prices', name='Rafraîchissement cache de prix', replace_existing=True,
    )

    # 5) Collecte yfinance (SP500 + NDX100) : tous les jours à 00h00 Europe/Paris
    PARIS = 'Europe/Paris'
    scheduler.add_job(
        job_collect_prices,
        CronTrigger(hour=0, minute=0, timezone=PARIS),
        id='collect_prices', name='Collecte de prix yfinance (SP500/NDX100)',
        replace_existing=True,
    )

    # 6) Digest d'actualités monde : 10h00 et 20h00 Europe/Paris (tous les jours)
    # misfire_grace_time=600 : si l'app redémarre dans les 10 min autour de l'heure
    # du digest, le job se déclenche quand même (évite les ratés sur redémarrage).
    scheduler.add_job(
        job_digest_actualites,
        CronTrigger(hour=10, minute=0, timezone=PARIS),
        id='digest_matin', name='Digest actualités (matin)', replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        job_digest_actualites,
        CronTrigger(hour=20, minute=0, timezone=PARIS),
        id='digest_soir', name='Digest actualités (soir)', replace_existing=True,
        misfire_grace_time=600,
    )

    # 7) Digest Tech & IA : 10h05 Europe/Paris uniquement (→ kouatebryan38@gmail.com)
    scheduler.add_job(
        job_digest_tech,
        CronTrigger(hour=10, minute=5, timezone=PARIS),
        id='digest_tech', name='Digest Tech & IA (matin)', replace_existing=True,
        misfire_grace_time=600,
    )

    # 8) Rappel screener trimestriel : dernier jour de mars, juin, sept, déc à 9h00 ET
    scheduler.add_job(
        job_screener_reminder,
        CronTrigger(month='3,6,9,12', day='last', hour=9, minute=0, timezone=ET),
        id='screener_reminder', name='Rappel screener trimestriel', replace_existing=True,
    )

    # 9) Collecte fondamentaux yfinance (stratégie short) : chaque nuit 01h00 Paris
    # (décalé de la collecte de prix pour ne pas saturer Yahoo simultanément).
    scheduler.add_job(
        job_collect_fundamentals,
        CronTrigger(hour=1, minute=0, timezone=PARIS),
        id='collect_fundamentals', name='Collecte fondamentaux yfinance (short)',
        replace_existing=True,
    )

    # 10) Collecte short interest FINRA : bi-mensuel (le 5 et le 20 à 12h00 Paris,
    # après la dissémination FINRA ~8 jours post-règlement). collect_recent tolère
    # les dates absentes → aucun risque si le calendrier FINRA décale.
    scheduler.add_job(
        job_collect_finra,
        CronTrigger(day='5,20', hour=12, minute=0, timezone=PARIS),
        id='collect_finra', name='Collecte short interest FINRA (bi-mensuel)',
        replace_existing=True,
    )

    # 11) Collecte fondamentaux longs SEC EDGAR : mensuel (8 du mois, 02h00 Paris)
    scheduler.add_job(
        job_collect_edgar,
        CronTrigger(day=8, hour=2, minute=0, timezone=PARIS),
        id='collect_edgar', name='Collecte fondamentaux SEC EDGAR (mensuel)',
        replace_existing=True,
    )

    # 12) Rééquilibrage Quality-Value : semestriel (15 janvier & 15 juillet, 8h ET)
    scheduler.add_job(
        job_qv_rebalance,
        CronTrigger(month='1,7', day=15, hour=8, minute=0, timezone=ET),
        id='qv_rebalance', name='Rééquilibrage Quality-Value (semestriel)',
        replace_existing=True,
    )

    scheduler.start()
    print("[scheduler] demarre - 14 crons actifs :")
    print("  - Surveillance marche (1min)   : 9h30-16h00 ET, lun-ven (séance US)")
    print("  - Surveillance marche (15-min) : 24h/24, 7j/7 hors séance (VIX nocturne)")
    print("  - Briefings : 9h35 / 12h30 / 16h05 ET (lun-ven)")
    print("  - Rappel reequilibrage : 1er du mois 8h00 ET")
    print("  - Cache de prix : 22h00 ET (lun-ven)")
    print("  - Collecte yfinance SP500/NDX100 : 00h00 Europe/Paris (quotidien)")
    print("  - Digest actualites : 10h00 + 20h00 Europe/Paris (quotidien)")
    print("  - Digest Tech & IA  : 10h05 Europe/Paris (quotidien, perso)")
    print("  - Screener trimestriel : dernier jour de Q1/Q2/Q3/Q4 à 9h00 ET")
    print("  - Signal short mensuel : 1er du mois 9h00 ET")
    print("  - Collecte fondamentaux (short) : 01h00 Europe/Paris (quotidien)")
    print("  - Collecte short interest FINRA : 5 & 20 du mois, 12h00 Europe/Paris")
    print("  - Collecte fondamentaux SEC EDGAR : 8 du mois, 02h00 Europe/Paris")
    print("  - Rééquilibrage Quality-Value : 15 jan & 15 juil, 8h00 ET")
    return scheduler
