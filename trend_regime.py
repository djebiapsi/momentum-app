# -*- coding: utf-8 -*-
"""
Régime de tendance « 4 états » (Goulding, Harvey & Mazzoleni, *Momentum Turning
Points*, JFE 2023) — signal de sortie / rentrée du momentum long.

Deux momentums du MARCHÉ (SPY, rendement total) :
  - lent  = rendement 12 mois (12 fins de mois, ou 252 séances en mode jour) ;
  - rapide = rendement 1 mois  (1 fin de mois,   ou 21 séances).

              rapide > 0     rapide ≤ 0
  lent > 0    BULL           CORRECTION
  lent ≤ 0    REBOUND        BEAR

Règle (pré-enregistrée, sprint 3) : investi dans BULL, CORRECTION et REBOUND,
cash uniquement en BEAR. On ne sort donc que si la baisse est confirmée à la fois
sur 12 mois ET sur le dernier mois, et on rentre dès que le dernier mois
redevient positif — sans attendre que la tendance longue se répare : c'est l'état
REBOUND, le plus rentable après BULL (≈ +14 %/an sur le marché US 1926-2026,
contre −3 %/an en BEAR).

Version mensuelle = règle de décision (validée face au hasard sur 1927-1962,
1963-1999 et 2000-2026). Version quotidienne = alerte précoce seulement : elle
fait mieux avant 1963 mais se fait piéger par les krachs éclair (1987).

Formulation équivalente en un seul seuil (mode mensuel) : à la prochaine fin de
mois T, BEAR ⇔ P_T < min(P_{T-1}, P_{T-12}). Ce « pivot » est connu à l'avance,
ce qui permet d'afficher le niveau du SPY qui déclencherait la sortie.

Aucun lookahead : l'état calculé à la clôture de t s'applique à partir de t+1.
"""
import numpy as np
import pandas as pd

BULL, CORRECTION, REBOUND, BEAR = 'BULL', 'CORRECTION', 'REBOUND', 'BEAR'

MODES = {
    'monthly': {'slow': 12, 'fast': 1},     # en fins de mois
    'daily': {'slow': 252, 'fast': 21},     # en séances
    'blend': None,                          # ½ mensuel + ½ quotidien
}

DEFAULT_PARAMS = {
    'mode': 'monthly',
    'bull_exposure': 1.0,       # > 1 = levier limité à l'état BULL (exploratoire)
    'bear_exposure': 0.0,       # exposition en BEAR (0 = cash)
}


def regime_params(overrides=None):
    p = dict(DEFAULT_PARAMS)
    for k, v in (overrides or {}).items():
        if k in p and v is not None:
            p[k] = v
    if p['mode'] not in MODES:
        raise ValueError(f"mode inconnu : {p['mode']}")
    return p


def _clean(close):
    s = pd.Series(close).dropna().astype(float).sort_index()
    s = s[~s.index.duplicated(keep='last')]
    s.index = pd.DatetimeIndex(s.index)
    return s[s > 0]


def _month_end_closes(s):
    """Dernière clôture de chaque mois, datée du dernier jour de bourse du mois."""
    g = s.groupby([s.index.year, s.index.month])
    return pd.Series(g.last().values, index=pd.DatetimeIndex(g.apply(lambda x: x.index[-1]).values))


def classify(slow_ret, fast_ret):
    """Vectoriel : états à partir des rendements lent / rapide (NaN → None)."""
    slow_up = slow_ret > 0
    fast_up = fast_ret > 0
    st = np.where(slow_up & fast_up, BULL,
                  np.where(slow_up, CORRECTION, np.where(fast_up, REBOUND, BEAR)))
    st = pd.Series(st, index=slow_ret.index, dtype=object)
    st[slow_ret.isna() | fast_ret.isna()] = None
    return st


def compute_states(close, mode='monthly'):
    """
    DataFrame des états aux dates d'évaluation (fins de mois ou séances) :
      close, slow_ret, fast_ret, state.
    En mode mensuel, la fin de mois en cours n'apparaît qu'une fois le mois clos
    (la dernière ligne n'est retenue que si la séance suivante est dans un autre
    mois — sinon c'est un état provisoire, cf. current_regime).
    """
    s = _clean(close)
    if s.empty:
        return pd.DataFrame(columns=['close', 'slow_ret', 'fast_ret', 'state'])
    cfg = MODES[mode]
    pts = _month_end_closes(s) if mode == 'monthly' else s
    slow = pts / pts.shift(cfg['slow']) - 1.0
    fast = pts / pts.shift(cfg['fast']) - 1.0
    return pd.DataFrame({'close': pts, 'slow_ret': slow, 'fast_ret': fast,
                         'state': classify(slow, fast)})


def _held_states(s, mode):
    """États quotidiens connus à la clôture de chaque séance (mensuel : tenu)."""
    st = compute_states(s, mode)['state']
    if mode == 'daily':
        return st
    if len(st):
        last = st.index[-1]
        # mois en cours non clos : la dernière « fin de mois » est la dernière séance
        if last == s.index[-1] and (last + pd.offsets.BDay(1)).month == last.month:
            st = st.iloc[:-1]
    return st.reindex(s.index).ffill()


def exposure_series(close, **overrides):
    """
    Multiplicateur d'exposition QUOTIDIEN connu à la clôture de t (à appliquer à
    t+1), 1 tant que l'historique est insuffisant.
      - monthly / daily : bull_exposure en BULL, 1 en CORRECTION / REBOUND,
        bear_exposure en BEAR. En mensuel, l'état d'une fin de mois est tenu
        jusqu'à la fin de mois suivante (le mois en cours garde l'état précédent).
      - blend : ½ règle mensuelle + ½ règle quotidienne (expo 0 / 0,5 / 1), plus
        (bull_exposure − 1) quand l'état MENSUEL est BULL. Diversifie la vitesse
        du signal : le mensuel évite les faux signaux des krachs éclair (1987),
        le quotidien réagit plus vite aux baisses longues (1929, 2008).
    Retourne (expo, états mensuels quotidiens — ou quotidiens en mode daily).
    """
    p = regime_params(overrides)
    s = _clean(close)
    if s.empty:
        return pd.Series(dtype=float), pd.Series(dtype=object)
    bull_x, bear_x = float(p['bull_exposure']), float(p['bear_exposure'])

    def _expo(st, bull):
        m = {BULL: bull, CORRECTION: 1.0, REBOUND: 1.0, BEAR: bear_x}
        return st.map(lambda x: m.get(x, 1.0) if isinstance(x, str) else 1.0).astype(float)

    if p['mode'] == 'blend':
        st_m = _held_states(s, 'monthly')
        st_d = _held_states(s, 'daily')
        expo = (0.5 * _expo(st_m, 1.0) + 0.5 * _expo(st_d, 1.0)
                + (bull_x - 1.0) * (st_m == BULL).astype(float))
        return expo, st_m
    st = _held_states(s, p['mode'])
    return _expo(st, bull_x), st


def exposure_at(expo, as_of):
    """Exposition connue à la clôture de `as_of` (dernier point ≤ as_of), 1.0 sinon."""
    if expo is None or len(expo) == 0:
        return 1.0
    sub = expo.loc[:as_of]
    return float(sub.iloc[-1]) if len(sub) else 1.0


def episodes(close, **overrides):
    """
    Une ligne par épisode de réduction d'exposition (< 100 %) : dates de sortie /
    rentrée, exposition minimale, SPY. En mensuel / quotidien = passages en BEAR.
    """
    expo, _ = exposure_series(close, **overrides)
    s = _clean(close)
    out = []
    if expo.empty:
        return out
    cut = expo < 1.0 - 1e-9
    chg = cut.ne(cut.shift(1, fill_value=False))
    for d0 in cut.index[chg & cut]:
        after = cut.loc[d0:]
        off = after[~after]
        d1 = off.index[0] if len(off) else None
        seg = expo.loc[d0:d1].iloc[:-1] if d1 is not None else expo.loc[d0:]
        out.append({
            'exit': d0.strftime('%Y-%m-%d'),
            'reentry': d1.strftime('%Y-%m-%d') if d1 is not None else None,
            'min_exposure': round(float(seg.min()), 2),
            'px_exit': round(float(s.loc[d0]), 2),
            'px_reentry': round(float(s.loc[d1]), 2) if d1 is not None else None,
            'days_out': int(len(s.loc[d0:d1]) - 1) if d1 is not None else int(len(s.loc[d0:])),
        })
    return out


def current_regime(close):
    """
    Tableau de bord live (SPY) :
      - `monthly` : état officiel de la dernière fin de mois close (= décision) ;
      - `provisional` : état qu'on obtiendrait si le mois se terminait aujourd'hui ;
      - `daily` : état 252 j / 21 j (alerte précoce, non décisionnel) ;
      - `pivot` : niveau du SPY sous lequel la prochaine fin de mois passe en BEAR
        (= min(clôture de la dernière fin de mois, clôture d'il y a 12 mois)), et
        `bull_level` au-dessus duquel elle est BULL (= max des deux) ;
      - `action` : RESTER_INVESTI | SORTIR | RESTER_EN_CASH | RENTRER.
    """
    s = _clean(close)
    if len(s) < 260:
        return {'state': None, 'error': 'Historique insuffisant (≥ 13 mois requis)'}
    last_dt, last_px = s.index[-1], float(s.iloc[-1])
    me = _month_end_closes(s)
    month_open = (last_dt + pd.offsets.BDay(1)).month == last_dt.month
    closed = me.iloc[:-1] if (month_open and me.index[-1] == last_dt) else me
    if len(closed) < 13:
        return {'state': None, 'error': 'Historique mensuel insuffisant'}

    def _state(px, ref_fast, ref_slow):
        return classify(pd.Series([px / ref_slow - 1]), pd.Series([px / ref_fast - 1])).iloc[0]

    # officiel : dernière fin de mois close vs 1 et 12 fins de mois avant
    off_state = _state(closed.iloc[-1], closed.iloc[-2], closed.iloc[-13])
    prev_state = _state(closed.iloc[-2], closed.iloc[-3], closed.iloc[-14]) if len(closed) >= 14 else None
    # provisoire : prix du jour vs dernière fin de mois close et celle d'il y a 11 mois
    ref_fast, ref_slow = float(closed.iloc[-1]), float(closed.iloc[-12])
    # mois clos aujourd'hui : l'état provisoire est l'état officiel
    prov_state = _state(last_px, ref_fast, ref_slow) if month_open else off_state
    pivot = min(ref_fast, ref_slow)
    bull_level = max(ref_fast, ref_slow)
    d = compute_states(s, 'daily')
    daily_state = d['state'].iloc[-1]

    invested_now = off_state != BEAR
    invested_prev = prev_state != BEAR if prev_state else True
    if invested_now:
        action = 'RENTRER' if not invested_prev else 'RESTER_INVESTI'
    else:
        action = 'SORTIR' if invested_prev else 'RESTER_EN_CASH'
    # Exposition recommandée du mode blend (½ mensuel + ½ quotidien) : la moitié
    # « quotidienne » réagit dès aujourd'hui, la moitié mensuelle en fin de mois.
    blend_exposure = (0.5 * (0.0 if off_state == BEAR else 1.0)
                      + 0.5 * (0.0 if daily_state == BEAR else 1.0))

    return {
        'state': off_state,
        'as_of': closed.index[-1].strftime('%Y-%m-%d'),
        'previous_state': prev_state,
        'action': action,
        'exposure': 0.0 if off_state == BEAR else 1.0,
        'blend_exposure': blend_exposure,
        'slow_ret_pct': round((closed.iloc[-1] / closed.iloc[-13] - 1) * 100, 2),
        'fast_ret_pct': round((closed.iloc[-1] / closed.iloc[-2] - 1) * 100, 2),
        'provisional_state': prov_state,
        'provisional_as_of': last_dt.strftime('%Y-%m-%d'),
        'price': round(last_px, 2),
        'pivot': round(pivot, 2),
        'pct_vs_pivot': round((last_px / pivot - 1) * 100, 2),
        'bull_level': round(bull_level, 2),
        'daily_state': daily_state,
        'daily_slow_ret_pct': round(float(d['slow_ret'].iloc[-1]) * 100, 2),
        'daily_fast_ret_pct': round(float(d['fast_ret'].iloc[-1]) * 100, 2),
        'error': None,
    }


def rebound_index_series(close, bear_months=24):
    """
    Poche « rebond » (exploratoire) : True quand, à la dernière fin de mois close,
    le marché est en REBOUND (12 mois ≤ 0, 1 mois > 0) ET que son rendement sur
    `bear_months` mois est négatif. Daniel & Moskowitz (2016) : après un bear
    market, les gagnants du 12-1 sont les titres défensifs et ratent la reprise,
    portée par les perdants à bêta élevé → on détient l'indice à leur place.
    Série quotidienne, tenue jusqu'à la fin de mois suivante.
    """
    s = _clean(close)
    if s.empty:
        return pd.Series(dtype=bool)
    st = compute_states(s, 'monthly')
    pts = st['close']
    long_bear = (pts / pts.shift(bear_months) - 1.0) < 0
    sig = (st['state'] == REBOUND) & long_bear
    last = sig.index[-1]
    if last == s.index[-1] and (last + pd.offsets.BDay(1)).month == last.month:
        sig = sig.iloc[:-1]
    return sig.reindex(s.index).ffill().fillna(False).astype(bool)
