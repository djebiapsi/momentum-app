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
    """
    Génère les clés VAPID si absentes et les stocke en Settings.
    Utilise cryptography.ec.generate_private_key(SECP256R1) directement
    pour garantir une courbe nommée (P-256) compatible pywebpush/py_vapid.
    """
    from models import Settings
    priv = Settings.get(_VAPID_PRIVATE_KEY)
    pub  = Settings.get(_VAPID_PUBLIC_KEY)
    if priv and pub:
        # Vérifier que la clé stockée est rechargeable (migration si ancienne clé)
        try:
            from py_vapid import Vapid
            Vapid.from_pem(priv.encode())
            return priv, pub
        except Exception:
            logger.warning('Clés VAPID en base invalides — régénération')

    try:
        import base64
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat, PrivateFormat, NoEncryption)
        from cryptography.hazmat.backends import default_backend
        from py_vapid import Vapid

        # Générer avec courbe nommée (pas de paramètres explicites) pour éviter
        # l'erreur "EC curves with explicit parameters" dans py_vapid/pywebpush
        priv_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        priv = priv_key.private_bytes(
            Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
        ).decode()

        # Vérifier que Vapid peut charger cette clé
        v = Vapid.from_pem(priv.encode())

        # Clé publique : uncompressed EC point (65 bytes) → base64url sans padding
        pub_bytes = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub = base64.urlsafe_b64encode(pub_bytes).rstrip(b'=').decode()

        Settings.set(_VAPID_PRIVATE_KEY, priv)
        Settings.set(_VAPID_PUBLIC_KEY,  pub)
        logger.info('Clés VAPID générées et stockées (SECP256R1 named curve)')
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
    priv, _ = _get_or_create_vapid_keys()
    if not priv:
        logger.warning('send_push: clés VAPID manquantes')
        return False
    try:
        from pywebpush import webpush, WebPushException
        from py_vapid import Vapid
        vapid_obj = Vapid.from_pem(priv.encode())
        payload = json.dumps({
            'title': title,
            'body':  body,
            'url':   url,
            'icon':  icon,
            'badge': badge,
            'tag':   tag or title,
        })
        response = webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=vapid_obj,
            vapid_claims={'sub': VAPID_CLAIMS_SUB},
        )
        return response.status_code in (200, 201, 202)
    except Exception as e:
        err_str = str(e)
        # 410 Gone = abonné révoqué (iOS a retiré la permission)
        if '410' in err_str:
            logger.info('send_push: abonné révoqué (410), suppression')
            _remove_subscription_by_endpoint(subscription_info.get('endpoint', ''))
        else:
            logger.warning('send_push: échec (%s)', e)
        return False


def _remove_subscription_by_endpoint(endpoint: str):
    """Supprime un abonnement expiré/révoqué de la base."""
    if not endpoint:
        return
    try:
        from models import db, PushSubscription
        sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
        if sub:
            db.session.delete(sub)
            db.session.commit()
    except Exception as e:
        logger.warning('_remove_subscription: %s', e)


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
