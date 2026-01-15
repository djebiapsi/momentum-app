# Tests Unitaires - OptionsService

## Vue d'ensemble

Le fichier `test_options_service.py` contient une suite complète de tests unitaires pour valider les calculs Black-Scholes, les Greeks, et la logique de stratégie d'options.

## Structure des tests

### 1. Tests Black-Scholes Core
- Calcul de `d1` et `d2`
- Prix PUT et CALL
- Parité put-call
- Cas limites (expiration, volatilité nulle)

### 2. Tests Greeks
- Delta PUT/CALL (plages de valeurs)
- Gamma (toujours positif)
- Theta (négatif pour décroissance temporelle)
- Vega (positif)
- Cas limites

### 3. Tests Strike Finder
- Recherche de strike par delta (PUT et CALL)
- Delta extrêmes

### 4. Tests Stratégies
- PUT SPREAD (structure, breakeven, risk/reward, Greeks)
- PUT simple (structure, breakeven, Greeks)

### 5. Tests Strategy Engine
- Conditions d'entrée (hard et soft)
- Scoring des conditions soft
- Sélection PUT vs PUT SPREAD
- IV Rank manquant
- Tous les champs requis

### 6. Tests Helpers
- Conversion DTE en années
- Calcul date d'expiration
- Estimation IV depuis prix de marché

### 7. Tests Volatilité Historique
- Calcul basique
- Données insuffisantes
- Prix constants

### 8. Tests d'Intégration
- Workflow complet
- Volatilité extrême
- DTE très court

## Exécution des tests

### Avec unittest (standard Python)
```bash
python test_options_service.py
```

### Avec pytest (si installé)
```bash
pytest test_options_service.py -v
```

### Avec couverture de code (si pytest-cov installé)
```bash
pytest test_options_service.py --cov=options_service --cov-report=html
```

## Résultats attendus

Tous les tests devraient passer. Les tests vérifient :
- ✅ Calculs mathématiques corrects
- ✅ Plages de valeurs raisonnables
- ✅ Gestion des cas limites
- ✅ Cohérence des structures de données
- ✅ Logique métier de la stratégie

## Ajout de nouveaux tests

Pour ajouter un nouveau test :

1. Créer une méthode `test_*` dans la classe appropriée
2. Utiliser `self.assert*` pour les assertions
3. Documenter le test avec une docstring

Exemple :
```python
def test_ma_nouvelle_fonctionnalite(self):
    """Test ma nouvelle fonctionnalité."""
    result = self.service.ma_fonction(param1, param2)
    self.assertGreater(result, 0)
```

## Notes importantes

- Les tests utilisent des valeurs de test standardisées (S=100, K=95, T=45/365, etc.)
- Les tolérances d'arrondi sont ajustées selon la précision attendue
- Les cas limites sont testés pour éviter les erreurs en production

