# -*- coding: utf-8 -*-
import asyncio
import base64
import hashlib
import logging
import random
import threading
import time

from cryptography.fernet import Fernet
from ib_async import IB, Stock, Index, Order

logger = logging.getLogger(__name__)


class IBKRService:
    """
    Connexion à IB Gateway via ib_async.

    Architecture : UNE connexion persistante (self._ib) maintenue par un event loop
    PERMANENT (run_forever) tournant dans un thread dédié. ib_async ne fonctionne pas
    avec asyncio.run() dans un thread secondaire (gunicorn gthread) ; il faut un loop
    unique auquel on soumet les coroutines, et créer l'objet IB() À L'INTÉRIEUR de ce
    loop. Les opérations sont sérialisées par self._lock (une seule à la fois).

    - clientId aléatoire (100→99999) à chaque (re)connexion : évite l'erreur 326
      "client id already in use" si une ancienne socket traîne côté gateway. Comme
      la connexion est persistante et fermée proprement, un seul clientId est actif
      à la fois — on reste loin de la limite de 32 connexions simultanées d'IBKR.
    - readonly=True par défaut (lecture seule, sécurité). La connexion bascule en
      mode trading (readonly=False) uniquement pour exécuter des ordres réels.
    - cooldown après échec pour ne pas marteler le gateway (51 tickers d'un calcul).
    """

    CONNECT_COOLDOWN = 60  # secondes avant de retenter après un échec

    # Throttle pacing IBKR : ~60 requêtes historiques / 10 min → on espace de 0.4s
    HIST_MIN_INTERVAL = 0.4

    def __init__(self, host='ib-gateway', port=4003, client_id=1):
        self.host = host
        self.port = port
        self._client_id = 0
        self._lock = threading.Lock()
        self._connected_at = None
        self._last_error = None
        self._last_failed_at = None  # horodatage du dernier échec de connexion
        self._ib = None
        self._readonly = True        # mode de la connexion courante
        self._last_hist_call = 0.0   # throttle des requêtes historiques (pacing)

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

    def connect(self, force: bool = True, readonly: bool = True) -> dict:
        with self._lock:
            # Si déjà connecté dans le bon mode et pas de force → rien à faire
            if not force and self._is_connected() and self._readonly == readonly:
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
                        clientId=cid, readonly=readonly, timeout=15,
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
                self._readonly = readonly
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
                                          'timed out', 'Errno 111', 'Errno 10061',
                                          'Errno -3', 'name resolution', 'Temporary failure')):
                    friendly = (
                        f"IB Gateway non accessible (hôte '{self.host}:{self.port}'). "
                        "Le conteneur est peut-être en cours de redémarrage (~90s après un "
                        "changement de mode). Réessayez dans une minute."
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
                except Exception as e:
                    logger.warning('Erreur lors de la déconnexion IBKR : %s', e)
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

        Respecte le pacing IBKR (~60 requêtes historiques / 10 min) :
          - espacement minimal entre requêtes (HIST_MIN_INTERVAL)
          - retry unique avec back-off si violation de pacing (erreur 162)

        Returns: [{'date': 'YYYY-MM-DD', 'adj_close': float, 'close': float, 'volume': float|None}]
        """
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def fn():
            # Throttle : espacer les requêtes historiques pour éviter le pacing
            elapsed = time.time() - self._last_hist_call
            if elapsed < self.HIST_MIN_INTERVAL:
                await asyncio.sleep(self.HIST_MIN_INTERVAL - elapsed)

            contract = Stock(ticker.upper(), 'SMART', 'USD')
            await self._ib.qualifyContractsAsync(contract)

            for attempt in range(2):
                try:
                    bars = await self._ib.reqHistoricalDataAsync(
                        contract, endDateTime='', durationStr=duration,
                        barSizeSetting='1 day', whatToShow='ADJUSTED_LAST', useRTH=True,
                    )
                    self._last_hist_call = time.time()
                    return bars
                except Exception as e:
                    msg = str(e).lower()
                    # Erreur 162 / pacing violation → back-off et retry une fois
                    if attempt == 0 and ('pacing' in msg or '162' in msg):
                        logger.warning('Pacing IBKR sur %s — back-off 12s', ticker)
                        await asyncio.sleep(12)
                        continue
                    self._last_hist_call = time.time()
                    raise

        bars = self._submit(fn(), timeout=90)
        if not bars:
            raise RuntimeError(f'Aucune donnée historique IBKR pour {ticker}')

        result = []
        for b in bars:
            d = b.date
            date_str = d.isoformat() if hasattr(d, 'isoformat') else str(d)[:10]
            vol = getattr(b, 'volume', None)
            try:
                vol = float(vol) if vol is not None and float(vol) >= 0 else None
            except (TypeError, ValueError):
                vol = None
            result.append({
                'date': date_str[:10],
                'adj_close': float(b.close),
                'close': float(b.close),
                'volume': vol,
            })
        return result

    def get_quotes(self, tickers: list, include_vix: bool = True) -> dict:
        """
        Récupère un snapshot temps réel (ou différé) pour une liste de tickers
        et, optionnellement, l'indice VIX.

        On bascule en données différées (reqMarketDataType=3) en fallback pour ne
        PAS exiger d'abonnement temps réel : le moniteur de marché tourne chaque
        minute et n'a pas besoin de la précision tick.

        Returns:
            { 'AAPL': {'last': float|None, 'prev_close': float|None, 'pct': float|None},
              'VIX':  {'last': ..., 'prev_close': ..., 'pct': ...}, ... }
            Les tickers sans donnée exploitable sont omis.
        """
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        symbols = [t.upper() for t in tickers if t]

        async def fn():
            contracts = {}
            for sym in symbols:
                contracts[sym] = Stock(sym, 'SMART', 'USD')
            if include_vix:
                contracts['VIX'] = Index('VIX', 'CBOE', 'USD')

            # Qualifier les contrats (ignore ceux qui échouent)
            valid = {}
            for sym, c in contracts.items():
                try:
                    await self._ib.qualifyContractsAsync(c)
                    valid[sym] = c
                except Exception as e:
                    logger.warning('get_quotes: contrat non qualifié %s (%s)', sym, e)

            if not valid:
                return {}

            # 1 = temps réel ; 3 = différé (15 min). On tente le temps réel puis on
            # laisse le différé prendre le relais si pas d'abonnement.
            self._ib.reqMarketDataType(3)
            tickers_data = await self._ib.reqTickersAsync(*valid.values())
            return {c.symbol: t for c, t in zip(valid.values(), tickers_data)}

        raw = self._submit(fn(), timeout=60)

        def _num(x):
            try:
                if x is None:
                    return None
                xf = float(x)
                # ib_async renvoie nan quand la donnée est absente
                return None if xf != xf else xf
            except (TypeError, ValueError):
                return None

        result = {}
        for sym, t in (raw or {}).items():
            last = _num(getattr(t, 'last', None))
            if last is None:
                last = _num(getattr(t, 'marketPrice', lambda: None)() if callable(getattr(t, 'marketPrice', None)) else None)
            if last is None:
                last = _num(getattr(t, 'close', None))
            prev_close = _num(getattr(t, 'close', None))
            pct = None
            if last is not None and prev_close not in (None, 0):
                pct = round((last - prev_close) / prev_close * 100, 2)
            if last is None and prev_close is None:
                continue
            result[sym] = {'last': last, 'prev_close': prev_close, 'pct': pct}
        return result

    def _ensure_trading(self) -> None:
        """Bascule en mode trading (readonly=False) si nécessaire. Lève ConnectionError si impossible."""
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')
        if self._readonly:
            res = self.connect(force=True, readonly=False)
            if not res.get('success'):
                raise ConnectionError(f"Passage en mode trading impossible : {res.get('error')}")

    async def _qualify_and_place(self, contract, order, dry_run: bool) -> dict:
        """Qualifie le contrat et place l'ordre. Retourne {'status', 'order_id'?, 'error'?}."""
        if dry_run:
            return {'status': 'preview'}
        try:
            qualified = await self._ib.qualifyContractsAsync(contract)
            if not qualified:
                return {'status': 'failed', 'error': 'Contrat non qualifiable'}
            trade = self._ib.placeOrder(contract, order)
            await asyncio.sleep(1.0)   # laisser le gateway accuser réception
            order_id = getattr(trade.order, 'orderId', None)
            order_status = getattr(trade.orderStatus, 'status', 'Submitted')
            return {'status': 'placed', 'order_id': order_id, 'order_status': order_status}
        except Exception as e:
            return {'status': 'failed', 'error': str(e)[:200]}

    async def _get_market_price(self, ticker: str, currency: str = 'USD') -> float | None:
        """Prix marché en temps réel (ou différé) pour calculer la quantité en actions."""
        try:
            contract = Stock(ticker, 'SMART', currency)
            await self._ib.qualifyContractsAsync(contract)
            self._ib.reqMarketDataType(3)   # données différées si pas d'abonnement
            tickers = await self._ib.reqTickersAsync(contract)
            if not tickers:
                return None
            t = tickers[0]
            for attr in ('last', 'close', 'bid', 'ask'):
                v = getattr(t, attr, None)
                try:
                    f = float(v) if v is not None else None
                    if f and f == f and f > 0:
                        return f
                except (TypeError, ValueError):
                    pass
            return None
        except Exception as e:
            logger.warning('_get_market_price %s : %s', ticker, e)
            return None

    def place_rebalance_orders(self, targets: list, dry_run: bool = True) -> dict:
        """
        Rééquilibrage vers les cibles (ticker + target_pct du portefeuille).
        Utilise totalQuantity (actions calculées depuis le prix live) — plus fiable
        que cashQty qui n'est pas supporté sur tous les comptes/instruments IBKR.
        """
        if not dry_run:
            self._ensure_trading()
        elif not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        stats          = self.get_portfolio_stats()
        total_value    = stats['total_value']
        current        = {p['ticker']: p for p in stats['positions']}
        target_tickers = {t['ticker'].upper() for t in targets}
        total_target_pct = sum(float(t.get('target_pct', 0)) for t in targets)

        async def fn():
            orders = []
            # Contrats réels du portfolio (pour ventes exactes)
            real_contracts = {}
            for item in self._ib.portfolio():
                c = item.contract
                real_contracts[c.localSymbol or c.symbol] = c

            # 1) Positions cibles — calcul en nb d'actions depuis le prix live
            for t in targets:
                ticker       = t['ticker'].upper()
                target_pct   = float(t.get('target_pct', 0))
                currency     = t.get('currency', 'USD')
                target_value = total_value * target_pct / 100
                cur_value    = current.get(ticker, {}).get('market_value', 0) or 0
                cur_qty      = current.get(ticker, {}).get('qty', 0) or 0
                diff_usd     = target_value - cur_value

                if abs(diff_usd) < 10:   # mouvement < $10 → on ignore
                    continue

                action = 'BUY' if diff_usd > 0 else 'SELL'
                contract = real_contracts.get(ticker) or Stock(ticker, 'SMART', currency)

                # Récupérer le prix pour calculer la quantité en actions
                price = await self._get_market_price(ticker, currency) if not dry_run else None
                if price is None:
                    # En dry_run ou si prix indisponible : estimer depuis market_value/qty
                    cur_price = current.get(ticker, {}).get('market_price')
                    price = cur_price if cur_price else (abs(cur_value / cur_qty) if cur_qty else 100)

                qty = max(1, int(abs(diff_usd) / price))
                if action == 'SELL':
                    qty = min(qty, int(abs(cur_qty)))   # ne pas vendre plus qu'on a

                if qty < 1:
                    continue

                entry = {
                    'ticker': ticker, 'action': action, 'qty': qty,
                    'est_price': round(price, 2), 'est_value': round(qty * price, 2),
                    'current_value': round(cur_value, 2), 'target_value': round(target_value, 2),
                    'liquidation': False, 'status': 'preview',
                }
                orders.append(entry)
                order = Order(action=action, orderType='MKT', totalQuantity=qty, tif='DAY')
                result = await self._qualify_and_place(contract, order, dry_run)
                entry.update(result)

            # 2) Liquidations — positions hors cibles, vente totalité
            for ticker, p in current.items():
                if ticker in target_tickers:
                    continue
                qty = p.get('qty') or 0
                if abs(qty) < 1e-6:
                    continue
                mv       = p.get('market_value') or 0
                action   = 'SELL' if qty > 0 else 'BUY'
                entry = {
                    'ticker': ticker, 'action': action, 'qty': abs(qty),
                    'est_price': round(abs(mv / qty), 2) if qty else 0,
                    'est_value': round(abs(mv), 2),
                    'current_value': round(mv, 2), 'target_value': 0.0,
                    'liquidation': True, 'status': 'preview',
                }
                orders.append(entry)
                contract = real_contracts.get(ticker) or Stock(ticker, 'SMART', p.get('currency', 'USD'))
                order = Order(action=action, orderType='MKT', totalQuantity=abs(qty), tif='DAY')
                result = await self._qualify_and_place(contract, order, dry_run)
                entry.update(result)

            return {'orders': orders, 'total_target_pct': round(total_target_pct, 1)}

        try:
            return self._submit(fn(), timeout=120)
        finally:
            if not dry_run and not self._readonly:
                try:
                    self.connect(force=True, readonly=True)
                except Exception:
                    pass

    def place_single_order(self, ticker: str, action: str, qty: float,
                           order_type: str = 'MKT', limit_price: float = None,
                           currency: str = 'USD', tif: str = 'DAY') -> dict:
        """
        Passe un ordre unique sur un ticker (utile pour tester la connectivité).
        action: 'BUY' | 'SELL'
        order_type: 'MKT' | 'LMT'
        Retourne {'success', 'order_id'?, 'order_status'?, 'error'?}.
        """
        self._ensure_trading()

        async def fn():
            contract = Stock(ticker.upper(), 'SMART', currency)
            qualified = await self._ib.qualifyContractsAsync(contract)
            if not qualified:
                return {'success': False, 'error': f'Contrat {ticker} non qualifiable'}

            order = Order(
                action=action.upper(),
                orderType=order_type.upper(),
                totalQuantity=float(qty),
                tif=tif,
            )
            if order_type.upper() == 'LMT' and limit_price:
                order.lmtPrice = round(float(limit_price), 2)

            trade = self._ib.placeOrder(contract, order)
            await asyncio.sleep(1.5)
            order_id = getattr(trade.order, 'orderId', None)
            status   = getattr(trade.orderStatus, 'status', 'Submitted')
            logger.info('Ordre unique : %s %s %s qty=%s → orderId=%s status=%s',
                        action, ticker, order_type, qty, order_id, status)
            return {'success': True, 'order_id': order_id, 'order_status': status,
                    'ticker': ticker, 'action': action, 'qty': qty,
                    'order_type': order_type, 'limit_price': limit_price}

        try:
            result = self._submit(fn(), timeout=30)
            return result
        except Exception as e:
            return {'success': False, 'error': str(e)[:300]}
        finally:
            try:
                self.connect(force=True, readonly=True)
            except Exception:
                pass

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
