---

# STRATÉGIE OPTIONS – MOMENTUM SHORT

**Version implémentable (rules-based, sans discrétion humaine)**

---

## 1. Objectif de la stratégie

* Exploiter des **mouvements baissiers courts (1–3 mois)**
* Via des **options à convexité contrôlée**
* Avec **risque plafonné et EV positive**

---

## 2. Univers d’investissement (INPUT DATA)

### 2.1 Actions éligibles

```text
Market Cap ≥ 2B$
Price ≥ 5$
Avg Volume (30j) ≥ 500k
Shortable = true
```

### 2.2 Exclusions

```text
Earnings dans ≤ 7 jours
Biotech / Pharma pré-clinique (afficher un disclaimer car complexe à filtrer)
Penny stocks
```

---

## 3. Pré-sélection Momentum Short (SIGNAL ENGINE)

### 3.1 Calcul des performances (non recouvrantes)

```text
Perf_63_5 = (Price[t-5] / Price[t-63]) - 1
Perf_5_0  = (Price[t]   / Price[t-5])  - 1
```

### 3.2 Score Momentum Short

```text
MomentumScore = Perf_63_5 - Perf_5_0
```

### 3.3 Conditions minimales de signal

```text
Perf_63_5 ≤ -15%
Perf_5_0  ≤ +5%
Price < SMA50
Price < SMA200
SMA50 < SMA200
```

### 3.4 Sélection finale

```text
Classer par MomentumScore croissant
Sélectionner Top N (ex : N = 5 à 8)
```

---

## 4. MODULE OPTIONS – SÉLECTION DE L’OPTION

### 4.1 Type d’option

```text
Instrument = PUT ou PUT SPREAD
```
 Sélectionner le plus pertinent pour chaque cas
---

### 4.2 Maturité (DTE)

```text
DTE cible = 30 à 60 jours
```

Règle :

```text
Choisir expiration la plus proche ≥ 30 jours
```

---

### 4.3 Choix du strike (critère scientifique)

> ⚠️ On choisit **le delta**, pas le strike.

```text
Delta cible (put long) ∈ [-0.25 ; -0.40]
```

Pour un put spread :

```text
Put long   : delta ≈ -0.30
Put short  : delta ≈ -0.10
```

---

### 4.4 Filtre de volatilité (anti-surpaiement)

```text
IV Rank ≤ 60
OU
IV implicite ≤ Vol réalisée (30j) × 1.1
```

Sinon :

```text
TRADE REJETÉ
```

---

## 5. PRICING & RISK MANAGEMENT

### 5.1 Budget de risque par trade

```text
Risk per trade = 0.5% à 0.75% du capital
```

```text
Max risk mensuel = 5% du capital
```

---

### 5.2 Taille de position

```text
Nb contrats = floor(
  RiskBudget / (Prime × ContractMultiplier)
)
```

Pour spread :

```text
Risk = (StrikeLong - StrikeShort - Prime) × Multiplier
```

---

## 6. CONDITIONS D’ENTRÉE (TIMING)

Entrée **uniquement si** :

```text
Dernier mouvement = pullback ≤ 50% de l’impulsion baissière
RSI(14) ∈ [40 ; 55]
Pas de gap haussier > 3%
```

Sinon :

```text
Signal en attente
```

---

## 7. GESTION DE POSITION (LIFECYCLE)

### 7.1 Take Profit

```text
+70% à +100% de la prime → sortie partielle ou totale
```

### 7.2 Stop Loss

```text
-50% de la prime payée → sortie automatique
```

### 7.3 Time Stop

```text
DTE ≤ 14 jours → sortie forcée
```

---

## 8. SORTIES ANTICIPÉES (RISK EVENTS)

Sortie immédiate si :

```text
Price > SMA50
OU
RSI > 60
OU
MomentumScore devient positif
```

---

## 9. OUTPUT POUR L’APPLICATION (STRUCTURE JSON)

Exemple de payload que Cursor peut exploiter :

```json
{
  "ticker": "XYZ",
  "signal": "SHORT_MOMENTUM_OPTION",
  "score": -0.82,
  "option": {
    "type": "PUT_SPREAD",
    "expiration": "2026-03-20",
    "long_strike": 18,
    "short_strike": 15,
    "delta_long": -0.31,
    "delta_short": -0.11,
    "max_risk_pct": 0.75
  },
  "entry_conditions": {
    "RSI_range": [40, 55],
    "pullback_max": 0.5
  },
  "exit_rules": {
    "take_profit": "+80%",
    "stop_loss": "-50%",
    "time_stop_days": 14
  }
}
```

---

## 10. PHILOSOPHIE DE LA STRATÉGIE (à documenter)

* Peu de trades
* Convexité > fréquence
* Discipline stricte
* Aucun trade “forcé”
* Survie > performance

---