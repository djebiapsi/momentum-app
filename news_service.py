# -*- coding: utf-8 -*-
"""
Service d'agrégation et de résumé des news
==========================================
Récupère les actualités financières via des flux RSS gratuits (Yahoo Finance par
ticker + flux marché global), puis produit un résumé trié par pertinence.

Le résumé est généré par une API LLM compatible OpenAI (par défaut GPT-4o-mini ;
DeepSeek/Gemini/OpenRouter via base_url + model). Garde-fous anti-surfacturation :
nombre d'articles borné, contenu tronqué, plafond de tokens en sortie. Sans clé API
ou en cas d'échec, on retombe sur un résumé heuristique (titres groupés) — jamais
d'erreur silencieuse bloquante qui empêcherait l'envoi du briefing.

Variables d'environnement : LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.
"""

import os
import re
import html as html_lib
import logging
import requests

logger = logging.getLogger(__name__)

# Flux RSS marché global (gratuits, sans clé)
GLOBAL_FEEDS = [
    ('MarketWatch', 'http://feeds.marketwatch.com/marketwatch/topstories/'),
    ('CNBC', 'https://www.cnbc.com/id/100003114/device/rss/rss.html'),
]


class NewsService:
    def __init__(self, api_key=None, base_url=None, model=None, timeout=60):
        # Client LLM compatible OpenAI (GPT-4o-mini par défaut ; DeepSeek/Gemini/
        # OpenRouter fonctionnent en changeant base_url + model). Si aucune clé n'est
        # fournie, on retombe directement sur le résumé heuristique.
        self.api_key = api_key or os.environ.get('LLM_API_KEY', '')
        self.base_url = (base_url or os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')).rstrip('/')
        self.model = model or os.environ.get('LLM_MODEL', 'gpt-4o-mini')
        self.timeout = timeout
        # Récupération du corps des articles (best-effort). Désactivable via env.
        self.fetch_articles = os.environ.get('NEWS_FETCH_ARTICLES', '1') not in ('0', 'false', 'False')

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
            # Description / résumé fourni par le flux RSS (souvent un extrait de l'article)
            desc = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
            items.append({
                'title': title,
                'link': getattr(entry, 'link', ''),
                'published': published,
                'published_parsed': getattr(entry, 'published_parsed', None),
                'source': source,
                'ticker': ticker,
                'summary': self._strip_html(desc)[:600],
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
    # Extraction du contenu des articles
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_html(text):
        """Retire les balises HTML et décode les entités → texte brut compact."""
        if not text:
            return ''
        text = re.sub(r'(?is)<(script|style).*?</\1>', ' ', text)
        text = re.sub(r'(?s)<[^>]+>', ' ', text)
        text = html_lib.unescape(text)
        return re.sub(r'\s+', ' ', text).strip()

    def _fetch_article(self, url, max_chars=1800):
        """
        Extrait le corps d'un article via trafilatura (suppression du boilerplate :
        menus, pubs, légendes). Renvoie '' si trafilatura est absent ou en cas
        d'échec → l'appelant retombe alors sur la description RSS.
        """
        if not url:
            return ''
        try:
            import trafilatura
        except ImportError:
            return ''  # dépendance absente → on utilisera la description RSS
        try:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return ''
            text = trafilatura.extract(downloaded, include_comments=False,
                                       include_tables=False, favor_precision=True) or ''
            return re.sub(r'\s+', ' ', text).strip()[:max_chars]
        except Exception as e:
            logger.debug('Article non récupéré (%s): %s', url, e)
            return ''

    def _enrich_content(self, news_items, limit=8):
        """
        Complète chaque item avec un champ 'content' = corps de l'article si
        disponible, sinon la description RSS. Limité aux `limit` premiers items
        pour borner le temps du briefing.
        """
        for i, it in enumerate(news_items):
            content = ''
            if self.fetch_articles and i < limit:
                content = self._fetch_article(it.get('link', ''))
            if not content:
                content = it.get('summary', '')
            it['content'] = content
        return news_items

    # ------------------------------------------------------------------
    # Résumé via API LLM (compatible OpenAI) avec garde-fous de coût
    # ------------------------------------------------------------------

    # Plafonds anti-surfacturation (bornent la taille de chaque appel)
    MAX_GLOBAL = 5             # articles marché global envoyés au modèle
    MAX_PER_TICKER = 2         # articles par position envoyés au modèle
    MAX_FETCH_ARTICLES = 10    # nb d'articles dont on récupère le corps complet
    MAX_CONTENT_CHARS = 900    # contenu max par article
    MAX_OUTPUT_TOKENS = 700    # plafond de tokens générés

    def summarize(self, news_items, context='', tickers=None):
        """
        Produit un résumé en français : une partie marché global + une partie par
        position du portefeuille (si `tickers` fourni). Basé sur le contenu des
        articles. Retombe sur l'heuristique si aucune clé API ou si l'appel échoue.
        """
        if not news_items:
            return "Aucune actualité notable récupérée."

        self._enrich_content(news_items, limit=self.MAX_FETCH_ARTICLES)
        if self.api_key:
            summary = self._summarize_api(news_items, context, tickers)
            if summary:
                return summary
        return self._summarize_fallback(news_items)

    def _select_articles(self, news_items, tickers):
        """
        Sélectionne les articles envoyés au modèle en garantissant la couverture
        de chaque position : jusqu'à MAX_PER_TICKER par ticker + MAX_GLOBAL globaux.
        Borne le coût tout en assurant un résumé par action.
        """
        tickers = [t.upper() for t in (tickers or [])]
        per_ticker, globals_ = {}, []
        for it in news_items:
            tk = (it.get('ticker') or '').upper()
            if tk and tk in tickers:
                per_ticker.setdefault(tk, [])
                if len(per_ticker[tk]) < self.MAX_PER_TICKER:
                    per_ticker[tk].append(it)
            elif not tk:
                if len(globals_) < self.MAX_GLOBAL:
                    globals_.append(it)
        # Ordre : global d'abord, puis par ticker (ordre du portefeuille)
        selected = list(globals_)
        for tk in tickers:
            selected.extend(per_ticker.get(tk, []))
        return selected, [tk for tk in tickers if per_ticker.get(tk)]

    def _build_messages(self, news_items, context, tickers=None):
        """Construit les messages (system + user) en bornant la taille (coût)."""
        selected, covered = self._select_articles(news_items, tickers)
        blocks = []
        for it in selected:
            tag = it.get('ticker') or 'MARCHÉ'
            body = (it.get('content') or it.get('summary') or '').strip()[:self.MAX_CONTENT_CHARS]
            block = f"[{tag}] {it['title']} (source: {it['source']})"
            if body:
                block += f"\nContenu: {body}"
            blocks.append(block)
        articles = "\n\n".join(blocks) if blocks else "(aucun article)"

        portfolio = [t.upper() for t in (tickers or [])]
        positions_line = (
            f"Actions de mon portefeuille : {', '.join(portfolio)}.\n" if portfolio else ""
        )

        system_msg = (
            "Tu es un analyste financier francophone. Tu réponds TOUJOURS et "
            "EXCLUSIVEMENT en français, quelle que soit la langue des sources. "
            "Tu ne dois écrire aucune phrase en anglais : traduis tout en français."
        )
        user_msg = (
            f"Voici des articles d'actualité financière récents"
            f"{(' — contexte: ' + context) if context else ''}.\n"
            f"{positions_line}\n"
            f"{articles}\n\n"
            "À partir du CONTENU de ces articles (pas seulement des titres), rédige "
            "en français un résumé structuré en DEUX sections :\n\n"
            "## Marché global\n"
            "4 à 6 puces synthétisant ce qui compte pour un investisseur momentum passif "
            "(tendances de fond, risques macro, mouvements majeurs, signes de stress).\n\n"
            "## Mes positions\n"
            "Pour CHAQUE action du portefeuille ci-dessus qui a des news pertinentes, une "
            "puce commençant par le ticker en gras suivi de 1 à 2 phrases de synthèse "
            "(résultats, annonces, mouvement notable). Si une action n'a aucune news "
            "pertinente, ne la mentionne pas. Si aucune position n'a de news, écris "
            "« Rien de notable sur tes positions aujourd'hui. »\n\n"
            "Ignore le bruit (clickbait, news mineures). Sois factuel et concis. "
            "Rédige intégralement en français."
        )
        return system_msg, user_msg

    def _summarize_api(self, news_items, context, tickers=None):
        """Appel à l'API LLM (compatible OpenAI /chat/completions)."""
        system_msg, user_msg = self._build_messages(news_items, context, tickers)
        try:
            r = requests.post(
                f'{self.base_url}/chat/completions',
                headers={'Authorization': f'Bearer {self.api_key}',
                         'Content-Type': 'application/json'},
                json={
                    'model': self.model,
                    'temperature': 0.2,
                    'max_tokens': self.MAX_OUTPUT_TOKENS,
                    'messages': [
                        {'role': 'system', 'content': system_msg},
                        {'role': 'user', 'content': user_msg},
                    ],
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            text = (data['choices'][0]['message']['content'] or '').strip()
            usage = data.get('usage', {})
            if usage:
                logger.info('LLM news: %s tokens (in=%s, out=%s)',
                            usage.get('total_tokens'), usage.get('prompt_tokens'),
                            usage.get('completion_tokens'))
            return text or None
        except Exception as e:
            logger.warning('API LLM indisponible, fallback heuristique (%s)', e)
            return None

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
