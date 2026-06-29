# Stratégie Long — Momentum 12-1

## 1. Principe

La stratégie exploite l'**anomalie de momentum cross-sectionnel** documentée par Jegadeesh & Titman (1993) : les actions ayant le mieux performé sur les 12 derniers mois (en excluant le dernier mois) continuent statistiquement de surperformer à court terme.

Elle est implémentée en **gestion long-only** avec rééquilibrage mensuel, sur un univers large cap US.

---

## 2. Univers d'investissement

**Source** : table `IndexConstituent` (peuplée chaque nuit par `price_data_service.py` via Wikipedia + yfinance).

**Composition** : S&P 500 ∪ Nasdaq-100 ≈ 600 tickers dédupliqués (les constituants actuels des deux indices).

**Cascade de repli** (si la collecte nocturne n'a jamais tourné) :
1. `IndexConstituent` actifs — source normale
2. `PanelAction` actif — ancien panel manuel (legacy)
3. `DEFAULT_PANEL` dans `config.py` — 51 tickers codés en dur (repli ultime)

> **Biais de survivance résiduel assumé** : l'univers candidat correspond aux constituants *actuels* des indices. Les sociétés radiées ou sorties historiquement ne figurent pas dans les backtests. Ce biais est documenté et affiché dans l'UI.

---

## 3. Données de prix

### Source primaire : `MonthlyPriceBar` (yfinance, 20 ans)

Barres mensuelles collectées la nuit par `job_collect_prices` (00h00 Paris) pour l'intégralité de l'univers. Chargées en une seule requête SQL bulk (`_bulk_load_monthly`) pour éviter les timeouts sur ~600 tickers.

### Cascade de repli par ticker :
1. `MonthlyPriceBar` — si ≥ 13 barres disponibles
2. `MarketPriceBar` daily resampleé en mensuel (`resample('ME').last()`)
3. Tiingo API mensuel direct — si `allow_network=True` (jamais lors du calcul live sur l'univers complet)

Le calcul live sur l'univers complet s'exécute en **DB-only** (`allow_network=False`) pour garantir la réactivité sans timeout. Les tickers absents de la base sont signalés en erreur.

---

## 4. Formule de momentum

```
Momentum = (Prix[T-1m] - Prix[T-12m]) / Prix[T-12m] × 100
```

**Définition des bornes** :
- `T-12m` = `adjClose` de la barre d'index `[-13]` (avant-dernier mois de la fenêtre de 13 barres)
- `T-1m`  = `adjClose` de la barre d'index `[-2]` (avant-dernière barre disponible)
- `T`     = `adjClose` de la barre d'index `[-1]` (mois le plus récent — **exclu du calcul**)

**Pourquoi exclure le mois T ?**  
Le mois en cours est souvent incomplet au moment du calcul. De plus, le mois `T-1` → `T` présente un effet de retour à la moyenne documenté (Jegadeesh, 1990) : inclure ce mois dégraderait le signal. La formule correspond au **momentum 12-1 académique canonique**.

**Période d'analyse** calculée par `calculer_periode_analyse(date_calcul)` :
- `date_fin` = dernier jour du mois précédent (ex. calcul en juin → `date_fin = 31 mai`)
- `date_debut` = `date_fin - 13 mois`

**Métrique complémentaire** : `perf_recent_1m` = perf du mois exclu (`T-1` → `T`), affiché à titre informatif comme signal de mean-reversion.

---

## 5. Classement et sélection

1. Tous les titres de l'univers couverts en base sont calculés.
2. Classement par momentum **décroissant** — le rang 1 est le meilleur momentum.
3. **Top N** sélectionnés (`nb_top`, réglable dans Settings, défaut : 5).
4. Parmi le top N, seuls les titres avec `momentum > 0` reçoivent le signal `"Investir"`. Un titre top-N mais à momentum négatif reçoit le signal `"Cash"` (rester en liquidités sur cette fraction).
5. Les rangs > N reçoivent le signal `"Sortir"`.

`nb_top` est configurable depuis l'interface Settings et lu depuis la table `Settings` (clé `nb_top`).

---

## 6. Pondération — Inverse-Volatilité (mode par défaut)

```
vol_i          = std(rendements_mensuels_12m_i) × √12   [annualisée]
poids_brut_i   = 1 / vol_i
allocation_i   = poids_brut_i / Σ(poids_bruts) × 100%
```

La somme des allocations est normalisée à **100%** (pas de levier). Les titres les moins volatils reçoivent une allocation plus importante. Une correction d'arrondi est appliquée sur le premier ticker pour que la somme soit exactement 100.

**Fallback** : si moins de 3 rendements mensuels sont disponibles pour un titre, `vol = 20%` (constante `VOL_DEFAULT`).

---

## 7. Pondération — Volatility Scaling (mode optionnel)

> Référence : Barroso & Santa-Clara, *"Momentum Has Its Moments"*, JFE 2015, footnote 13.

Activé via le paramètre `vol_scaling = True` (Settings : `vol_scaling_enabled`).

```
vol_réalisée_i  = √(252 × moyenne(r²_t) sur les 126 dernières sessions)
poids_i         = σ_cible / vol_réalisée_i
allocation_i    = poids_i × 100%
```

- `σ_cible` : volatilité cible annualisée, défaut `12%` (`vol_target`, Settings)
- L'exposition brute totale peut dépasser 100% (levier implicite)
- Plafond strict : `Σ allocations ≤ max_exposure_pct` (`250%` par défaut) — si dépassé, toutes les allocations sont rabattues proportionnellement

**Source de la vol réalisée** : cascade `_fetch_daily_adjusted` (DB → IBKR → Tiingo), 200 jours demandés, 126 sessions retenues.

---

## 8. Filtre portefeuille anti-krach — Layer 2 (optionnel)

> Référence : Barroso & Santa-Clara (2015), équations 5-6.

Activé via `portfolio_filter = True` (Settings : `portfolio_filter_enabled`).

```
σ̂_panier = vol_réalisée_126j du panier pondéré (intègre les corrélations entre titres)
f         = min(1, σ_seuil / σ̂_panier)
allocation_i_finale = allocation_i × f
```

- `σ_seuil` : seuil de vol annualisée du panier, défaut `20%` (Settings : `portfolio_vol_threshold`)
- `f ≤ 1` : le filtre ne peut que **réduire** l'exposition, jamais l'augmenter
- Si le panier est calme (`σ̂_panier ≤ σ_seuil`), `f = 1` → aucun impact

Ce filtre est indépendant du vol scaling (Layer 1). Les deux peuvent être combinés : le Layer 1 construit les poids par actif, le Layer 2 applique un frein global.

---

## 9. Indicateur de régime de marché

Calculé par `get_market_regime()` — **informatif uniquement**, n'influe pas sur les allocations.

```
SMA200 = moyenne(adjClose_SPY, 200 derniers jours)
régime = BULL  si SPY > SMA200
régime = BEAR  sinon
```

Affiché dans l'UI pour alerter que le momentum 12-1 est historiquement moins fiable en régime BEAR (drawdowns plus sévères, momentum crashes plus fréquents — Daniel & Moskowitz 2016).

---

## 10. Rééquilibrage

**Fréquence** : mensuelle — le premier du mois (`job_rebalance_reminder`, 8h00 ET).

**Déclenchement manuel** : bouton "Calculer" dans l'UI → appel `/api/momentum/calculate`.

**Persistance** : chaque calcul crée un `RecommendationHistory` + jusqu'à `max(50, nb_top)` lignes `RecommendationDetail`. Les ~550 titres "Sortir" hors top-50 ne sont pas persistés (économie de base).

---

## 11. Export TWS (Interactive Brokers)

Deux fonctions d'export génèrent un fichier `tws_rebalance.csv` au format TWS Rebalance :

```
DES,<TICKER>,STK,SMART/AMEX,,,,,,<PCT>
```

`<PCT>` est le pourcentage cible du portefeuille (ex. `19.500000` = 19,5%).

**`exportTWSFromReco()`** : export direct depuis les recommandations, sans tenir compte du portefeuille actuel. Applique un `CASH_BUFFER_PCT = 1%` par ticker (marge de sécurité).

**`exportTWS()`** : export depuis le calculateur de rééquilibrage (après import du portefeuille IBKR). Inclut :
- Les positions cibles avec leur pourcentage calculé (après cash buffer)
- Les positions actuelles dont la cible est **0%** — pour que TWS les liquide lors du rééquilibrage

---

## 12. Backtest

Le backtest (`backtest_service.py`, worker `_bt_worker.py`) rejoue la stratégie sur l'historique avec **exactement les mêmes formules** que le calcul live (`generer_recommandations`, `calculer_momentum_12_1`), y compris vol scaling et filtre portefeuille si activés dans les Settings.

**Spécificités du moteur** :
- Simulation **jour par jour** (non mensuelle) pour capter les coûts intraday et les appels de marge
- **Point-in-time** : un titre n'entre dans l'univers que s'il a ≥ 13 mois d'historique à la date simulée
- **Coûts de transaction** : commission + spread en bps appliqués sur le turnover mensuel
- **Intérêts d'emprunt** sur la partie financée si exposition > 100% (mode vol scaling)
- **Rémunération du cash** oisif (configurable, défaut 0%)
- **Appels de marge** : si `equity < taux_maintenance × exposition_brute` → liquidation forcée avec coûts
- **DCA** optionnel : apports périodiques, avec séparation TWR (performance stratégie) / MWR/XIRR (expérience investisseur)
- Métriques : Sharpe, Sortino, Calmar, max drawdown, VaR/CVaR, Omega, Ulcer Index, durées de drawdown, levier moyen, coûts totaux payés — via `quantstats` + calculs maison

Le worker s'exécute en sous-processus séparé pour ne pas bloquer Gunicorn (1 worker) pendant le calcul.

---

## 13. Références académiques

| Référence | Contribution |
|---|---|
| Jegadeesh & Titman (1993), *JF* | Anomalie momentum cross-sectionnel, formule 12-1 |
| Jegadeesh (1990), *JF* | Mean reversion à 1 mois → justifie l'exclusion de T |
| Carhart (1997), *JF* | Facteur momentum dans les modèles à 4 facteurs |
| Asness, Moskowitz & Pedersen (2013), *JF* | Momentum universel (classes d'actifs, pays) |
| Barroso & Santa-Clara (2015), *JFE* | Vol scaling, réduction des momentum crashes |
| Daniel & Moskowitz (2016), *JFE* | Momentum crashes, bear market indicator |
| Faber (2007), *JAIM* | SMA10m comme filtre de régime de marché |
| Antonacci (2012/2014) | Dual momentum, absolute momentum overlay |
