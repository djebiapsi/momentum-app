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
            # Demander une mise à jour du portfolio et attendre les données
            accounts = self._ib.managedAccounts()
            acct = accounts[0] if accounts else ''
            self._ib.reqAccountUpdates(True, acct)
            await asyncio.sleep(3)
            self._ib.reqAccountUpdates(False, acct)
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
