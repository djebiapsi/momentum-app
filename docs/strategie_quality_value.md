# STRATÉGIE LONG — QUALITY-VALUE MULTIFACTEUR
## Version 1.0 — Fondements, méthodologie, validation

---

## RÉSUMÉ EXÉCUTIF

Stratégie **long, buy-and-hold, faible rotation**, qui sélectionne les « **bonnes
entreprises à prix correct** » : des sociétés à la fois **rentables/solides
(qualité)** et **raisonnablement valorisées (value)**.

- **Univers** : S&P 500 (constituants, via `IndexConstituent`), ~440-500 titres éligibles.
- **Score** : composite 50 % Qualité + 50 % Valeur, par rangs percentiles cross-sectionnels.
- **Portefeuille** : top ~15-20 titres, équipondérés, max 2-3 par secteur GICS.
- **Rebalance** : annuel ou semestriel (l'edge est maximal à 12 mois).
- **Rôle** : **poche séparée**, complémentaire de la stratégie momentum 12-1 existante.

**Validation (backtest SEC EDGAR, 2007-2026, 18 ans, 196 dates mensuelles)** : le
meilleur quintile (Q5) surperforme le pire (Q1) de **+3.8 %/an**, en absolu comme
en relatif, avec un rendement absolu positif (compatible détention longue).

---

## 1. PHILOSOPHIE

Contrairement à un pari directionnel de court terme, cette stratégie **capture des
primes de risque documentées depuis 30+ ans** dans la littérature financière. Elle
ne se bat pas contre la dérive haussière du marché — elle sélectionne, à l'intérieur
de cette dérive, les titres au meilleur profil fondamental/valorisation.

Deux idées combinées :

1. **Qualité** *(Novy-Marx 2013, Asness-Frazzini-Pedersen « Quality Minus Junk »)* :
   les entreprises rentables, peu endettées, génératrices de cash et à la
   comptabilité saine **surperforment durablement** la « camelote ». Facteur
   **stable à travers les régimes**.

2. **Valeur** *(Fama-French 1992, value premium)* : à qualité comparable, les
   titres **moins chers** (rendements bénéfice/FCF/actif élevés) surperforment les
   titres chers sur le long terme. Facteur **plus lent et cyclique**, mais réel.

**Pourquoi les combiner ?** Les deux primes sont **peu corrélées** et opèrent
différemment : la qualité protège des « value traps » (sociétés bon marché parce
que fondamentalement dégradées) ; la value évite de surpayer la qualité. Le
composite **bat empiriquement chacun des deux facteurs pris isolément** (voir §6).

---

## 2. UNIVERS ET ÉLIGIBILITÉ

| Filtre | Seuil | Raison |
|--------|-------|--------|
| Univers de départ | S&P 500 (`IndexConstituent` actifs) | Liquidité, données fiables |
| Market cap | ≥ 2 Mds $ | Évite micro-caps illiquides |
| Métriques qualité disponibles | ≥ 4 sur 9 | Score fiable |
| Métriques value disponibles | ≥ 3 sur 5 | Score fiable |

Point-in-time : le **backtest** filtre l'univers via `IndexMembership` (appartenance
historique à l'indice) pour réduire le biais de survivance.

---

## 3. LES DEUX FACTEURS — MÉTRIQUES EXACTES

Chaque métrique est convertie en **rang percentile cross-sectionnel** (0-100, où
100 = meilleur) à chaque date, puis moyennée par dimension. Les rangs rendent le
score **robuste aux valeurs extrêmes** et **comparable entre secteurs**.

### 3.1 Score QUALITÉ (9 métriques)

| Métrique | Sens | Ce qu'elle capture |
|----------|------|--------------------|
| Gross profit / total assets | ↑ | Rentabilité brute (Novy-Marx : le meilleur prédicteur qualité) |
| Return on equity (ROE) | ↑ | Rentabilité des fonds propres |
| Return on assets (ROA) | ↑ | Rentabilité des actifs |
| Marge brute | ↑ | Pouvoir de fixation des prix |
| Marge opérationnelle | ↑ | Efficacité opérationnelle |
| FCF margin (FCF / revenus) | ↑ | Conversion des ventes en cash |
| Accruals ratio | ↓ | Qualité des bénéfices (bas = bénéfices adossés au cash, Sloan 1996) |
| Dette / fonds propres | ↓ | Solidité du bilan |
| Current ratio | ↑ | Liquidité court terme |

### 3.2 Score VALEUR (5 métriques — des rendements : plus haut = moins cher)

| Métrique | Formule | Ce qu'elle capture |
|----------|---------|--------------------|
| FCF yield | FCF / market cap | Rendement cash (le plus robuste) |
| Earnings yield | 1 / PER | Rendement bénéficiaire |
| Book yield | 1 / (P/B) | Décote sur actif net |
| Sales yield | 1 / (P/S) | Décote sur chiffre d'affaires |
| EBITDA/EV | 1 / (EV/EBITDA) | Rendement opérationnel sur valeur d'entreprise |

### 3.3 Composite

```
Score Qualité  = moyenne des percentiles qualité disponibles
Score Valeur   = moyenne des percentiles valeur disponibles
Composite      = 0.5 × Qualité + 0.5 × Valeur
```

Le portefeuille retient les **plus hauts composites**.

---

## 4. CONSTRUCTION DU PORTEFEUILLE

1. Calculer le composite de tous les titres éligibles.
2. Classer par composite décroissant.
3. Sélectionner en descendant, avec un **plafond de 2-3 titres par secteur GICS**
   (évite la surpondération naturelle du value sur finance/énergie).
4. **Équipondérer** (ou pondérer ∝ composite) sur ~15-20 lignes.
5. **Rebalancer annuellement ou semestriellement.**

**Pourquoi rebalance long ?** L'edge du composite **croît avec l'horizon** :
+0.73 % à 3 mois → +1.64 % à 6 mois → +3.79 % à 12 mois (spread Q5-Q1). Les primes
qualité et value se matérialisent lentement ; une rotation faible **minimise les
coûts** et colle au profil « positions stables long terme ».

---

## 5. PIPELINE DE DONNÉES

| Donnée | Source | Table |
|--------|--------|-------|
| États financiers longs (18 ans) | **SEC EDGAR** (API company facts, XBRL) | `FundamentalSnapshot` (source='edgar') |
| États financiers récents | yfinance | `FundamentalSnapshot` (source='yfinance') |
| Ratios de marché / valorisation | yfinance `.info` | `TickerInfoSnapshot` |
| Prix journaliers/mensuels | yfinance | `MarketPriceBar`, `MonthlyPriceBar` |
| Composition point-in-time | Wikipédia | `IndexMembership`, `IndexConstituent` |

**Avantage clé d'EDGAR** : historique complet **et date de dépôt réelle** (`filed`),
stockée dans `report_date` → **anti-look-ahead exact** dans le backtest (on n'utilise
une donnée qu'à partir du jour où elle a été publiée). Collecte rafraîchie par le
cron mensuel `job_collect_edgar` (8 du mois).

---

## 6. VALIDATION (BACKTEST)

**Méthode** : à chaque fin de mois (2007-2026, 196 dates), classer l'univers en
quintiles par composite, mesurer le rendement forward moyen à 3/6/12 mois. Un
facteur viable = le meilleur quintile (Q5) surperforme le pire (Q1), spread positif.
Anti-look-ahead via `report_date`, univers point-in-time via `IndexMembership`.

### 6.1 Résultat principal — Quality-Value 50/50

| Horizon | Q5 (meilleur) | Q1 (pire) | Spread Q5-Q1 |
|---------|---------------|-----------|--------------|
| 3 mois  | +4.92 % | +4.19 % | **+0.73 %** |
| 6 mois  | +10.17 % | +8.53 % | **+1.64 %** |
| 12 mois | +21.35 % | +17.55 % | **+3.79 %** |

Spread positif, **monotone**, croissant avec l'horizon, en **absolu ET en relatif**.
Rendement absolu de Q5 fortement positif → compatible buy-and-hold.

### 6.2 Décomposition des deux legs (spread à 12 mois)

| Leg | Spread Q5-Q1 @ 12m | Comportement |
|-----|--------------------|--------------|
| Qualité pure | +3.34 % | Régulière à tous horizons ; la camelote (Q1) sous-performe |
| Valeur pure | +3.08 % | Lente : faible à 3 mois, forte à 12 mois |
| **Composite 50/50** | **+3.79 %** | **Meilleur que chaque leg seul** (diversification) |

C'est la démonstration empirique de l'intérêt de combiner : le composite capte plus
que la qualité ou le value isolés.

### 6.3 Pourquoi le momentum est EXCLU

Test d'ajout du momentum 12-1 comme 3e facteur (QVM), spread à 12 mois :

| Config (Qualité/Valeur/Momentum) | Spread Q5-Q1 @ 12m |
|----------------------------------|--------------------|
| 0.5 / 0.5 / 0 (QV) | **+3.79 %** |
| 0.33 / 0.33 / 0.33 (QVM) | +2.10 % |
| 0.25 / 0.25 / 0.50 | +0.59 % |
| 0 / 0 / 1 (momentum seul) | **-0.91 % (adverse)** |

**Ajouter le momentum dégrade le composite.** Raison : les facteurs fondamentaux
paient sur un **horizon long (12 mois)**, tandis que le momentum 12-1 paie sur
**1-3 mois puis se retourne** (long-term reversal) à 12 mois. Mesurés au même
horizon long, ils se contrarient.

**Conséquence architecturale** : ne pas fusionner. Garder **deux poches séparées** —
le momentum 12-1 (rotation mensuelle, horizon court) et la Quality-Value (rebalance
annuel, horizon long). Elles se **diversifient mutuellement** (horizons différents,
faible corrélation).

### 6.4 Sensibilité aux poids

Pencher le composite (0.7/0.3 ou 0.3/0.7) réduit légèrement le spread (+3.53 % et
+3.29 % vs +3.79 % pour 50/50). **Le 50/50 est le point optimal.**

---

## 7. LIMITES ET PRÉCAUTIONS

- **L'edge est un spread brut** (Q5 vs Q1) : un portefeuille top-quintile vs le
  marché en capte une partie. ~3.8 %/an reste significatif après coûts (large-caps,
  faible rotation).
- **Biais de survivance réduit mais non nul** (point-in-time via `IndexMembership`).
- **Valorisation live** : `TickerInfoSnapshot` n'est qu'un instantané courant → le
  screen live est fiable, mais le backtest reconstruit la valorisation historique
  via market cap = (net income / EPS dilué) × prix.
- **Régime** : 2007-2026 couvre plusieurs cycles (crise 2008, value 2016, growth/IA
  2020-2025) ; le résultat est robuste, mais le value peut sous-performer plusieurs
  années d'affilée (ex. 2023-2025). Horizon d'investissement long requis.
- Ce document n'est **pas un conseil financier**.

---

## 8. IMPLÉMENTATION (RÉFÉRENCES CODE)

| Fichier | Rôle |
|---------|------|
| `fundamental_screen_service.py` | Moteur de score + `build_portfolio()` (screen live) |
| `edgar_collector.py` | Collecte fondamentaux longs SEC EDGAR |
| `fundamentals_collector.py` | Collecte fondamentaux récents + ratios yfinance |
| `backtest_fundamental.py` | Backtest quintiles Quality-Value(-Momentum) |
| `short_data.py` | Chargement prix/membership + helpers point-in-time (partagés) |
| `models.py` | `FundamentalSnapshot` (+`report_date`), `TickerInfoSnapshot` |
| `services.py` | `get_fundamental_screen_service()`, `get_edgar_collector()` |
| `jobs.py` / `scheduler.py` | `job_collect_edgar` (cron mensuel, 8 du mois) |

### Comment lancer

```bash
# Screen live (portefeuille actuel)
python -c "from app import create_app; a=create_app(); \
  a.app_context().push(); \
  from services import get_fundamental_screen_service as g; \
  print(g().build_portfolio(top_n=20, quality_weight=0.5, max_per_sector=3))"

# Backtest (validation / calibration)
python backtest_fundamental.py --wq 0.5 --wv 0.5 --wm 0     # QV
python backtest_fundamental.py --wq 1 --wv 1 --wm 1          # QVM (comparaison)

# Rafraîchir les fondamentaux longs
python _run_edgar.py
```

---

## 9. PARAMÈTRES RECOMMANDÉS (SPEC FINALE)

```
Facteurs        : Qualité 50 % + Valeur 50 %
Momentum        : exclu du composite (poche séparée)
Sélection       : top quintile (~15-20 titres)
Pondération     : équipondérée
Plafond secteur : 2-3 titres / secteur GICS
Rebalance       : annuel ou semestriel
Rôle            : complément de la poche momentum 12-1
```

---

*Sources académiques : Fama & French (1992, 1993), Novy-Marx (2013), Asness,
Frazzini & Pedersen « Quality Minus Junk » (2019), Sloan (1996), Piotroski (2000),
Asness, Moskowitz & Pedersen « Value and Momentum Everywhere » (2013).*
