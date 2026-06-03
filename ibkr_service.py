# -*- coding: utf-8 -*-
import asyncio
import base64
import hashlib
import logging
import threading
import time

from cryptography.fernet import Fernet
from ib_async import IB, Stock, Order

logger = logging.getLogger(__name__)


class IBKRService:
    """
    Connexion à IB Gateway via ib_async.

    Architecture (pattern prouvé, robuste) : chaque opération ouvre une connexion
    fraîche dans un thread isolé via asyncio.run(), exécute la requête, puis ferme
    proprement (disconnect). Aucune connexion persistante → aucun thread zombie,
    aucun clientId bloqué côté gateway.

    - clientId rotatif (1→32) pour éviter l'erreur 326 "client id already in use"
    - les opérations sont sérialisées par un lock (une connexion à la fois)
    - get_status() reflète la dernière opération réussie (TTL 5 min)
    """

    def __init__(self, host='ib-gateway', port=4003, client_id=1):
        self.host = host
        self.port = port
        self._client_id = 0
        self._lock = threading.Lock()
        self._last_ok = None
        self._last_error = None

    # ------------------------------------------------------------------
    # Cœur : exécution d'une opération avec connexion fraîche
    # ------------------------------------------------------------------

    def _next_cid(self):
        self._client_id = (self._client_id % 32) + 1
        return self._client_id

    def _run(self, async_fn, timeout=45, hold=1.5):
        """
        Ouvre une connexion fraîche, exécute async_fn(ib), ferme.

        Args:
            async_fn: coroutine prenant l'objet ib, retournant un résultat
            timeout: délai max global
            hold: pause après connexion pour laisser arriver les données initiales

        Le tout tourne dans un thread isolé avec asyncio.run() (loop propre fermé
        en fin → pas de thread zombie). Sérialisé par self._lock.
        """
        with self._lock:
            result = [None]
            error = [None]
            done = threading.Event()
            cid = self._next_cid()

            def worker():
                async def main():
                    ib = IB()
                    await ib.connectAsync(
                        self.host, self.port,
                        clientId=cid, readonly=True, timeout=20,
                    )
                    try:
                        if hold:
                            await asyncio.sleep(hold)
                        return await async_fn(ib)
                    finally:
                        ib.disconnect()

                try:
                    result[0] = asyncio.run(main())
                except Exception as e:
                    error[0] = e
                finally:
                    done.set()

            threading.Thread(target=worker, daemon=True).start()

            if not done.wait(timeout=timeout):
                self._last_error = 'Timeout'
                raise TimeoutError('IBKR : délai dépassé')
            if error[0] is not None:
                self._last_error = str(error[0])
                raise error[0]

            self._last_ok = time.time()
            self._last_error = None
            return result[0]

    # ------------------------------------------------------------------
    # Connexion / statut
    # ------------------------------------------------------------------

    def connect(self, force: bool = True) -> dict:
        """Teste une connexion au gateway (handshake + déconnexion)."""
        try:
            self._run(lambda ib: asyncio.sleep(0), timeout=40, hold=0.5)
            logger.info('IBKR connexion OK (%s:%s)', self.host, self.port)
            return {'success': True}
        except Exception as e:
            logger.warning('IBKR connexion échouée : %s', e)
            return {'success': False, 'error': str(e) or 'Connexion impossible'}

    def disconnect(self):
        """Sans effet : pas de connexion persistante à fermer."""
        self._last_ok = None

    def get_status(self) -> dict:
        connected = bool(self._last_ok and (time.time() - self._last_ok < 300))
        return {
            'connected': connected,
            'connected_at': self._last_ok,
            'last_error': self._last_error,
        }

    def ensure_connected(self) -> bool:
        """
        Vérifie que le gateway répond. Comme chaque opération se connecte d'elle-même,
        on teste juste la disponibilité (avec un cache court pour éviter le spam).
        """
        if self._last_ok and (time.time() - self._last_ok < 120):
            return True
        return self.connect().get('success', False)

    # ------------------------------------------------------------------
    # Données portfolio
    # ------------------------------------------------------------------

    def get_positions(self) -> list:
        async def fn(ib):
            return ib.portfolio()
        portfolio = self._run(fn, timeout=40, hold=2.0)
        return self._format_portfolio(portfolio)

    def get_portfolio_stats(self) -> dict:
        positions = self.get_positions()
        total_value = sum(p['market_value'] or 0 for p in positions)
        total_cost  = sum((p['avg_cost'] or 0) * abs(p['qty'] or 0) for p in positions)
        total_unrl  = sum(p['unrealized_pnl'] or 0 for p in positions)
        total_real  = sum(p['realized_pnl']   or 0 for p in positions)
        winners     = [p for p in positions if (p['unrealized_pnl'] or 0) > 0]

        for p in positions:
            mv   = p['market_value'] or 0
            cost = (p['avg_cost'] or 0) * abs(p['qty'] or 0)
            p['allocation_pct'] = round(mv / total_value * 100, 1) if total_value else 0
            p['return_pct']     = round((mv - cost) / cost * 100, 1) if cost else 0

        return {
            'positions': positions,
            'total_value':          round(total_value, 2),
            'total_cost':           round(total_cost,  2),
            'total_unrealized_pnl': round(total_unrl,  2),
            'total_realized_pnl':   round(total_real,  2),
            'total_pnl':            round(total_unrl + total_real, 2),
            'return_pct':           round((total_value - total_cost) / total_cost * 100, 1) if total_cost else 0,
            'positions_count':      len(positions),
            'winning_count':        len(winners),
        }

    def get_daily_bars(self, ticker: str, duration: str = '2 Y') -> list:
        """
        Récupère les barres journalières ajustées (ADJUSTED_LAST) via IBKR.
        Returns: [{'date': 'YYYY-MM-DD', 'adj_close': float, 'close': float}]
        """
        async def fn(ib):
            contract = Stock(ticker.upper(), 'SMART', 'USD')
            await ib.qualifyContractsAsync(contract)
            return await ib.reqHistoricalDataAsync(
                contract, endDateTime='', durationStr=duration,
                barSizeSetting='1 day', whatToShow='ADJUSTED_LAST', useRTH=True,
            )

        bars = self._run(fn, timeout=60, hold=0.5)
        if not bars:
            raise RuntimeError(f'Aucune donnée historique IBKR pour {ticker}')

        result = []
        for b in bars:
            d = b.date
            date_str = d.isoformat() if hasattr(d, 'isoformat') else str(d)[:10]
            result.append({
                'date': date_str[:10],
                'adj_close': float(b.close),
                'close': float(b.close),
            })
        return result

    def place_rebalance_orders(self, targets: list, dry_run: bool = True) -> list:
        # Récupérer les stats hors connexion (get_portfolio_stats ouvre sa propre connexion)
        stats       = self.get_portfolio_stats()
        total_value = stats['total_value']
        current     = {p['ticker']: p for p in stats['positions']}

        async def fn(ib):
            orders = []
            for t in targets:
                ticker       = t['ticker'].upper()
                target_pct   = float(t.get('target_pct', 0))
                currency     = t.get('currency', 'USD')
                target_value = total_value * target_pct / 100
                cur_value    = current.get(ticker, {}).get('market_value', 0) or 0
                diff         = target_value - cur_value

                if abs(diff) < 10:
                    continue

                action   = 'BUY' if diff > 0 else 'SELL'
                cash_qty = abs(round(diff, 2))
                orders.append({
                    'ticker':        ticker,
                    'action':        action,
                    'cash_qty_usd':  cash_qty,
                    'current_value': round(cur_value, 2),
                    'target_value':  round(target_value, 2),
                })

                if not dry_run:
                    contract = Stock(ticker, 'SMART', currency)
                    await ib.qualifyContractsAsync(contract)
                    order = Order(action=action, orderType='MKT',
                                  totalQuantity=0, cashQty=cash_qty, tif='DAY')
                    ib.placeOrder(contract, order)
                    await asyncio.sleep(0.5)
            return orders

        # readonly=True empêche placeOrder → pour l'exécution réelle, utiliser une
        # connexion non-readonly. Ici on garde le dry_run comme défaut sûr.
        return self._run(fn, timeout=120, hold=1.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_portfolio(portfolio) -> list:
        result = []
        for item in portfolio:
            c = item.contract
            result.append({
                'ticker':         c.localSymbol or c.symbol,
                'qty':            item.position,
                'avg_cost':       item.averageCost,
                'market_price':   item.marketPrice,
                'market_value':   item.marketValue,
                'unrealized_pnl': item.unrealizedPNL,
                'realized_pnl':   item.realizedPNL,
                'currency':       c.currency,
            })
        return result


# ---------------------------------------------------------------------------
# Chiffrement AES-256 (Fernet) — clé dérivée du SECRET_KEY Flask
# Attention : changer SECRET_KEY invalide les credentials stockés en base.
# ---------------------------------------------------------------------------

def _make_fernet(secret_key: str) -> Fernet:
    key = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_credential(value: str, secret_key: str) -> str:
    return _make_fernet(secret_key).encrypt(value.encode()).decode()


def decrypt_credential(token: str, secret_key: str) -> str:
    return _make_fernet(secret_key).decrypt(token.encode()).decode()
