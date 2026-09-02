# -*- coding: utf-8 -*-
"""Driver one-shot : collecte des données pour l'univers PEA Europe uniquement."""
import sys
import time
from datetime import datetime


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
        try:
            from services import ibkr_service as _ib
            _ib.ensure_connected = lambda: False
        except Exception:
            pass

        from eu_universe import seed_pea_universe, PEA_EU_UNIVERSE
        from services import get_price_data_service, get_fundamentals_collector
        from models import MarketPriceBar, MonthlyPriceBar

        n = seed_pea_universe()
        eu = sorted(PEA_EU_UNIVERSE.keys())
        log(f"=== Univers PEA seedé: {n} actifs ({len(eu)} tickers) ===")

        # 1) Prix (daily + monthly)
        pds = get_price_data_service()
        log("=== 1/2 collecte prix EU (daily + monthly) ===")
        t0 = time.time()
        try:
            d = pds._collect_interval(eu, MarketPriceBar, '1d', False, 'daily_eu')
            m = pds._collect_interval(eu, MonthlyPriceBar, '1mo', False, 'monthly_eu')
            log(f"prix EU: daily ok={d['ok']}/{d['total']} new={d['new_bars']} | "
                f"monthly ok={m['ok']}/{m['total']} new={m['new_bars']} | {round(time.time()-t0)}s")
        except Exception as e:
            log(f"ERREUR prix EU: {e}")

        # 2) Fondamentaux + info
        log("=== 2/2 collecte fondamentaux + info EU ===")
        t0 = time.time()
        try:
            fc = get_fundamentals_collector()
            st = fc.collect_statements(eu, full=False)
            info = fc.collect_info(eu)
            log(f"fondamentaux EU: statements ok={st['ok']}/{st['total']} new={st['new_rows']} | "
                f"info ok={info['ok']}/{info['total']} | {round(time.time()-t0)}s")
        except Exception as e:
            log(f"ERREUR fondamentaux EU: {e}")

        log("=== COLLECTE EU TERMINÉE ===")


if __name__ == '__main__':
    main()
