# -*- coding: utf-8 -*-
"""Driver one-shot : backfill fondamentaux longs SEC EDGAR sur tout l'univers."""
import sys
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
        from services import get_edgar_collector
        c = get_edgar_collector()
        log("=== Backfill EDGAR (univers complet) ===")
        summary = c.collect()
        log(f"EDGAR terminé: {summary}")
        log(f"couverture: {c.coverage()}")


if __name__ == '__main__':
    main()
