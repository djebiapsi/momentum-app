# -*- coding: utf-8 -*-
"""
Service de calcul du Momentum
=============================
Contient toute la logique métier pour calculer le momentum 12-1.
Adapté du script strategy.py original.
"""

import requests
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta


class MomentumService:
    """
    Service pour calculer le momentum des actions.
    """
    
    def __init__(self, api_key):
        """
        Initialise le service avec la clé API Tiingo.
        
        Args:
            api_key: Clé API Tiingo
        """
        self.api_key = api_key
        self.base_url = "https://api.tiingo.com/tiingo/daily"
    
    def calculer_periode_analyse(self, date_calcul):
        """
        Calcule les dates de début et fin pour récupérer les données.
        On a besoin de 13 mois de données pour calculer le momentum 12-1.
        
        Args:
            date_calcul: Date de fin du calcul (datetime ou string YYYY-MM-DD)
        
        Returns:
            tuple: (date_debut, date_fin) au format string "YYYY-MM-DD"
        """
        if isinstance(date_calcul, str):
            date_calcul = datetime.strptime(date_calcul, "%Y-%m-%d")
        
        # 13 mois avant pour avoir assez de données
        date_debut = date_calcul - relativedelta(months=13)
        
        return date_debut.strftime("%Y-%m-%d"), date_calcul.strftime("%Y-%m-%d")
    
    def recuperer_prix_tiingo(self, ticker, date_debut, date_fin):
        """
        Récupère les prix historiques ajustés depuis l'API Tiingo.
        
        Args:
            ticker: Symbole de l'action (str)
            date_debut: Date de début au format "YYYY-MM-DD"
            date_fin: Date de fin au format "YYYY-MM-DD"
        
        Returns:
            DataFrame pandas avec les prix ou None en cas d'erreur
        """
        url = f"{self.base_url}/{ticker}/prices"
        
        params = {
            "startDate": date_debut,
            "endDate": date_fin,
            "token": self.api_key,
            "resampleFreq": "monthly"
        }
        
        headers = {"Content-Type": "application/json"}
        
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                
                if len(data) == 0:
                    return None, f"Aucune donnée disponible"
                
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                
                return df, None
                
            elif response.status_code == 404:
                return None, f"Ticker non trouvé sur Tiingo"
            elif response.status_code == 401:
                return None, f"Erreur d'authentification API"
            else:
                return None, f"Erreur API: Code {response.status_code}"
                
        except requests.exceptions.Timeout:
            return None, "Timeout de la requête"
        except requests.exceptions.RequestException as e:
            return None, f"Erreur de connexion: {str(e)}"
    
    def calculer_momentum_12_1(self, df_prix):
        """
        Calcule le momentum 12-1 (rendement sur 12 mois, excluant le dernier mois).
        
        La stratégie momentum 12-1 classique utilise le rendement des 12 derniers mois
        en excluant le mois le plus récent (pour éviter l'effet de retour à court terme).
        
        Args:
            df_prix: DataFrame avec les prix mensuels (doit contenir 'adjClose')
        
        Returns:
            float: Rendement momentum en pourcentage, ou None si données insuffisantes
        """
        if df_prix is None or len(df_prix) < 13:
            return None
        
        # Trier par date croissante
        df_prix = df_prix.sort_index()
        
        # Prix ajusté il y a 12 mois (le plus ancien)
        prix_debut = df_prix['adjClose'].iloc[-13]
        
        # Prix ajusté il y a 1 mois (on exclut le mois le plus récent)
        prix_fin = df_prix['adjClose'].iloc[-2]
        
        # Calcul du rendement en pourcentage
        if prix_debut > 0:
            momentum = ((prix_fin - prix_debut) / prix_debut) * 100
            return momentum
        else:
            return None
    
    def analyser_panel(self, panel_tickers, date_calcul=None):
        """
        Analyse l'ensemble du panel d'actions et calcule le momentum de chacune.
        
        Args:
            panel_tickers: Liste des tickers à analyser
            date_calcul: Date du calcul (datetime ou string, None = aujourd'hui)
        
        Returns:
            dict: {
                'success': bool,
                'date_calcul': str,
                'resultats': list of dict,
                'erreurs': list of dict
            }
        """
        if date_calcul is None:
            date_calcul = datetime.now()
        elif isinstance(date_calcul, str):
            date_calcul = datetime.strptime(date_calcul, "%Y-%m-%d")
        
        date_debut, date_fin = self.calculer_periode_analyse(date_calcul)
        
        resultats = []
        erreurs = []
        
        for ticker in panel_tickers:
            ticker = ticker.upper().strip()
            
            # Récupération des prix
            df_prix, erreur = self.recuperer_prix_tiingo(ticker, date_debut, date_fin)
            
            if erreur:
                erreurs.append({'ticker': ticker, 'erreur': erreur})
                continue
            
            # Calcul du momentum
            momentum = self.calculer_momentum_12_1(df_prix)
            
            if momentum is not None:
                resultats.append({
                    'ticker': ticker,
                    'momentum': momentum
                })
            else:
                erreurs.append({'ticker': ticker, 'erreur': 'Données insuffisantes pour le calcul'})
        
        # Tri par momentum décroissant
        resultats.sort(key=lambda x: x['momentum'], reverse=True)
        
        # Ajout du rang
        for i, r in enumerate(resultats):
            r['rank'] = i + 1
        
        return {
            'success': len(resultats) > 0,
            'date_calcul': date_calcul.strftime("%Y-%m-%d"),
            'resultats': resultats,
            'erreurs': erreurs
        }
    
    def generer_recommandations(self, resultats_analyse, nb_top):
        """
        Génère les signaux d'investissement et calcule les allocations.
        
        Args:
            resultats_analyse: Résultat de analyser_panel()
            nb_top: Nombre d'actions à sélectionner pour investir
        
        Returns:
            dict: {
                'date_calcul': str,
                'nb_top': int,
                'recommandations': list,
                'total_investir': int,
                'erreurs': list
            }
        """
        if not resultats_analyse['success']:
            return {
                'date_calcul': resultats_analyse['date_calcul'],
                'nb_top': nb_top,
                'recommandations': [],
                'total_investir': 0,
                'erreurs': resultats_analyse['erreurs']
            }
        
        resultats = resultats_analyse['resultats']
        nb_actions = len(resultats)
        nb_selection = min(nb_top, nb_actions)
        
        # Allocation par action sélectionnée (répartition égale)
        allocation_par_action = round(100.0 / nb_selection, 2) if nb_selection > 0 else 0
        
        recommandations = []
        
        for i, r in enumerate(resultats):
            if i < nb_selection:
                signal = "Investir"
                allocation = allocation_par_action
            else:
                signal = "Sortir"
                allocation = 0.0
            
            recommandations.append({
                'ticker': r['ticker'],
                'momentum': round(r['momentum'], 2),
                'signal': signal,
                'allocation': allocation,
                'rank': r['rank']
            })
        
        return {
            'date_calcul': resultats_analyse['date_calcul'],
            'nb_top': nb_top,
            'recommandations': recommandations,
            'total_investir': nb_selection,
            'erreurs': resultats_analyse['erreurs']
        }
    
    def valider_ticker(self, ticker):
        """
        Vérifie si un ticker existe sur Tiingo.
        
        Args:
            ticker: Symbole à vérifier
        
        Returns:
            dict: {'valid': bool, 'name': str or None, 'error': str or None}
        """
        url = f"{self.base_url}/{ticker}"
        
        try:
            response = requests.get(
                url,
                params={"token": self.api_key},
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'valid': True,
                    'name': data.get('name', ''),
                    'error': None
                }
            elif response.status_code == 404:
                return {
                    'valid': False,
                    'name': None,
                    'error': 'Ticker non trouvé'
                }
            else:
                return {
                    'valid': False,
                    'name': None,
                    'error': f'Erreur API: {response.status_code}'
                }
                
        except Exception as e:
            return {
                'valid': False,
                'name': None,
                'error': str(e)
            }

