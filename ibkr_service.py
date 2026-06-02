# -*- coding: utf-8 -*-
import asyncio
import base64
import hashlib
import logging
import threading
import time

from cryptography.fernet import Fernet
from ib_async import IB

logger = logging.getLogger(__name__)


class IBKRService:
    """
    Gère la connexion persistante à IB Gateway via ib_insync.
    Tourne dans un thread dédié avec sa propre boucle asyncio.
    """

    def __init__(self, host='ib-gateway', port=4001, client_id=1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib = IB()
        self._lock = threading.Lock()
        self._connected_at = None
        self._last_error = None

        self._loop = asyncio.new_event_loop()
        t = threading.Thread(target=self._start_loop, daemon=True)
        t.start()

    def _start_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro, timeout=60):
        """Exécute une coroutine dans la boucle ib_insync depuis n'importe quel thread."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def connect(self) -> dict:
        with self._lock:
            async def _do():
                if self._ib.isConnected():
                    self._ib.disconnect()
                await self._ib.connectAsync(
                    self.host, self.port,
                    clientId=self.client_id,
                    readonly=True,
                )

            last_exc = None
            for attempt in range(3):
                try:
                    self._run(_do())
                    self._connected_at = time.time()
                    self._last_error = None
                    logger.info('IBKR connecté à %s:%s', self.host, self.port)
                    return {'success': True}
                except Exception as exc:
                    last_exc = exc
                    logger.warning('IBKR tentative %d échouée : %s', attempt + 1, exc)
                    if attempt < 2:
                        time.sleep(10)

            self._last_error = str(last_exc)
            return {'success': False, 'error': str(last_exc)}

    def disconnect(self):
        if self._ib.isConnected():
            self._ib.disconnect()
        self._connected_at = None

    def get_status(self) -> dict:
        return {
            'connected': self._ib.isConnected(),
            'connected_at': self._connected_at,
            'last_error': self._last_error,
        }

    def get_positions(self) -> list:
        if not self._ib.isConnected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def _do():
            # portfolio() est alimenté automatiquement après connexion
            await asyncio.sleep(2)
            return self._ib.portfolio()

        portfolio = self._run(_do())
        result = []
        for item in portfolio:
            c = item.contract
            result.append({
                'ticker': c.localSymbol or c.symbol,
                'qty': item.position,
                'avg_cost': item.averageCost,
                'market_price': item.marketPrice,
                'market_value': item.marketValue,
                'unrealized_pnl': item.unrealizedPNL,
                'realized_pnl': item.realizedPNL,
                'currency': c.currency,
            })
        return result

    def get_portfolio_stats(self) -> dict:
        """Retourne les stats du portefeuille : positions, P&L, allocation, valeur totale."""
        positions = self.get_positions()
        total_value = sum(p['market_value'] or 0 for p in positions)
        total_cost  = sum((p['avg_cost'] or 0) * abs(p['qty'] or 0) for p in positions)
        total_unrl  = sum(p['unrealized_pnl'] or 0 for p in positions)
        total_real  = sum(p['realized_pnl']   or 0 for p in positions)
        winners     = [p for p in positions if (p['unrealized_pnl'] or 0) > 0]

        for p in positions:
            mv = p['market_value'] or 0
            p['allocation_pct'] = round(mv / total_value * 100, 1) if total_value else 0
            cost = (p['avg_cost'] or 0) * abs(p['qty'] or 0)
            p['return_pct'] = round((mv - cost) / cost * 100, 1) if cost else 0

        return {
            'positions': positions,
            'total_value': round(total_value, 2),
            'total_cost':  round(total_cost, 2),
            'total_unrealized_pnl': round(total_unrl, 2),
            'total_realized_pnl':   round(total_real, 2),
            'total_pnl': round(total_unrl + total_real, 2),
            'return_pct': round((total_value - total_cost) / total_cost * 100, 1) if total_cost else 0,
            'positions_count': len(positions),
            'winning_count': len(winners),
        }

    def place_rebalance_orders(self, targets: list, dry_run: bool = True) -> dict:
        """
        Passe des ordres de rééquilibrage.
        targets = [{'ticker': str, 'target_pct': float, 'currency': str}]
        Utilise des ordres en montant USD (cashQty) pour les fractional shares.
        """
        if not self._ib.isConnected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def _do():
            from ib_async import Stock, Order
            stats = self.get_portfolio_stats()
            total_value = stats['total_value']
            current = {p['ticker']: p for p in stats['positions']}
            orders_preview = []

            for t in targets:
                ticker = t['ticker'].upper()
                target_pct = float(t.get('target_pct', 0))
                currency = t.get('currency', 'USD')
                target_value = total_value * target_pct / 100
                current_value = current.get(ticker, {}).get('market_value', 0) or 0
                diff = target_value - current_value

                if abs(diff) < 10:  # ignorer les petits écarts < 10 USD
                    continue

                action = 'BUY' if diff > 0 else 'SELL'
                cash_qty = abs(round(diff, 2))

                orders_preview.append({
                    'ticker': ticker,
                    'action': action,
                    'cash_qty_usd': cash_qty,
                    'current_value': round(current_value, 2),
                    'target_value': round(target_value, 2),
                })

                if not dry_run:
                    contract = Stock(ticker, 'SMART', currency)
                    await self._ib.qualifyContractsAsync(contract)
                    order = Order(
                        action=action,
                        orderType='MKT',
                        totalQuantity=0,
                        cashQty=cash_qty,
                        tif='DAY',
                    )
                    self._ib.placeOrder(contract, order)
                    await asyncio.sleep(0.5)

            return orders_preview

        return self._run(_do(), timeout=120)


# ---------------------------------------------------------------------------
# Chiffrement des identifiants (AES-256 via Fernet)
# Clé dérivée du SECRET_KEY de l'application — ne jamais changer SECRET_KEY
# sans re-saisir les identifiants IBKR dans l'interface.
# ---------------------------------------------------------------------------

def _make_fernet(secret_key: str) -> Fernet:
    key = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_credential(value: str, secret_key: str) -> str:
    return _make_fernet(secret_key).encrypt(value.encode()).decode()


def decrypt_credential(token: str, secret_key: str) -> str:
    return _make_fernet(secret_key).decrypt(token.encode()).decode()
