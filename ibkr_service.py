# -*- coding: utf-8 -*-
import asyncio
import base64
import hashlib
import logging
import random
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

    CONNECT_COOLDOWN = 60  # secondes avant de retenter après un échec

    def __init__(self, host='ib-gateway', port=4003, client_id=1):
        self.host = host
        self.port = port
        self._client_id = 0
        self._lock = threading.Lock()
        self._connected_at = None
        self._last_error = None
        self._last_failed_at = None  # horodatage du dernier échec de connexion
        self._ib = None

        # Event loop PERMANENT dans un thread dédié. ib_async ne fonctionne pas
        # avec asyncio.run() dans un thread secondaire (gunicorn gthread) : il faut
        # un loop run_forever() unique auquel on soumet les coroutines, et créer
        # l'objet IB() À L'INTÉRIEUR de ce loop.
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

    def _next_cid(self):
        # clientId aléatoire dans une grande plage : évite les collisions avec
        # d'éventuelles connexions zombies (erreur 326). Le loop permanent +
        # disconnect propre évite d'en créer de nouvelles.
        self._client_id = random.randint(100, 99999)
        return self._client_id

    def _submit(self, coro, timeout=45):
        """Soumet une coroutine au loop permanent et attend le résultat."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def _is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    # ------------------------------------------------------------------
    # Connexion / statut
    # ------------------------------------------------------------------

    def connect(self, force: bool = True) -> dict:
        with self._lock:
            if not force and self._is_connected():
                return {'success': True}

            async def _connect():
                # Fermer l'ancienne connexion si présente
                if self._ib is not None:
                    try:
                        self._ib.disconnect()
                    except Exception:
                        pass
                    self._ib = None
                # IB() créé DANS le loop permanent → apiStart lié au bon loop
                ib = IB()
                cid = self._next_cid()
                try:
                    await ib.connectAsync(
                        self.host, self.port,
                        clientId=cid, readonly=True, timeout=15,
                    )
                except Exception as e:
                    # connectAsync lance reqPositions/reqExecutions/reqAccountUpdates
                    # qui peuvent timeout sur ce gateway SANS empêcher la connexion
                    # socket. Si la socket est connectée, on garde — portfolio()
                    # fonctionne malgré ces warnings.
                    if not ib.isConnected():
                        raise
                    logger.info('connectAsync : warnings de sync ignorés (%s)', e)
                await asyncio.sleep(1.5)  # laisser arriver les données initiales
                if not ib.isConnected():
                    raise RuntimeError('Connexion non établie')
                self._ib = ib
                return cid

            try:
                cid = self._submit(_connect(), timeout=40)
                self._connected_at = time.time()
                self._last_error = None
                self._last_failed_at = None  # reset cooldown après succès
                logger.info('IBKR connecté (%s:%s, clientId=%s)', self.host, self.port, cid)
                return {'success': True}
            except Exception as e:
                raw = str(e)
                # DNS / réseau : le conteneur Docker n'est pas démarré ou pas accessible
                if any(k in raw for k in ('getaddrinfo', 'Errno 11001', 'Name or service not known',
                                          'nodename nor servname', 'ConnectionRefused', 'Connection refused',
                                          'timed out', 'Errno 111', 'Errno 10061')):
                    friendly = (
                        f"IB Gateway non accessible (hôte '{self.host}:{self.port}'). "
                        "Vérifiez que le conteneur Docker ib-gateway est démarré et que "
                        "IB_GATEWAY_HOST/PORT sont corrects dans les variables d'environnement."
                    )
                else:
                    friendly = raw or 'Connexion impossible'
                self._last_error = friendly
                self._last_failed_at = time.time()
                logger.warning('IBKR connexion échouée : %s', raw)
                return {'success': False, 'error': friendly}

    def disconnect(self):
        with self._lock:
            if self._ib is not None:
                try:
                    self._submit(self._async_disconnect(), timeout=10)
                except Exception:
                    pass
                self._ib = None
            self._connected_at = None

    async def _async_disconnect(self):
        if self._ib is not None:
            self._ib.disconnect()

    def get_status(self) -> dict:
        cooldown_remaining = None
        if self._last_failed_at and not self._is_connected():
            remaining = self.CONNECT_COOLDOWN - (time.time() - self._last_failed_at)
            cooldown_remaining = max(0, round(remaining))
        return {
            'connected': self._is_connected(),
            'connected_at': self._connected_at,
            'last_error': self._last_error,
            'host': self.host,
            'port': self.port,
            'cooldown_remaining_s': cooldown_remaining,
        }

    def ensure_connected(self) -> bool:
        """
        Reconnecte automatiquement si la session est tombée.
        Respecte un cooldown après un échec pour ne pas retenter
        la connexion pour chaque ticker lors d'un calcul (51 appels).
        """
        if self._is_connected():
            return True
        # Cooldown : si le dernier échec date de moins de CONNECT_COOLDOWN secondes, on skip
        if self._last_failed_at and (time.time() - self._last_failed_at) < self.CONNECT_COOLDOWN:
            return False
        return self.connect(force=False).get('success', False)

    # ------------------------------------------------------------------
    # Données portfolio
    # ------------------------------------------------------------------

    def get_positions(self) -> list:
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def fn():
            return self._ib.portfolio()
        portfolio = self._submit(fn(), timeout=20)
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
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def fn():
            contract = Stock(ticker.upper(), 'SMART', 'USD')
            await self._ib.qualifyContractsAsync(contract)
            return await self._ib.reqHistoricalDataAsync(
                contract, endDateTime='', durationStr=duration,
                barSizeSetting='1 day', whatToShow='ADJUSTED_LAST', useRTH=True,
            )

        bars = self._submit(fn(), timeout=60)
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

        stats       = self.get_portfolio_stats()
        total_value = stats['total_value']
        current     = {p['ticker']: p for p in stats['positions']}

        async def fn():
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
                    await self._ib.qualifyContractsAsync(contract)
                    order = Order(action=action, orderType='MKT',
                                  totalQuantity=0, cashQty=cash_qty, tif='DAY')
                    self._ib.placeOrder(contract, order)
                    await asyncio.sleep(0.5)
            return orders

        # Note : la connexion est readonly → l'exécution réelle (dry_run=False)
        # nécessitera une connexion non-readonly à mettre en place séparément.
        return self._submit(fn(), timeout=120)

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
