# -*- coding: utf-8 -*-
"""
Régime de marché LIVE de la stratégie momentum (sprint 3).

Deux indices, le SPY (S&P 500) et le QQQ (Nasdaq-100), chacun classé en 4 états
par trend_regime (momentum 12 mois × 1 mois, Goulding-Harvey-Mazzoleni 2023).
Chaque action suit le régime de SON indice (« régime par poche ») :

  - poche Nasdaq  : titres cotés au Nasdaq (ou membres du Nasdaq-100) → QQQ
  - poche S&P     : tous les autres                                  → SPY

Exposition d'une poche (mode « mixte », recommandé) :
  ½ × (état mensuel ≠ BEAR) + ½ × (état quotidien ≠ BEAR)
  + (bull_exposure − 1) si l'état mensuel est BULL
→ 0 % (BEAR confirmé), 50 % (alerte quotidienne), 100 %, 125 % (BULL).
Mode « mensuel » : décision en fin de mois seulement. Mode « quotidien » :
100 % cash dès le premier jour où le signal quotidien passe en BEAR.

Une poche à 0 % est retirée du classement : le top N est pris dans les poches
encore investies. Les deux à 0 % → tout en cash.

Variantes (réglage `regime_scope`) : 'sleeve' (par poche, défaut), 'spy' (SPY
seul pour tout le portefeuille), 'either' (sortir si SPY OU QQQ en BEAR).
"""
import logging

import pandas as pd

import trend_regime as tr
from cache_utils import TTLCache

logger = logging.getLogger(__name__)

INDEXES = {'spy': 'SPY', 'qqq': 'QQQ'}
SLEEVES = {
    'nasdaq': {'label': 'Nasdaq', 'index': 'qqq'},
    'other': {'label': 'S&P 500', 'index': 'spy'},
}
SCOPES = ('sleeve', 'spy', 'either')
MODES = ('blend', 'monthly', 'daily')
DEFAULTS = {
    'regime_scope': 'sleeve',
    'regime_mode': 'blend',
    'regime_bull_exposure': 1.25,
    'regime_alerts': True,
}
NASDAQ_EXCHANGES = ('NMS', 'NGM', 'NCM', 'NAS', 'NASDAQ')

_close_cache = TTLCache(1800)     # 30 min : un appel Tiingo par indice
_nasdaq_cache = TTLCache(6 * 3600)


# ---------------------------------------------------------------- réglages
def get_regime_settings():
    """Réglages du régime (Settings, avec défauts et bornes)."""
    from models import Settings
    scope = (Settings.get('regime_scope', DEFAULTS['regime_scope']) or '').lower()
    mode = (Settings.get('regime_mode', DEFAULTS['regime_mode']) or '').lower()
    try:
        bull = float(Settings.get('regime_bull_exposure', DEFAULTS['regime_bull_exposure']))
    except (TypeError, ValueError):
        bull = DEFAULTS['regime_bull_exposure']
    alerts = str(Settings.get('regime_alerts', 'true')).lower() != 'false'
    return {
        'regime_scope': scope if scope in SCOPES else DEFAULTS['regime_scope'],
        'regime_mode': mode if mode in MODES else DEFAULTS['regime_mode'],
        'regime_bull_exposure': min(1.5, max(1.0, bull)),
        'regime_alerts': alerts,
    }


def backtest_kwargs(cfg=None):
    """Traduit les réglages live en options du moteur de backtest."""
    cfg = cfg or get_regime_settings()
    params = {'mode': cfg['regime_mode'], 'bull_exposure': cfg['regime_bull_exposure']}
    scope = cfg['regime_scope']
    if scope == 'sleeve':
        return {'sleeve_regime': True, 'trend_params': params}
    if scope == 'either':
        return {'trend_regime': True, 'trend_params': params, 'regime_either': True}
    return {'trend_regime': True, 'trend_params': params}


# ---------------------------------------------------------------- données
def index_closes(symbol, nb_jours=1200):
    """
    Clôtures ajustées FRAÎCHES d'un indice : Tiingo, puis yfinance, puis la base.
    La cascade DB-d'abord des prix journaliers n'est pas utilisée ici : elle peut
    renvoyer la barre de la veille, et l'alerte quotidienne doit voir la clôture
    du jour.
    """
    key = f'{symbol}:{nb_jours}'
    cached, hit = _close_cache.get(key)
    if hit:
        return cached
    s = None
    try:
        from services import get_momentum_service
        ms = get_momentum_service()
        if ms is not None:
            df, err = ms._fetch_daily_tiingo(symbol, nb_jours)
            if df is not None and 'adjClose' in df.columns and len(df) > 300:
                s = df['adjClose']
    except Exception as e:
        logger.info('Tiingo %s indisponible : %s', symbol, e)
    if s is None:
        try:
            import yfinance as yf
            start = (pd.Timestamp.today() - pd.Timedelta(days=nb_jours + 45)).strftime('%Y-%m-%d')
            y = yf.download(symbol, start=start, progress=False, auto_adjust=True)['Close']
            y = y.squeeze()
            if len(y) > 300:
                s = y
        except Exception as e:
            logger.info('yfinance %s indisponible : %s', symbol, e)
    if s is None:
        try:
            from services import get_momentum_service
            ms = get_momentum_service()
            start = (pd.Timestamp.today() - pd.Timedelta(days=nb_jours + 45)).date()
            df = ms._load_bars_from_db(symbol, start) if ms else None
            if df is not None and len(df) > 300:
                s = df['adjClose']
        except Exception as e:
            logger.warning('Base %s indisponible : %s', symbol, e)
    if s is None:
        return None
    s = pd.Series(s).dropna().astype(float)
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    s = s[~s.index.duplicated(keep='last')].sort_index()
    s = _drop_partial_session(s)
    _close_cache.set(key, s)
    return s


def _drop_partial_session(s, now_et=None):
    """Retire la barre du jour tant que la séance US n'est pas close (prix intraday)."""
    if s.empty:
        return s
    now_et = now_et or pd.Timestamp.now(tz='America/New_York')
    today = pd.Timestamp(now_et.date())
    closed = now_et.hour > 16 or (now_et.hour == 16 and now_et.minute >= 5)
    if s.index[-1] >= today and not closed:
        return s[s.index < today]
    return s


def nasdaq_tickers(tickers=None):
    """Titres de la poche Nasdaq : cotés au Nasdaq (profil yfinance) ∪ Nasdaq-100 actuel."""
    cached, hit = _nasdaq_cache.get('all')
    if not hit:
        import json
        out = set()
        try:
            from models import TickerInfoSnapshot, IndexConstituent
            for r in TickerInfoSnapshot.query.with_entities(
                    TickerInfoSnapshot.ticker, TickerInfoSnapshot.raw_info).all():
                try:
                    ex = (json.loads(r.raw_info or '{}') or {}).get('exchange', '')
                except Exception:
                    ex = ''
                if ex in NASDAQ_EXCHANGES:
                    out.add(r.ticker.upper())
            for r in IndexConstituent.query.filter_by(index_name='NDX100', is_active=True).all():
                out.add(r.ticker.upper())
        except Exception as e:
            logger.warning('nasdaq_tickers : %s', e)
        cached = out
        _nasdaq_cache.set('all', cached)
    if tickers is None:
        return set(cached)
    return {t for t in (x.upper() for x in tickers) if t in cached}


# ---------------------------------------------------------------- calcul
def exposure_from_states(monthly, daily, mode='blend', bull=1.25):
    """Exposition d'une poche à partir des états mensuel et quotidien."""
    if not monthly:
        return 1.0
    if mode == 'monthly':
        if monthly == tr.BEAR:
            return 0.0
        return float(bull) if monthly == tr.BULL else 1.0
    if mode == 'daily':
        # 100 % cash dès le premier jour où le signal quotidien passe en BEAR
        if daily == tr.BEAR:
            return 0.0
        return float(bull) if daily == tr.BULL else 1.0
    m_in = 0.0 if monthly == tr.BEAR else 1.0
    d_in = 0.0 if daily == tr.BEAR else 1.0
    return round(0.5 * m_in + 0.5 * d_in + (float(bull) - 1.0) * (monthly == tr.BULL), 4)


def _last_business_day(d):
    end = pd.Timestamp(d) + pd.offsets.MonthEnd(0)
    while end.weekday() >= 5:
        end -= pd.Timedelta(days=1)
    return end


def index_regime(key, cfg=None, close=None):
    """État complet d'un indice (clé 'spy' | 'qqq') + KPI de suivi."""
    cfg = cfg or get_regime_settings()
    symbol = INDEXES[key]
    s = close if close is not None else index_closes(symbol)
    if s is None or len(s) < 300:
        return {'key': key, 'symbol': symbol, 'state': None,
                'error': f'Historique {symbol} indisponible'}
    cur = tr.current_regime(s)
    if cur.get('error'):
        return {'key': key, 'symbol': symbol, 'state': None, 'error': cur['error']}

    # Historique des états officiels (fins de mois closes)
    st_m = tr.compute_states(s, 'monthly')
    closed = st_m[st_m.index <= pd.Timestamp(cur['as_of'])].dropna(subset=['state'])
    history = [{'month': d.strftime('%Y-%m'), 'state': row.state,
                'slow_ret_pct': round(row.slow_ret * 100, 1), 'fast_ret_pct': round(row.fast_ret * 100, 1)}
               for d, row in closed.tail(24).iterrows()]
    since = closed.index[-1]
    for d, stt in zip(reversed(closed.index), reversed(closed['state'].tolist())):
        if stt != cur['state']:
            break
        since = d
    # Dernier changement d'état quotidien
    st_d = tr.compute_states(s, 'daily')['state'].dropna()
    d_since = st_d.index[-1]
    for d, stt in zip(reversed(st_d.index), reversed(st_d.tolist())):
        if stt != cur['daily_state']:
            break
        d_since = d
    # Dernière sortie (passage en BEAR mensuel)
    bear_eps = tr.episodes(s, mode='monthly')
    last_exit = bear_eps[-1] if bear_eps else None

    expo = exposure_from_states(cur['state'], cur['daily_state'],
                                cfg['regime_mode'], cfg['regime_bull_exposure'])
    today = pd.Timestamp(cur['provisional_as_of'])
    next_decision = _last_business_day(today)
    days_left = int(len(pd.bdate_range(today + pd.Timedelta(days=1), next_decision)))
    return {
        **cur,
        'key': key, 'symbol': symbol,
        'exposure': expo,
        'state_since': since.strftime('%Y-%m-%d'),
        'months_in_state': int(len(closed.loc[since:])),
        'daily_since': d_since.strftime('%Y-%m-%d'),
        'history': history,
        'last_exit': last_exit,
        'next_decision': next_decision.strftime('%Y-%m-%d'),
        'trading_days_to_decision': days_left,
        # À l'approche de la fin de mois, le provisoire annonce la décision
        'month_end_watch': bool(days_left <= 3 and cur['provisional_state'] != cur['state']),
    }


def _status(expos):
    vals = list(expos.values())
    if all(v <= 0 for v in vals):
        return 'CASH'
    if any(v < 1 for v in vals):
        return 'REDUIT'
    if any(v > 1 for v in vals):
        return 'LEVIER'
    return 'INVESTI'


def live_regime(cfg=None, closes=None):
    """
    Régime live complet : les deux indices, l'exposition de chaque poche et le
    message d'action principal. `closes` (dict optionnel {'spy': Series, 'qqq':
    Series}) sert aux tests.
    """
    cfg = cfg or get_regime_settings()
    closes = closes or {}
    idx = {k: index_regime(k, cfg, closes.get(k)) for k in INDEXES}
    spy, qqq = idx['spy'], idx['qqq']
    e_spy = spy.get('exposure', 1.0) if spy.get('state') else 1.0
    e_qqq = qqq.get('exposure', 1.0) if qqq.get('state') else e_spy
    scope = cfg['regime_scope']
    if scope == 'spy':
        sleeves = {'nasdaq': e_spy, 'other': e_spy}
    elif scope == 'either':
        sleeves = {'nasdaq': min(e_spy, e_qqq), 'other': min(e_spy, e_qqq)}
    else:
        sleeves = {'nasdaq': e_qqq, 'other': e_spy}
    status = _status(sleeves)
    headline = {
        'CASH': 'Tout en cash : les deux marchés sont en BEAR',
        'REDUIT': 'Exposition réduite : au moins une poche est en alerte',
        'LEVIER': 'Rester investi, avec levier sur la tendance haussière',
        'INVESTI': 'Rester investi',
    }[status]
    return {
        'as_of': max((v.get('provisional_as_of') or '') for v in idx.values()) or None,
        'settings': cfg,
        'indexes': idx,
        'sleeves': {k: {**SLEEVES[k], 'exposure': v,
                        'state': idx[SLEEVES[k]['index']].get('state') if scope == 'sleeve'
                        else spy.get('state'),
                        'daily_state': idx[SLEEVES[k]['index']].get('daily_state') if scope == 'sleeve'
                        else spy.get('daily_state')}
                    for k, v in sleeves.items()},
        'status': status,
        'headline': headline,
        'month_end_watch': any(v.get('month_end_watch') for v in idx.values()),
        'next_decision': spy.get('next_decision') or qqq.get('next_decision'),
        'errors': [v['error'] for v in idx.values() if v.get('error')],
    }


def sleeve_of(ticker, nasdaq_set):
    return 'nasdaq' if ticker.upper() in nasdaq_set else 'other'


def multipliers_for(tickers, regime, nasdaq_set=None):
    """{ticker: multiplicateur d'exposition} selon la poche de chaque titre."""
    nasdaq_set = nasdaq_tickers(tickers) if nasdaq_set is None else nasdaq_set
    sl = regime['sleeves']
    return {t: float(sl[sleeve_of(t, nasdaq_set)]['exposure']) for t in tickers}
