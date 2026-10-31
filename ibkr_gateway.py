# -*- coding: utf-8 -*-
"""
Configuration et (re)création du conteneur IB Gateway — source de vérité UNIQUE.

Le conteneur `ib-gateway` est créé de deux façons :
  - par docker-compose (profil « ibkr ») : `docker compose --profile ibkr up -d ib-gateway`
  - par l'app elle-même via le SDK Docker : sauvegarde des identifiants, bascule
    live/paper (routes/ibkr.py) et auto-restart après coupure (jobs.py).

Les deux chemins DOIVENT produire la même configuration. Historiquement ce n'était
pas le cas : docker-compose.yml avait reçu READ_ONLY_API=no (sept. 2026) mais les
recréations SDK dupliquaient une liste d'environnement figée sans cette variable.
Le conteneur réellement en prod tournait donc avec l'API en lecture seule et IBKR
rejetait TOUT ordre (erreur 321 « The API interface is currently in Read-Only
mode »), ce que le code interprétait comme « aucun statut reçu ».

Garder docker-compose.yml (service ib-gateway) aligné sur `gateway_environment()`.

Variables de l'image ghcr.io/gnzsnz/ib-gateway (substituées par envsubst dans le
config.ini d'IBC ; une variable absente laisse le réglage du gateway inchangé, donc
en lecture seule sur une installation neuve sans volume persistant) :
  READ_ONLY_API        → IBC ReadOnlyApi           (no  = l'API peut passer des ordres)
  BYPASS_WARNING       → IBC BypassOrderPrecautions (yes = case « Bypass Order
                         Precautions for API Orders » cochée ; sinon un ordre qui
                         déclenche une précaution — taille, valeur, % du capital —
                         ouvre un dialogue que personne ne peut valider → rejet)
  ALLOW_BLIND_TRADING  → IBC AllowBlindTrading     (yes = pas de dialogue « blind
                         trading » quand le compte n'a pas d'abonnement temps réel
                         pour le titre ; défaut de l'image : no)
"""
import logging
import os
import threading

logger = logging.getLogger(__name__)

GATEWAY_IMAGE = 'ghcr.io/gnzsnz/ib-gateway:stable'
GATEWAY_CONTAINER = 'momentum-app-ib-gateway-1'
GATEWAY_NETWORK = 'momentum-app_internal'
GATEWAY_ALIAS = 'ib-gateway'   # alias DNS attendu par IBKRService(host='ib-gateway')

# Port SOCAT du gateway (accessible depuis le réseau Docker) selon le mode.
# Live  : API interne 4001 → socat 4003
# Paper : API interne 4002 → socat 4004
SOCAT_PORT = {'live': 4003, 'paper': 4004}


def gateway_environment(username: str, password: str, trading_mode: str) -> dict:
    """Environnement du conteneur — identique à `services.ib-gateway` du compose."""
    return {
        'TWS_USERID': username,
        'TWS_PASSWORD': password,
        'TRADING_MODE': trading_mode,
        'TWS_SETTINGS_PATH': '/home/ibgateway/Jts',
        'VNC_SERVER_PASSWORD': os.environ.get('VNC_PASSWORD') or 'changeme',
        'TWS_ACCEPT_INCOMING': 'accept',
        # --- Ordres via API (voir docstring du module) ---
        'READ_ONLY_API': os.environ.get('IB_READ_ONLY_API') or 'no',
        'BYPASS_WARNING': os.environ.get('IB_BYPASS_WARNING') or 'yes',
        'ALLOW_BLIND_TRADING': os.environ.get('IB_ALLOW_BLIND_TRADING') or 'yes',
        # Restart quotidien programmé (soft restart IBC : garde la session, PAS de 2FA)
        # Doit tourner AVANT la maintenance forcée IBKR (~minuit ET) pour éviter le crash
        'AUTO_RESTART_TIME': '11:30 PM',
        'TIME_ZONE': 'America/New_York',
        # Si timeout 2FA, IBC relance la séquence de login au lieu de mourir
        'RELOGIN_AFTER_TWOFA_TIMEOUT': 'yes',
        'TWOFA_TIMEOUT_ACTION': 'restart',
    }


def gateway_healthcheck() -> dict:
    """
    Teste le port 4001 (process Java réel). Si le gateway crashe, ce port tombe
    même si socat/4003 répond encore → détection fiable du crash. Durées en ns
    (format attendu par le SDK Docker).
    """
    return {
        'test': ["CMD-SHELL", "bash -c 'echo > /dev/tcp/127.0.0.1/4001' || exit 1"],
        'interval': 60_000_000_000,
        'timeout': 10_000_000_000,
        'retries': 3,
        'start_period': 180_000_000_000,
    }


def recreate_gateway(username: str, password: str, trading_mode: str) -> None:
    """
    Supprime puis recrée le conteneur IB Gateway via le SDK Docker, en répliquant
    les options du docker-compose.yml (env, healthcheck, auto-restart, autoheal,
    VNC local, alias réseau). Bloquant (~2 s) ; le gateway met ensuite ~90 s à
    démarrer et demande une 2FA sur le téléphone IBKR.
    """
    import docker as docker_sdk
    client = docker_sdk.from_env()
    for c in client.containers.list(all=True, filters={'name': GATEWAY_ALIAS}):
        try:
            c.stop(timeout=10)
            c.remove()
        except Exception as e:  # conteneur déjà parti : on continue
            logger.warning('recreate_gateway: arrêt de %s : %s', c.name, e)
    # Créer sans démarrer, puis connecter au réseau avec l'alias DNS 'ib-gateway'
    # (sinon seul le nom du conteneur est résolvable et l'app échoue en
    # "name resolution"). docker-compose crée cet alias automatiquement, le SDK
    # doit le faire explicitement.
    container = client.containers.create(
        GATEWAY_IMAGE,
        name=GATEWAY_CONTAINER,
        detach=True,
        restart_policy={'Name': 'unless-stopped'},
        labels={'autoheal': 'true'},
        environment=gateway_environment(username, password, trading_mode),
        ports={'5900/tcp': ('127.0.0.1', 5900)},
        healthcheck=gateway_healthcheck(),
    )
    client.networks.get(GATEWAY_NETWORK).connect(container, aliases=[GATEWAY_ALIAS])
    container.start()
    logger.info('IB Gateway recréé (mode=%s, alias=%s)', trading_mode, GATEWAY_ALIAS)


def recreate_gateway_async(username: str, password: str, trading_mode: str) -> None:
    """Variante non bloquante pour les routes HTTP (le gateway met ~90 s + 2FA)."""
    def _run():
        try:
            recreate_gateway(username, password, trading_mode)
        except Exception as e:
            logger.warning('recreate_gateway_async: %s', e)
    threading.Thread(target=_run, daemon=True).start()
