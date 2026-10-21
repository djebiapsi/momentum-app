# -*- coding: utf-8 -*-
"""
Performance du compte IBKR : import Flex fiable, cycle de vie des positions,
positions réelles et calculs du tableau de bord Perf.

Pourquoi ce module (audit du 21/10/2026) :
  - l'ancien import dédoublonnait les transactions par (date, ticker, qté, prix) :
    deux exécutions identiques d'un même ordre devenaient une seule → une vente
    perdue laissait une position « fantôme » ouverte dans l'impact par position ;
  - les lignes de synthèse Flex, les divisions d'actions et les changements de
    ticker faussaient les quantités ;
  - rien ne confrontait la reconstruction aux positions réellement détenues.

Désormais :
  - l'import remplace intégralement la fenêtre couverte par le relevé Flex
    (source officielle), exécution par exécution ;
  - le cycle de vie est reconstruit en une passe chronologique (achats, ventes,
    ajustements d'opérations sur titres) puis RÉCONCILIÉ avec les positions
    réelles (IBKR live, sinon positions ouvertes du dernier relevé Flex) ; chaque
    correction est signalée.
"""
import json
from collections import defaultdict
from datetime import date as _date, datetime

import pandas as pd

QTY_EPS = 1e-6           # quantité nulle (absolu)
QTY_EPS_REL = 1e-4       # reliquat fractionnaire négligeable (relatif au pic détenu)
MIN_DAYS_ANNUALIZED = 90  # en dessous, CAGR / Sharpe annualisés n'ont pas de sens


# =============================================================================
# Positions réelles (vérité du courtier)
# =============================================================================
def actual_positions(ibkr_service=None):
    """
    Positions réellement détenues : IBKR live si connecté, sinon positions
    ouvertes du dernier relevé Flex. Retourne (dict ticker → {qty, price, value,
    cost_basis, unrealized}, source 'ibkr' | 'flex' | None, as_of).
    """
    if ibkr_service is not None:
        try:
            if ibkr_service.ensure_connected():
                out = {}
                for p in ibkr_service.get_positions():
                    q = float(p.get('qty') or 0)
                    if abs(q) <= QTY_EPS:
                        continue
                    price = p.get('market_price')
                    out[p['ticker']] = {
                        'qty': q, 'price': float(price) if price else None,
                        'value': p.get('market_value'),
                        'cost_basis': (p.get('avg_cost') or 0) * q if p.get('avg_cost') else None,
                        'unrealized': p.get('unrealized_pnl'),
                    }
                return out, 'ibkr', datetime.now().date().isoformat()
        except Exception:
            pass
    try:
        from models import Settings
        raw = Settings.get('flex_open_positions')
        if raw:
            snap = json.loads(raw)
            out = {p['ticker']: {'qty': float(p['qty']), 'price': p.get('mark_price'),
                                 'value': p.get('value'), 'cost_basis': p.get('cost_basis'),
                                 'unrealized': p.get('unrealized')}
                   for p in snap.get('positions', [])}
            return out, 'flex', snap.get('as_of')
    except Exception:
        pass
    return None, None, None


# =============================================================================
# Import Flex
# =============================================================================
def _window_bounds(lo, hi):
    return (datetime.combine(lo, datetime.min.time()),
            datetime.combine(hi, datetime.max.time()))


def import_flex(parsed):
    """
    Écrit un relevé Flex parsé en base. NAV : mise à jour par date. Transactions,
    dividendes et flux de capitaux : la fenêtre [from_date, to_date] du relevé
    est REMPLACÉE (le relevé est complet sur sa fenêtre), ce qui corrige aussi les
    exécutions perdues par l'ancien dédoublonnage. Positions ouvertes : stockées
    dans Settings 'flex_open_positions'. Retourne les compteurs.
    """
    from models import db, PortfolioSnapshot, Transaction, Dividend, CashFlow, Settings

    counts = {'snapshots': 0, 'transactions': 0, 'dividends': 0, 'cash_flows': 0,
              'replaced_transactions': 0, 'replaced_dividends': 0, 'replaced_cash_flows': 0}
    lo, hi = parsed.get('from_date'), parsed.get('to_date')

    existing_snap = {s.date: s for s in PortfolioSnapshot.query.all()}
    with db.session.no_autoflush:
        nav_by_date = {row['date']: row for row in parsed.get('nav', [])}
        for d, row in nav_by_date.items():
            if d in existing_snap:
                existing_snap[d].nav = row['nav']
                if row.get('cash') is not None:
                    existing_snap[d].cash = row['cash']
            else:
                db.session.add(PortfolioSnapshot(date=d, nav=row['nav'], cash=row.get('cash')))
                counts['snapshots'] += 1

        trades = parsed.get('trades') or []
        if trades and lo and hi:
            t0, t1 = _window_bounds(lo, hi)
            counts['replaced_transactions'] = (Transaction.query
                                               .filter(Transaction.date >= t0, Transaction.date <= t1)
                                               .delete(synchronize_session=False))
            for tr in trades:
                db.session.add(Transaction(
                    date=tr.get('ts') or datetime.combine(tr['date'], datetime.min.time()),
                    ticker=tr['ticker'], type=tr['type'], quantity=tr['quantity'],
                    price=tr['price'], amount=tr['amount'], currency=tr.get('currency', 'USD'),
                    ext_id=tr.get('ext_id')))
                counts['transactions'] += 1

        divs = parsed.get('dividends') or []
        if divs and lo and hi:
            counts['replaced_dividends'] = (Dividend.query
                                            .filter(Dividend.date >= lo, Dividend.date <= hi)
                                            .delete(synchronize_session=False))
            for dv in divs:
                db.session.add(Dividend(date=dv['date'], ticker=dv['ticker'],
                                        amount=dv['amount'], currency=dv.get('currency', 'USD')))
                counts['dividends'] += 1

        flows = parsed.get('cash_flows') or []
        if flows and lo and hi:
            counts['replaced_cash_flows'] = (CashFlow.query
                                             .filter(CashFlow.date >= lo, CashFlow.date <= hi)
                                             .delete(synchronize_session=False))
            for cf in flows:
                db.session.add(CashFlow(date=cf['date'], amount=cf['amount'],
                                        description=(cf.get('description') or '')[:200],
                                        currency=cf.get('currency', 'USD')))
                counts['cash_flows'] += 1
    db.session.commit()

    ops = parsed.get('open_positions')
    if ops is not None and (ops or trades):
        as_of = max((p['report_date'] for p in ops if p.get('report_date')), default=hi)
        Settings.set('flex_open_positions', json.dumps({
            'as_of': as_of.isoformat() if hasattr(as_of, 'isoformat') else as_of,
            'positions': [{**p, 'report_date': p['report_date'].isoformat()
                           if hasattr(p.get('report_date'), 'isoformat') else p.get('report_date')}
                          for p in ops]}))
    counts['window'] = [lo.isoformat() if lo else None, hi.isoformat() if hi else None]
    counts['open_positions'] = len(ops or [])
    return counts


# =============================================================================
# Cycle de vie des positions
# =============================================================================
_TYPE_ORDER = {'BUY': 0, 'ADJ-': 1, 'ADJ+': 2, 'SELL': 3}


def _sort_key(t):
    kind = t['type'] if t['type'] != 'ADJ' else ('ADJ-' if t['quantity'] < 0 else 'ADJ+')
    return (t['ts'], _TYPE_ORDER.get(kind, 9), t.get('seq', 0))


def position_lifecycles(txs, dividends=None, prices=None, actual=None, today=None):
    """
    Reconstruit le cycle de vie de chaque position (coût moyen, ordre
    chronologique) puis le réconcilie avec les positions réelles.

    txs : [{'ts': datetime, 'ticker', 'type': BUY|SELL|ADJ, 'quantity', 'price', 'amount', 'seq'}]
          (ADJ : quantité signée, sans flux de trésorerie — division, changement de ticker)
    dividends : {ticker: montant net}
    prices : {ticker: dernier prix connu}
    actual : {ticker: {'qty', 'price', ...}} ou None (pas de réconciliation)

    Retourne (positions, totals, realized_events, recon) ; realized_events =
    [(date, ticker, pnl)] pour le P&L réalisé sur une période ; recon = écarts
    corrigés entre reconstruction et positions réelles.
    """
    dividends = dividends or {}
    prices = prices or {}
    today = today or datetime.now().date()
    st = defaultdict(lambda: {'q': 0.0, 'cost': 0.0, 'realized': 0.0, 'invested': 0.0,
                              'proceeds': 0.0, 'buys': 0, 'sells': 0, 'profit_takings': 0,
                              'open_date': None, 'close_date': None, 'last_date': None,
                              'unknown_cost': False, 'peak_q': 0.0, 'adjusted': False})
    realized_events = []

    def _is_zero(s):
        return s['q'] <= max(QTY_EPS, QTY_EPS_REL * s['peak_q'])

    ordered = sorted(txs, key=_sort_key)
    # Les ajustements d'un même instant se compensent (division, changement de
    # ticker) : le coût sorti par la jambe négative est repris par la jambe positive.
    by_ts = defaultdict(list)
    for t in ordered:
        by_ts[t['ts']].append(t)

    for ts in sorted(by_ts):
        pool = []   # [(ticker, cost)] sorti par les jambes négatives
        for t in by_ts[ts]:
            s = st[t['ticker']]
            d = ts.date() if hasattr(ts, 'date') else ts
            s['last_date'] = d
            qty = float(t['quantity'] or 0)
            amt = float(t.get('amount') or 0)
            if t['type'] == 'BUY':
                s['buys'] += 1
                spend = abs(amt) if amt else qty * float(t.get('price') or 0)
                s['invested'] += spend
                if _is_zero(s):
                    s['open_date'], s['close_date'] = d, None
                    s['q'], s['cost'] = 0.0, 0.0
                s['q'] += qty
                s['cost'] += spend
            elif t['type'] == 'SELL':
                s['sells'] += 1
                recv = abs(amt) if amt else qty * float(t.get('price') or 0)
                s['proceeds'] += recv
                if s['q'] > QTY_EPS:
                    avg = s['cost'] / s['q']
                    sold = min(qty, s['q'])
                    pnl = recv * (sold / qty if qty else 1) - avg * sold
                    s['realized'] += pnl
                    realized_events.append((d, t['ticker'], pnl))
                    s['cost'] -= avg * sold
                    s['q'] -= sold
                    if qty - sold > max(QTY_EPS, QTY_EPS_REL * s['peak_q']):
                        s['unknown_cost'] = True      # vente au-delà du stock connu
                else:
                    s['unknown_cost'] = True          # vente sans achat connu (avant l'historique)
                if _is_zero(s):
                    s['close_date'] = d
                    s['q'], s['cost'] = 0.0, 0.0
                else:
                    s['profit_takings'] += 1
            else:  # ADJ
                s['adjusted'] = True
                if qty < 0:
                    out_q = min(-qty, s['q']) if s['q'] > 0 else 0.0
                    cost_out = s['cost'] * (out_q / s['q']) if s['q'] > QTY_EPS else 0.0
                    s['q'] -= out_q
                    s['cost'] -= cost_out
                    pool.append((t['ticker'], cost_out))
                    if _is_zero(s):
                        s['q'], s['cost'] = 0.0, 0.0
                        s['close_date'] = d
                else:
                    if _is_zero(s):
                        s['open_date'], s['close_date'] = d, None
                    # reprend le coût sorti au même instant (même ticker en priorité)
                    idx = next((i for i, (tk, _) in enumerate(pool) if tk == t['ticker']),
                               0 if pool else None)
                    if idx is not None:
                        s['cost'] += pool.pop(idx)[1]
                    elif s['q'] <= QTY_EPS:
                        s['unknown_cost'] = True
                    s['q'] += qty
            s['peak_q'] = max(s['peak_q'], s['q'])
        # coût sorti sans contrepartie (radiation, titre sans valeur) : perte réalisée
        for tk, cost_out in pool:
            if cost_out > 0:
                st[tk]['realized'] -= cost_out
                realized_events.append((ts.date() if hasattr(ts, 'date') else ts, tk, -cost_out))

    # ── Réconciliation avec les positions réelles ─────────────────────────────
    recon = []
    tickers = set(st)
    if actual is not None:
        tickers |= set(actual)
    out = []
    for tk in sorted(tickers):
        s = st[tk]
        rec_q = 0.0 if _is_zero(s) else s['q']
        status = 'open' if rec_q > 0 else 'closed'
        qty = rec_q
        note = None
        incomplete = False
        if actual is not None:
            act_q = float((actual.get(tk) or {}).get('qty') or 0)
            tol = max(1e-3, 1e-3 * max(abs(act_q), rec_q))
            if abs(act_q - rec_q) > tol:
                if act_q <= QTY_EPS:
                    status, qty, note, incomplete = 'closed', 0.0, 'missing_exit', True
                    s['close_date'] = s['close_date'] or s['last_date']
                elif rec_q <= QTY_EPS:
                    status, qty, note = 'open', act_q, 'missing_entry'
                    s['unknown_cost'] = True
                else:
                    status, qty, note = 'open', act_q, 'qty_mismatch'
                recon.append({'ticker': tk, 'reconstructed_qty': round(rec_q, 4),
                              'actual_qty': round(act_q, 4), 'fix': note})
        price = None
        if actual is not None and tk in actual and actual[tk].get('price'):
            price = float(actual[tk]['price'])
        elif prices.get(tk):
            price = float(prices[tk])
        is_open = status == 'open'
        market_value = round(qty * price, 2) if (is_open and price) else 0.0
        # Prix de revient : celui du courtier fait foi quand il est connu (divisions,
        # achats antérieurs à l'historique) ; sinon le coût moyen reconstruit.
        broker_cb = (actual.get(tk) or {}).get('cost_basis') if (actual is not None and is_open) else None
        if broker_cb is not None:
            cost = float(broker_cb)
        elif is_open and note != 'missing_entry' and not s['unknown_cost']:
            cost = s['cost']
        else:
            cost = None
        unrealized = round(market_value - cost, 2) if (is_open and price and cost is not None) else 0.0
        div = round(float(dividends.get(tk, 0.0)), 2)
        total = round(s['realized'] + unrealized + div, 2)
        known = s['invested'] > 1e-6 and not s['unknown_cost'] and not incomplete
        o = s['open_date']
        c = s['close_date'] if not is_open else None
        end_ref = c if c else today
        out.append({
            'ticker': tk,
            'status': status,
            'open_date': o.isoformat() if o else None,
            'close_date': c.isoformat() if c else None,
            'holding_days': (end_ref - o).days if o else None,
            'buys': s['buys'], 'sells': s['sells'], 'profit_takings': s['profit_takings'],
            'invested': round(s['invested'], 2),
            'proceeds': round(s['proceeds'], 2),
            'dividends': div,
            'remaining_qty': round(qty, 4) if is_open else 0.0,
            'current_price': round(price, 2) if (is_open and price) else None,
            'market_value': market_value,
            'cost_basis': round(cost, 2) if (is_open and cost is not None) else None,
            'realized_pnl': round(s['realized'], 2),
            'unrealized_pnl': unrealized,
            'total_pnl': total,
            'perf_pct': round(total / s['invested'] * 100, 2) if known else None,
            'unknown_cost': s['unknown_cost'],
            'incomplete': incomplete,
            'adjusted': s['adjusted'],
            'reconciled': note,
        })
    out.sort(key=lambda p: (p['status'] != 'open', -p['total_pnl']))
    totals = {
        'realized_pnl': round(sum(p['realized_pnl'] for p in out), 2),
        'unrealized_pnl': round(sum(p['unrealized_pnl'] for p in out), 2),
        'dividends': round(sum(p['dividends'] for p in out), 2),
        'total_pnl': round(sum(p['total_pnl'] for p in out), 2),
        'open_count': sum(1 for p in out if p['status'] == 'open'),
        'closed_count': sum(1 for p in out if p['status'] == 'closed'),
        'open_value': round(sum(p['market_value'] for p in out), 2),
    }
    return out, totals, realized_events, recon


def load_transactions():
    """Transactions actions (hors forex « EUR.USD ») au format de position_lifecycles."""
    from models import Transaction
    rows = (Transaction.query.filter(~Transaction.ticker.like('%.%'))
            .order_by(Transaction.date.asc(), Transaction.id.asc()).all())
    return [{'ts': r.date, 'ticker': r.ticker, 'type': r.type, 'quantity': r.quantity,
             'price': r.price, 'amount': r.amount, 'seq': r.id} for r in rows]


# =============================================================================
# Calculs du tableau de bord
# =============================================================================
def align_cash_flows(nav_index, flows):
    """
    Rattache chaque flux (dépôt / retrait) à la PREMIÈRE date de NAV ≥ sa date.
    Un dépôt tombé un jour sans relevé (week-end, férié) n'est plus perdu — sinon
    il serait compté comme du rendement le jour suivant.
    flows : [(date, montant)]. Retourne une Series alignée sur nav_index.
    """
    out = pd.Series(0.0, index=nav_index)
    if len(nav_index) == 0:
        return out
    for d, amt in flows:
        pos = nav_index.searchsorted(pd.Timestamp(d))
        if pos < len(nav_index):
            out.iloc[pos] += float(amt)
    return out


def twr_from_nav(nav, flows):
    """Indice TWR (Modified Dietz journalier, flux en fin de journée) + rendements quotidiens."""
    cf = align_cash_flows(nav.index, flows)
    prev = nav.shift(1)
    hpr = ((nav - prev - cf) / prev).fillna(0.0).clip(-0.95, 0.95)
    twr = (1 + hpr).cumprod()
    return twr / twr.iloc[0], hpr.iloc[1:]


def risk_metrics(twr, daily, rf=0.04):
    """Rendement total, CAGR et Sharpe (annualisés seulement à partir de 3 mois), vol, perte max."""
    days = max(1, (twr.index[-1] - twr.index[0]).days)
    total = float(twr.iloc[-1] - 1)
    annualize = days >= MIN_DAYS_ANNUALIZED
    cagr = float(twr.iloc[-1] ** (365.0 / days) - 1) if annualize else None
    vol = float(daily.std() * (252 ** 0.5)) if len(daily) > 1 else None
    sharpe = None
    if annualize and vol and vol > 1e-9:
        sharpe = float((daily.mean() * 252 - rf) / vol)
    dd = (twr / twr.cummax() - 1)
    return {'days': days, 'total': total, 'cagr': cagr, 'vol': vol, 'sharpe': sharpe,
            'max_dd': float(dd.min()), 'drawdown': dd}


def period_returns(twr, rule):
    """Rendements par période ('ME' mois, 'W' semaine) en incluant la 1re période partielle."""
    ends = twr.resample(rule).last().dropna()
    base = pd.concat([pd.Series([twr.iloc[0]], index=[twr.index[0] - pd.Timedelta(days=1)]), ends])
    return base.pct_change().dropna()


# =============================================================================
# Synchronisation Flex (route + cron nocturne)
# =============================================================================
def run_flex_sync(force=False):
    """
    Récupère le relevé Flex, applique les garde-fous (compte paper, NAV
    incohérente) puis l'importe. Retourne (code HTTP, payload JSON).
    """
    from flask import current_app
    import flex_service
    from ibkr_service import decrypt_credential
    from models import Settings, PortfolioSnapshot, Transaction, Dividend, CashFlow

    enc_token = Settings.get('flex_token_enc')
    query_id = Settings.get('flex_query_id')
    if not enc_token or not query_id:
        return 400, {'success': False, 'error': 'Flex non configuré (token + query_id)'}
    try:
        token = decrypt_credential(enc_token, current_app.config.get('SECRET_KEY', ''))
    except Exception:
        return 500, {'success': False, 'error': 'Déchiffrement du token impossible'}
    try:
        parsed = flex_service.fetch_and_parse(token, query_id)
    except Exception as e:
        Settings.set('flex_last_error', str(e)[:300])
        return 502, {'success': False, 'error': f'Flex : {e}'}

    new_account_id = (parsed.get('account_id') or '').strip()
    stored_account_id = (Settings.get('flex_account_id') or '').strip()
    if new_account_id.upper().startswith('DU') and not force:
        Settings.set('flex_last_error', f'Compte paper détecté ({new_account_id}). Utilisez force=true pour confirmer.')
        return 409, {'success': False, 'is_paper': True, 'force_available': True,
                     'account_id': new_account_id,
                     'error': (f'Le rapport Flex appartient à un compte PAPER ({new_account_id}). '
                               f'Vérifiez que votre Flex Query pointe sur votre compte LIVE.')}
    account_changed = bool(stored_account_id and new_account_id and stored_account_id != new_account_id)

    if not force and parsed.get('nav'):
        existing = sorted(s.nav for s in PortfolioSnapshot.query.all())
        if existing:
            med_old = existing[len(existing) // 2]
            new = sorted(r['nav'] for r in parsed['nav'])
            med_new = new[len(new) // 2]
            if med_old > 0 and not (0.1 <= med_new / med_old <= 10):
                ratio = round(med_new / med_old, 2)
                return 409, {'success': False, 'force_available': True, 'account_id': new_account_id,
                             'nav_inconsistency': {'median_existing': round(med_old, 2),
                                                   'median_new': round(med_new, 2), 'ratio': ratio},
                             'error': (f'Incohérence NAV : médiane existante {med_old:.0f}$ vs médiane du '
                                       f'rapport {med_new:.0f}$ ({ratio}×). Le rapport semble pointer sur un '
                                       f'autre compte. Passez force=true pour importer quand même.')}

    counts = import_flex(parsed)
    Settings.set('flex_last_sync', datetime.now().isoformat())
    Settings.set('flex_last_error', '')
    if new_account_id:
        Settings.set('flex_account_id', new_account_id)
    return 200, {
        'success': True, 'account_id': new_account_id,
        'is_paper': new_account_id.upper().startswith('DU'),
        'account_changed': account_changed,
        'imported': counts,
        'totals': {'snapshots': PortfolioSnapshot.query.count(),
                   'transactions': Transaction.query.count(),
                   'dividends': Dividend.query.count(),
                   'cash_flows': CashFlow.query.count()},
    }
