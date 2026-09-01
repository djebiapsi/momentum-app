# -*- coding: utf-8 -*-
"""
Driver de collecte one-shot (backfill stratégie short)
======================================================
Exécute séquentiellement, dans un seul processus (à lancer en arrière-plan) :

  1. refresh_constituents  → peuple IndexConstituent + IndexMembership
  2. collecte prix yfinance → MarketPriceBar (daily) + MonthlyPriceBar (univers large + ETF)
  3. collecte fondamentaux  → FundamentalSnapshot + TickerInfoSnapshot
  4. backfill FINRA          → ShortInterestSnapshot (depuis juin 2021)

Synchrone (pas de threads démons) pour que le processus reste vivant jusqu'à la
fin quand il est lancé en tâche de fond. Logue avec horodatage + flush.
"""
import sys
import time
from datetime import date, datetime


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    import app as app_module
    application = app_module.create_app()

    with application.app_context():
        # IBKR neutralisé (DB-only)
        try:
            from services import ibkr_service as _ib
            _ib.ensure_connected = lambda: False
        except Exception:
            pass

        from services import (get_price_data_service, get_fundamentals_collector,
                              get_finra_collector)

        # 1) Composition des indices (point-in-time)
        log("=== 1/4 refresh_constituents ===")
        try:
            pds = get_price_data_service()
            refreshed, n = pds.refresh_constituents(force=True)
            log(f"constituents: refreshed={refreshed} actifs={n}")
        except Exception as e:
            log(f"ERREUR refresh_constituents: {e}")

        # 2) Prix (univers large + ETF sectoriels)
        log("=== 2/4 collecte prix yfinance (peut durer 30-60 min) ===")
        try:
            t0 = time.time()
            summary = pds.collect(full=False)
            log(f"prix: {summary.get('tickers')} tickers, "
                f"daily new_bars={summary.get('daily', {}).get('new_bars')}, "
                f"monthly new_bars={summary.get('monthly', {}).get('new_bars')}, "
                f"elapsed={round(time.time()-t0)}s")
        except Exception as e:
            log(f"ERREUR collecte prix: {e}")

        # 3) Fondamentaux + info marché
        log("=== 3/4 collecte fondamentaux + info ===")
        try:
            t0 = time.time()
            fc = get_fundamentals_collector()
            summary = fc.collect(full=False, with_info=True)
            log(f"fondamentaux: {summary}")
            log(f"elapsed={round(time.time()-t0)}s")
        except Exception as e:
            log(f"ERREUR fondamentaux: {e}")

        # 4) FINRA short interest depuis juin 2021 (couverture titres cotés)
        log("=== 4/4 backfill FINRA (depuis 2021-06) ===")
        try:
            t0 = time.time()
            fi = get_finra_collector()
            summary = fi.collect_range(date(2021, 6, 1), date.today(),
                                       only_universe=True, skip_existing=True)
            log(f"FINRA: {summary}")
            log(f"elapsed={round(time.time()-t0)}s")
        except Exception as e:
            log(f"ERREUR FINRA: {e}")

        log("=== COLLECTE TERMINÉE ===")


if __name__ == '__main__':
    main()
