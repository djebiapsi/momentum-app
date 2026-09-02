# -*- coding: utf-8 -*-
"""Contrôle qualité des données collectées (à exécuter dans le conteneur prod)."""
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import app as m
a = m.create_app()
with a.app_context():
    try:
        from services import ibkr_service as _ib
        _ib.ensure_connected = lambda: False
    except Exception:
        pass
    from models import (db, FundamentalSnapshot, TickerInfoSnapshot,
                        ShortInterestSnapshot, MarketPriceBar, MonthlyPriceBar,
                        IndexConstituent)
    from sqlalchemy import func

    def line(*a): print(*a, flush=True)

    line("=" * 60)
    line("VOLUMÉTRIE PAR TABLE")
    line("=" * 60)
    for name, mod in [('MarketPriceBar', MarketPriceBar), ('MonthlyPriceBar', MonthlyPriceBar),
                      ('TickerInfoSnapshot', TickerInfoSnapshot),
                      ('ShortInterestSnapshot', ShortInterestSnapshot)]:
        r, t = db.session.query(func.count(mod.id), func.count(func.distinct(mod.ticker))).one()
        line(f"  {name:24} rows={r:>8}  tickers={t}")

    # FundamentalSnapshot par source
    line("\n  FundamentalSnapshot par source :")
    for (src,) in db.session.query(FundamentalSnapshot.source).distinct():
        r, t, mn, mx = db.session.query(
            func.count(FundamentalSnapshot.id), func.count(func.distinct(FundamentalSnapshot.ticker)),
            func.min(FundamentalSnapshot.period_date), func.max(FundamentalSnapshot.period_date)
        ).filter(FundamentalSnapshot.source == src).one()
        line(f"    {src:10} rows={r:>7} tickers={t:>4} periode={mn}..{mx}")

    # report_date (anti-look-ahead EDGAR)
    edgar_total = db.session.query(func.count(FundamentalSnapshot.id)).filter_by(source='edgar').scalar()
    edgar_rd = db.session.query(func.count(FundamentalSnapshot.id)).filter(
        FundamentalSnapshot.source == 'edgar', FundamentalSnapshot.report_date.isnot(None)).scalar()
    line(f"\n  EDGAR report_date renseigné : {edgar_rd}/{edgar_total} "
         f"({100*edgar_rd//max(edgar_total,1)}%)")

    # Constituants
    line("\n  IndexConstituent :")
    for (idx,) in db.session.query(IndexConstituent.index_name).distinct():
        n = IndexConstituent.query.filter_by(index_name=idx, is_active=True).count()
        line(f"    {idx:10} actifs={n}")

    line("\n" + "=" * 60)
    line("COUVERTURE UNIVERS PEA EUROPE (116 tickers)")
    line("=" * 60)
    from eu_universe import PEA_EU_UNIVERSE
    eu = set(PEA_EU_UNIVERSE)
    for name, mod in [('prix daily', MarketPriceBar), ('fondamentaux', FundamentalSnapshot),
                      ('info marché', TickerInfoSnapshot)]:
        got = db.session.query(func.count(func.distinct(mod.ticker))).filter(mod.ticker.in_(eu)).scalar()
        line(f"  {name:14} : {got}/116")

    line("\n" + "=" * 60)
    line("COHÉRENCE — échantillon EDGAR (AAPL) & EU (MC.PA)")
    line("=" * 60)
    for tk in ('AAPL', 'MC.PA'):
        rows = (FundamentalSnapshot.query.filter_by(ticker=tk)
                .order_by(FundamentalSnapshot.period_date.desc()).limit(3).all())
        line(f"  {tk} : {FundamentalSnapshot.query.filter_by(ticker=tk).count()} périodes")
        for r in rows:
            rev = f"{r.total_revenue/1e9:.1f}B" if r.total_revenue else "?"
            ni = f"{r.net_income/1e9:.1f}B" if r.net_income else "?"
            acc = f"{r.accruals_ratio:.3f}" if r.accruals_ratio is not None else "?"
            line(f"    {r.period_date} [{r.period_type}/{r.source}] rev={rev} ni={ni} "
                 f"accruals={acc} filed={r.report_date}")

    line("\n" + "=" * 60)
    line("VALEURS ABERRANTES — accruals_ratio (tous)")
    line("=" * 60)
    accs = [x for (x,) in db.session.query(FundamentalSnapshot.accruals_ratio)
            .filter(FundamentalSnapshot.accruals_ratio.isnot(None)).all()]
    if accs:
        accs.sort()
        n = len(accs)
        extreme = sum(1 for x in accs if abs(x) > 1.0)
        line(f"  n={n} min={accs[0]:.3f} médiane={accs[n//2]:.3f} max={accs[-1]:.3f} "
             f"| |ratio|>1 (suspect): {extreme} ({100*extreme//n}%)")

    line("\n" + "=" * 60)
    line("BOUT-EN-BOUT — screen QV US & EU")
    line("=" * 60)
    from services import get_fundamental_screen_service
    svc = get_fundamental_screen_service()
    for mkt in ('us', 'eu'):
        r = svc.build_portfolio(top_n=5, market=mkt, max_per_sector=3)
        if r.get('success'):
            tops = ', '.join(f"{h['ticker']}({h['composite']})" for h in r['holdings'])
            line(f"  {mkt.upper():4} éligibles={r['eligible']:>4} | top5: {tops}")
        else:
            line(f"  {mkt.upper():4} ERREUR: {r.get('error')}")
    line("\n=== FIN CONTRÔLE QUALITÉ ===")
