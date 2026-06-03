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

    Architecture : chaque opération crée son propre event loop via asyncio.run()
    dans un thread dédié. C'est l'approche qui fonctionne de façon prouvée avec
    ib_async et le gateway gnzsnz (connexion via socat port 4003).

    Pour les connexions persistantes (ex: streaming), une connexion "hold" tourne
    en arrière-plan et les opérations suivantes y sont soumises via run_coroutine_threadsafe.
    """

    def __init__(self, host='ib-gateway', port=4003, client_id=1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self._lock = threading.Lock()
        self._connected_at = None
        self._last_error = None

        # Connexion persistante (thread + loop + ib)
        self._ib = None
        self._loop = None
        self._conn_thread = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_connected(self) -> bool:
        return (self._ib is not None
                and self._loop is not None
                and self._loop.is_running()
                and self._ib.isConnected())

    def _run_in_conn_loop(self, coro, timeout=30):
        """Soumet une coroutine au loop de connexion persistante."""
        if not self._loop or not self._loop.is_running():
            raise RuntimeError('Loop non disponible')
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    @staticmethod
    def _run_async(coro, timeout=30):
        """Exécute une coroutine dans un loop neuf (thread dédié). Approche prouvée."""
        result_holder = [None]
        error_holder  = [None]
        done = threading.Event()

        def _thread():
            try:
                result_holder[0] = asyncio.run(coro)
            except Exception as e:
                error_holder[0] = e
            finally:
                done.set()

        t = threading.Thread(target=_thread, daemon=True)
        t.start()
        if not done.wait(timeout=timeout):
            raise TimeoutError(f'IBKR opération timeout ({timeout}s)')
        if error_holder[0]:
            raise error_holder[0]
        return result_holder[0]

    # ------------------------------------------------------------------
    # Connexion persistante
    # ------------------------------------------------------------------

    def connect(self) -> dict:
        with self._lock:
            # Arrêter une éventuelle connexion précédente
            self._close_connection()

            conn_event = threading.Event()
            conn_error = [None]

            def _thread():
                async def _connect_and_hold():
                    ib = IB()
                    await ib.connectAsync(
                        self.host, self.port,
                        clientId=self.client_id,
                        readonly=True,
                        timeout=20,
                    )
                    self._ib = ib
                    self._connected_at = time.time()
                    conn_event.set()
                    # Maintenir le loop actif pour traiter les données entrantes
                    while ib.isConnected():
                        await asyncio.sleep(1)

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                try:
                    loop.run_until_complete(_connect_and_hold())
                except Exception as e:
                    conn_error[0] = e
                    conn_event.set()
                finally:
                    loop.close()
                    self._loop = None
                    self._ib = None

            self._conn_thread = threading.Thread(target=_thread, daemon=True)
            self._conn_thread.start()

            if not conn_event.wait(timeout=30):
                self._last_error = 'Timeout de connexion'
                return {'success': False, 'error': 'Timeout de connexion'}

            if conn_error[0]:
                self._last_error = str(conn_error[0])
                return {'success': False, 'error': str(conn_error[0])}

            self._last_error = None
            logger.info('IBKR connecté à %s:%s', self.host, self.port)
            return {'success': True}

    def _close_connection(self):
        if self._ib is not None:
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._ib = None
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._connected_at = None

    def disconnect(self):
        with self._lock:
            self._close_connection()

    def get_status(self) -> dict:
        return {
            'connected': self._is_connected(),
            'connected_at': self._connected_at,
            'last_error': self._last_error,
        }

    def ensure_connected(self) -> bool:
        """
        Garantit une connexion active. Reconnecte automatiquement si la session
        app est tombée (ex: après le restart quotidien du gateway).
        Retourne True si connecté, False sinon.
        """
        if self._is_connected():
            return True
        logger.info('IBKR session perdue — tentative de reconnexion automatique')
        result = self.connect()
        return bool(result.get('success'))

    # ------------------------------------------------------------------
    # Données portfolio
    # ------------------------------------------------------------------

    def get_positions(self) -> list:
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def _do():
            await asyncio.sleep(1)
            return self._ib.portfolio()

        portfolio = self._run_in_conn_loop(_do(), timeout=15)
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

        Args:
            ticker: symbole (ex: 'AAPL')
            duration: durée IBKR (ex: '1 Y', '2 Y', '6 M')

        Returns:
            liste de dicts [{'date': 'YYYY-MM-DD', 'adj_close': float, 'close': float}]

        Raises:
            ConnectionError si non connecté, RuntimeError si données vides.
        """
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def _do():
            contract = Stock(ticker.upper(), 'SMART', 'USD')
            await self._ib.qualifyContractsAsync(contract)
            bars = await self._ib.reqHistoricalDataAsync(
                contract, endDateTime='', durationStr=duration,
                barSizeSetting='1 day', whatToShow='ADJUSTED_LAST', useRTH=True,
            )
            return bars

        bars = self._run_in_conn_loop(_do(), timeout=45)
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
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def _do():
            stats       = self.get_portfolio_stats()
            total_value = stats['total_value']
            current     = {p['ticker']: p for p in stats['positions']}
            orders      = []

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
                    await self._ib.qualifyContractsAsync(contract)
                    order = Order(
                        action=action, orderType='MKT',
                        totalQuantity=0, cashQty=cash_qty, tif='DAY',
                    )
                    self._ib.placeOrder(contract, order)
                    await asyncio.sleep(0.5)

            return orders

        return self._run_in_conn_loop(_do(), timeout=120)

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
