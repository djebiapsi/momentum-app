# -*- coding: utf-8 -*-
"""
Cache en mémoire avec expiration (TTL)
=======================================
Évite les appels API redondants à Tiingo (limité à ~50 req/h).
"""

import time


class TTLCache:
    """
    Cache dictionnaire avec expiration automatique par entrée.
    Thread-safe grâce au GIL Python (1 worker Gunicorn).
    """

    def __init__(self, ttl_seconds):
        self._cache = {}
        self._ttl = ttl_seconds

    def get(self, key):
        """Retourne (valeur, True) si présent et valide, (None, False) sinon."""
        entry = self._cache.get(key)
        if entry is not None:
            value, expires_at = entry
            if time.time() < expires_at:
                return value, True
            del self._cache[key]
        return None, False

    def set(self, key, value):
        self._cache[key] = (value, time.time() + self._ttl)

    def invalidate(self, key=None):
        """Invalide une clé ou tout le cache si key=None."""
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)

    def size(self):
        """Nombre d'entrées valides (non expirées)."""
        now = time.time()
        return sum(1 for _, (_, exp) in self._cache.items() if exp > now)

    def ttl_seconds(self):
        return self._ttl
