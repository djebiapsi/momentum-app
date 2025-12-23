# 🚀 Guide de Démarrage Rapide

Ce guide vous accompagne pas à pas pour déployer votre application Momentum Strategy.

---

## ⏱️ Temps estimé : 30 minutes

---

## Étape 1 : Obtenir vos clés API (10 min)

### 1.1 Clé API Tiingo

1. Allez sur **https://www.tiingo.com/**
2. Cliquez sur **"Sign Up"** et créez un compte (gratuit)
3. Confirmez votre email
4. Connectez-vous et allez dans **Account** → **API**
5. Copiez votre **Token** (ressemble à `abc123def456...`)

📝 **Notez votre clé Tiingo :** `_______________________________`

### 1.2 Clé API Resend (pour les emails)

1. Allez sur **https://resend.com/**
2. Cliquez sur **"Start for Free"** et créez un compte
3. Une fois connecté, allez dans **API Keys** (menu gauche)
4. Cliquez sur **"Create API Key"**
5. Donnez un nom (ex: "momentum-app") et créez
6. Copiez la clé (commence par `re_...`)

📝 **Notez votre clé Resend :** `_______________________________`

📝 **Notez votre email personnel :** `_______________________________`

---

## Étape 2 : Créer le repository GitHub (5 min)

### 2.1 Créer un compte GitHub (si nécessaire)

1. Allez sur **https://github.com/**
2. Cliquez sur **"Sign Up"** et suivez les instructions

### 2.2 Créer un nouveau repository

1. Cliquez sur le **"+"** en haut à droite → **"New repository"**
2. Nom : `momentum-strategy`
3. Choisissez **Public** ou **Private**
4. ❌ Ne cochez PAS "Add a README file"
5. Cliquez sur **"Create repository"**

### 2.3 Uploader les fichiers

**Option A - Via l'interface GitHub (plus simple) :**
1. Sur la page de votre nouveau repo, cliquez sur **"uploading an existing file"**
2. Faites glisser TOUS les fichiers du dossier `momentum-app`
3. Cliquez sur **"Commit changes"**

**Option B - Via Git en ligne de commande :**
```bash
cd "C:\Users\kouat\OneDrive\Documents\Stratégie\momentum-app"
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/VOTRE_USERNAME/momentum-strategy.git
git push -u origin main
```

---

## Étape 3 : Déployer sur Render (10 min)

### 3.1 Créer un compte Render

1. Allez sur **https://render.com/**
2. Cliquez sur **"Get Started for Free"**
3. Choisissez **"GitHub"** pour vous connecter
4. Autorisez Render à accéder à vos repos

### 3.2 Créer le Blueprint

1. Dans le dashboard Render, cliquez sur **"New +"** → **"Blueprint"**
2. Sélectionnez votre repo `momentum-strategy`
3. Render détecte automatiquement le fichier `render.yaml`
4. Cliquez sur **"Apply"**

⏳ Attendez quelques minutes que le déploiement se termine.

### 3.3 Configurer les variables d'environnement

1. Dans Render, cliquez sur votre service **"momentum-strategy"**
2. Allez dans l'onglet **"Environment"**
3. Ajoutez ces variables :

| Key | Value |
|-----|-------|
| `TIINGO_API_KEY` | `votre_cle_tiingo_copiee` |
| `RESEND_API_KEY` | `re_votre_cle_resend` |
| `EMAIL_FROM` | `onboarding@resend.dev` |
| `EMAIL_TO` | `votre.email@gmail.com` |

4. Cliquez sur **"Save Changes"**
5. Le service va redémarrer automatiquement

---

## Étape 4 : Tester l'application (5 min)

### 4.1 Accéder à l'app

1. Dans Render, copiez l'URL de votre service (ex: `https://momentum-strategy.onrender.com`)
2. Ouvrez cette URL dans votre navigateur

### 4.2 Premier test

1. Cliquez sur **"🔄 Mettre à jour"**
2. Attendez le calcul (30-60 secondes)
3. Les recommandations s'affichent ! 🎉

### 4.3 Tester l'email

1. Allez dans l'onglet **"Réglages"**
2. Cliquez sur **"🧪 Envoyer un email de test"**
3. Vérifiez votre boîte mail (et les spams)

---

## Étape 5 : Installer sur iPhone (2 min)

1. Ouvrez **Safari** sur votre iPhone
2. Allez sur l'URL de votre app
3. Appuyez sur le bouton **Partager** (carré avec flèche vers le haut)
4. Faites défiler et appuyez sur **"Sur l'écran d'accueil"**
5. Donnez un nom (ex: "Momentum") et appuyez sur **"Ajouter"**

✅ **L'app est maintenant sur votre écran d'accueil comme une vraie app !**

---

## 🎯 Utilisation mensuelle

### Chaque 1er du mois (automatique)

- L'app calcule automatiquement le momentum à 8h00 UTC
- Vous recevez un email avec les recommandations

### À tout moment (manuel)

1. Ouvrez l'app
2. Cliquez sur **"Mettre à jour"** ou **"Mettre à jour + Envoyer email"**

---

## ❓ Résolution de problèmes

### L'app ne charge pas ?
- Attendez quelques minutes (Render gratuit peut être lent au démarrage)
- Rafraîchissez la page

### Les calculs échouent ?
- Vérifiez que `TIINGO_API_KEY` est correctement configurée
- Vérifiez que votre panel contient des tickers valides (ex: AAPL, pas Apple)

### Pas d'email reçu ?
- Vérifiez vos spams
- Vérifiez que `RESEND_API_KEY` et `EMAIL_TO` sont configurés
- Testez avec le bouton "Envoyer un email de test"

### Erreur 500 ?
- Dans Render, allez dans **"Logs"** pour voir les erreurs
- Vérifiez toutes les variables d'environnement

---

## 📞 Support

Si vous avez des questions, consultez la documentation complète dans `README.md`.

---

**Félicitations ! Votre application Momentum Strategy est prête ! 🎉**

