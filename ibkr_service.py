# -*- coding: utf-8 -*-
import asyncio
import base64
import concurrent.futures
import hashlib
import logging
import math
import random
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from ib_async import IB, Stock, Index, Order
from ib_async.order import OrderStatus
from ib_async.util import UNSET_DOUBLE

logger = logging.getLogger(__name__)

_ET = ZoneInfo('America/New_York')


def _us_market_open() -> bool:
    """True si le marché US est actuellement ouvert (lun-ven 9h30-16h00 ET)."""
    now = datetime.now(_ET)
    if now.weekday() >= 5:          # samedi=5, dimanche=6
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= t < 16 * 60


def _num(x):
    """float propre ou None (ib_async renvoie nan / UNSET_DOUBLE quand absent)."""
    try:
        if x is None or x == '':
            return None
        f = float(x)
        if f != f or f >= UNSET_DOUBLE / 2:
            return None
        return f
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Plan de rééquilibrage — logique PURE (sans IBKR), couverte par test_ibkr_service.py
# ---------------------------------------------------------------------------

# En dessous de cet écart on ne corrige pas : la commission IBKR Pro (0,35–1 $ min)
# mangerait plusieurs % du mouvement.
MIN_ORDER_VALUE_USD = 25.0
# Précision des quantités IBKR (positions fractionnaires tenues à 4 décimales).
# Vérifié en prod le 31/10/2026 : la TWS API REFUSE toute quantité fractionnaire sur
# actions (erreur 10243 « Fractional-sized order cannot be placed via API »), y
# compris pour clôturer une position, et refuse les ordres en montant (10244
# « Cash Quantity cannot be used for this order »). Le fractionnaire n'existe que
# dans IBKR Mobile / TWS / Client Portal → par défaut on raisonne en actions entières
# (`fractional=False`) et on signale les fractions résiduelles.
FRACTIONAL_DECIMALS = 4


def _floor_qty(qty: float, fractional: bool) -> float:
    """Arrondi vers le bas (ne jamais acheter plus que l'écart visé)."""
    if fractional:
        scale = 10 ** FRACTIONAL_DECIMALS
        return math.floor(qty * scale + 1e-9) / scale
    return float(math.floor(qty + 1e-9))


def plan_rebalance(positions: list, base_value: float, targets: list,
                   prices: dict | None = None, fractional: bool = False,
                   min_order_value: float = MIN_ORDER_VALUE_USD) -> dict:
    """
    Construit la liste d'ordres pour amener le compte sur les cibles.

    positions  : sortie de IBKRService.get_positions() (ticker, qty, market_value,
                 market_price, currency, con_id, primary_exchange)
    base_value : valeur de référence des pourcentages — la valeur nette du compte
                 (NetLiquidation, cash inclus), pas la somme des positions
    targets    : [{'ticker', 'target_pct', 'currency'?}] ; liste vide → tout vendre
    prices     : {ticker: prix indicatif} pour les titres pas encore détenus

    Ordre d'exécution : liquidations (hors cibles), allègements, puis achats — le
    produit des ventes finance les achats. Retour :
      {'orders': [...], 'skipped': [{'ticker','reason'}], 'total_target_pct': float}
    """
    prices = prices or {}
    targets_pct = {}
    for t in targets:
        tk = (t.get('ticker') or '').upper().strip()
        if tk:
            targets_pct[tk] = targets_pct.get(tk, 0.0) + float(t.get('target_pct') or 0)
    current = {(p.get('ticker') or '').upper(): p for p in positions}

    def _entry(tk, p, action, qty, price, cur_val, target_val, target_pct, liquidation):
        return {
            'ticker': tk, 'action': action,
            'qty': round(qty, FRACTIONAL_DECIMALS),
            'est_price': round(price, 2) if price else None,
            'est_value': round(qty * price, 2) if price else None,
            'current_value': round(cur_val, 2), 'target_value': round(target_val, 2),
            'target_pct': round(target_pct, 2), 'liquidation': liquidation,
            'currency': p.get('currency') or 'USD',
            'con_id': p.get('con_id'), 'primary_exchange': p.get('primary_exchange'),
            'status': 'preview',
        }

    sells, buys, skipped = [], [], []

    # 1) Liquidations — positions hors cibles. En mode entier (TWS API) on vend la
    #    partie entière ; la fraction restante — achetée via IBKR Mobile — est
    #    signalée car l'API ne peut pas la clôturer (10243).
    for tk, p in current.items():
        qty = float(p.get('qty') or 0)
        if targets_pct.get(tk, 0.0) > 0 or abs(qty) < 1e-9:
            continue
        mv = float(p.get('market_value') or 0)
        price = _num(p.get('market_price')) or (abs(mv / qty) if qty else None)
        action = 'SELL' if qty > 0 else 'BUY'      # BUY = couvrir un short résiduel
        sell_qty = round(abs(qty), FRACTIONAL_DECIMALS) if fractional else _floor_qty(abs(qty), False)
        residual = round(abs(qty) - sell_qty, FRACTIONAL_DECIMALS)
        if sell_qty <= 0:
            skipped.append({'ticker': tk, 'reason': f'position fractionnaire ({abs(qty):.4f}) invendable via '
                                                     'l\'API IBKR (10243) — à clôturer dans IBKR Mobile / TWS'})
            continue
        entry = _entry(tk, p, action, sell_qty, price or 0.0, mv, 0.0, 0.0, True)
        if residual > 0:
            entry['residual_qty'] = residual
            entry['note'] = f'reste {residual:.4f} (fraction invendable via l\'API) — à clôturer dans IBKR Mobile / TWS'
        sells.append(entry)

    # 2) Cibles — écart entre valeur actuelle et valeur visée
    for tk, pct in targets_pct.items():
        if pct <= 0:
            continue
        p = current.get(tk, {})
        cur_val = float(p.get('market_value') or 0)
        cur_qty = float(p.get('qty') or 0)
        target_val = base_value * pct / 100.0
        diff = target_val - cur_val
        if abs(diff) < min_order_value:
            skipped.append({'ticker': tk, 'reason': f'écart {diff:+.0f} $ sous le seuil de {min_order_value:.0f} $'})
            continue
        price = _num(p.get('market_price')) or _num(prices.get(tk))
        if not price or price <= 0:
            skipped.append({'ticker': tk, 'reason': 'prix indisponible pour dimensionner l\'ordre'})
            continue
        action = 'BUY' if diff > 0 else 'SELL'
        qty = _floor_qty(abs(diff) / price, fractional)
        if action == 'SELL':
            qty = min(qty, round(abs(cur_qty), FRACTIONAL_DECIMALS))
        if qty <= 0:
            skipped.append({'ticker': tk, 'reason': 'quantité nulle après arrondi (écart inférieur à une action)'})
            continue
        entry = _entry(tk, p, action, qty, price, cur_val, target_val, pct, False)
        (sells if action == 'SELL' else buys).append(entry)

    # 3) Compléments fractionnaires — ce que l'API refuse (10243) et qu'il reste à
    #    passer à la main (IBKR Mobile / Client Portal acceptent le fractionnaire et
    #    les montants) pour coller aux cibles : fractions résiduelles des liquidations,
    #    positions < 1 action, écart restant après les ordres entiers.
    manual = []
    if not fractional:
        planned_value = {}
        for o in sells + buys:
            sign = 1 if o['action'] == 'BUY' else -1
            planned_value[o['ticker']] = planned_value.get(o['ticker'], 0.0) + sign * (o['est_value'] or 0.0)
        for tk, p in current.items():
            if targets_pct.get(tk, 0.0) > 0:
                continue
            qty = float(p.get('qty') or 0)
            frac = round(abs(qty) - math.floor(abs(qty) + 1e-9), FRACTIONAL_DECIMALS)
            if frac <= 0:
                continue
            price = _num(p.get('market_price')) or 0.0
            manual.append({'ticker': tk, 'action': 'SELL' if qty > 0 else 'BUY', 'qty': frac,
                           'cash': None, 'est_value': round(frac * price, 2),
                           'reason': 'fraction résiduelle — invendable via l\'API'})
        for tk, pct in targets_pct.items():
            if pct <= 0:
                continue
            p = current.get(tk, {})
            cur_val = float(p.get('market_value') or 0)
            residual = base_value * pct / 100.0 - cur_val - planned_value.get(tk, 0.0)
            if abs(residual) < min_order_value:
                continue
            price = _num(p.get('market_price')) or _num(prices.get(tk))
            if residual > 0:
                manual.append({'ticker': tk, 'action': 'BUY', 'cash': round(residual, 2),
                               'qty': round(residual / price, FRACTIONAL_DECIMALS) if price else None,
                               'est_value': round(residual, 2), 'reason': 'complément fractionnaire vers la cible'})
            elif price:
                held_after = float(p.get('qty') or 0) - sum(
                    o['qty'] for o in sells if o['ticker'] == tk and o['action'] == 'SELL')
                sell_qty = min(round(-residual / price, FRACTIONAL_DECIMALS), round(max(held_after, 0), FRACTIONAL_DECIMALS))
                if sell_qty > 0:
                    manual.append({'ticker': tk, 'action': 'SELL', 'qty': sell_qty, 'cash': None,
                                   'est_value': round(sell_qty * price, 2),
                                   'reason': 'allègement fractionnaire vers la cible'})

    return {'orders': sells + buys, 'skipped': skipped, 'manual_orders': manual,
            'total_target_pct': round(sum(targets_pct.values()), 1)}


class IBKRService:
    """
    Connexion à IB Gateway via ib_async.

    Architecture : UNE connexion persistante (self._ib) maintenue par un event loop
    PERMANENT (run_forever) tournant dans un thread dédié. ib_async ne fonctionne pas
    avec asyncio.run() dans un thread secondaire (gunicorn gthread) ; il faut un loop
    unique auquel on soumet les coroutines, et créer l'objet IB() À L'INTÉRIEUR de ce
    loop. Les opérations sont sérialisées par self._lock (une seule à la fois).

    Ordres — ce qui protège réellement le compte :
      - côté gateway : READ_ONLY_API=no / BYPASS_WARNING / ALLOW_BLIND_TRADING
        (ibkr_gateway.py) ; avec l'API en lecture seule IBKR rejette tout ordre
        par l'erreur 321 « API interface is currently in Read-Only mode » ;
      - côté app : `dry_run` (aperçu + whatIf) puis confirmation explicite, routes
        protégées par @require_admin.
      Le paramètre `readonly` d'ib_async n'a AUCUN effet serveur (il saute juste la
      synchro des ordres au démarrage) : l'ancienne bascule readonly→trading avant
      chaque ordre n'apportait rien et déstabilisait la session (reconnexions,
      timeouts de synchro). On se connecte une fois, en mode trading.
      clientId fixe (PREFERRED_CLIENT_ID) pour retrouver et annuler nos ordres après
      une reconnexion ; repli sur un id aléatoire si l'id est encore pris (326).
    """

    CONNECT_COOLDOWN = 60       # secondes avant de retenter après un échec
    HIST_MIN_INTERVAL = 0.4     # pacing IBKR : ~60 requêtes historiques / 10 min
    PREFERRED_CLIENT_ID = 11
    QUALIFY_TIMEOUT = 6.0       # qualification d'un contrat (secdef) — lent le week-end
    WHATIF_TIMEOUT = 10.0
    ORDER_ACK_TIMEOUT = 15.0    # attente d'un statut décisif après placeOrder

    # Statuts après lesquels attendre davantage n'apporte rien
    _DECISIVE_STATUSES = frozenset({
        OrderStatus.PreSubmitted, OrderStatus.Submitted, OrderStatus.Filled,
        OrderStatus.Cancelled, OrderStatus.ApiCancelled, OrderStatus.Inactive,
        OrderStatus.ValidationError,
    })

    def __init__(self, host='ib-gateway', port=4003, client_id=PREFERRED_CLIENT_ID):
        self.host = host
        self.port = port
        self.preferred_client_id = int(client_id)
        self._client_id = 0
        self._lock = threading.Lock()
        self._connected_at = None
        self._last_error = None
        self._last_failed_at = None  # horodatage du dernier échec de connexion
        self._ib = None
        self._last_hist_call = 0.0   # throttle des requêtes historiques (pacing)
        self.fractional = False      # TWS API : actions entières uniquement (10243)

        # Event loop PERMANENT dans un thread dédié (voir docstring de la classe).
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

    def _submit(self, coro, timeout=45):
        """
        Soumet une coroutine au loop permanent et attend le résultat. En cas de
        timeout la coroutine est ANNULÉE : sinon elle continuerait en arrière-plan
        (ordres placés après que l'appelant a reçu une erreur, loop bouché).
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

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
                # clientId fixe d'abord (retrouve nos ordres après reconnexion), puis
                # aléatoire si le gateway garde encore une socket zombie avec cet id.
                candidates = [self.preferred_client_id, random.randint(100, 99999)]
                last_exc = None
                for attempt, cid in enumerate(candidates):
                    ib = IB()   # créé DANS le loop permanent → apiStart lié au bon loop
                    try:
                        await ib.connectAsync(self.host, self.port, clientId=cid,
                                              readonly=False, timeout=15)
                    except Exception as e:
                        # connectAsync lance reqPositions/reqOpenOrders/reqAccountUpdates
                        # qui peuvent timeout sur ce gateway SANS empêcher la connexion
                        # socket. Si la socket est connectée, on garde — portfolio()
                        # fonctionne malgré ces warnings.
                        if not ib.isConnected():
                            last_exc = e
                            if attempt == 0:
                                logger.warning('IBKR clientId %s indisponible (%s) — essai id aléatoire', cid, e)
                                continue
                            raise
                        logger.info('connectAsync : warnings de sync ignorés (%s)', e)
                    await asyncio.sleep(1.5)  # laisser arriver les données initiales
                    if not ib.isConnected():
                        raise RuntimeError('Connexion non établie')
                    # Déconnecter l'ancienne instance APRÈS que la nouvelle est prête.
                    # Ne jamais mettre self._ib = None avant : d'autres coroutines sur le
                    # même loop l'utilisent et obtiendraient NoneType AttributeError.
                    if self._ib is not None:
                        try:
                            self._ib.disconnect()
                        except Exception:
                            pass
                    self._ib = ib
                    self._client_id = cid
                    return cid
                raise last_exc or RuntimeError('Connexion impossible')

            try:
                cid = self._submit(_connect(), timeout=60)
                self._connected_at = time.time()
                self._last_error = None
                self._last_failed_at = None  # reset cooldown après succès
                logger.info('IBKR connecté (%s:%s, clientId=%s)', self.host, self.port, cid)
                return {'success': True, 'client_id': cid}
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
            'client_id': self._client_id or None,
            'cooldown_remaining_s': cooldown_remaining,
            'fractional': self.fractional,
        }

    def ensure_connected(self, trading_mode: bool = False) -> bool:
        """
        Reconnecte automatiquement si la session est tombée. Respecte un cooldown
        après un échec. `trading_mode` est conservé pour les appelants : la
        connexion autorise toujours les ordres (voir docstring de la classe).
        """
        if self._is_connected():
            return True
        if self._last_failed_at and (time.time() - self._last_failed_at) < self.CONNECT_COOLDOWN:
            remaining = self.CONNECT_COOLDOWN - (time.time() - self._last_failed_at)
            logger.debug('ensure_connected: cooldown actif (%.0fs restants) — dernière erreur : %s',
                         remaining, self._last_error)
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
        portfolio = self._submit(fn(), timeout=8)
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

    def get_account_summary(self) -> dict:
        """
        Valeurs de compte synchronisées à la connexion (accountValues) :
        net_liquidation, total_cash, buying_power, available_funds, account_type.
        Devise USD/BASE privilégiée. Clés absentes si non disponibles.
        """
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def fn():
            return list(self._ib.accountValues())
        vals = self._submit(fn(), timeout=10)
        wanted = {'NetLiquidation': 'net_liquidation', 'TotalCashValue': 'total_cash',
                  'BuyingPower': 'buying_power', 'AvailableFunds': 'available_funds'}
        out = {}
        for v in vals:
            if v.tag == 'AccountType':
                out['account_type'] = v.value
                continue
            key = wanted.get(v.tag)
            if not key:
                continue
            f = _num(v.value)
            if f is None:
                continue
            if v.currency in ('USD', 'BASE') or key not in out:
                out[key] = round(f, 2)
        return out

    def get_cash_balance(self) -> float | None:
        """
        Retourne le cash disponible (TotalCashValue BASE) depuis le compte IBKR.
        Utilise accountValues() — données synchronisées à la connexion puis en live.
        Retourne None si non connecté ou non disponible.
        """
        if not self._is_connected():
            return None
        try:
            return self.get_account_summary().get('total_cash')
        except Exception as e:
            logger.warning('get_cash_balance: %s', e)
        return None

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

            # Qualifier les contrats avec timeout individuel pour ne pas bloquer le loop
            valid = {}
            for sym, c in contracts.items():
                try:
                    await asyncio.wait_for(self._ib.qualifyContractsAsync(c), timeout=5.0)
                    valid[sym] = c
                except Exception as e:
                    logger.warning('get_quotes: contrat non qualifié %s (%s)', sym, e)

            if not valid:
                return {}

            # 1 = temps réel ; 3 = différé (15 min). On tente le temps réel puis on
            # laisse le différé prendre le relais si pas d'abonnement.
            self._ib.reqMarketDataType(3)
            # asyncio.wait_for garantit l'annulation propre de la coroutine si timeout
            tickers_data = await asyncio.wait_for(
                self._ib.reqTickersAsync(*valid.values()),
                timeout=8.0,
            )
            return {c.symbol: t for c, t in zip(valid.values(), tickers_data)}

        raw = self._submit(fn(), timeout=15)

        result = {}
        for sym, t in (raw or {}).items():
            last = _num(getattr(t, 'last', None))
            if last is None:
                last = _num(getattr(t, 'marketPrice', lambda: None)() if callable(getattr(t, 'marketPrice', None)) else None)
            if last is None:
                last = _num(getattr(t, 'close', None))
            # Prix de 0 = donnée manquante post-reconnexion IBKR, pas un vrai cours
            if last is not None and last <= 0:
                last = None
            prev_close = _num(getattr(t, 'close', None))
            if prev_close is not None and prev_close <= 0:
                prev_close = None
            pct = None
            if last is not None and prev_close not in (None, 0):
                pct = round((last - prev_close) / prev_close * 100, 2)
            if last is None and prev_close is None:
                continue
            result[sym] = {'last': last, 'prev_close': prev_close, 'pct': pct}
        return result

    async def _get_market_price(self, ticker: str, currency: str = 'USD',
                               timeout: float = 12.0) -> float | None:
        """Prix marché en temps réel (ou différé) pour calculer la quantité en actions."""
        try:
            contract = Stock(ticker, 'SMART', currency)
            await asyncio.wait_for(self._ib.qualifyContractsAsync(contract), timeout=timeout)
            self._ib.reqMarketDataType(3)   # données différées si pas d'abonnement
            tickers = await asyncio.wait_for(self._ib.reqTickersAsync(contract), timeout=timeout)
            if not tickers:
                return None
            t = tickers[0]
            for attr in ('last', 'close', 'bid', 'ask'):
                f = _num(getattr(t, attr, None))
                if f and f > 0:
                    return f
            return None
        except Exception as e:
            logger.warning('_get_market_price %s : %s', ticker, e)
            return None

    # ------------------------------------------------------------------
    # Ordres
    # ------------------------------------------------------------------

    async def _resolve_contract(self, ticker: str, currency: str = 'USD',
                                con_id=None, primary_exchange: str | None = None):
        """
        Contrat routé SMART. Les contrats renvoyés par portfolio() ont exchange=''
        (ordre invalide tel quel) : on repart toujours de Stock(..., 'SMART').
        Avec un conId connu (position détenue) IBKR identifie le titre sans
        ambiguïté ; sinon on qualifie (secdef), et à défaut on laisse IBKR router
        le symbole SMART/USD (+ primaryExchange si connu).
        """
        ticker = ticker.upper()
        if con_id:
            return Stock(ticker, 'SMART', currency, conId=int(con_id),
                         primaryExchange=primary_exchange or '')
        # Classes d'actions : Tiingo/Wikipédia écrivent BRK.B / BRK-B, IBKR « BRK B »
        ib_symbol = ticker.replace('.', ' ').replace('-', ' ')
        contract = Stock(ib_symbol, 'SMART', currency, primaryExchange=primary_exchange or '')
        try:
            await asyncio.wait_for(self._ib.qualifyContractsAsync(contract), self.QUALIFY_TIMEOUT)
        except Exception as e:
            logger.warning('qualification %s impossible (%s) — contrat SMART non qualifié', ticker, e)
        return contract

    @staticmethod
    def _build_order(action: str, qty: float | None = None, order_type: str = 'MKT',
                     limit_price: float | None = None, tif: str = 'DAY',
                     cash: float | None = None) -> Order:
        """
        MKT DAY par défaut : exécution immédiate en séance, sinon file d'attente
        pour la prochaine ouverture (IBKR accepte un MKT hors séance, PreSubmitted).
        `cash` → ordre en montant (cashQty) : IBKR calcule la quantité.
        """
        order = Order(action=action, orderType=order_type, tif=tif, outsideRth=False,
                      totalQuantity=round(float(qty or 0), FRACTIONAL_DECIMALS))
        if order_type == 'LMT' and limit_price is not None:
            order.lmtPrice = float(limit_price)
        if cash is not None:
            order.cashQty = round(float(cash), 2)
        return order

    def _error_collector(self):
        """(liste, handler) pour capturer les erreurs IBKR émises pendant un appel."""
        errors = []

        def handler(reqId, errorCode, errorString, *rest):
            errors.append((reqId, errorCode, errorString))
        return errors, handler

    async def _whatif(self, contract, order) -> dict:
        """
        Simulation serveur (commission, marge) SANS placer l'ordre. Remonte aussi la
        raison d'un rejet (ex. 321 API en lecture seule, précautions, titre non
        fractionnable) : c'est la pré-validation du dry_run.
        """
        errors, handler = self._error_collector()
        self._ib.errorEvent += handler
        state, timed_out = None, False
        try:
            state = await asyncio.wait_for(self._ib.whatIfOrderAsync(contract, order), self.WHATIF_TIMEOUT)
        except asyncio.TimeoutError:
            timed_out = True
        except Exception as e:
            return {'whatif': 'error', 'whatif_error': str(e)[:200]}
        finally:
            self._ib.errorEvent -= handler
        await asyncio.sleep(0)   # laisser passer un éventuel message d'erreur tardif
        rejects = [(c, m) for r, c, m in errors if r != -1 and not (2100 <= c < 2200)]
        if rejects:
            code, msg = rejects[-1]
            return {'whatif': 'rejected', 'whatif_error': f'{code} · {msg[:200]}'}
        if state is None:
            return {'whatif': 'timeout' if timed_out else 'error'}
        commission = _num(state.commission)
        if commission is None:
            commission = _num(state.maxCommission)
        return {
            'whatif': 'ok',
            'commission': round(commission, 2) if commission is not None else None,
            'commission_currency': state.commissionCurrency or None,
            'margin_change': _num(state.initMarginChange),
            'whatif_warning': (state.warningText or '')[:200] or None,
        }

    async def _place_and_wait(self, contract, order, timeout: float | None = None) -> dict:
        """
        Place l'ordre et attend un statut DÉCISIF via les événements du loop
        (ib_async met à jour le Trade au fil des messages). Les codes d'erreur IBKR
        liés à l'orderId sont capturés pour remonter la vraie raison d'un rejet.
        """
        timeout = timeout or self.ORDER_ACK_TIMEOUT
        errors, handler = self._error_collector()
        self._ib.errorEvent += handler
        try:
            trade = self._ib.placeOrder(contract, order)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if trade.orderStatus.status in self._DECISIVE_STATUSES:
                    break
                await asyncio.sleep(0.25)
            await asyncio.sleep(0.3)   # message d'erreur qui suit parfois le statut
            # ib_async bascule le Trade en ValidationError sur un simple avertissement
            # (399 « will not be placed at the exchange until … », 404, 10349 préréglage
            # TIF…) alors que l'ordre est bien vivant côté IBKR. Pour tout statut autre
            # qu'une acceptation franche, on se réaligne sur le serveur : si l'ordre
            # figure encore dans les ordres ouverts, c'est lui qui a raison.
            server_status = None
            if trade.orderStatus.status not in (OrderStatus.PreSubmitted, OrderStatus.Submitted, OrderStatus.Filled):
                server_status = await self._server_order_status(trade)
        except Exception as e:
            return {'status': 'failed', 'order_status': 'exception', 'error': str(e)[:200]}
        finally:
            self._ib.errorEvent -= handler
        return self._trade_result(trade, errors, server_status)

    async def _server_order_status(self, trade) -> str | None:
        """Statut côté serveur via reqAllOpenOrders (None si absent des ordres ouverts)."""
        try:
            trades = await asyncio.wait_for(self._ib.reqAllOpenOrdersAsync(), 8)
        except Exception as e:
            logger.warning('_server_order_status: %s', e)
            return None
        for t in trades:
            same_perm = trade.order.permId and t.order.permId == trade.order.permId
            same_id = t.order.orderId == trade.order.orderId and t.order.clientId == trade.order.clientId
            if same_perm or same_id:
                return t.orderStatus.status
        return None

    @staticmethod
    def _trade_result(trade, errors, server_status: str | None = None) -> dict:
        oid = trade.order.orderId
        st = trade.orderStatus.status or ''
        if server_status in (OrderStatus.PreSubmitted, OrderStatus.Submitted, OrderStatus.Filled):
            st = server_status
        msgs = [f'{c} · {m}' for r, c, m in errors if r == oid]
        if not msgs:
            msgs = [e.message for e in trade.log if e.message and e.message != 'Modify']
        detail = ' | '.join(m[:220] for m in msgs[-2:])
        res = {
            'order_id': oid, 'perm_id': trade.order.permId or None,
            'client_id': trade.order.clientId, 'order_status': st or 'unknown',
            'filled': _num(trade.orderStatus.filled) or 0.0,
            'remaining': _num(trade.orderStatus.remaining),
            'avg_fill_price': _num(trade.orderStatus.avgFillPrice) or None,
            'why_held': trade.orderStatus.whyHeld or None,
        }
        if st == OrderStatus.Filled:
            res.update(status='filled')
        elif st in (OrderStatus.PreSubmitted, OrderStatus.Submitted):
            res.update(status='placed', note=detail or None)
        elif st == OrderStatus.ValidationError:
            res.update(status='failed', error=f'Rejeté par IBKR : {detail or "erreur de validation"}')
        elif st in OrderStatus.DoneStates:
            res.update(status='failed', error=f'Ordre {st} : {detail or "annulé par IBKR"}')
        else:
            res.update(status='pending',
                       note=f'Statut « {st or "inconnu"} » après délai — à vérifier dans les ordres IBKR'
                            + (f' · {detail}' if detail else ''))
        return res

    def place_rebalance_orders(self, targets: list, dry_run: bool = True,
                               price_hints: dict | None = None) -> dict:
        """
        Rééquilibrage vers les cibles (ticker + target_pct de la valeur nette du compte).

        dry_run=True  : plan + simulation whatIf par ordre (commission, rejet éventuel).
        dry_run=False : ventes puis achats, MKT DAY. Hors séance les ordres sont
                        acceptés et exécutés à la prochaine ouverture (queued_for_open).
                        Si une vente échoue, les achats sont suspendus (l'exposition
                        visée suppose le produit des ventes).
        """
        if not self._is_connected():
            res = self.connect(force=True)
            if not res.get('success'):
                raise ConnectionError(f"Non connecté à IB Gateway : {res.get('error', '')}")

        stats = self.get_portfolio_stats()
        try:
            account = self.get_account_summary()
        except Exception as e:
            logger.warning('place_rebalance_orders: account summary indisponible (%s)', e)
            account = {}
        base_value = account.get('net_liquidation') or stats['total_value']
        market_open = _us_market_open()

        plan = plan_rebalance(stats['positions'], base_value, targets,
                              prices=price_hints, fractional=self.fractional)
        orders = plan['orders']
        logger.info('place_rebalance_orders: %d ordre(s), base=%.0f, marché %s, dry_run=%s',
                    len(orders), base_value, 'OUVERT' if market_open else 'FERMÉ', dry_run)

        async def fn():
            sells_failed = False
            for entry in orders:
                if not dry_run and entry['action'] == 'BUY' and not entry['liquidation'] and sells_failed:
                    entry.update(status='skipped',
                                 error='Achat suspendu : une vente a échoué (relancer après correction)')
                    continue
                contract = await self._resolve_contract(
                    entry['ticker'], entry.get('currency') or 'USD',
                    entry.get('con_id'), entry.get('primary_exchange'))
                order = self._build_order(entry['action'], entry['qty'])
                entry.update(order_type=order.orderType, tif=order.tif)
                if dry_run:
                    entry.update(await self._whatif(contract, order))
                    continue
                entry.update(await self._place_and_wait(contract, order))
                if entry['action'] == 'SELL' and entry['status'] == 'failed':
                    sells_failed = True
            return orders

        timeout = 30 + len(orders) * (self.ORDER_ACK_TIMEOUT + self.QUALIFY_TIMEOUT + 2)
        orders = self._submit(fn(), timeout=timeout)
        return {
            'orders': orders, 'skipped': plan['skipped'], 'manual_orders': plan['manual_orders'],
            'total_target_pct': plan['total_target_pct'],
            'base_value': round(base_value, 2), 'cash': account.get('total_cash'),
            'market_open': market_open, 'queued_for_open': not market_open,
            'fractional': self.fractional, 'dry_run': dry_run,
        }

    def place_single_order(self, ticker: str, action: str, qty: float | None = None,
                           cash: float | None = None, order_type: str = 'MKT',
                           limit_price: float | None = None, tif: str = 'DAY',
                           dry_run: bool = False, currency: str = 'USD') -> dict:
        """
        Un ordre isolé (tests de bout en bout, dépannage). dry_run → whatIf seulement.
        Réutilise le contrat réel (conId) si le titre est en portefeuille.
        """
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')
        action = action.upper()
        held = {p['ticker']: p for p in self.get_positions()}.get(ticker.upper(), {})

        async def fn():
            contract = await self._resolve_contract(ticker, currency, held.get('con_id'),
                                                    held.get('primary_exchange'))
            order = self._build_order(action, qty, order_type, limit_price, tif, cash)
            base = {'ticker': ticker.upper(), 'action': action, 'qty': order.totalQuantity,
                    'cash': cash, 'order_type': order_type, 'limit_price': limit_price, 'tif': tif,
                    'contract': f'{contract.symbol} conId={contract.conId or "?"} {contract.exchange}'}
            if dry_run:
                base.update(await self._whatif(contract, order))
            else:
                base.update(await self._place_and_wait(contract, order))
            return base
        return self._submit(fn(), timeout=self.ORDER_ACK_TIMEOUT + self.QUALIFY_TIMEOUT + 15)

    def get_open_orders(self) -> list:
        """Ordres ouverts de TOUS les clients API (reqAllOpenOrders)."""
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def fn():
            trades = await asyncio.wait_for(self._ib.reqAllOpenOrdersAsync(), 10)
            return [self._format_trade(t) for t in trades]
        return self._submit(fn(), timeout=20)

    def get_executions_today(self) -> list:
        """Exécutions du jour (reqExecutions, filtre vide)."""
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def fn():
            fills = await asyncio.wait_for(self._ib.reqExecutionsAsync(), 10)
            out = []
            for f in fills:
                ex, cr = f.execution, f.commissionReport
                out.append({
                    'time': f.time.isoformat() if hasattr(f.time, 'isoformat') else str(f.time),
                    'ticker': f.contract.localSymbol or f.contract.symbol,
                    'side': 'BUY' if ex.side == 'BOT' else 'SELL' if ex.side == 'SLD' else ex.side,
                    'shares': _num(ex.shares), 'price': _num(ex.price),
                    'order_id': ex.orderId, 'perm_id': ex.permId, 'exec_id': ex.execId,
                    'commission': _num(cr.commission), 'commission_currency': cr.currency or None,
                    'realized_pnl': _num(cr.realizedPNL),
                })
            out.sort(key=lambda r: r['time'])
            return out
        return self._submit(fn(), timeout=20)

    def cancel_order(self, order_id: int) -> dict:
        """Annule un ordre ouvert placé par cette app (orderId ou permId)."""
        if not self._is_connected():
            raise ConnectionError('Non connecté à IB Gateway')

        async def fn():
            trades = list(self._ib.openTrades())
            trade = next((t for t in trades if t.order.orderId == order_id or t.order.permId == order_id), None)
            if trade is None:
                trades = await asyncio.wait_for(self._ib.reqAllOpenOrdersAsync(), 10)
                trade = next((t for t in trades if t.order.orderId == order_id or t.order.permId == order_id), None)
            if trade is None:
                return {'success': False, 'error': 'Ordre introuvable parmi les ordres ouverts'}
            # IBKR n'autorise l'annulation que depuis le client qui a placé l'ordre
            # (ou le client maître 0) → clientId fixe (PREFERRED_CLIENT_ID).
            if trade.order.clientId not in (self._client_id, 0):
                return {'success': False,
                        'error': f'Ordre placé par un autre client API (clientId {trade.order.clientId}) '
                                 '— annulable depuis TWS / IBKR Mobile'}
            errors, handler = self._error_collector()
            self._ib.errorEvent += handler
            try:
                self._ib.cancelOrder(trade.order)
                for _ in range(40):          # ~10 s
                    await asyncio.sleep(0.25)
                    if trade.orderStatus.status in OrderStatus.DoneStates:
                        break
                await asyncio.sleep(0.3)
            finally:
                self._ib.errorEvent -= handler
            st = trade.orderStatus.status
            # 10148 = déjà en cours d'annulation (fréquent le week-end : IBKR garde
            # PendingCancel jusqu'à la reprise des serveurs) ; 10147 = ordre inconnu.
            msgs = [f'{c} · {m[:160]}' for r, c, m in errors if r == trade.order.orderId]
            res = {'success': st in OrderStatus.DoneStates or st == OrderStatus.PendingCancel,
                   'order_status': st, 'order_id': trade.order.orderId}
            if msgs:
                res['note'] = ' | '.join(msgs[-2:])
            if st == OrderStatus.PendingCancel:
                res['note'] = (res.get('note') or '') + ' Annulation transmise — finalisée par IBKR à la reprise des serveurs.'
            return res
        return self._submit(fn(), timeout=30)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_trade(t) -> dict:
        c, o, s = t.contract, t.order, t.orderStatus
        last_msg = next((e.message for e in reversed(t.log) if e.message), None)
        return {
            'order_id': o.orderId or None, 'perm_id': o.permId or None, 'client_id': o.clientId,
            'ticker': c.localSymbol or c.symbol, 'action': o.action,
            'qty': _num(o.totalQuantity), 'cash_qty': _num(o.cashQty),
            'order_type': o.orderType, 'limit_price': _num(o.lmtPrice), 'tif': o.tif,
            'status': s.status, 'filled': _num(s.filled) or 0.0, 'remaining': _num(s.remaining),
            'avg_fill_price': _num(s.avgFillPrice) or None, 'why_held': s.whyHeld or None,
            'message': last_msg,
        }

    @staticmethod
    def _format_portfolio(portfolio) -> list:
        result = []
        for item in portfolio:
            c = item.contract
            result.append({
                'ticker':           c.localSymbol or c.symbol,
                'qty':              item.position,
                'avg_cost':         item.averageCost,
                'market_price':     item.marketPrice,
                'market_value':     item.marketValue,
                'unrealized_pnl':   item.unrealizedPNL,
                'realized_pnl':     item.realizedPNL,
                'currency':         c.currency,
                'con_id':           c.conId or None,
                'primary_exchange': c.primaryExchange or None,
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
