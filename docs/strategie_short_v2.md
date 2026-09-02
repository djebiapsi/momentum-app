# STRATÉGIE SHORT — MOMENTUM + CONFIRMATION MULTI-FACTEURS
## Version 3.0 — Fondements académiques, révision critique et protocole de validation

---

## PHILOSOPHIE GÉNÉRALE

Cette stratégie repose sur une vérité empirique bien documentée dans la littérature financière :
**les losers continuent de perdre, et ce mouvement est amplifié lorsque plusieurs signaux indépendants convergent.**

L'objectif n'est pas de shorter le maximum de titres, mais de shorter uniquement ceux où :
1. Le momentum baissier est fort et récent
2. Les fondamentaux confirment une dégradation réelle
3. Le "smart money" est déjà positionné dans le même sens
4. Le timing d'entrée minimise le risque de rebond

L'instrument privilégié est l'**option PUT ou PUT SPREAD** : la perte maximale est la prime payée, ce qui est non-négociable dans une stratégie short où les pertes peuvent théoriquement être illimitées sur un sous-jacent nu.

---

## MISE EN GARDE MÉTHODOLOGIQUE — Facteurs documentés vs Paramètres hypothétiques

Cette distinction est fondamentale et doit être lue avant tout le reste.

**Ce que la littérature académique valide :**

| Famille de facteurs | Statut académique |
|---------------------|-------------------|
| Le momentum de prix persiste à 3-12 mois | ✅ Très robuste (Jegadeesh & Titman 1993, 2001) |
| L'anomalie des accruals existe | ✅ Robuste (Sloan 1996, répliqué de nombreuses fois) |
| Le short interest prédit les rendements | ✅ Documenté (Dechow et al. 2001, répliqué internationalement) |
| Le volume d'options contient de l'information | ✅ Documenté (Pan & Poteshman 2006) |
| Le post-earnings drift existe | ✅ Très robuste (Bernard & Thomas 1989) |

**Ce que la littérature académique ne valide PAS :**

> Les seuils précis suivants **ne sont pas démontrés** — ce sont des **hypothèses de travail** à calibrer par le backtest :

```
-15% / -20%  (fenêtre momentum)      RSI 35 / 55 / 65
63 jours / 5 jours (fenêtre)         gap 3%
SMA50 / SMA200                       rebond +5% / capitulation -10%
accruals +5% / +10%                  Skew 1.3 / 1.6
SIR 5 / 8 / 20 jours                 DTE 45-60 jours
P/C 1.0 / 1.5                        delta -0.30 / -0.40
earnings -10%                         TP +70% / +100%
```

**Règle de lecture de ce document :** chaque fois qu'un seuil numérique apparaît, il est à lire comme *"valeur de départ à tester"*, pas comme *"valeur optimale démontrée"*. Le backtest décidera.

---

## PARTIE 1 — UNIVERS D'INVESTISSEMENT

### 1.1 Périmètre de recherche

L'univers de départ est **SP500 + NDX100** (~600 titres), identique à la stratégie long. Cela présente plusieurs avantages :
- Données disponibles et fiables
- Liquidité suffisante pour des options avec bid-ask serré
- Cohérence avec la stratégie long (même base de données)

### 1.2 Filtres de liquidité obligatoires

Ces filtres sont non-négociables. Un titre qui ne les respecte pas est exclu automatiquement, sans exception.

| Critère | Seuil | Raison |
|---------|-------|--------|
| Market Cap | ≥ 2 Mds $ | Liquidité options, résistance aux manipulations |
| Prix | ≥ 10 $ | Évite les penny stocks et les distorsions d'options |
| Avg Volume 30j | ≥ 500k actions/jour | Exécution des options sans impact prix |
| Options disponibles | Oui | Obligatoire pour notre instrument |
| Bid-ask spread option | ≤ 5% du mid-price | Un spread trop large détruit l'EV du trade |

### 1.3 Exclusions systématiques

Ces catégories sont exclues même si tous les signaux sont présents :

- **Biotech / Pharma pré-FDA** : un communiqué de presse peut faire +100% en une nuit
- **Earnings dans ≤ 10 jours** : le risque binaire de l'earnings détruit la prédictibilité du signal
- **Actions sous OPA annoncée** : le prix converge vers le prix d'offre, le signal est caduque
- **Spinoffs récents (< 6 mois)** : pas d'historique de prix fiable pour le calcul du momentum

---

## PARTIE 2 — SYSTÈME DE SIGNAL COMPOSITE (SCORING)

La grande faiblesse des stratégies short basées sur un seul signal (ex : momentum seul) est le taux de faux positifs : un titre peut baisser fortement pour des raisons temporaires (liquidation forcée d'un fonds, événement ponctuel) et rebondir violemment.

La solution est un **système de score à 4 couches indépendantes**. Chaque couche mesure quelque chose de différent. La convergence de plusieurs couches est le signal robuste.

> **Attention au data mining.** Ce système comporte environ 25 paramètres numériques. Même si chacun paraît rationnel, leur combinaison crée un risque élevé d'overfitting : on peut construire une stratégie qui aurait été excellente sur l'historique simplement parce qu'on a essayé suffisamment de seuils. La validation out-of-sample (Partie 13) est obligatoire avant toute mise en réel.

### Score total : 0 à 7 points (valeurs initiales — à calibrer par backtest)
- **Score ≥ 5 : signal fort** → PUT seul ou PUT SPREAD selon le skew (voir Partie 5)
- **Score 3-4 : signal modéré** → PUT SPREAD, taille réduite
- **Score ≤ 2 : pas de trade** → signal insuffisant, passer au suivant

**Objectif post-backtest :** transformer ce score ordinal en probabilité calibrée (ex : score 5 → P(baisse -8% sur 30j) = 63%). Voir Partie 13.

---

### COUCHE 1 — MOMENTUM DE PRIX (0 à 3 points)

*Fondement : Jegadeesh & Titman (2001), Israel & Moskowitz (2013)*

Le momentum est le signal déclencheur. Il doit être présent pour qu'il y ait un trade. Sans momentum, on ne regarde pas les autres couches.

#### 1.1 Signal absolu : fenêtre 63-5 jours

```
Perf_63_5 = (Prix[J-5] / Prix[J-63]) - 1
```

**Seuils de départ (hypothèses à calibrer) :**
- **2 points** si Perf_63_5 ≤ -20%
- **1 point** si Perf_63_5 entre -15% et -20%
- **0 point** si Perf_63_5 > -15% → STOP

La fenêtre 63-5 (et non 63-0) exclut les 5 derniers jours pour éviter de shorter après un pic de panique.

**Test de monotonicity obligatoire au backtest** : vérifier que le résultat est monotone sur les zones (-10/-15%, -15/-20%, -20/-25%, -25/-30%). Si le rendement n'est pas croissant avec l'intensité de la baisse, le seuil n'a pas de sens et doit être revu.

#### 1.2 Signal relatif : momentum vs marché et secteur (nouveau — hypothèse forte)

Un titre qui baisse de -18% dans un marché qui baisse de -17% n'est pas un short candidat. Un titre qui baisse de -18% dans un secteur qui monte de +2% est un signal de dégradation idiosyncratique beaucoup plus intéressant.

```
Alpha_marché  = Perf_63_5(titre) - Perf_63_5(SPY)
Alpha_secteur = Perf_63_5(titre) - Perf_63_5(ETF secteur GICS)
```

- **1 point bonus** si `Alpha_secteur ≤ -10%` (titre sous-performe son secteur de plus de 10pp sur 63 jours)

Le profil idéal :
```
Titre       : -20%
Secteur ETF : -4%
SPY         : +2%
Alpha       : -22%   → dégradation idiosyncratique, pas un effet marché
```

#### 1.3 Régime de tendance (filtre, ne rapporte plus de point)

Le Death Cross (`Prix < SMA50 < SMA200`) est fortement corrélé à `Perf_63_5` — ce ne sont pas deux signaux indépendants. Utiliser les deux comme sources de points revient à compter le même phénomène deux fois.

Le Death Cross devient donc un **filtre de régime** :
- `Prix > SMA200` (uptrend long terme) → réduire la taille de 50%, même si le score est élevé
- `Prix < SMA200` (downtrend long terme) → taille normale
- `SMA200 en baisse depuis 2+ mois` → signal de tendance structurelle, bonus informatif (pas de point)

**Score Couche 1 : 0, 1, 2 ou 3 points (2 pts momentum absolu + 1 pt alpha relatif)**

---

### COUCHE 2 — CONFIRMATION FONDAMENTALE : LES ACCRUALS (0 à 2 points)

*Fondement : Sloan (1996) — "Do Stock Prices Fully Reflect Information in Accruals and Cash Flows?"*

#### Le concept

Les **accruals comptables** mesurent l'écart entre les bénéfices affichés et les liquidités réellement générées. Une entreprise peut afficher des profits élevés tout en ayant un cash flow opérationnel faible ou négatif — c'est le signe que ses bénéfices sont "gonflés" par des éléments non-cash (créances clients agressives, stocks surévalués, amortissements optimistes).

La découverte de Sloan est empiriquement robuste : **les entreprises avec des accruals élevés sous-performent massivement les 12 mois suivants**, car le marché met du temps à "voir" que les bénéfices ne se convertissent pas en cash.

#### Calcul

```
Accruals Ratio = (Net Income - Cash Flow from Operations) / Total Assets
```

**Problème des seuils absolus (+5%, +10%) :** un accruals ratio de +10% peut être parfaitement normal dans un secteur à forte intensité capitalistique ou en période d'acquisition, et anormal dans un secteur de services. Un seuil universel est donc une simplification grossière.

**Approche cible : percentile cross-sectionnel**

À chaque cycle d'évaluation, calculer le percentile de chaque titre dans l'univers :

```
Percentile_accruals(titre) = rang(accruals ratio) / nb_titres_univers × 100
```

- **Percentile > 95** (accruals dans les 5% les plus élevés de l'univers) → **2 points**
- **Percentile 80-95** → **1 point**
- **Percentile < 80** → **0 point**

**Approche secondaire (avant d'avoir l'historique pour le percentile) :** comparer le ratio du titre à sa propre médiane historique sur 8 trimestres. Un accruals ratio +2 écarts-types au-dessus de sa propre moyenne est plus informatif qu'un seuil absolu de +10%.

**Seuils absolus (+5%, +10%) :** valeurs de départ utilisables uniquement si les données cross-sectionnelles ne sont pas encore disponibles. À remplacer par le percentile dès que l'historique est suffisant.

#### Données nécessaires

Ces données sont disponibles dans les rapports financiers trimestriels (10-Q/10-K).

**Source retenue : `yfinance` (gratuit, intégré à la collecte quotidienne existante)**

Les trois grandeurs nécessaires sont disponibles via :
```python
ticker = yf.Ticker("XYZ")
income_stmt  = ticker.income_stmt          # contient "Net Income"
cash_flow    = ticker.cashflow             # contient "Operating Cash Flow"
balance_sheet = ticker.balance_sheet       # contient "Total Assets"
```

**Collecte incrémentale** : le job `job_collect_prices` (qui tourne chaque nuit) sera étendu pour vérifier, sur chaque ticker du panel, si de nouvelles données fondamentales sont disponibles depuis la dernière collecte (date de publication du dernier 10-Q/10-K). Si oui, les trois grandeurs sont récupérées et stockées en base. Les données n'étant mises à jour que 4 fois par an (aux earnings), la charge est négligeable.

Le stockage en base permet d'accéder aux accruals sans appel réseau au moment du scoring — seule la collecte nocturne interroge yfinance.

#### Interprétation qualitative

Un accruals ratio élevé combiné à un momentum baissier est un signal particulièrement fort : cela signifie que le marché commence à "voir" que les bénéfices étaient gonflés et re-price le titre à la baisse. La correction peut être durable.

**Score Couche 2 : 0, 1 ou 2 points**

---

### COUCHE 3 — SMART MONEY : SHORT INTEREST ET PUT/CALL RATIO (0 à 2 points)

*Fondement : Dechow et al. (2001), Pan & Poteshman (2006)*

#### 3.1 Short Interest Ratio (SIR)

```
SIR = Actions Vendues à Découvert / Volume Moyen Journalier
```

Le SIR mesure combien de jours de trading seraient nécessaires pour que les shorts couvrent leurs positions.

**Erreur conceptuelle à éviter :** SIR élevé ≠ automatiquement "les professionnels ont une conviction baissière". Un SIR élevé peut refléter un hedge de position longue, un arbitrage convertible, une stratégie statistique ou un crowded trade. Il est un **indicateur de positionnement**, pas d'intention.

**Le SIR doit être traité comme un double signal :**

```
Signal directionnel : short interest en hausse → sentiment baissier s'accumule
Signal de risque    : SIR très élevé          → risque de squeeze si rebond
```

**Problème des seuils absolus (5/8/20 jours) :** 8 jours n'a pas la même signification selon les secteurs. Les utilities ou les REITs ont structurellement des SIR plus faibles que les small-caps tech.

**Approche cible : percentile cross-sectionnel + trend**

```
SIR_percentile = rang(SIR(titre)) / nb_titres_univers × 100
SIR_trend      = variation du SIR vs publication FINRA précédente
```

- **SIR_percentile > 80 ET SIR_trend positif** → **1 point** (positionnement élevé et croissant)
- **SIR_percentile > 80 ET SIR_trend négatif** → **0 point** (les shorts couvrent → signal affaibli)
- **SIR_percentile 50-80** → **0.5 point** si trend positif, 0 sinon
- **SIR_percentile < 50** → **0 point**

**Source testée : FINRA** — CSV bi-hebdomadaire téléchargeable gratuitement. Source prioritaire avant toute API payante.

> **Règle de risque :** si `SIR > 20 jours` (valeur absolue, indépendamment du percentile) → PUT SPREAD obligatoire. Ce seuil absolu est maintenu pour le risque squeeze car il représente un plancher universel au-delà duquel le danger est structurel.

#### 3.2 Put/Call Ratio (P/C Ratio)

```
P/C Ratio = Volume d'options PUT achetées / Volume d'options CALL achetées
  (sur 20 jours glissants, sur le titre spécifique)
```

Pan & Poteshman (2006) ont montré que les acheteurs d'options PUT sur actions spécifiques ont un avantage informationnel : un P/C ratio anormalement élevé prédit une baisse du titre à horizon 20-30 jours avec une significativité statistique robuste.

**Limitation fondamentale du P/C brut :** toutes les puts ne sont pas des paris baissiers. Un investisseur achète un put pour hedger une position longue, construire un collar, réduire son beta avant earnings, ou arbitrer la volatilité. Le volume PUT brut ne distingue pas une conviction baissière d'une couverture de portefeuille.

**Ce que mesure vraiment l'étude Pan & Poteshman :** leur résultat repose sur les *transactions initiées par les acheteurs* (buyer-initiated), et non le volume brut. Le signal provient de l'achat de puts par des agents qui ne détiennent pas le titre sous-jacent — c'est-à-dire un pari directionnel pur.

**Implémentation accessible vs idéale :**

*Idéale (non disponible facilement) :* Buyer-Initiated Put/Call en delta-notional
```
Put flow directionnel = Σ(volume × |delta| × prix contrat × 100)
  pour les transactions initiées acheteur, OTM, DTE 20-60j
```

*Accessible via IBKR (implémentation retenue) :* P/C ratio sur volume total, interprété avec précaution comme signal de confirmation, pas déclencheur

- **P/C Ratio > 1.5** → **1 point** (signal, pas certitude)
- **P/C Ratio entre 1.0 et 1.5** → **0.5 point**
- **P/C Ratio < 1.0** → **0 point**

**Approche retenue (Option A — ciblée)** : calculé uniquement sur les **candidats finalistes** (5-15 titres après couches 1+2). Source : IBKR via `ibkr_service.py` (`reqMktData`, champs option statistics). Aucune infrastructure additionnelle.

**Amélioration future :** filtrer par moneyness (puts OTM 10-30%), DTE 20-60j, et si possible orienter vers les transactions à l'ask (proxy d'achat initié). La précision du signal augmente significativement avec ces filtres.

**Score Couche 3 : 0 à 2 points (combiné SIR + P/C)**

---

### COUCHE 4 — CATALYSEUR RÉCENT (0 ou 1 point, bonus)

*Fondement : Bernard & Thomas (1989) — Post-Earnings Announcement Drift (PEAD)*

#### Le concept

Après une surprise bénéficiaire négative (un "earnings miss"), le titre continue de baisser en moyenne pendant 45-60 jours. Ce phénomène est contre-intuitif mais empiriquement très stable : le marché **sous-réagit** à la mauvaise nouvelle initiale et continue d'intégrer l'information progressivement.

La fenêtre optimale pour entrer sur un PUT après un earnings miss est **1 à 5 jours ouvrés après la publication**.

#### Signal bonus

- **Earnings miss dans les 10 derniers jours** (EPS réel < consensus estimé d'au moins 10%) → **+1 point**

Ce point est un bonus, pas une condition nécessaire. Un titre sans earnings récent peut tout à fait être un excellent signal si les 3 autres couches sont présentes.

---

## PARTIE 3 — TABLEAU DE DÉCISION

| Score total | Décision | Instrument | Taille |
|-------------|----------|------------|--------|
| 0-2 | Aucun trade | — | — |
| 3 | Signal faible | PUT SPREAD | 50% de la taille standard |
| 4 | Signal modéré | PUT SPREAD | 75% de la taille standard |
| 5 | Signal fort | PUT seul si Skew Ratio ≤ 1.3, sinon PUT SPREAD | 100% de la taille standard |
| 6-7 | Signal très fort | PUT seul si Skew Ratio ≤ 1.3, sinon PUT SPREAD | 100% (max, pas de sur-sizing) |

**Note sur la règle Perf_63_5 < -20% → PUT SPREAD :** c'est une heuristique raisonnable mais pas démontrée. Un titre à -25% peut présenter un skew acceptable et une convexité très favorable au PUT seul. À l'inverse, un titre à -12% peut avoir un skew explosé. **La vraie règle à terme doit comparer l'EV du PUT vs l'EV du PUT SPREAD** sur la base des prix d'options réels, pas uniquement sur le niveau de baisse. En attendant les données pour calibrer cette comparaison, la règle -20% sert de proxy conservateur.

> **Règle d'or : on ne double jamais la taille même sur un score parfait.** La diversification entre plusieurs petites positions est supérieure à une grosse position concentrée.

---

## PARTIE 4 — TIMING D'ENTRÉE PRÉCIS

Avoir un bon signal ne suffit pas. Entrer au mauvais moment (sur un titre en rebond technique) peut transformer un bon trade en perte rapide avant que le mouvement baissier reprenne.

### 4.1 Conditions obligatoires pour entrer (TOUTES doivent être vraies)

```
Condition A : RSI(14) ∈ [35 ; 55]
Condition B : Pas de gap haussier > 3% dans les 3 derniers jours
Condition C : Perf_5_0 ≤ +5% (pas de rebond récent supérieur à +5%)
Condition D : Le titre n'est pas en phase d'extension baissière extrême
               (Perf_5_0 < -10% → attendre un rebond avant d'entrer)
```

#### Pourquoi ces conditions ?

**RSI entre 35 et 55** : 
- RSI < 35 = titre en territoire de survente → fort risque de rebond technique qui mettra l'option en perte immédiatement
- RSI > 55 = le titre est en rebond, pas encore prêt pour la continuation baissière
- La zone 35-55 représente le "no man's land" baissier : le rebond s'est essoufflé sans que le titre soit survendu

**Pas de gap récent** : un gap haussier récent change la dynamique à court terme et suggère l'arrivée d'acheteurs. Attendre que ce gap soit "digéré".

**Perf_5_0 ≤ +5%** : confirme que le rebond récent est limité. Si le titre a rebondi de +8% en 5 jours, attendre que le rebond se termine.

**Pas d'extension extrême** : shorter un titre qui vient de perdre -12% en 5 jours, c'est shorter la capitulation — le rebond qui suit peut être violent. Attendre la stabilisation.

### 4.2 Conditions idéales (bonus, pas obligatoires)

```
+ Earnings miss dans les 5 derniers jours ouvrés → timing parfait pour le PEAD
+ Volume en hausse sur la baisse récente (distribution institutionnelle)
+ Secteur du titre en sous-performance relative (beta négatif vs SPY)
```

### 4.3 Schéma de timing typique

```
J-63  ──────────────────────────────  Début de la fenêtre momentum
J-5   ────────────────────────────    Fin de la fenêtre momentum (Perf_63_5 calculée)
J     ┌─────────────────────────────  Jour d'évaluation
      │  ✓ Score ≥ 3 ?
      │  ✓ RSI ∈ [35-55] ?
      │  ✓ Pas de gap récent ?
      │  ✓ Skew Ratio ≤ 1.6 ?
      │  ✓ Instrument choisi selon skew et Perf_63_5 ?
      └→ ENTRÉE le jour J ou J+1 (si toutes les conditions sont réunies)
```

---

## PARTIE 5 — SÉLECTION ET STRUCTURATION DE L'OPTION

### 5.1 Choix de l'instrument selon le contexte

Le Volatility Skew (biais de volatilité) est le premier critère de sélection de l'instrument, avant même le score. Les market makers "pricent" le risque de continuation baissière dans le skew des puts — sur un titre en chute de 15-20%, la prime du put 30-delta peut être 30% à 60% plus chère que ce que le IV Rank global suggère.

```
Skew Ratio = IV implicite (Put 30-delta) / IV implicite (ATM)
```

| Situation | Instrument | Raison |
|-----------|------------|--------|
| Perf_63_5 < -20% | **PUT SPREAD obligatoire** | Skew structurellement écrasé, EV du PUT seul dégradée |
| Skew Ratio > 1.6 | **AUCUN TRADE** | Put trop cher, break-even inatteignable |
| Skew Ratio entre 1.3 et 1.6 | **PUT SPREAD** | Skew élevé, le spread absorbe une partie du surcoût |
| Skew Ratio ≤ 1.3 + Score ≥ 5 | **PUT seul** | Skew acceptable, signal fort → maximiser l'exposition |
| Score 3-4 (quel que soit le skew) | **PUT SPREAD** | Signal modéré → limiter le risque |
| SIR > 20 (risque squeeze) | **PUT SPREAD** | Perte plafonnée si squeeze violent |

> Le PUT SPREAD n'est pas l'instrument de secours — c'est **l'instrument par défaut** dès que le titre a déjà significativement chuté. La bonne nouvelle : le put vendu (delta -0.10) bénéficie d'un skew encore plus prononcé que le put acheté, ce qui rend la prime nette du spread souvent meilleure marché que son apparence.

### 5.2 Paramètres de l'option

#### Maturité (DTE — Days To Expiration)

```
DTE cible : 45 à 60 jours
Règle : choisir l'expiration mensuelle la plus proche ≥ 45 jours
```

**Pourquoi 45-60 jours ?**
- La valeur temps (thêta) se dégrade lentement dans cette zone → temps de laisser le trade se développer
- Le delta est sensible au mouvement du sous-jacent sans être trop faible
- Compatible avec la durée naturelle du signal PEAD (45-60 jours)
- Sortir à DTE ≤ 14 jours évite la dégradation thêta accélérée

#### Strike — PUT seul

```
Delta cible : -0.30 à -0.40
```

- Delta -0.35 est le "sweet spot" : pas trop OTM (peu de chances de gagner), pas trop ITM (prime trop chère)
- En pratique : chercher le strike avec delta le plus proche de -0.35 parmi les strikes disponibles

#### Strike — PUT SPREAD

```
PUT long  : Delta ≈ -0.30 à -0.35
PUT short : Delta ≈ -0.10 à -0.15
Écart entre strikes : 5% à 8% du prix du sous-jacent
```

Le PUT short réduit le coût de la prime (~40-50% de réduction) mais plafonne le gain au maximum si le titre s'effondre.

#### Filtre de volatilité : Skew Ratio (remplace le filtre IV Rank seul)

Le IV Rank est un indicateur incomplet car il mesure le **niveau global** de la volatilité sur l'année, pas la **forme de la surface** (le skew). Deux titres avec IV Rank = 40 peuvent avoir des puts 30-delta à des prix très différents si leurs skews divergent.

Le filtre retenu est le **Skew Ratio**, mais **relatif à son propre historique** — pas comme valeur absolue universelle.

**Pourquoi relatif ?** Un skew de 1.35 peut être parfaitement normal sur un titre volatil et très cher sur un autre. Les valeurs absolues (1.3/1.6) sont des points de départ, pas des vérités universelles.

```
Skew Ratio courant = IV(Put 30-delta) / IV(ATM)

Skew percentile = rang(Skew Ratio courant) dans l'historique 252 jours du même titre
```

**Interprétation :**

| Skew percentile | Skew courant vs historique | Action |
|-----------------|---------------------------|--------|
| < 50% | Skew dans la normale ou bas | PUT seul possible |
| 50-80% | Skew légèrement élevé | PUT SPREAD recommandé |
| 80-95% | Skew historiquement cher | PUT SPREAD obligatoire |
| > 95% | Skew extrême | AUCUN TRADE |

**En l'absence d'historique de skew suffisant (phase initiale) :** utiliser les seuils absolus 1.3/1.6 comme proxy conservateur, avec conscience de leur limitation.

**Source** : IV par strike disponible via IBKR (`reqMktData`, `reqSecDefOptParams`) pour les titres finalistes. Stocker l'historique des Skew Ratios en base pour construire les percentiles au fur et à mesure.

### 5.3 Exemples concrets

**Titre XYZ, cours = 45 $, Score = 6/7, Perf_63_5 = -17%**

```
Perf_63_5     : -17% (entre -15% et -20% → PUT seul possible si skew ok)
Skew Ratio    : 1.22 (≤ 1.3 → acceptable)
Instrument    : PUT seul
Expiration    : 48 jours (prochain mensuel ≥ 45 jours)
Strike choisi : 43 $ (delta -0.33, proche de -0.35)
Prime         : 1.85 $ par action = 185 $ par contrat
IV Rank       : 38 (contexte favorable confirmé)
```

**Titre ABC, cours = 80 $, Score = 6/7, Perf_63_5 = -24%**

```
Perf_63_5     : -24% (< -20% → PUT SPREAD par règle structurelle)
Skew Ratio    : 1.55 (modéré, confirme le PUT SPREAD)
Instrument    : PUT SPREAD
Put long      : strike 76 $ (delta -0.32), prime 3.20 $
Put short     : strike 68 $ (delta -0.11), prime 1.30 $ (vendu)
Prime nette   : 3.20 - 1.30 = 1.90 $ = 190 $ par contrat
Gain max      : (76 - 68 - 1.90) × 100 = 610 $ par contrat si ABC ≤ 68 $ à expiration
```

---

## PARTIE 6 — GESTION DU RISQUE ET TAILLE DE POSITION

### 6.1 Budget de risque par trade

```
Risque par trade  : 0.5% à 0.75% du capital total
Maximum absolu    : 1% du capital par trade (jamais plus)
```

La perte maximale sur un PUT seul = la prime payée intégralement. C'est ce montant qui doit représenter 0.5-0.75% du capital.

```
Nombre de contrats = floor( (Capital × 0.75%) / (Prime × 100) )
```

**Exemple** : Capital = 100 000 $, Prime = 1.85 $
```
Budget risque = 100 000 × 0.75% = 750 $
Nb contrats   = floor(750 / (1.85 × 100)) = floor(750 / 185) = 4 contrats
Risque réel   = 4 × 185 = 740 $ = 0.74% du capital ✓
```

### 6.2 Limites de portefeuille short

```
Nombre maximum de positions short simultanées : 5 à 8
Risque total alloué au short                  : ≤ 6% du capital
Exposition par secteur                         : ≤ 2 positions par secteur GICS
```

La limite par secteur évite de shorter 5 banques en même temps — si le secteur rebondit (Fed dovish, baisse des taux surprise), toutes les positions perdent simultanément.

### 6.3 Relation avec la stratégie long

La stratégie short représente une **couche complémentaire** au portefeuille long, pas un portefeuille séparé. Les deux coexistent :
- En tendance haussière (SPY > SMA200) : réduire les positions short, favoriser le long
- En marché baissier (SPY < SMA200) : la stratégie short devient plus agressive
- En marché latéral : les deux coexistent, les shorts se concentrent sur les secteurs faibles

---

## PARTIE 7 — GESTION DE LA POSITION : LIFECYCLE COMPLET

### 7.1 Scénarios de sortie

Un trade a **plusieurs sorties possibles**, hiérarchisées par priorité :

#### Sortie 1 : Take Profit (sortie normale, la meilleure)

```
+70% de la prime payée → sortie partielle (50% de la position)
+100% de la prime payée → sortie totale
```

**Attention : le seuil +70% comme point optimal n'est pas une vérité démontrée — c'est une hypothèse à valider.** Le bon take-profit dépend du delta, du DTE restant, de l'IV, de la vitesse du mouvement et du régime de marché. Il varie par trade.

**Approche alternative à tester :** sortie basée sur le sous-jacent plutôt que sur le rendement de la prime

```
Alternative A (prime-based, actuel) :
  +70% prime → sortie partielle, +100% → sortie totale

Alternative B (sous-jacent-based) :
  Sous-jacent atteint un target statistique (ex: -1.5 ATR depuis l'entrée)
  OU signal momentum disparaît (MomentumScore positif)
  OU IV devient extrêmement élevée (Skew percentile > 90%)
```

**Stratégie de sortie partielle (point de départ) :**
- À +70% : vendre 50% des contrats → sécurise le profit, laisse courir la moitié
- À +100% : vendre le reste
- Si on ne revient jamais à +100% : time stop à DTE ≤ 14 jours

Le backtest comparera ces deux approches sur le critère P&L total (pas win rate seul).

#### Sortie 2 : Stop Loss (protection du capital)

Le stop loss basé uniquement sur la prime est problématique : un rebond technique de 3-4% en 48h combiné à une compression de volatilité implicite peut effacer 50% de la prime sans que la thèse baissière soit invalidée. Un stop sur prime trop serré génère des faux exits (whipsaws) coûteux.

**Stop loss principal : basé sur le prix du sous-jacent**

**Problème du seuil fixe à +6% :** un rebond de +6% peut être du bruit sur un titre avec ATR(14) = 5%, et une vraie invalidation sur un titre avec ATR(14) = 1.2%. Le seuil absolu est donc un proxy grossier de la volatilité réelle du titre.

**Approche cible : stop adapté à la volatilité (ATR-based)**

```
ATR(14) = Average True Range sur 14 jours

Stop Niveau 1 : Prix d'entrée + 2 × ATR(14)   (adaptatif, ~2 jours de mouvement "normal")
Stop Niveau 2 : SMA50 du sous-jacent           (invalidation structure technique)
```

Le premier des deux niveaux atteint à la clôture déclenche la sortie.

**Comparaison à backtester :**
- `Fixed +6%` vs `1.5 × ATR` vs `2 × ATR` vs `SMA50 seul`

Le backtest déterminera quelle version offre le meilleur équilibre faux-positifs / vrais-stops.

**En attendant le backtest (valeur initiale) :** utiliser `2 × ATR(14)` avec un plancher à +5% et un plafond à +10% pour éviter les extrêmes.

**Stop d'urgence sur prime : -75% (filet de sécurité)**

```
Déclenché si la prime tombe à 25% de la prime initiale
→ Cas d'usage : OPA surprise, gap haussier massif, squeeze violent
  avant que le stop sous-jacent puisse être évalué à la clôture
```

Si la prime était 1.85 $, le stop d'urgence se déclenche à 0.46 $. Dans la majorité des trades normaux, le stop sous-jacent sera atteint bien avant ce niveau.

#### Sortie 3 : Time Stop (protection contre le thêta)

```
DTE ≤ 14 jours → fermer la position, quelle que soit la performance
```

En-dessous de 14 jours, la dégradation thêta s'accélère drastiquement. Même si le sous-jacent continue de baisser, la perte de valeur temps peut compenser le gain directionnel. On sort et on "roll" (rouvrir une position similaire avec une nouvelle expiration) si le signal est toujours actif.

#### Sortie 4 : Signal Reversal (sortie d'urgence)

Fermer immédiatement si l'un des événements suivants survient :

```
Prix > SMA50                    → structure technique invalidée
RSI(14) > 65                    → rebond fort, momentum inversé
Perf_5_0 > +8%                 → rebond trop violent, stop squeeze
MomentumScore devient positif  → signal principal disparu
Gap haussier > 5% en une journée → événement imprévu (OPA, news positive)
```

Ne pas attendre le stop loss dans ces cas — sortir immédiatement au marché.

### 7.2 Roll (renouvellement de position)

Si à DTE = 14 jours la position est profitable (+30% à +70%) et le signal est toujours actif :

```
1. Fermer la position actuelle
2. Réévaluer le score (recalculer toutes les couches)
3. Si score ≥ 3 : ouvrir un nouveau PUT sur la prochaine expiration (DTE 45-60j)
4. Si score < 3 : ne pas renouveler, le signal s'est affaibli
```

---

## PARTIE 8 — SUIVI ET MÉTRIQUES DE PERFORMANCE

### 8.1 Ce qu'il faut tracker par trade

```
Date d'entrée / Date de sortie
Score composite au moment de l'entrée (détail par couche)
Instrument (PUT / PUT SPREAD), Strike, Expiration
Prime d'entrée / Prime de sortie
P&L en $ et en % de la prime
Raison de sortie (TP / SL / Time stop / Signal reversal)
DTE restant à la sortie
```

### 8.2 Métriques d'évaluation stratégique (à calculer sur 20+ trades)

```
Win Rate               : % de trades fermés en profit (cible : > 45%)
Payoff Ratio           : Gain moyen / Perte moyenne (cible : > 2.0)
Expected Value (EV)    : Win Rate × Gain moyen - (1 - Win Rate) × Perte moyenne > 0
Profit Factor          : Somme gains / Somme pertes (cible : > 1.5)
Max Drawdown capital   : Perte cumulée maximale (seuil d'alerte : 3% du capital)
Avg Score des trades   : Score moyen des trades gagnants vs perdants
```

> **Règle empirique** : un Win Rate de 40% avec un Payoff Ratio de 3.0 est largement profitable. On préfère moins de trades avec un meilleur score plutôt que de "forcer" des trades à score faible.

### 8.3 Revue mensuelle

À chaque fin de mois :
1. Calculer les métriques ci-dessus sur tous les trades du mois
2. Identifier les trades perdants : quel signal avait-il manqué ? Quel signal était faux ?
3. Identifier les meilleurs trades : quelle couche a été la plus discriminante ?
4. Ajuster les seuils de score si le biais est systématique (ex : les trades à score 3 perdent tous → relever le seuil minimum à 4)

---

## PARTIE 9 — FLUX DE DÉCISION COMPLET (CHECKLIST)

Voici la séquence exacte à suivre pour évaluer un candidat short :

```
ÉTAPE 1 — ÉLIGIBILITÉ (filtres durs)
□ Market Cap ≥ 2 Mds $
□ Prix ≥ 10 $
□ Volume moyen ≥ 500k/jour
□ Options disponibles avec bid-ask ≤ 5%
□ Pas d'earnings dans ≤ 10 jours
□ Pas d'OPA annoncée
□ Pas de biotech pré-FDA
→ Si un critère non respecté : STOP, titre suivant

ÉTAPE 2 — SIGNAL MOMENTUM (Couche 1, obligatoire)
□ Calculer Perf_63_5
□ Si Perf_63_5 > -15% : STOP, signal insuffisant
□ Attribuer 1 ou 2 points selon l'intensité
□ Vérifier Death Cross (Prix < SMA50 < SMA200)
□ Ajouter 1 point si Death Cross confirmé

ÉTAPE 3 — SIGNAL FONDAMENTAL (Couche 2)
□ Récupérer Net Income, CFO, Total Assets du dernier trimestre
□ Calculer Accruals Ratio
□ Attribuer 0, 1 ou 2 points

ÉTAPE 4 — SMART MONEY (Couche 3)
□ Récupérer SIR et son évolution sur 1 mois
□ Calculer P/C Ratio 20 jours
□ Attribuer 0 à 2 points

ÉTAPE 5 — CATALYSEUR (Couche 4, bonus)
□ Earnings miss dans les 10 derniers jours ?
□ Ajouter 1 point si oui

ÉTAPE 6 — DÉCISION
□ Calculer le score total
□ Score < 3 : pas de trade
□ Score 3-4 : PUT SPREAD, taille réduite
□ Score ≥ 5 : PUT seul, taille pleine

ÉTAPE 7 — TIMING (conditions d'entrée)
□ RSI(14) ∈ [35-55]
□ Pas de gap haussier > 3% dans les 3 derniers jours
□ Perf_5_0 ≤ +5%
□ Perf_5_0 ≥ -10% (pas en capitulation)
→ Si une condition non respectée : mettre en "watchlist", réévaluer dans 3-5 jours

ÉTAPE 8 — SÉLECTION DE L'OPTION
□ Calculer le Skew Ratio (IV put 30-delta / IV ATM) via IBKR
□ Si Skew Ratio > 1.6 : STOP, trade rejeté
□ Si Perf_63_5 < -20% OU Skew Ratio > 1.3 : instrument = PUT SPREAD
□ Sinon (Skew Ratio ≤ 1.3 + Score ≥ 5) : instrument = PUT seul
□ Choisir expiration avec DTE entre 45 et 60 jours
□ Choisir strike avec delta -0.30 à -0.40 (PUT long) et delta -0.10 à -0.15 (PUT short si spread)
□ Calculer le nombre de contrats selon le budget risque
□ Vérifier que le risque total short ne dépasse pas 6% du capital

ÉTAPE 9 — ENTRÉE
□ Placer l'ordre LIMIT (jamais au marché, surtout sur options peu liquides)
□ Prix limite : mid-price + 10% maximum (si non exécuté en 30min, ajuster)
□ Enregistrer tous les paramètres du trade

ÉTAPE 10 — SUIVI QUOTIDIEN
□ Vérifier les conditions de sortie chaque jour à la clôture
□ Stop sous-jacent : action a-t-elle clôturé au-dessus de (Prix entrée × 1.06) ou de la SMA50 ?
□ Stop urgence : prime tombée en-dessous de 25% de la prime initiale ?
□ Take Profit : prime à +70% ou +100% ?
□ Signal reversal : RSI > 65, momentum positif, gap > 5% ?
□ À DTE = 14 jours : décider roll ou fermeture
```

---

## PARTIE 10 — GESTION DES SITUATIONS EXCEPTIONNELLES

### 10.1 Marché en stress (régime VIX)

**Contradiction du freeze à VIX > 35 :** c'est précisément en période de stress élevé que la stratégie momentum short devrait avoir le plus de signaux pertinents. Geler toutes les entrées supprime potentiellement les meilleures périodes de la stratégie. La bonne réponse n'est pas "ne pas shorter", c'est "adapter l'instrument pour plafonner le risque".

**Réponse graduée par régime VIX :**

| VIX | Action | Raison |
|-----|--------|--------|
| < 20 | Normal — toutes règles standards | Faible volatilité, primes raisonnables |
| 20-30 | Normal — vérifier le Skew percentile | IV monte, surveiller la cherté relative |
| 30-40 | Réduire la taille de 50% | Risque de rebond brutal, IV élevée |
| 40-50 | PUT SPREAD uniquement, taille 25% | Primes très chères, risque directionnel fort |
| > 50 | Sélectif — uniquement score ≥ 6, PUT SPREAD | Corrélations extrêmes, bruit dominant |

**Principe :** ne pas supprimer le signal, adapter le payoff. Le PUT SPREAD plafonne la perte quelle que soit la volatilité.

### 10.2 Short Squeeze

Un short squeeze se produit quand un titre très shorté remonte et force les vendeurs à découvert à couvrir leurs positions en achetant — ce qui amplifie la hausse.

Signaux d'alerte précoce d'un squeeze potentiel :
- SIR > 20 jours
- Volume anormalement élevé en hausse
- Titre qui perce la SMA50 à la hausse

```
Si SIR > 20 jours : utiliser UNIQUEMENT le PUT SPREAD (perte plafonnée)
Si squeeze en cours (hausse > 10% en 2 jours) : fermer immédiatement
```

### 10.3 Annonce inattendue (news positive, rachat d'actions, OPA)

```
Gap haussier > 5% → fermer immédiatement au marché (prix de sortie peu favorable
mais préférable à une perte encore plus grande)
```

Ne pas "attendre de voir" sur une news fondamentale positive — le signal est invalidé.

---

## PARTIE 11 — ARCHITECTURE DE PERSISTANCE DES DONNÉES

### Principe directeur

**Tout ce qui est récupéré est stocké, même si inutilisé aujourd'hui.** La collecte a un coût (temps, quota API, dépendance externe) ; la requête en base n'en a pratiquement pas. Stocker plus large maintenant évite de devoir re-collecter l'historique plus tard quand un nouveau signal est identifié.

Chaque table suit un schéma **hybride** : colonnes typées pour les champs immédiatement exploitables par le scoring + colonne `raw_*` JSON pour l'intégralité de la réponse source. Les colonnes typées permettent des requêtes SQL directes ; le JSON garantit qu'aucune donnée n'est perdue.

---

### Table `fundamental_snapshots` (source : yfinance)

Une ligne = une période de reporting (trimestrielle `Q` ou annuelle `A`) pour un ticker.

**Colonnes typées persistées :**

| Groupe | Champs |
|--------|--------|
| Compte de résultat | `total_revenue`, `gross_profit`, `operating_income`, `ebitda`, `net_income`, `eps_diluted` |
| Bilan | `total_assets`, `total_liabilities`, `total_equity`, `cash_and_equivalents`, `total_debt`, `current_assets`, `current_liabilities`, `inventory`, `accounts_receivable` |
| Flux de trésorerie | `operating_cash_flow`, `investing_cash_flow`, `financing_cash_flow`, `capital_expenditure`, `free_cash_flow` |
| Ratios dérivés | `accruals_ratio`, `current_ratio`, `debt_to_equity`, `fcf_margin` |

**Colonnes brutes :** `raw_income_stmt`, `raw_balance_sheet`, `raw_cashflow` (lignes des DataFrames yfinance sérialisées en JSON).

**Clé unique :** `(ticker, period_date, period_type)` — un upsert par période, jamais de doublon.

**Collecte :** job nocturne `job_collect_prices` étendu. Pour chaque ticker, on compare les dates de colonnes des DataFrames `quarterly_income_stmt` retournés par yfinance avec les `period_date` déjà en base. Si une nouvelle date apparaît → insertion. Sinon → aucun appel inutile. Charge négligeable : 4 mises à jour par an par ticker.

---

### Table `ticker_info_snapshots` (source : yfinance `.info`)

Une ligne par (ticker, date de collecte). Contrairement aux états financiers, ces données reflètent des valeurs courantes et sont rafraîchies **mensuellement**.

**Colonnes typées persistées :**

| Groupe | Champs |
|--------|--------|
| Valorisation | `market_cap`, `enterprise_value`, `trailing_pe`, `forward_pe`, `price_to_book`, `price_to_sales`, `ev_to_ebitda` |
| Marges | `gross_margins`, `operating_margins`, `profit_margins` |
| Croissance | `revenue_growth`, `earnings_growth` |
| Qualité | `return_on_equity`, `return_on_assets`, `debt_to_equity`, `current_ratio`, `quick_ratio` |
| Short Interest | `short_ratio` (SIR yfinance), `short_percent_float` |
| Dividendes | `dividend_yield`, `payout_ratio` |
| Risque | `beta` |
| Identité | `sector`, `industry`, `full_time_employees`, `country` |

**Colonne brute :** `raw_info` — dict complet `yf.Ticker(t).info` en JSON. Intérêt : yfinance expose parfois 80-100 champs selon le ticker (analyst ratings, earnings dates, 52w high/low, etc.) — tout est conservé.

> Note : `short_ratio` et `short_percent_float` de yfinance sont une approximation (source : Yahoo Finance, mise à jour mensuelle). Les données FINRA sont la source primaire pour le signal SIR ; celles de yfinance servent de cross-check et de fallback.

**Données supplémentaires à collecter pour le scoring cross-sectionnel :**
- Le calcul des percentiles (accruals, SIR) nécessite les valeurs de l'ensemble de l'univers au même instant. La table `FundamentalSnapshot` stocke déjà ces données par ticker — le percentile est calculé à la volée depuis la base en comparant les dernières valeurs disponibles pour chaque ticker de l'univers.
- Pour le SIR : idem, le percentile est calculé depuis `ShortInterestSnapshot` en prenant la dernière publication FINRA pour chaque ticker de l'univers.

---

### Table `short_interest_snapshots` (source : FINRA — à tester)

Une ligne = un ticker pour une date de publication FINRA. FINRA publie deux fois par mois (autour du 15 et de la fin du mois), couvrant tous les titres côtés sur les marchés US.

**Colonnes typées persistées :**

| Champ | Description |
|-------|-------------|
| `settlement_date` | Date de règlement FINRA (clé de la publication) |
| `report_date` | Date de publication du rapport |
| `short_interest` | Nombre d'actions vendues à découvert |
| `avg_daily_volume` | Volume moyen journalier (source FINRA) |
| `days_to_cover` | `short_interest / avg_daily_volume` (SIR) — champ principal du scoring |
| `previous_short_interest` | Valeur de la publication précédente |
| `change_from_previous` | Variation absolue |
| `change_pct` | Variation en % vs publication précédente |
| `sir_trend` | `'up'` / `'down'` / `'stable'` (calculé à la collecte) |
| `squeeze_risk` | `True` si `days_to_cover > 20` |

**Colonne brute :** `raw_data` — ligne CSV FINRA complète en JSON. Préserve tous les champs du fichier source, y compris ceux non encore exploités (market maker data, exchange breakdown, etc.).

**Clé unique :** `(ticker, settlement_date)`.

**Collecte :** téléchargement du fichier CSV FINRA bi-hebdomadaire, parsing, upsert par `(ticker, settlement_date)`. Si FINRA est validé comme source fiable, cette collecte sera automatisée dans un job dédié (2×/mois, jours de publication FINRA).

---

### Cohérence entre les tables

```
FundamentalSnapshot   ← mis à jour aux earnings (4×/an par ticker)
TickerInfoSnapshot    ← mis à jour mensuellement
ShortInterestSnapshot ← mis à jour 2×/mois (FINRA)
MarketPriceBar        ← mis à jour quotidiennement (existant)
```

Le scoring de la stratégie short lit toujours depuis la base de données, jamais directement depuis les APIs — les collecteurs alimentent la base, les services de scoring la consomment. Cela découple la fragilité des sources externes (quota, downtime, format changeant) de la logique de décision.

---

## PARTIE 12 — FEUILLE DE ROUTE D'IMPLÉMENTATION

### Phase 1 — Signal momentum + Death Cross (déjà implémenté)
*Fondement : Jegadeesh & Titman (2001)*

### Phase 2 — Skew Ratio + Stop sous-jacent (priorité 1 — correctif)
- Calculer le Skew Ratio via IBKR sur les candidats finalistes (`reqMktData`)
- Remplacer le filtre IV Rank seul par le Skew Ratio dans la logique de sélection d'instrument
- Remplacer le stop -50% prime par le stop sur clôture sous-jacent (Prix × 1.06 ou SMA50)
- Garder -75% prime comme stop d'urgence uniquement

### Phase 3 — Ajout Short Interest FINRA (priorité 2)
- **Source à tester : FINRA** — CSV bi-hebdomadaire téléchargeable gratuitement
- Parser le fichier, calculer le SIR (short shares / avg daily volume) par ticker
- Vérifier l'évolution sur la publication précédente (en hausse / en baisse)
- Ajouter le SIR dans le score (Couche 3)
- Alerter si SIR > 20 (risque squeeze → forcer PUT SPREAD)
- Si FINRA s'avère insuffisant (couverture, fréquence), évaluer S3 Partners ou Quandl

### Phase 4 — Ajout Accruals via yfinance (priorité 3)
- Étendre `job_collect_prices` pour vérifier quotidiennement si de nouvelles données fondamentales sont disponibles par ticker (comparer la date du dernier 10-Q en base vs celle publiée par yfinance)
- Si nouvelle publication détectée : récupérer Net Income, CFO, Total Assets et stocker en base
- Calcul du ratio au scoring : lecture base uniquement, pas d'appel réseau en temps réel
- Nouvelle table DB suggérée : `FundamentalSnapshot` (ticker, date_publication, net_income, cfo, total_assets, accruals_ratio)

### Phase 5 — Ajout P/C Ratio via IBKR (priorité 4)
- Calculer uniquement sur les finalistes (5-15 titres après filtres couches 1+2)
- Source : IBKR `reqMktData` avec generic tick 100 (option volume) et 101 (put volume)
- P/C Ratio = put_volume_20j / call_volume_20j, stocké en cache TTL 24h
- Pas d'infrastructure additionnelle : réutilise la connexion IBKR existante

### Phase 6 — Intégration post-earnings timing (priorité 5)
- Source : Yahoo Finance Earnings Calendar (yfinance `calendar`)
- Comparer EPS réel vs consensus au dernier earnings
- Flag "earnings miss" si EPS_réel < EPS_consensus × 0.90 dans les 10 derniers jours
- Ajouter +1 point dans le score (Couche 4 bonus)

---

## PARTIE 13 — PROTOCOLE DE BACKTEST ET VALIDATION

Cette partie est aussi importante que les règles de la stratégie. Une belle stratégie sur le papier sans protocole de validation rigoureux n'est qu'une histoire.

### 13.1 Pipeline de backtest événementiel

Le backtest doit modéliser le P&L réel de l'**option**, pas seulement du sous-jacent. Avoir raison sur la direction de l'action n'implique pas de gagner de l'argent avec le put si l'IV s'effondre, si le thêta dévore la prime ou si le strike était mal choisi.

```
Universe point-in-time constituants (IndexMembership table — déjà implémentée)
        ↓
Signal composite (momentum + accruals + SIR + P/C + PEAD)
  → données point-in-time : publication_date ≤ signal_date pour chaque source
        ↓
Surface de vol historique du titre à la date d'entrée
  → prix du PUT / PUT SPREAD théorique (Black-Scholes ou vol historique)
        ↓
Entrée simulée (prix mid-point − slippage estimé 0.5 × bid-ask spread)
        ↓
Évolution quotidienne : prix sous-jacent + IV estimée → P&L option
        ↓
Application des règles de sortie (stop ATR, TP%, time stop DTE 14)
        ↓
P&L final de l'option par trade
        ↓
Agrégation portefeuille (sizing, limite secteur, limite capital short)
```

### 13.2 Pièges à éviter impérativement

**Survivorship bias (biais de survivant)**

Si le backtest utilise la composition actuelle du SP500/NDX100 appliquée à l'historique, les entreprises qui ont fait faillite, été retirées ou fortement chuté sont absentes — ce qui surestime les performances. La table `IndexMembership` (déjà implémentée, alimentée par Wikipedia) est la solution : utiliser les membres point-in-time à chaque date de signal.

**Look-ahead bias (biais de regard en avant)**

Chaque source de données a une date de disponibilité réelle différente de la date de la période qu'elle couvre :

| Source | Décalage réel |
|--------|---------------|
| États financiers (10-Q) | 30-45 jours après la fin du trimestre |
| EPS consensus | Mis à jour en temps réel, mais l'historique peut être révisé |
| Short interest FINRA | Publication ~2 semaines après la date de règlement |
| Options IV historique | Instantanée à la date, pas de biais |

Règle : utiliser `collected_at` ou `report_date` (dates de disponibilité réelle) comme date de signal, jamais `period_date` (date de fin de période comptable).

**Multiple testing / data mining**

Avec 25+ paramètres numériques, le risque de trouver des seuils "optimaux" par hasard est élevé. Protocole obligatoire : définir les seuils initiaux **avant** le backtest, fixer une période in-sample (ex : 2010-2020), valider sur out-of-sample (2021-2026) sans re-optimisation.

### 13.3 Test incrémental des couches (A→E)

Mesurer la contribution marginale de chaque couche en construisant 5 versions :

| Version | Couches actives | Question répondue |
|---------|----------------|-------------------|
| **A** | Momentum seul | Quel est le benchmark de base ? |
| **B** | Momentum + Accruals | Les fondamentaux ajoutent-ils vraiment de l'alpha ? |
| **C** | B + Short Interest | Le positionnement améliore-t-il les résultats ? |
| **D** | C + P/C Ratio | Le flow d'options apporte-t-il de l'information incrémentale ? |
| **E** | D + PEAD (earnings) | Le catalyseur améliore-t-il le timing ? |

Si le Sharpe de C est identique à B, le short interest n'apporte rien et peut être supprimé malgré son élégance théorique. C'est une excellente découverte.

### 13.4 Test de monotonicity des seuils

Pour chaque paramètre numérique, vérifier que le résultat est monotone (croissant ou décroissant) sur une grille de valeurs. Exemple pour le momentum :

```
Zone          Win rate    Sharpe   Méthode
-10% à -15%   ???         ???      → in-sample
-15% à -20%   ???         ???
-20% à -25%   ???         ???
-25% à -30%   ???         ???
```

Si la relation n'est pas monotone (zigzag), le seuil est probablement du bruit. Si elle est monotone (plus c'est bas, plus c'est bon), le facteur est robuste.

### 13.5 Métriques cibles (après backtest — pas avant)

Ne pas fixer de win rate ou de Sharpe cible avant le backtest. Observer ce que le système produit réellement, puis juger. Les métriques à calculer :

```
Expected Value (EV)    = Σ(P(gain) × gain moyen) − Σ(P(perte) × perte moyenne)
Profit Factor          = Somme des gains / Somme des pertes
Sharpe Ratio           = (Rendement annualisé − rf) / Volatilité rendements
Sortino Ratio          = (Rendement annualisé − rf) / Volatilité downside
Max Drawdown           = pire perte cumulée de pic à creux
Return per unit θ      = P&L total / Thêta total payé (spécifique aux options)
```

**Analyse par régime de marché — obligatoire :**

```
SPY > SMA200 (bull)     : Sharpe = ?, Win Rate = ?, EV = ?
SPY < SMA200 (bear)     : Sharpe = ?, Win Rate = ?, EV = ?
VIX < 20               : ...
VIX 20-30              : ...
VIX > 30               : ...
Période 2020 (covid)    : ...
Période 2022 (taux)     : ...
```

Une stratégie short peut être excellente en bear et catastrophique en bull. Il faut le savoir explicitement pour décider quand l'activer.

### 13.6 Walk-forward validation

```
In-sample (calibration)   : 2010–2020  → optimiser les seuils
Out-of-sample (validation) : 2021–2026  → aucune modification des paramètres
```

Si la performance out-of-sample est significativement inférieure à l'in-sample, le système est overfit. La règle : le out-of-sample doit représenter au minimum 30% de la période totale et ne jamais être touché pendant la calibration.

---

## RÉSUMÉ EXÉCUTIF

Cette stratégie repose sur **4 familles de facteurs empiriquement validées** dans la littérature académique. Les paramètres spécifiques (seuils, fenêtres, DTE) sont des hypothèses de travail à calibrer par backtest.

| Famille | Fondement académique | Paramètres à calibrer |
|---------|---------------------|----------------------|
| Momentum de prix | Jegadeesh & Titman (2001) | Fenêtre 63-5j, seuils -15%/-20%, alpha relatif |
| Dégradation fondamentale | Sloan (1996) | Percentile accruals, horizon |
| Positionnement marché | Dechow et al. (2001), Pan & Poteshman (2006) | Percentile SIR, P/C threshold |
| Catalyseur news | Bernard & Thomas (1989) | Fenêtre PEAD, magnitude miss |

La robustesse vient de la **convergence de signaux indépendants**. Le risque est **structurellement plafonné** par l'utilisation d'options. La rigueur du protocole de validation déterminera si la stratégie possède un edge réel ou une belle histoire statistique.

---

*Sources académiques : Jegadeesh & Titman (1993, 2001), Israel & Moskowitz (2013), Sloan (1996), Dechow, Hutton, Meulbroek & Sloan (2001), Pan & Poteshman (2006), Bernard & Thomas (1989), Ang, Hodrick, Xing & Zhang (2006)*
