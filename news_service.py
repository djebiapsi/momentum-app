# -*- coding: utf-8 -*-
"""
Service d'agrégation et de résumé des news
==========================================
Récupère les actualités financières via des flux RSS gratuits (Yahoo Finance par
ticker + flux marché global), puis produit un résumé trié par pertinence.

Le résumé est généré par un petit LLM open-source auto-hébergé via **Ollama**
(modèle configurable, ex: qwen2.5:3b). Si Ollama est injoignable, on retombe sur
un résumé heuristique (regroupement + titres récents) — jamais d'erreur silencieuse
bloquante qui empêcherait l'envoi du briefing.
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)

# Flux RSS marché global (gratuits, sans clé)
GLOBAL_FEEDS = [
    ('MarketWatch', 'http://feeds.marketwatch.com/marketwatch/topstories/'),
    ('CNBC', 'https://www.cnbc.com/id/100003114/device/rss/rss.html'),
]


class NewsService:
    def __init__(self, ollama_host=None, model=None, timeout=45):
        self.ollama_host = (ollama_host or os.environ.get('OLLAMA_HOST', 'http://ollama:11434')).rstrip('/')
        self.model = model or os.environ.get('OLLAMA_MODEL', 'qwen2.5:3b')
        self.timeout = timeout
        self._model_ready = False

    # ------------------------------------------------------------------
    # Récupération RSS
    # ------------------------------------------------------------------
    def fetch_news(self, tickers, max_per_ticker=3, global_max=5):
        """
        Agrège les news par ticker + marché global.
        Returns: [{'title','link','published','source','ticker'}] dédupliqué, trié.
        """
        try:
            import feedparser
        except ImportError:
            logger.error('feedparser non installé — news désactivées')
            return []

        items, seen = [], set()

        def _add(entry, source, ticker):
            title = (getattr(entry, 'title', '') or '').strip()
            if not title:
                return
            key = title.lower()
            if key in seen:
                return
            seen.add(key)
            published = getattr(entry, 'published', '') or getattr(entry, 'updated', '')
            items.append({
                'title': title,
                'link': getattr(entry, 'link', ''),
                'published': published,
                'published_parsed': getattr(entry, 'published_parsed', None),
                'source': source,
                'ticker': ticker,
            })

        # News par ticker (Yahoo Finance)
        for t in (tickers or []):
            url = f'https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US'
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:max_per_ticker]:
                    _add(entry, 'Yahoo Finance', t)
            except Exception as e:
                logger.warning('fetch_news: échec RSS %s (%s)', t, e)

        # News marché global
        for source, url in GLOBAL_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:global_max]:
                    _add(entry, source, None)
            except Exception as e:
                logger.warning('fetch_news: échec RSS %s (%s)', source, e)

        # Tri par date décroissante (les entrées sans date passent à la fin)
        items.sort(key=lambda x: x['published_parsed'] or (0,), reverse=True)
        for it in items:
            it.pop('published_parsed', None)
        return items

    # ------------------------------------------------------------------
    # Résumé via Ollama (avec fallback heuristique)
    # ------------------------------------------------------------------
    def summarize(self, news_items, context=''):
        """
        Produit un résumé en prose des news, trié par pertinence pour un investisseur
        momentum passif. Retombe sur l'heuristique si Ollama est indisponible.
        """
        if not news_items:
            return "Aucune actualité notable récupérée."

        summary = self._summarize_ollama(news_items, context)
        if summary:
            return summary
        return self._summarize_fallback(news_items)

    def _summarize_ollama(self, news_items, context):
        if not self._ensure_model():
            return None

        headlines = "\n".join(
            f"- [{(it['ticker'] or 'MARCHÉ')}] {it['title']} ({it['source']})"
            for it in news_items[:25]
        )
        prompt = (
            "Tu es un analyste financier francophone. Voici des titres d'actualité "
            "récents (souvent en anglais)"
            f"{(' — contexte: ' + context) if context else ''}.\n\n"
            f"{headlines}\n\n"
            "IMPORTANT : réponds EXCLUSIVEMENT en français. Traduis en français toute "
            "information issue de titres en anglais ; n'écris aucune phrase en anglais.\n\n"
            "Rédige un résumé court et structuré (4 à 6 puces) qui :\n"
            "1) met en avant uniquement ce qui est pertinent pour un investisseur "
            "momentum passif (tendances de fond, risques macro, mouvements majeurs),\n"
            "2) ignore le bruit (clickbait, news mineures),\n"
            "3) signale tout signe de stress de marché.\n"
            "Sois factuel et concis, et rédige intégralement en français."
        )
        try:
            r = requests.post(
                f'{self.ollama_host}/api/generate',
                json={'model': self.model, 'prompt': prompt, 'stream': False,
                      'options': {'temperature': 0.3}},
                timeout=self.timeout,
            )
            r.raise_for_status()
            text = (r.json().get('response') or '').strip()
            return text or None
        except Exception as e:
            logger.warning('Ollama indisponible, fallback heuristique (%s)', e)
            return None

    def _ensure_model(self):
        """Vérifie qu'Ollama est joignable et que le modèle est présent (pull sinon)."""
        if self._model_ready:
            return True
        try:
            tags = requests.get(f'{self.ollama_host}/api/tags', timeout=10)
            tags.raise_for_status()
            names = {m.get('name', '').split(':')[0] for m in tags.json().get('models', [])}
            if self.model.split(':')[0] in names:
                self._model_ready = True
                return True
            # Modèle absent → pull (bloquant mais une seule fois)
            logger.info('Ollama: téléchargement du modèle %s…', self.model)
            pull = requests.post(f'{self.ollama_host}/api/pull',
                                 json={'name': self.model, 'stream': False}, timeout=600)
            pull.raise_for_status()
            self._model_ready = True
            return True
        except Exception as e:
            logger.warning('Ollama non prêt (%s)', e)
            return False

    @staticmethod
    def _summarize_fallback(news_items):
        """Résumé sans LLM : regroupe par ticker/marché et liste les titres récents."""
        by_ticker, glob = {}, []
        for it in news_items:
            if it['ticker']:
                by_ticker.setdefault(it['ticker'], []).append(it['title'])
            else:
                glob.append(it['title'])

        lines = []
        if glob:
            lines.append("Marché global :")
            lines += [f"• {t}" for t in glob[:5]]
        for tk, titles in by_ticker.items():
            lines.append(f"\n{tk} :")
            lines += [f"• {t}" for t in titles[:3]]
        return "\n".join(lines) if lines else "Aucune actualité notable récupérée."
