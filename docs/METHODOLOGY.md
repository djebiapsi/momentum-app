# 📊 Méthodologie Momentum Strategy

## Vue d'ensemble

Cette application implémente deux stratégies de momentum complémentaires :
- **Stratégie Long** : Acheter les actions ayant le meilleur momentum haussier
- **Stratégie Short** : Vendre à découvert les actions ayant le pire momentum baissier

---

## 🟢 STRATÉGIE LONG

### 1. Sélection du Panel (50 tickers)

**Source des données** : Finviz (gratuit, sans API)

**Critères de sélection** :

| Critère | Valeur | Raison |
|---------|--------|--------|
| Market Cap | ≥ $10B | Liquidité, stabilité, couverture analystes |
| Avg Daily Volume | ≥ 1M actions | Facilité d'exécution, spread serré |
| ADV (Avg Dollar Volume) | ≥ $5M | Volume en dollars pour éviter les penny stocks |

**Score de classement** :
```
Score = log(Market Cap) × log(ADV)
```

Ce score favorise les grandes capitalisations liquides. Les 50 meilleurs scores sont sélectionnés.

**Pourquoi ces critères ?**
- Les grandes capitalisations offrent moins de volatilité idiosyncratique
- La liquidité permet d'entrer/sortir sans impact sur le prix
- Le score logarithmique évite une domination des mega-caps

### 2. Calcul du Momentum Long

**Méthode** : Momentum 12-1 (classique académique)

**Formule** :
```
Momentum = (Prix[T-1 mois] - Prix[T-12 mois]) / Prix[T-12 mois] × 100
```

**Interprétation** :
- On mesure le rendement sur les 12 derniers mois
- On **exclut le mois le plus récent** (T-1 mois au lieu de T)
- Cette exclusion évite l'effet de "mean reversion" à très court terme

**Données utilisées** : Prix mensuels ajustés (dividendes + splits) via API Tiingo

**Pourquoi exclure le dernier mois ?**
- Recherche académique (Jegadeesh & Titman, 1993) : le dernier mois présente un effet de retour à la moyenne
- Le momentum persiste sur 12 mois mais s'inverse souvent le mois suivant
- Exclure ce mois améliore significativement la performance

### 3. Génération des Recommandations

1. Les actions sont triées par momentum **décroissant** (meilleurs en premier)
2. Les **Top N** reçoivent le signal "Investir"
3. L'allocation est équipondérée : 100% / N par action
4. Les autres reçoivent le signal "Sortir"

---

## 🔴 STRATÉGIE SHORT

### 1. Sélection du Panel (50 tickers)

**Source des données** : Finviz (gratuit, sans API)

**Critères de sélection (stricts)** :

| Critère | Valeur | Raison |
|---------|--------|--------|
| Market Cap | ≥ $2B | Shortabilité (disponibilité des titres à emprunter) |
| Avg Volume | ≥ 500K | Liquidité pour shorter |
| Price | ≥ $5 | Évite les penny stocks (règle SEC) |
| Perf 1 mois | ≤ -8% | Momentum négatif court terme confirmé |
| Perf 3 mois | ≤ -15% | Momentum négatif moyen terme confirmé |
| Price < SMA50 | ✓ | Tendance baissière court terme |
| Price < SMA200 | ✓ | Tendance baissière long terme |
| SMA50 < SMA200 | ✓ | **Death Cross** confirmé |

**Score de classement** :
```
Score = (Perf_1M × 0.4) + (Perf_3M × 0.6)
```

Les 50 scores les plus **négatifs** sont sélectionnés.

**Pourquoi ces critères ?**
- La configuration technique (Death Cross) confirme une tendance baissière établie
- Le momentum négatif sur plusieurs horizons temporels réduit le risque de "short squeeze"
- Les contraintes de liquidité garantissent que les titres sont empruntables

### 2. Calcul du Momentum Short

**Méthode** : Momentum Court Terme avec exclusion des jours récents

**Formule** :
```
Momentum = (Prix[T-5] / Prix[T-63]) - 1
```

Soit la performance de T-63 à T-5, **excluant les 5 derniers jours**.

**Paramètres par défaut** :
- **Lookback** : 63 jours (~3 mois de trading)
- **Skip recent** : 5 jours (dernière semaine exclue)

**Segments NON recouvrants** :
```
T-63 -------- T-5 -------- T
|-- Momentum --|-- Exclu --|
```

**Interprétation** :
- Un momentum très **négatif** = forte baisse sur la période
- La performance récente (T-5 à T) est affichée à titre informatif mais n'entre pas dans le calcul

**Pourquoi exclure les 5 derniers jours ?**
- Évite l'**overshoot** : les actions qui chutent trop vite rebondissent souvent
- Capture la **vraie tendance** : on veut des baisses continues, pas des crashs ponctuels
- Plus **robuste** : réduit le risque de shorter juste avant un rebond technique

### 3. Génération des Recommandations

1. Les actions sont triées par score **croissant** (plus négatifs en premier)
2. Les **Top N** reçoivent le signal "Shorter"
3. L'allocation est équipondérée : 100% / N par action
4. Les autres reçoivent le signal "Couvrir"

---

## 📈 Résumé des Différences

| Aspect | Long | Short |
|--------|------|-------|
| **Objectif** | Acheter les gagnants | Vendre les perdants |
| **Panel** | MarketCap ≥ $10B, ADV ≥ $5M | MarketCap ≥ $2B, Death Cross |
| **Momentum** | 12-1 (12 mois, exclut 1 mois) | 63-5 (63 jours, exclut 5 jours) |
| **Données** | Mensuelles | Journalières |
| **Tri** | Décroissant (meilleurs) | Croissant (pires) |
| **Signal** | Investir / Sortir | Shorter / Couvrir |

---

## ⚠️ Avertissements

### Risques de la stratégie Short
- **Pertes illimitées** : contrairement au long, une position short peut perdre plus de 100%
- **Short squeeze** : si trop d'investisseurs shortent, le prix peut exploser à la hausse
- **Coût d'emprunt** : shorter coûte des frais de financement quotidiens
- **Rappel des titres** : le prêteur peut rappeler les titres à tout moment

### Limites de la stratégie
- Basée sur des données historiques (le passé ne prédit pas le futur)
- Les conditions de marché peuvent invalider le momentum
- Nécessite une exécution disciplinée et régulière

### Recommandations
- Utiliser avec une gestion de risque stricte
- Définir des stop-loss pour chaque position
- Rebalancer mensuellement
- Ne pas sur-pondérer une seule position

---

## 📚 Références Académiques

1. **Jegadeesh, N., & Titman, S. (1993)**. "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency". *Journal of Finance*.

2. **Carhart, M. (1997)**. "On Persistence in Mutual Fund Performance". *Journal of Finance*.

3. **Asness, C., Moskowitz, T., & Pedersen, L. (2013)**. "Value and Momentum Everywhere". *Journal of Finance*.

---

## 🔧 Configuration Technique

### API Utilisées
- **Finviz** : Screening gratuit (pas d'API key requise)
- **Tiingo** : Données historiques (API key requise, 50 appels/mois gratuit)

### Paramètres Configurables
- `nb_top` : Nombre d'actions à sélectionner (défaut: 5)
- `short_lookback` : Période lookback Short en jours (défaut: 63)
- `short_skip_recent` : Jours récents à exclure (défaut: 5)

---

## 📈 MODULE OPTIONS - PUT & PUT SPREAD

### Stratégie Options Short Momentum

Le module Options permet d'exprimer une vue baissière via des options PUT ou PUT SPREAD avec un risque contrôlé.

### 1. Type d'instrument

| Instrument | Avantages | Inconvénients |
|------------|-----------|---------------|
| **PUT simple** | Profit illimité si baisse | Prime plus élevée |
| **PUT SPREAD** | Prime réduite, risque défini | Profit plafonné |

### 2. Paramètres de sélection

**Maturité (DTE)** :
- Cible : 30 à 60 jours
- Règle : Choisir l'expiration la plus proche ≥ 30 jours

**Delta (critère scientifique)** :
- PUT Long (acheté) : Delta cible ∈ [-0.40 ; -0.25]
- PUT Short (vendu, pour spread) : Delta cible ≈ -0.10

### 3. Construction du PUT SPREAD

```
PUT SPREAD = Achat PUT (Strike haut) + Vente PUT (Strike bas)
```

| Composant | Strike | Delta |
|-----------|--------|-------|
| PUT Long (acheté) | Plus élevé | -0.30 |
| PUT Short (vendu) | Plus bas | -0.10 |

**Métriques calculées** :
- **Net Debit** = Prix PUT Long - Prix PUT Short (prime payée)
- **Max Profit** = (Strike Long - Strike Short) - Net Debit
- **Max Loss** = Net Debit (prime payée)
- **Breakeven** = Strike Long - Net Debit
- **Risk/Reward** = Max Profit / Max Loss

### 4. Filtre de volatilité

Pour éviter de surpayer les options :
```
IV Rank ≤ 60
OU
IV implicite ≤ Vol réalisée (30j) × 1.1
```

### 5. Conditions d'entrée

Entrée **uniquement si** :
- RSI(14) ∈ [40 ; 55]
- Pullback ≤ 50% de l'impulsion baissière
- Pas de gap haussier > 3%

### 6. Gestion de position

| Règle | Seuil | Action |
|-------|-------|--------|
| **Take Profit** | +70% à +100% de la prime | Sortie partielle ou totale |
| **Stop Loss** | -50% de la prime | Sortie automatique |
| **Time Stop** | DTE ≤ 14 jours | Sortie forcée |

### 7. Sorties anticipées

Sortie immédiate si :
- Price > SMA50
- RSI > 60
- Momentum Score devient positif

### 8. Calcul Black-Scholes

Le calculateur utilise le modèle Black-Scholes pour estimer :
- Prix des options (PUT/CALL)
- Greeks : Delta, Gamma, Theta, Vega
- Strikes optimaux basés sur le delta cible

**Formule Black-Scholes (PUT)** :
```
P = K × e^(-rT) × N(-d2) - S × N(-d1)

d1 = [ln(S/K) + (r + σ²/2)T] / (σ√T)
d2 = d1 - σ√T
```

Où :
- S = Prix spot
- K = Strike
- T = Temps jusqu'à expiration (années)
- r = Taux sans risque
- σ = Volatilité implicite
- N() = Distribution normale cumulative

