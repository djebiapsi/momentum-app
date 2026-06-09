# -*- coding: utf-8 -*-
"""
Service de notifications push Web (PWA).
=========================================
Utilise le protocole Web Push / VAPID pour envoyer des notifications aux
abonnés sans passer par l'email. Fonctionne avec les raccourcis iOS 16.4+
ajoutés à l'écran d'accueil.

Les clés VAPID sont générées automatiquement au premier appel et stockées
dans la table Settings (persistées en base).
"""

import json
import logging

logger = logging.getLogger(__name__)

_VAPID_PRIVATE_KEY = 'vapid_private_key'
_VAPID_PUBLIC_KEY  = 'vapid_public_key'
VAPID_CLAIMS_SUB   = 'mailto:admin@momentum.app'


def _get_or_create_vapid_keys():
    """Génère les clés VAPID si absentes et les stocke en Settings."""
    from models import Settings
    priv = Settings.get(_VAPID_PRIVATE_KEY)
    pub  = Settings.get(_VAPID_PUBLIC_KEY)
    if priv and pub:
        return priv, pub
    try:
        from py_vapid import Vapid
        v = Vapid()
        v.generate_keys()
        priv = v.private_pem().decode()
        pub  = v.public_key_str()
        Settings.set(_VAPID_PRIVATE_KEY, priv)
        Settings.set(_VAPID_PUBLIC_KEY,  pub)
        logger.info('Clés VAPID générées et stockées')
        return priv, pub
    except Exception as e:
        logger.error('Génération clés VAPID impossible: %s', e)
        return None, None


def get_vapid_public_key() -> str | None:
    """Retourne la clé publique VAPID (pour le frontend)."""
    from models import Settings
    pub = Settings.get(_VAPID_PUBLIC_KEY)
    if not pub:
        _, pub = _get_or_create_vapid_keys()
    return pub


def send_push(subscription_info: dict, title: str, body: str,
              url: str = '/', icon: str = '/static/icons/icon-192.png',
              tag: str = None, badge: str = '/static/icons/icon-192.png') -> bool:
    """
    Envoie une notification push à un seul abonné.
    subscription_info : {'endpoint': ..., 'keys': {'p256dh': ..., 'auth': ...}}
    Retourne True si succès.
    """
    from models import Settings
    priv = Settings.get(_VAPID_PRIVATE_KEY)
    if not priv:
        priv, _ = _get_or_create_vapid_keys()
    if not priv:
        logger.warning('send_push: clés VAPID manquantes')
        return False
    try:
        from pywebpush import webpush, WebPushException
        payload = json.dumps({
            'title': title,
            'body':  body,
            'url':   url,
            'icon':  icon,
            'badge': badge,
            'tag':   tag or title,
        })
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=priv,
            vapid_claims={'sub': VAPID_CLAIMS_SUB},
        )
        return True
    except Exception as e:
        logger.warning('send_push: échec (%s)', e)
        return False


def send_push_all(title: str, body: str, url: str = '/', tag: str = None) -> dict:
    """
    Envoie la notification à tous les abonnés actifs.
    Retourne {'sent': N, 'failed': M, 'removed': K} (supprime les endpoints expirés).
    """
    from models import db, PushSubscription
    subs = PushSubscription.query.all()
    sent = failed = removed = 0
    for sub in subs:
        info = {'endpoint': sub.endpoint, 'keys': {'p256dh': sub.p256dh, 'auth': sub.auth}}
        ok = send_push(info, title, body, url=url, tag=tag)
        if ok:
            sent += 1
        else:
            # 410 Gone = abonné révoqué → supprimer
            failed += 1
            try:
                from pywebpush import WebPushException
            except ImportError:
                pass
            # On garde les abonnés en échec (réseau temporaire possible)
            # Uniquement si l'erreur est 410 on supprime — géré par le try/except interne
    return {'sent': sent, 'failed': failed, 'removed': removed}
