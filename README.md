# 📈 Momentum Strategy App

Application PWA pour suivre une stratégie momentum mensuelle (12-1) sur un panel d'actions.

## ✨ Fonctionnalités

- 🔄 **Calcul du momentum** : Rendement 12-1 automatique via API Tiingo
- 📊 **Panel personnalisable** : Ajoutez/retirez des actions facilement
- 📧 **Notifications email** : Recevez les recommandations chaque mois
- 📅 **Historique** : Consultez les recommandations passées
- ⚙️ **Paramètres flexibles** : Nombre de top actions, date de calcul
- 📱 **PWA** : Installable sur iPhone comme une app native

---

## 🚀 Déploiement sur Render (Gratuit)

### Étape 1 : Créer un compte GitHub et Render

1. Créez un compte sur [GitHub](https://github.com) si vous n'en avez pas
2. Créez un compte sur [Render](https://render.com) (connexion avec GitHub)

### Étape 2 : Créer le repository GitHub

1. Créez un nouveau repository sur GitHub (ex: `momentum-strategy`)
2. Uploadez tous les fichiers du dossier `momentum-app`

**En ligne de commande :**
```bash
cd momentum-app
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/VOTRE_USERNAME/momentum-strategy.git
git push -u origin main
```

### Étape 3 : Déployer sur Render

1. Allez sur [Render Dashboard](https://dashboard.render.com)
2. Cliquez sur **"New +"** → **"Blueprint"**
3. Connectez votre repo GitHub
4. Render va automatiquement détecter le fichier `render.yaml`
5. Cliquez sur **"Apply"**

### Étape 4 : Configurer les variables d'environnement

Dans le dashboard Render, allez dans votre service et configurez :

| Variable | Description | Où l'obtenir |
|----------|-------------|--------------|
| `TIINGO_API_KEY` | Clé API Tiingo | [tiingo.com](https://www.tiingo.com/) |
| `RESEND_API_KEY` | Clé API Resend | [resend.com](https://resend.com) |
| `EMAIL_FROM` | Email expéditeur | `onboarding@resend.dev` (par défaut) |
| `EMAIL_TO` | Votre email | Votre adresse email personnelle |

### Étape 5 : Installer sur iPhone

1. Ouvrez Safari sur iPhone
2. Allez sur `https://votre-app.onrender.com`
3. Appuyez sur le bouton **Partager** (carré avec flèche)
4. Sélectionnez **"Sur l'écran d'accueil"**
5. L'app apparaît comme une vraie application ! 🎉

---

## 🔑 Obtenir les clés API

### Tiingo API (Gratuit)

1. Créez un compte sur [tiingo.com](https://www.tiingo.com/)
2. Allez dans **Account** → **API** → **Token**
3. Copiez votre token

### Resend API (Gratuit - 100 emails/jour)

1. Créez un compte sur [resend.com](https://resend.com)
2. Allez dans **API Keys** → **Create API Key**
3. Copiez la clé (commence par `re_`)

> **Note** : Par défaut, utilisez `onboarding@resend.dev` comme EMAIL_FROM.
> Pour utiliser votre propre domaine, vérifiez-le dans Resend.

---

## 💻 Développement local

### Prérequis

- Python 3.9+
- pip

### Installation

```bash
# Cloner le projet
git clone https://github.com/VOTRE_USERNAME/momentum-strategy.git
cd momentum-strategy

# Créer l'environnement virtuel
python -m venv venv

# Activer (Windows)
venv\Scripts\activate

# Activer (Mac/Linux)
source venv/bin/activate

# Installer les dépendances
pip install -r requirements.txt
```

### Configuration

1. Copiez `env-example.txt` en `.env`
2. Remplissez avec vos vraies valeurs

```bash
# Windows
copy env-example.txt .env

# Mac/Linux
cp env-example.txt .env
```

### Lancement

```bash
python app.py
```

Ouvrez http://localhost:5000

---

## 📱 Utilisation

### Dashboard

- **Mettre à jour** : Calcule le momentum actuel
- **Mettre à jour + Email** : Calcule et envoie par email

### Panel

- Ajoutez des tickers (ex: AAPL, MSFT)
- Supprimez ceux que vous ne voulez plus suivre

### Paramètres

- **Nombre de Top Actions** : Combien sélectionner (défaut: 5)
- **Date de calcul** : Vide = aujourd'hui, ou date spécifique

### Automatisation

L'app calcule automatiquement le momentum le **1er de chaque mois à 8h00 UTC** et envoie un email.

---

## 🛠️ Structure du projet

```
momentum-app/
├── app.py                 # Application Flask principale
├── config.py              # Configuration et secrets
├── models.py              # Modèles de base de données
├── momentum_service.py    # Logique métier (calcul momentum)
├── email_service.py       # Service d'envoi d'emails
├── requirements.txt       # Dépendances Python
├── render.yaml            # Configuration Render
├── static/
│   ├── manifest.json      # Config PWA
│   ├── sw.js              # Service Worker
│   └── icons/             # Icônes de l'app
└── templates/
    └── index.html         # Interface utilisateur
```

---

## ⚠️ Avertissement

**Ceci n'est pas un conseil financier.**

Cette application est un outil de suivi personnel. Les performances passées ne garantissent pas les résultats futurs. Faites vos propres recherches avant d'investir.

---

## 📄 Licence

MIT License - Utilisez librement pour votre usage personnel.

