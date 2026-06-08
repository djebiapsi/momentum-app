# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Local Development

```bash
# Setup
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
cp env-example.txt .env   # fill in API keys

# Run development server
flask run                  # or: python app.py

# Run tests
python -m pytest test_options_service.py -v

# Run a single test
python -m pytest test_options_service.py::TestBlackScholes::test_put_price_atm -v

# Production server (local)
gunicorn app:app --workers 1
```

### Environment Variables (see `env-example.txt`)
- `TIINGO_API_KEY` — required for stock data and screener
- `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_TO` — optional, for email notifications
- `SECRET_KEY` — Flask session secret
- `ADMIN_PASSWORD` — PIN-only digits; omit to run in open mode (no auth)
- `DATABASE_URL` — PostgreSQL URL for production; defaults to SQLite
- `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` — optional, LLM for news digest (falls back to heuristic if absent). Compatible OpenAI API (OpenAI, DeepSeek, Gemini)

## Architecture

### Overview

Single-process Flask app with APScheduler for monthly automation. Serves a vanilla-JS SPA (no framework, no build step) whose markup, CSS and JS are split across `templates/` and `static/`. Backend logic lives in service classes; the app is assembled from focused modules (blueprints, services registry, shared core, jobs, scheduler) rather than one monolithic `app.py`.

### Key Files

**App assembly (split out of the former monolithic `app.py`):**

| File | Role |
|---|---|
| `app.py` | Thin entry point: `create_app()` factory, registers blueprints, starts scheduler. Exposes `app` → `gunicorn app:app` |
| `auth.py` | `@require_admin` decorator (checks `X-Admin-Token` via `current_app.config`) |
| `services.py` | Service singletons registry + accessors (`get_momentum_service()`, …) and the `ibkr_service` instance |
| `core.py` | Business logic shared by routes **and** jobs (long calc, momentum CSV, market monitor lifecycle, briefing payload) |
| `routes/` | One Flask Blueprint per domain: `pages`, `settings`, `panel`, `momentum`, `short`, `options`, `ibkr`, `market`, `backtest`, `prices` |
| `jobs.py` | Scheduled task bodies (`app` injected by the scheduler) |
| `scheduler.py` | `create_scheduler(app)` — builds the APScheduler and registers the 4 crons |

**Service classes (unchanged, already modular):**

| File | Role |
|---|---|
| `models.py` | SQLAlchemy ORM models (see below) |
| `config.py` | Config classes, `DEFAULT_PANEL` (51 tickers), `DEFAULT_NB_TOP` |
| `momentum_service.py` | 12-1 momentum via Tiingo monthly price API (+ IBKR fallback) |
| `finviz_screener_service.py` | Finviz scraping for Death Cross detection (Long & Short) |
| `short_screener_service.py` | Legacy short screener (superseded by `finviz_screener_service`) |
| `screener_service.py` | Long screener via Tiingo IEX bulk endpoint |
| `options_service.py` | Black-Scholes Greeks, PUT/PUT SPREAD strategy engine |
| `backtest_service.py` | Backtest momentum : univers re-screené /3 mois (ADV point-in-time), config live, moteur de rééquilibrage vectorisé, stats via `quantstats` |
| `email_service.py` | HTML email via Resend API |
| `news_service.py` | Fetch RSS feeds + LLM summary (OpenAI-compatible API) for the bi-daily digest |
| `market_monitor_service.py` | Real-time market alert monitoring (price alerts, VIX, futures) |
| `ibkr_service.py` | IBKR TWS API client (asyncio thread); port 4001 paper / 4003 live via socat |
| `price_data_service.py` | yfinance background collector: SP500 + NDX100 daily & monthly history |
| `flex_service.py` | IBKR Flex Web Service: NAV, transactions, dividends via Flex Queries (2-step async) |

**Frontend (split out of the former monolithic `templates/index.html`):**

| Path | Role |
|---|---|
| `templates/index.html` | HTML shell: `<head>`, `{% include %}` of page partials, `<link>`/`<script>` to assets |
| `templates/partials/*.html` | One partial per page (`_dashboard`, `_short`, `_panel`, `_history`, `_settings`, `_options`, `_perf`, `_market`) + `_modals`, `_nav` |
| `static/css/app.css` | All styles |
| `static/js/*.js` | Vanilla JS in load order: `core` → `dashboard` → `panel` → `short` → `options` → `perf` → `backtest` (classic scripts, shared global scope) |

### Database Models

**Long strategy**: `PanelAction` → `RecommendationHistory` → `RecommendationDetail`

**Short strategy**: `ShortPanelAction` → `ShortRecommendationHistory` → `ShortRecommendationDetail`

**Options**: `OptionRecommendation` (flat table with all Greeks and strategy details)

**Config**: `Settings` (key-value store for `nb_top`, `date_calcul`)

**Prices cache**: `MarketPriceBar` (IBKR daily bars per ticker, used by backtest prefill)

SQLite in development (`instance/` folder), PostgreSQL in production. `config.py` patches `postgres://` → `postgresql://` for SQLAlchemy compatibility.

### Strategy Logic

**Long (12-1 Momentum)**: Momentum = `(Price[T-1m] - Price[T-12m]) / Price[T-12m] × 100`. Skips last month to avoid mean reversion. Recommends top N tickers.

**Short (63-5 Momentum)**: Momentum = `(Price[T-5] / Price[T-63]) - 1`. Targets stocks with Death Cross (Price < SMA50 < SMA200). Entry criteria: `perf_1m ≤ -8%`, `perf_3m ≤ -15%`.

**Options**: Black-Scholes PUT pricing targeting 30–60 DTE, delta −0.25 to −0.40. PUT SPREAD uses a short put at delta −0.10.

### Authentication

`@require_admin` decorator checks `X-Admin-Token` header against `ADMIN_PASSWORD`. If `ADMIN_PASSWORD` is unset, all routes are open (no auth required).

### Frontend

Vanilla JavaScript (no framework, no build step). The HTML shell `templates/index.html` includes page partials from `templates/partials/` and loads `static/css/app.css` + `static/js/*.js`. The JS files are **classic scripts** sharing one global scope (handlers are wired via inline `onclick=`), so **load order matters** — keep the order in `index.html` (`core` first). All API calls go to `/api/*`. PWA manifest and service worker in `static/` enable iOS home screen installation.

### Deployment

VPS Hetzner (`root@95.216.198.241`), Docker Compose. Gunicorn 1 worker. **SSH** : `ssh -i ~/.ssh/id_ed25519 root@95.216.198.241`. PostgreSQL dans un conteneur `db`. Variables d'environnement dans `/opt/momentum-app/.env`. IB Gateway dans un conteneur séparé (`ib-gateway`), exposé via socat port 4003 (live) / 4001 (paper).

### Scheduled Jobs (8 crons)

| Job | Schedule | Description |
|---|---|---|
| `job_market_monitor` | Every minute, mon–fri 9h30–16h ET | Real-time alerts during US session |
| `job_market_monitor_offhours` | Every 15 min, 24/7 | VIX/futures off-hours (skips during US session) |
| `job_briefing('open')` | 9h15 ET mon–fri | Pre-open briefing email |
| `job_briefing('mid')` | 12h30 ET mon–fri | Mid-session briefing email |
| `job_briefing('close')` | 16h05 ET mon–fri | Closing briefing email |
| `job_rebalance_reminder` | 1st of month 8h00 ET | Runs momentum calc + sends rebalance email |
| `job_refresh_prices` | 22h00 ET mon–fri | Refreshes IBKR price cache + backtest pool prefill |
| `job_collect_prices` | 00h00 Europe/Paris | yfinance SP500/NDX100 incremental collection |
| `job_digest_actualites` | 10h00 + 20h00 Europe/Paris | RSS news digest via LLM, sent to hard-coded recipients |
