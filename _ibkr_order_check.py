# -*- coding: utf-8 -*-
"""
Validation bout-en-bout du passage d'ordres IBKR (à lancer DANS le conteneur app,
qui voit le gateway sur le réseau Docker) :

    docker exec momentum-app-app-1 python _ibkr_order_check.py status
    docker exec momentum-app-app-1 python _ibkr_order_check.py whatif F --qty 1
    docker exec momentum-app-app-1 python _ibkr_order_check.py probe F --lmt 1.00
    docker exec momentum-app-app-1 python _ibkr_order_check.py buy F --cash 1 --yes
    docker exec momentum-app-app-1 python _ibkr_order_check.py orders
    docker exec momentum-app-app-1 python _ibkr_order_check.py cancel 42

`probe` = séquence sans risque : simulations whatIf (1 action, 0,1 action,
1 $ en montant), puis un ordre LMT très loin du marché (jamais exécutable) pour
vérifier que le serveur ACCEPTE l'ordre (PreSubmitted/Submitted), puis annulation.
`buy`/`sell` placent un VRAI ordre (--yes obligatoire).

N'importe pas `app` (pas de DB, pas de scheduler) : uniquement IBKRService, donc
le code testé est exactement celui utilisé par /api/ibkr/rebalance.
"""
import argparse
import json
import os
import sys
import time

from ibkr_service import IBKRService


def dump(label, data):
    print(f'\n── {label}')
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def connect(args) -> IBKRService:
    svc = IBKRService(host=args.host, port=args.port)
    res = svc.connect()
    if not res.get('success'):
        print(f"❌ Connexion impossible : {res.get('error')}")
        sys.exit(2)
    print(f"✅ Connecté {args.host}:{args.port} clientId={res.get('client_id')}")
    return svc


def cmd_status(svc, args):
    dump('Compte', svc.get_account_summary())
    stats = svc.get_portfolio_stats()
    dump('Positions', [{k: p[k] for k in ('ticker', 'qty', 'market_price', 'market_value', 'con_id', 'primary_exchange')}
                       for p in stats['positions']])
    dump('Ordres ouverts', svc.get_open_orders())
    dump('Exécutions du jour', svc.get_executions_today())


def cmd_whatif(svc, args):
    res = svc.place_single_order(args.symbol, args.action, qty=args.qty, cash=args.cash,
                                 order_type='LMT' if args.lmt else 'MKT', limit_price=args.lmt,
                                 tif=args.tif, dry_run=True)
    dump(f'whatIf {args.action} {args.symbol}', res)


def cmd_probe(svc, args):
    sym = args.symbol
    print(f"\n=== PROBE {sym} — simulations (aucun ordre placé) ===")
    for label, kw in (('MKT 1 action', dict(qty=1)),
                      ('MKT 0.1 action (fractionnaire)', dict(qty=0.1)),
                      ('MKT 1 $ en montant (cashQty)', dict(qty=0, cash=1.0))):
        res = svc.place_single_order(sym, 'BUY', dry_run=True, **kw)
        verdict = res.get('whatif')
        extra = res.get('whatif_error') or (f"commission={res.get('commission')} {res.get('commission_currency') or ''}"
                                            if verdict == 'ok' else '')
        print(f"  {label:38} → {verdict:9} {extra}")

    print(f"\n=== PROBE {sym} — ordre réel LMT {args.lmt} $ (inexécutable), puis annulation ===")
    res = svc.place_single_order(sym, 'BUY', qty=args.qty or 1, order_type='LMT',
                                 limit_price=args.lmt, tif=args.tif)
    dump('Placement', res)
    oid = res.get('order_id')
    if res.get('status') in ('placed', 'pending') and oid:
        time.sleep(2)
        dump('Ordres ouverts', [o for o in svc.get_open_orders() if o.get('order_id') == oid or o.get('perm_id') == res.get('perm_id')])
        dump('Annulation', svc.cancel_order(oid))
    verdict = 'OK — le serveur accepte les ordres de cette app' if res.get('status') == 'placed' \
        else f"ÉCHEC — {res.get('error') or res.get('note') or res.get('order_status')}"
    print(f"\n🏁 {verdict}")


def cmd_trade(svc, args, action):
    if not args.yes:
        print('Refusé : ajoute --yes pour placer un VRAI ordre.')
        sys.exit(1)
    res = svc.place_single_order(args.symbol, action, qty=args.qty, cash=args.cash,
                                 order_type='LMT' if args.lmt else 'MKT', limit_price=args.lmt,
                                 tif=args.tif)
    dump(f'{action} {args.symbol}', res)
    if args.wait and res.get('status') in ('placed', 'pending'):
        deadline = time.time() + args.wait
        while time.time() < deadline:
            time.sleep(3)
            mine = [o for o in svc.get_open_orders() if o.get('order_id') == res.get('order_id')]
            if not mine:
                break
            print(f"  … {mine[0]['status']} filled={mine[0]['filled']} why_held={mine[0]['why_held']}")
        dump('Exécutions du jour', svc.get_executions_today())


def cmd_orders(svc, args):
    dump('Ordres ouverts', svc.get_open_orders())
    dump('Exécutions du jour', svc.get_executions_today())


def cmd_cancel(svc, args):
    dump(f'Annulation {args.order_id}', svc.cancel_order(args.order_id))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', default=os.environ.get('IB_GATEWAY_HOST', 'ib-gateway'))
    p.add_argument('--port', type=int, default=int(os.environ.get('IB_GATEWAY_PORT', 4003)))
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    sub.add_parser('orders')

    def order_args(sp):
        sp.add_argument('symbol')
        sp.add_argument('--qty', type=float)
        sp.add_argument('--cash', type=float, help='montant en $ (cashQty)')
        sp.add_argument('--lmt', type=float, help='prix limite (sinon MKT)')
        sp.add_argument('--tif', default='DAY')
        return sp
    w = order_args(sub.add_parser('whatif'))
    w.add_argument('--action', default='BUY')
    pr = order_args(sub.add_parser('probe'))
    pr.set_defaults(lmt=1.00)
    for name in ('buy', 'sell'):
        sp = order_args(sub.add_parser(name))
        sp.add_argument('--yes', action='store_true')
        sp.add_argument('--wait', type=int, default=0, help='secondes à suivre le statut après placement')
    c = sub.add_parser('cancel')
    c.add_argument('order_id', type=int)
    args = p.parse_args()

    svc = connect(args)
    try:
        {'status': cmd_status, 'orders': cmd_orders, 'whatif': cmd_whatif, 'probe': cmd_probe,
         'cancel': cmd_cancel,
         'buy': lambda s, a: cmd_trade(s, a, 'BUY'),
         'sell': lambda s, a: cmd_trade(s, a, 'SELL')}[args.cmd](svc, args)
    finally:
        svc.disconnect()


if __name__ == '__main__':
    main()
