# -*- coding: utf-8 -*-
"""
Scoring de la stratégie SHORT multi-facteurs (source de vérité unique)
======================================================================
Fonctions **pures** implémentant le système de score à 4 couches décrit dans
`docs/strategie_short_v2.md` (v3.0). Ce module est partagé par :

  - le backtest directionnel (`backtest_short_directional.py`), qui l'appelle
    point-in-time sur des données historiques ;
  - le futur service de signal live (`short_signal_service.py`).

Principe : ces fonctions ne font AUCUN accès base/réseau. Les appelants chargent
les données (prix, accruals, short interest) et passent des valeurs déjà calculées.
Cela garantit que backtest et live appliquent exactement les mêmes règles.

⚠️ Tous les seuils numériques sont des **hypothèses à calibrer** (voir la mise en
garde méthodologique de la stratégie), pas des vérités démontrées.
"""

from dataclasses import dataclass, field
from typing import Optional

# =============================================================================
# CONSTANTES (hypothèses de départ — à calibrer par backtest)
# =============================================================================

# Couche 1 — Momentum
MOM_LOOKBACK = 63          # jours de bourse (fenêtre longue)
MOM_SKIP = 5               # jours exclus en fin de fenêtre (bruit récent)
MOM_STRONG = -0.20         # perf_63_5 ≤ -20% → 2 pts
MOM_WEAK = -0.15           # -20% < perf_63_5 ≤ -15% → 1 pt ; > -15% → STOP
ALPHA_SECTOR_THRESHOLD = -0.10   # sous-performance sectorielle ≤ -10pp → +1 pt
SMA_FAST = 50
SMA_SLOW = 200

# Couche 2 — Accruals (percentile cross-sectionnel préféré)
ACCRUALS_PCTL_STRONG = 95.0   # top 5% → 2 pts
ACCRUALS_PCTL_WEAK = 80.0     # 80-95 → 1 pt
ACCRUALS_ABS_STRONG = 0.10    # repli seuils absolus si pas d'historique cross-sectionnel
ACCRUALS_ABS_WEAK = 0.05
FILING_LAG_DAYS = 45          # délai estimé période comptable → disponibilité (anti-look-ahead)

# Couche 3 — Short interest (percentile + trend)
SIR_PCTL_STRONG = 80.0
SIR_PCTL_MID = 50.0
SQUEEZE_DTC_THRESHOLD = 20.0  # days-to-cover absolu → PUT SPREAD forcé

# Couche 4 — Catalyseur earnings
EARNINGS_MISS_RATIO = 0.10    # EPS réel < consensus × (1-0.10)
EARNINGS_WINDOW_DAYS = 10

# Timing d'entrée (Partie 6 de la stratégie) — filtres pour éviter de shorter
# un titre survendu qui va rebondir techniquement.
RSI_PERIOD = 14
RSI_MIN = 35.0                # < 35 = survendu → risque de rebond
RSI_MAX = 55.0               # > 55 = déjà en rebond
TIMING_REBOUND_MAX = 0.05    # perf_5_0 ≤ +5% (pas de rebond récent marqué)
TIMING_CAPITULATION_MIN = -0.10  # perf_5_0 ≥ -10% (pas en capitulation extrême)

# Décision (score composite → instrument / taille)
SCORE_MIN_TRADE = 3
SKEW_PUT_MAX = 1.30           # skew ≤ 1.3 → PUT seul envisageable (live uniquement)

# Horizons de mesure directionnelle (backtest)
FORWARD_HORIZONS = [20, 30, 45, 60]

# Seuils de bucket de score (backtest : agrégation)
SCORE_BUCKETS = [(0, 2), (3, 3), (4, 4), (5, 5), (6, 7)]


# =============================================================================
# HELPERS NUMÉRIQUES
# =============================================================================
def percentile_rank(value, population):
    """
    Percentile (0-100) de `value` dans `population` (liste de floats).
    Renvoie None si value None ou population insuffisante. Convention : % de la
    population ≤ value (un accruals/SIR élevé → percentile élevé).
    """
    if value is None:
        return None
    vals = [v for v in population if v is not None]
    if len(vals) < 5:
        return None
    below = sum(1 for v in vals if v <= value)
    return 100.0 * below / len(vals)


def perf_window(prices, lookback=MOM_LOOKBACK, skip=MOM_SKIP):
    """
    Perf_63_5 = (P[t-skip] / P[t-lookback]) - 1 à partir d'une séquence de prix
    ordonnée du plus ancien au plus récent (le dernier = date d'évaluation).
    Renvoie None si données insuffisantes.
    """
    n = len(prices)
    if n < lookback + skip + 1:
        return None
    p_start = prices[-(lookback + skip + 1)]
    p_end = prices[-(skip + 1)]
    if p_start is None or p_end is None or p_start <= 0:
        return None
    return (p_end / p_start) - 1.0


def perf_recent(prices, skip=MOM_SKIP):
    """Perf des `skip` derniers jours = (P[t] / P[t-skip]) - 1. Pour les filtres de timing."""
    n = len(prices)
    if n < skip + 1:
        return None
    p0 = prices[-(skip + 1)]
    p1 = prices[-1]
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return (p1 / p0) - 1.0


def sma(prices, window):
    """Moyenne mobile simple sur les `window` derniers prix. None si insuffisant."""
    if len(prices) < window:
        return None
    tail = prices[-window:]
    if any(p is None for p in tail):
        tail = [p for p in tail if p is not None]
        if len(tail) < window // 2:
            return None
    return sum(tail) / len(tail)


def rsi(prices, period=RSI_PERIOD):
    """
    RSI de Wilder sur `period` (moyenne simple des gains/pertes). Renvoie 0-100,
    None si données insuffisantes. Sur une séquence ordonnée (ancien → récent).
    """
    if len(prices) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(len(prices) - period, len(prices)):
        delta = prices[i] - prices[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def entry_timing_ok(prices):
    """
    Filtres de timing d'entrée (Partie 6). Renvoie (ok: bool, details: dict).
    Conditions : RSI(14) ∈ [35,55], perf_5_0 ≤ +5% (pas de rebond),
    perf_5_0 ≥ -10% (pas de capitulation). Le filtre gap n'est pas appliqué ici.
    """
    r = rsi(prices, RSI_PERIOD)
    p5 = perf_recent(prices, MOM_SKIP)
    details = {'rsi': r, 'perf_5_0': p5}
    if r is None or p5 is None:
        return False, details
    ok = (RSI_MIN <= r <= RSI_MAX
          and p5 <= TIMING_REBOUND_MAX
          and p5 >= TIMING_CAPITULATION_MIN)
    return ok, details


# =============================================================================
# COUCHES DE SCORE (fonctions pures → points)
# =============================================================================
def score_momentum(perf_63_5, alpha_sector=None):
    """Couche 1 (0-3). Renvoie (points, stop) — stop=True si momentum insuffisant."""
    if perf_63_5 is None:
        return 0, True
    if perf_63_5 > MOM_WEAK:
        return 0, True  # > -15% → pas de signal, on arrête
    pts = 2 if perf_63_5 <= MOM_STRONG else 1
    if alpha_sector is not None and alpha_sector <= ALPHA_SECTOR_THRESHOLD:
        pts += 1
    return pts, False


def score_accruals(accruals_ratio, accruals_percentile=None):
    """Couche 2 (0-2). Percentile prioritaire ; repli sur seuils absolus."""
    if accruals_percentile is not None:
        if accruals_percentile >= ACCRUALS_PCTL_STRONG:
            return 2
        if accruals_percentile >= ACCRUALS_PCTL_WEAK:
            return 1
        return 0
    if accruals_ratio is not None:
        if accruals_ratio >= ACCRUALS_ABS_STRONG:
            return 2
        if accruals_ratio >= ACCRUALS_ABS_WEAK:
            return 1
    return 0


def score_short_interest(sir_percentile=None, sir_trend=None):
    """
    Couche 3 partielle (0-1 en phase 1 : SIR seul, sans P/C options).
    Percentile + trend. Renvoie un float (0, 0.5, 1).
    """
    if sir_percentile is None:
        return 0.0
    trend_up = (sir_trend == 'up')
    if sir_percentile >= SIR_PCTL_STRONG and trend_up:
        return 1.0
    if sir_percentile >= SIR_PCTL_MID and trend_up:
        return 0.5
    return 0.0


def score_earnings(earnings_miss_recent=False):
    """Couche 4 (0-1, bonus)."""
    return 1 if earnings_miss_recent else 0


# =============================================================================
# ENTRÉES / RÉSULTAT DU SCORE COMPOSITE
# =============================================================================
@dataclass
class ScoreInputs:
    """Valeurs déjà calculées par l'appelant (backtest ou service live)."""
    perf_63_5: Optional[float] = None
    alpha_sector: Optional[float] = None
    price: Optional[float] = None
    sma50: Optional[float] = None
    sma200: Optional[float] = None
    accruals_ratio: Optional[float] = None
    accruals_percentile: Optional[float] = None
    sir_percentile: Optional[float] = None
    sir_trend: Optional[str] = None
    sir_days_to_cover: Optional[float] = None
    earnings_miss_recent: bool = False


@dataclass
class ScoreResult:
    total: float = 0.0
    stop: bool = False
    points: dict = field(default_factory=dict)
    death_cross: bool = False
    uptrend_regime: bool = False      # price > sma200 → réduire la taille
    squeeze_risk: bool = False        # dtc > 20 → forcer PUT SPREAD
    bucket: Optional[str] = None


def compute_score(inp: ScoreInputs) -> ScoreResult:
    """
    Score composite complet à partir d'entrées pré-calculées. Applique la règle
    d'arrêt momentum (si perf_63_5 > -15%, total=0, stop=True).
    """
    res = ScoreResult()

    mom_pts, stop = score_momentum(inp.perf_63_5, inp.alpha_sector)
    res.points['momentum'] = mom_pts
    if stop:
        res.stop = True
        res.total = 0.0
        res.bucket = _bucket_label(0)
        return res

    acc_pts = score_accruals(inp.accruals_ratio, inp.accruals_percentile)
    sir_pts = score_short_interest(inp.sir_percentile, inp.sir_trend)
    earn_pts = score_earnings(inp.earnings_miss_recent)

    res.points['accruals'] = acc_pts
    res.points['short_interest'] = sir_pts
    res.points['earnings'] = earn_pts
    res.total = mom_pts + acc_pts + sir_pts + earn_pts

    # Signaux structurels (n'ajoutent pas de point mais influent sur la décision)
    if inp.price is not None and inp.sma50 is not None and inp.sma200 is not None:
        res.death_cross = (inp.price < inp.sma50 < inp.sma200)
        res.uptrend_regime = (inp.price > inp.sma200)
    res.squeeze_risk = (inp.sir_days_to_cover is not None
                        and inp.sir_days_to_cover > SQUEEZE_DTC_THRESHOLD)
    res.bucket = _bucket_label(res.total)
    return res


# =============================================================================
# DÉCISION D'INSTRUMENT (live — hors backtest directionnel)
# =============================================================================
def decide_instrument(score_total, perf_63_5, skew_ratio=None, squeeze_risk=False,
                      uptrend_regime=False):
    """
    Traduit le score en décision d'instrument + facteur de taille.
    Renvoie dict {trade, instrument, size_factor}. Utilisé en live uniquement
    (le backtest directionnel ne price pas d'options).
    """
    if score_total < SCORE_MIN_TRADE:
        return {'trade': False, 'instrument': None, 'size_factor': 0.0}

    # Taille de base par palier de score
    if score_total >= 5:
        size = 1.0
    elif score_total >= 4:
        size = 0.75
    else:
        size = 0.50

    # Choix PUT vs PUT SPREAD
    force_spread = (
        squeeze_risk
        or (perf_63_5 is not None and perf_63_5 < MOM_STRONG)   # < -20% → spread par défaut
        or (skew_ratio is not None and skew_ratio > SKEW_PUT_MAX)
        or score_total < 5
    )
    instrument = 'PUT_SPREAD' if force_spread else 'PUT'

    # Régime haussier long terme → réduire la taille de moitié
    if uptrend_regime:
        size *= 0.5

    return {'trade': True, 'instrument': instrument, 'size_factor': round(size, 3)}


# =============================================================================
# UTILITAIRES DE BUCKET (agrégation backtest)
# =============================================================================
def _bucket_label(total):
    for lo, hi in SCORE_BUCKETS:
        if lo <= total <= hi:
            return f"{lo}-{hi}" if lo != hi else f"{lo}"
    return str(total)
