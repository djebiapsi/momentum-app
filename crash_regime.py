# -*- coding: utf-8 -*-
"""
Détecteur de régime « krach → rebond » (levier conditionnel post-krach).

Idée : rester à 1× en temps normal et n'autoriser un levier modéré (1,3-1,5×)
que dans la fenêtre qui SUIT un krach, une fois le pic de volatilité passé —
pour toucher la jambe de rebond du levier sans subir sa jambe de krach.

Le détecteur est une machine à états évaluée jour par jour sur le cours de
clôture d'un indice (SPY / ^GSPC), avec UNIQUEMENT les données ≤ t (aucun
lookahead : l'état calculé à la clôture de t s'applique au jour suivant, comme
les poids du backtest).

  NORMAL   → ARMED     drawdown vs plus-haut glissant 252 j ≤ −dd_threshold
                       (ouvre un ÉPISODE de krach)
  ARMED    → LEVER     vol réalisée courte (20 j) < vol réalisée longue (60 j)
                       = le pic de volatilité est passé, et il reste du budget
  LEVER    → ARMED     (si exit_on_vol) la vol courte repasse au-dessus de la
                       longue : on coupe le levier, on pourra le remettre au
                       prochain retour au calme dans le même épisode
  LEVER    → COOLDOWN  le budget de `window_days` séances de levier de
                       l'épisode est épuisé (cumul, re-entrées comprises)
  ARMED / COOLDOWN → NORMAL
                       nouveau plus-haut 252 séances (drawdown = 0) : fin de
                       l'épisode (définition usuelle de la fin d'un bear market)

Un épisode ne se termine donc PAS quand le drawdown repasse juste au-dessus du
seuil : sans cette hystérésis, un marché qui oscille autour de −25 % ouvrait
plusieurs épisodes (et plusieurs fenêtres de levier) dans la même crise.

Spécification PRÉ-ENREGISTRÉE (fixée avant tout test, cf. docs) :
  dd_threshold=0.25, high_window=252, vol_short=20, vol_long=60,
  window_days=189 (≈ 9 mois), leverage=1.4, exit_on_vol=True.
Les autres valeurs ne servent qu'au test de robustesse (plateau, pas optimum).
"""
import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252

DEFAULT_PARAMS = {
    'dd_threshold': 0.25,
    'high_window': 252,
    'vol_short': 20,
    'vol_long': 60,
    'window_days': 189,
    'leverage': 1.4,
    'exit_on_vol': True,
}

NORMAL, ARMED, LEVER, COOLDOWN = 'normal', 'armed', 'lever', 'cooldown'


def regime_params(overrides=None):
    """Paramètres du détecteur = spec pré-enregistrée + surcharges éventuelles."""
    p = dict(DEFAULT_PARAMS)
    for k, v in (overrides or {}).items():
        if k in p and v is not None:
            p[k] = v
    return p


def compute_crash_regime(close, **overrides):
    """
    Machine à états jour par jour sur une série de clôtures (index = dates).

    Retourne un DataFrame indexé comme `close` (NaN retirés) avec :
      drawdown, vol_short, vol_long, state, lever (bool), episode (int, 0 = aucun).
    `lever[t]` = levier autorisé selon les données jusqu'à la clôture de t.
    """
    p = regime_params(overrides)
    s = pd.Series(close).dropna().astype(float).sort_index()
    s = s[~s.index.duplicated(keep='last')]
    if s.empty:
        return pd.DataFrame(columns=['drawdown', 'vol_short', 'vol_long', 'state',
                                     'lever', 'episode'])

    peak = s.rolling(int(p['high_window']), min_periods=1).max()
    dd = s / peak - 1.0
    ret = s.pct_change()
    ann = math.sqrt(TRADING_DAYS)
    vs = ret.rolling(int(p['vol_short']), min_periods=int(p['vol_short'])).std(ddof=0) * ann
    vl = ret.rolling(int(p['vol_long']), min_periods=int(p['vol_long'])).std(ddof=0) * ann

    thr = -abs(float(p['dd_threshold']))
    win = int(p['window_days'])
    exit_on_vol = bool(p['exit_on_vol'])

    states, levers, episodes = [], [], []
    state, used, episode = NORMAL, 0, 0
    for d, v_s, v_l in zip(dd.values, vs.values, vl.values):
        calm = (not np.isnan(v_s)) and (not np.isnan(v_l)) and v_s < v_l
        stressed = (not np.isnan(v_s)) and (not np.isnan(v_l)) and v_s > v_l
        new_high = d >= -1e-12

        if state == NORMAL:
            if d <= thr:
                state, episode, used = ARMED, episode + 1, 0
        elif state == LEVER:
            used += 1                      # la séance précédente était sous levier
            if used >= win:
                state = COOLDOWN
            elif exit_on_vol and stressed:
                state = ARMED
        if state in (ARMED, COOLDOWN) and new_high:
            state = NORMAL                 # fin de l'épisode
        if state == ARMED and calm and used < win:
            state = LEVER

        states.append(state)
        levers.append(state == LEVER)
        episodes.append(episode if state != NORMAL else 0)

    return pd.DataFrame({
        'drawdown': dd, 'vol_short': vs, 'vol_long': vl,
        'state': states, 'lever': levers, 'episode': episodes,
    }, index=s.index)


def lever_series(close, **overrides):
    """Raccourci : série booléenne `lever` (index = dates)."""
    reg = compute_crash_regime(close, **overrides)
    return reg['lever'] if not reg.empty else pd.Series(dtype=bool)


def lever_at(lever, as_of):
    """État `lever` connu à la clôture de `as_of` (dernier point ≤ as_of), False sinon."""
    if lever is None or len(lever) == 0:
        return False
    sub = lever.loc[:as_of]
    return bool(sub.iloc[-1]) if len(sub) else False


def episodes_summary(close, **overrides):
    """
    Une ligne par épisode détecté : dates d'armement, de levier et de fin,
    drawdown à l'armement et au plus bas, nombre de séances sous levier.
    Sert à l'audit du détecteur (étude d'événements).
    """
    reg = compute_crash_regime(close, **overrides)
    out = []
    if reg.empty:
        return out
    s = pd.Series(close).dropna().astype(float).sort_index()
    for ep, g in reg[reg['episode'] > 0].groupby('episode'):
        lev = g[g['lever']]
        out.append({
            'episode': int(ep),
            'armed': g.index[0].strftime('%Y-%m-%d'),
            'dd_at_arm': round(float(g['drawdown'].iloc[0]), 4),
            'lever_on': lev.index[0].strftime('%Y-%m-%d') if len(lev) else None,
            'lever_off': lev.index[-1].strftime('%Y-%m-%d') if len(lev) else None,
            'lever_days': int(len(lev)),
            'end': g.index[-1].strftime('%Y-%m-%d'),
            'min_dd': round(float(g['drawdown'].min()), 4),
            'trough': g['drawdown'].idxmin().strftime('%Y-%m-%d'),
            'px_lever_on': float(s.loc[lev.index[0]]) if len(lev) else None,
        })
    return out
