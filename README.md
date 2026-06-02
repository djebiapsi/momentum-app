# Momentum Strategy App

Application PWA pour suivre une stratégie momentum mensuelle (12-1) sur un panel d'actions, avec intégration IBKR pour le suivi des positions en temps réel.

## Fonctionnalités

- **Momentum 12-1** : calcul automatique via API Tiingo, recommandations mensuelles par email
- **Stratégie Short** : momentum 63-5 avec détection Death Cross via Finviz
- **Options** : calculateur Black-Scholes PUT / PUT SPREAD
- **IBKR** : connexion IB Gateway, positions en temps réel, email de suivi toutes les 2h (heures de marché US)
- **PWA** : installable sur iPhone / Android

---

## Déploiement sur Hetzner (Docker)

### Prérequis serveur

- VPS Hetzner Ubuntu 22.04+ (2 vCPU / 4 GB minimum)
- Docker + Compose plugin (`apt install docker.io docker-compose-plugin`)
- Domaine pointé sur l'IP du serveur

### Première installation (une seule fois sur le serveur)

```bash
# Réseau partagé Traefik (une seule fois par serveur, commun à tous les projets)
docker network create traefik_proxy

# Dossier du projet
mkdir -p /opt/momentum-app/letsencrypt
cd /opt/momentum-app

# Fichier requis par Traefik pour Let's Encrypt
touch letsencrypt/acme.json
chmod 600 letsencrypt/acme.json

# Copier docker-compose.yml et créer le .env
cp docker-compose.yml /opt/momentum-app/
cp env-example.txt .env
# → éditer .env avec les vraies valeurs
```

### Variables d'environnement (`.env` sur le serveur)

| Variable | Description | Requis |
|---|---|---|
| `DOMAIN` | Nom de domaine (ex: `momentum.mondomaine.com`) | Oui |
| `ACME_EMAIL` | Email pour Let's Encrypt | Oui |
| `DB_PASSWORD` | Mot de passe PostgreSQL | Oui |
| `SECRET_KEY` | Clé secrète Flask (aléatoire, longue) | Oui |
| `ADMIN_PASSWORD` | PIN numérique d'accès admin | Non |
| `TIINGO_API_KEY` | Clé API Tiingo pour les données de marché | Oui |
| `RESEND_API_KEY` | Clé API Resend pour les emails | Non |
| `EMAIL_FROM` | Email expéditeur (domaine vérifié Resend) | Non |
| `EMAIL_TO` | Email destinataire des alertes | Non |
| `IB_USERNAME` | Identifiant IBKR | Non |
| `IB_PASSWORD` | Mot de passe IBKR | Non |
| `IB_TRADING_MODE` | `paper` ou `live` (défaut : `paper`) | Non |
| `VNC_PASSWORD` | Mot de passe VNC IB Gateway (debug) | Non |
| `IB_GATEWAY_PORT` | Port TWS API (4001 live, 4002 paper) | Non |

### Démarrage

```bash
cd /opt/momentum-app
docker compose up -d
```

### Pour les autres projets sur le même serveur

Chaque projet a son propre `docker-compose.yml` qui s'appuie sur le réseau `traefik_proxy` externe. Traefik est défini dans le compose du premier projet lancé — les suivants retirent le bloc `traefik` et utilisent uniquement les labels.

---

## CI/CD automatique (GitHub Actions)

Le pipeline est défini dans `.github/workflows/deploy.yml` :

1. `push` sur `main` → build de l'image Docker
2. Push vers GitHub Container Registry (`ghcr.io/djebiapsi/momentum-app`)
3. SSH sur le serveur Hetzner → `docker compose pull app && docker compose up -d app`

### Secrets GitHub à configurer

| Secret | Description |
|---|---|
| `HETZNER_HOST` | IP du serveur |
| `HETZNER_USER` | Utilisateur SSH (ex: `ubuntu`) |
| `HETZNER_SSH_KEY` | Clé privée SSH (format PEM) |

---

## IBKR / Interactive Brokers

### Architecture

```
Flask app ──ib_insync──► IB Gateway container (gnzsnz/ib-gateway)
                              │
                        auto-login via TWS_USERID / TWS_PASSWORD
                        port 4001 (live) / 4002 (paper)
```

- L'IB Gateway container gère l'authentification IBKR automatiquement au démarrage
- La connexion est en `readonly=True` (lecture seule)
- Les identifiants peuvent aussi être saisis via l'interface (Settings → IBKR) — stockés chiffrés AES-256 en base

> **Important** : changer `SECRET_KEY` invalide les identifiants chiffrés stockés en base. Re-saisir les identifiants IBKR dans l'interface après toute rotation de `SECRET_KEY`.

### Cron positions automatique

Email envoyé à **9h30, 11h30, 13h30, 15h30 ET** (lun-ven) avec le récapitulatif des positions ouvertes.

### Endpoints API IBKR

| Méthode | Route | Auth | Description |
|---|---|---|---|
| GET | `/api/ibkr/status` | Non | Statut de connexion |
| POST | `/api/ibkr/connect` | Admin | Connexion / reconnexion |
| POST | `/api/ibkr/credentials` | Admin | Sauvegarde identifiants |
| GET | `/api/ibkr/positions` | Admin | Positions temps réel |

---

## Développement local

```bash
# Setup
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux
pip install -r requirements.txt
cp env-example.txt .env      # remplir les clés API

# Lancer
flask run                    # ou: python app.py
# → http://localhost:5000

# Tests
python -m pytest test_options_service.py -v
```

En local, l'IB Gateway n'est pas disponible — les routes `/api/ibkr/*` renvoient une erreur de connexion attendue.

---

## Structure du projet

```
momentum-app/
├── app.py                    # Flask : 37+ routes API, APScheduler
├── config.py                 # Configuration et variables d'environnement
├── models.py                 # Modèles SQLAlchemy (Long, Short, Options, Settings)
├── momentum_service.py       # Calcul momentum 12-1 via Tiingo
├── email_service.py          # Emails via Resend (recommandations + positions IBKR)
├── ibkr_service.py           # Connexion IB Gateway, récupération positions
├── screener_service.py       # Screener Long via Tiingo IEX
├── short_screener_service.py # Screener Short legacy
├── finviz_screener_service.py# Screener Long/Short via Finviz
├── options_service.py        # Black-Scholes PUT / PUT SPREAD
├── cache_utils.py            # Cache mémoire
├── Dockerfile                # Image Docker (python:3.11-slim + gunicorn)
├── docker-compose.yml        # Stack : app + PostgreSQL + IB Gateway + Traefik
├── .github/workflows/
│   └── deploy.yml            # CI/CD : build → GHCR → SSH Hetzner
├── requirements.txt
├── static/                   # PWA manifest, service worker, icônes
├── templates/
│   └── index.html            # Vue.js SPA (4700+ lignes)
└── docs/                     # Documentation de recherche (stratégies, méthodologie)
```

---

## Automatisation

| Job | Fréquence | Description |
|---|---|---|
| `monthly_momentum` | 1er du mois à 8h00 UTC | Calcul momentum + email recommandations |
| `ibkr_positions` | 9h30/11h30/13h30/15h30 ET (lun-ven) | Email récapitulatif positions IBKR |

---

## Avertissement

**Ceci n'est pas un conseil financier.**

Cette application est un outil de suivi personnel. Les performances passées ne garantissent pas les résultats futurs. Faites vos propres recherches avant d'investir.
