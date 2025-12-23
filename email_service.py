# -*- coding: utf-8 -*-
"""
Service d'envoi d'emails
========================
Gère l'envoi des notifications par email via Resend.
"""

import resend
from datetime import datetime


class EmailService:
    """
    Service pour envoyer des emails de notification.
    Utilise Resend (https://resend.com) - 100 emails/jour gratuits.
    """
    
    def __init__(self, api_key, from_email, to_email):
        """
        Initialise le service email.
        
        Args:
            api_key: Clé API Resend
            from_email: Email de l'expéditeur
            to_email: Email du destinataire
        """
        self.api_key = api_key
        self.from_email = from_email
        self.to_email = to_email
        
        if api_key:
            resend.api_key = api_key
    
    def is_configured(self):
        """Vérifie si le service email est configuré"""
        return all([self.api_key, self.from_email, self.to_email])
    
    def envoyer_recommandations(self, recommandations_data):
        """
        Envoie un email avec les recommandations du mois.
        
        Args:
            recommandations_data: Données de recommandations (dict)
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        if not self.is_configured():
            return {
                'success': False,
                'message': 'Service email non configuré. Vérifiez RESEND_API_KEY, EMAIL_FROM et EMAIL_TO.'
            }
        
        try:
            # Construire le contenu de l'email
            date_calcul = recommandations_data.get('date_calcul', datetime.now().strftime('%Y-%m-%d'))
            nb_top = recommandations_data.get('nb_top', 5)
            recommandations = recommandations_data.get('recommandations', [])
            
            # Séparer les actions à investir et à sortir
            investir = [r for r in recommandations if r['signal'] == 'Investir']
            sortir = [r for r in recommandations if r['signal'] == 'Sortir']
            
            # Construction du HTML
            html_content = self._construire_html_email(date_calcul, nb_top, investir, sortir)
            
            # Construction du texte brut
            text_content = self._construire_texte_email(date_calcul, nb_top, investir, sortir)
            
            # Envoi via Resend
            params = {
                "from": self.from_email,
                "to": [self.to_email],
                "subject": f"📈 Recommandations Momentum - {date_calcul}",
                "html": html_content,
                "text": text_content
            }
            
            response = resend.Emails.send(params)
            
            return {
                'success': True,
                'message': f'Email envoyé avec succès à {self.to_email}',
                'email_id': response.get('id') if isinstance(response, dict) else str(response)
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Erreur lors de l\'envoi: {str(e)}'
            }
    
    def _construire_html_email(self, date_calcul, nb_top, investir, sortir):
        """Construit le contenu HTML de l'email"""
        
        # Lignes du tableau des actions à investir
        lignes_investir = ""
        for r in investir:
            couleur = "#22c55e" if r['momentum'] > 0 else "#ef4444"
            lignes_investir += f"""
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; font-weight: bold;">{r['ticker']}</td>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; color: {couleur};">{r['momentum']:+.2f}%</td>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; text-align: center;">
                    <span style="background-color: #dcfce7; color: #166534; padding: 4px 12px; border-radius: 9999px; font-size: 12px;">
                        {r['allocation']}%
                    </span>
                </td>
            </tr>
            """
        
        # Lignes du tableau des actions à sortir (top 5 seulement)
        lignes_sortir = ""
        for r in sortir[:5]:
            couleur = "#22c55e" if r['momentum'] > 0 else "#ef4444"
            lignes_sortir += f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">{r['ticker']}</td>
                <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: {couleur};">{r['momentum']:+.2f}%</td>
            </tr>
            """
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #1f2937; max-width: 600px; margin: 0 auto; padding: 20px;">
            
            <!-- Header -->
            <div style="background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%); color: white; padding: 30px; border-radius: 12px; text-align: center; margin-bottom: 24px;">
                <h1 style="margin: 0; font-size: 24px;">📈 Recommandations Momentum</h1>
                <p style="margin: 10px 0 0 0; opacity: 0.9;">Mise à jour du {date_calcul}</p>
            </div>
            
            <!-- Résumé -->
            <div style="background-color: #f8fafc; padding: 20px; border-radius: 8px; margin-bottom: 24px;">
                <p style="margin: 0; font-size: 16px;">
                    🎯 <strong>Top {nb_top} actions</strong> sélectionnées pour ce mois<br>
                    💰 Allocation : <strong>{100/nb_top:.1f}%</strong> par action
                </p>
            </div>
            
            <!-- Actions à investir -->
            <h2 style="color: #166534; font-size: 18px; margin-bottom: 16px;">🟢 Actions à INVESTIR</h2>
            <table style="width: 100%; border-collapse: collapse; background-color: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <thead>
                    <tr style="background-color: #f1f5f9;">
                        <th style="padding: 12px; text-align: left; font-weight: 600;">Symbole</th>
                        <th style="padding: 12px; text-align: left; font-weight: 600;">Momentum</th>
                        <th style="padding: 12px; text-align: center; font-weight: 600;">Allocation</th>
                    </tr>
                </thead>
                <tbody>
                    {lignes_investir}
                </tbody>
            </table>
            
            <!-- Actions à sortir -->
            {f'''
            <h2 style="color: #dc2626; font-size: 18px; margin-top: 32px; margin-bottom: 16px;">🔴 Actions à SORTIR (top 5)</h2>
            <table style="width: 100%; border-collapse: collapse; background-color: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <thead>
                    <tr style="background-color: #fef2f2;">
                        <th style="padding: 8px; text-align: left; font-weight: 600;">Symbole</th>
                        <th style="padding: 8px; text-align: left; font-weight: 600;">Momentum</th>
                    </tr>
                </thead>
                <tbody>
                    {lignes_sortir}
                </tbody>
            </table>
            ''' if sortir else ''}
            
            <!-- Footer -->
            <div style="margin-top: 32px; padding-top: 20px; border-top: 1px solid #e5e7eb; text-align: center; color: #6b7280; font-size: 14px;">
                <p>Stratégie Momentum 12-1 • Généré automatiquement</p>
                <p style="font-size: 12px;">⚠️ Ceci n'est pas un conseil financier</p>
            </div>
            
        </body>
        </html>
        """
        
        return html
    
    def _construire_texte_email(self, date_calcul, nb_top, investir, sortir):
        """Construit la version texte brut de l'email"""
        
        texte = f"""
📈 RECOMMANDATIONS MOMENTUM
===========================
Date de calcul: {date_calcul}
Top {nb_top} actions sélectionnées

🟢 ACTIONS À INVESTIR
---------------------
"""
        
        for r in investir:
            texte += f"{r['ticker']:8} | Momentum: {r['momentum']:+8.2f}% | Allocation: {r['allocation']}%\n"
        
        if sortir:
            texte += """
🔴 ACTIONS À SORTIR (top 5)
---------------------------
"""
            for r in sortir[:5]:
                texte += f"{r['ticker']:8} | Momentum: {r['momentum']:+8.2f}%\n"
        
        texte += """
---
Stratégie Momentum 12-1
⚠️ Ceci n'est pas un conseil financier
"""
        
        return texte
    
    def envoyer_test(self):
        """
        Envoie un email de test pour vérifier la configuration.
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        if not self.is_configured():
            return {
                'success': False,
                'message': 'Service email non configuré'
            }
        
        try:
            params = {
                "from": self.from_email,
                "to": [self.to_email],
                "subject": "🧪 Test - Momentum Strategy App",
                "html": """
                <div style="font-family: sans-serif; padding: 20px;">
                    <h2>✅ Configuration email réussie !</h2>
                    <p>Votre application Momentum Strategy est correctement configurée pour envoyer des emails.</p>
                    <p>Vous recevrez les recommandations mensuelles à cette adresse.</p>
                </div>
                """,
                "text": "Configuration email réussie ! Vous recevrez les recommandations mensuelles."
            }
            
            response = resend.Emails.send(params)
            
            return {
                'success': True,
                'message': f'Email de test envoyé à {self.to_email}'
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Erreur: {str(e)}'
            }

