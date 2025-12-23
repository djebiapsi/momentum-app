## Formule recommandée 

### **Score de sélection univers**

[
\text{Score}_i = \log(\text{MarketCap}_i) \times \log(\text{ADV}_i)
]

où :

* (\text{MarketCap}_i) = capitalisation boursière moyenne sur 6 ou 12 mois
* (\text{ADV}_i) = Average Daily Dollar Volume sur 6 mois
  [
  \text{ADV} = \text{Prix} \times \text{Volume moyen}
  ]

---

## Procédure exacte (étape par étape)

### 1. Univers brut


* Toutes les actions US (NYSE + Nasdaq)


---

### 2. Filtre minimum (obligatoire)

Éliminer toute action qui ne respecte pas :

[
\text{MarketCap} < 1\text{B$} \Rightarrow \text{exclue}
]

[
\text{ADV} < 5\text{M$} \Rightarrow \text{exclue}
]

👉 Ceci élimine :

* micro-caps
* illiquides
* titres manipulables

---

### 3. Calcul du score

Pour chaque action restante :

[
\text{Score}_i = \log(\text{MarketCap}_i) \times \log(\text{ADV}_i)
]

Pourquoi le logarithme ?

* Réduit la domination des méga-caps
* Stabilise les rangs
* Conforme aux modèles multifactoriels

---

### 4. Classement et sélection

* Trier par **Score décroissant**
* Prendre les **50 premiers**

👉 **C’est crucial** pour éviter l’overfitting.

---

## Pourquoi cette formule est la meilleure

### 1. Elle est ex-ante

* Aucune information future
* Aucun lien avec le momentum
* Aucun paramètre ajusté

---

### 2. Elle est robuste empiriquement

Les ETF momentum (MSCI, AQR) utilisent :

* taille
* liquidité
* volatilité

mais **jamais** de critères qualitatifs ou fondamentaux subjectifs.

---

### 3. Elle minimise le turnover

* Les grandes, liquides changent peu
* Le top 50 est stable dans le temps

---

### 4. Elle est compatible petit capital

* Pas de spread dévastateur
* Exécution IBKR réaliste
* Pas de slippage excessif

---

## Variante encore plus simple (si tu veux aller au plus pur)

### Ultra-minimaliste mais valide :

[
\text{Score}_i = \log(\text{MarketCap}_i)
]

Puis :

* top 50
* momentum dessus

👉 C’est littéralement la base des modèles CRSP.

---

## Ce qu’il NE FAUT PAS faire (important)

❌ Sélectionner avec :

* croissance du CA
* PER
* ROE
* storytelling sectoriel
* “bon feeling”

👉 Tout ça introduit :

* biais humain
* data-snooping
* instabilité

---

## Résumé clair

| Objectif          | Méthode               |
| ----------------- | --------------------- |
| Sélection neutre  | MarketCap + liquidité |
| Robustesse        | Logarithmes           |
| Pas d’overfitting | Univers figé 12 mois  |
| Momentum pur      | 12-1 après sélection  |

