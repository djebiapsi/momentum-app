# -*- coding: utf-8 -*-
"""
Service d'envoi d'emails
========================
Gère l'envoi des notifications par email via Resend.
"""

import os
import re
import html as html_lib
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
    
    def envoyer_positions(self, positions: list) -> dict:
        """
        Envoie un email récapitulatif des positions IBKR ouvertes.

        Args:
            positions: liste de dicts (ticker, qty, avg_cost, market_price,
                       market_value, unrealized_pnl, realized_pnl, currency)
        """
        if not self.is_configured():
            return {'success': False, 'message': 'Service email non configuré'}
        if not positions:
            return {'success': False, 'message': 'Aucune position à envoyer'}

        try:
            now = datetime.now()
            date_str = now.strftime('%Y-%m-%d %H:%M ET')

            total_value = sum(p.get('market_value') or 0 for p in positions)
            total_upl = sum(p.get('unrealized_pnl') or 0 for p in positions)
            upl_color = '#22c55e' if total_upl >= 0 else '#ef4444'

            lignes = ''
            for p in positions:
                upl = p.get('unrealized_pnl')
                upl_c = '#22c55e' if (upl is not None and upl >= 0) else '#ef4444'
                upl_str = f'<span style="color:{upl_c}">{upl:+,.2f}</span>' if upl is not None else '—'
                rpl = p.get('realized_pnl')
                rpl_str = f'{rpl:+,.2f}' if rpl is not None else '—'
                mv = p.get('market_value') or 0
                lignes += f"""
                <tr>
                  <td style="padding:10px 12px;border-bottom:1px solid #27272a;font-weight:600;font-family:monospace">{p['ticker']}</td>
                  <td style="padding:10px 12px;border-bottom:1px solid #27272a;text-align:right">{p.get('qty', '—')}</td>
                  <td style="padding:10px 12px;border-bottom:1px solid #27272a;text-align:right">${p['avg_cost']:,.4f}</td>
                  <td style="padding:10px 12px;border-bottom:1px solid #27272a;text-align:right">${p['market_price']:,.4f}</td>
                  <td style="padding:10px 12px;border-bottom:1px solid #27272a;text-align:right">${mv:,.2f}</td>
                  <td style="padding:10px 12px;border-bottom:1px solid #27272a;text-align:right">{upl_str}</td>
                  <td style="padding:10px 12px;border-bottom:1px solid #27272a;text-align:right">{rpl_str}</td>
                </tr>"""

            html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             color:#fafafa;background:#09090b;max-width:700px;margin:0 auto;padding:20px;">
  <div style="background:linear-gradient(135deg,#1d4ed8,#7c3aed);color:white;padding:28px;
              border-radius:12px;text-align:center;margin-bottom:24px;">
    <h1 style="margin:0;font-size:22px;">📊 Positions IBKR</h1>
    <p style="margin:10px 0 0;opacity:0.9;font-size:14px;">{date_str}</p>
  </div>
  <div style="background:#18181b;padding:16px;border-radius:8px;margin-bottom:20px;
              display:flex;gap:24px;flex-wrap:wrap;">
    <div><div style="color:#a1a1aa;font-size:12px;">Valeur totale</div>
         <div style="font-size:20px;font-weight:700;">${total_value:,.2f}</div></div>
    <div><div style="color:#a1a1aa;font-size:12px;">P&amp;L non réalisé</div>
         <div style="font-size:20px;font-weight:700;color:{upl_color}">{total_upl:+,.2f}</div></div>
    <div><div style="color:#a1a1aa;font-size:12px;">Positions</div>
         <div style="font-size:20px;font-weight:700;">{len(positions)}</div></div>
  </div>
  <table style="width:100%;border-collapse:collapse;background:#18181b;border-radius:8px;overflow:hidden;">
    <thead><tr style="background:#27272a;">
      <th style="padding:10px 12px;text-align:left;font-size:11px;color:#a1a1aa;text-transform:uppercase">Ticker</th>
      <th style="padding:10px 12px;text-align:right;font-size:11px;color:#a1a1aa;text-transform:uppercase">Qty</th>
      <th style="padding:10px 12px;text-align:right;font-size:11px;color:#a1a1aa;text-transform:uppercase">Avg Cost</th>
      <th style="padding:10px 12px;text-align:right;font-size:11px;color:#a1a1aa;text-transform:uppercase">Prix</th>
      <th style="padding:10px 12px;text-align:right;font-size:11px;color:#a1a1aa;text-transform:uppercase">Valeur</th>
      <th style="padding:10px 12px;text-align:right;font-size:11px;color:#a1a1aa;text-transform:uppercase">P&amp;L non réalisé</th>
      <th style="padding:10px 12px;text-align:right;font-size:11px;color:#a1a1aa;text-transform:uppercase">P&amp;L réalisé</th>
    </tr></thead>
    <tbody>{lignes}</tbody>
  </table>
  <div style="margin-top:24px;text-align:center;color:#71717a;font-size:12px;">
    <p>Positions IBKR · Généré automatiquement · Momentum Strategy App</p>
    <p>⚠️ Ceci n'est pas un conseil financier</p>
  </div>
</body></html>"""

            text_content = f"POSITIONS IBKR — {date_str}\n" + "=" * 60 + "\n"
            for p in positions:
                upl = p.get('unrealized_pnl')
                text_content += (
                    f"{p['ticker']:<8} qty={p.get('qty','—'):>8}  "
                    f"valeur=${p.get('market_value') or 0:>10,.2f}  "
                    f"P&L={upl:+,.2f}\n" if upl is not None
                    else f"{p['ticker']:<8} qty={p.get('qty','—'):>8}\n"
                )

            params = {
                "from": self.from_email,
                "to": [self.to_email],
                "subject": f"📊 Positions IBKR — {now.strftime('%Y-%m-%d %H:%M')} ET",
                "html": html_content,
                "text": text_content,
            }
            response = resend.Emails.send(params)
            return {
                'success': True,
                'message': f'Email positions envoyé à {self.to_email}',
                'email_id': response.get('id') if isinstance(response, dict) else str(response),
            }

        except Exception as e:
            return {'success': False, 'message': f'Erreur: {str(e)}'}

    def envoyer_notification_gateway(self) -> dict:
        """Notifie l'utilisateur que l'application va démarrer IB Gateway et demander la 2FA."""
        if not self.is_configured():
            return {'success': False, 'message': 'Email non configuré'}
        try:
            params = {
                "from": self.from_email,
                "to": [self.to_email],
                "subject": "🔐 Momentum App — Démarrage IB Gateway (2FA requis)",
                "html": """
                <div style="font-family:-apple-system,sans-serif;max-width:520px;margin:0 auto;padding:24px;
                            background:#09090b;color:#fafafa;border-radius:12px;">
                    <div style="background:linear-gradient(135deg,#1d4ed8,#7c3aed);padding:24px;
                                border-radius:8px;text-align:center;margin-bottom:20px;">
                        <h2 style="margin:0;color:white;">🔐 Démarrage IB Gateway</h2>
                    </div>
                    <p>Votre application <strong>Momentum Strategy</strong> vient de démarrer
                    le service de connexion Interactive Brokers.</p>
                    <p>Vous allez recevoir une demande d'authentification à deux facteurs (2FA)
                    sur votre téléphone IBKR dans quelques secondes.</p>
                    <div style="background:#18181b;padding:16px;border-radius:8px;border-left:4px solid #22c55e;">
                        <strong>C'est bien votre application qui demande.</strong><br>
                        <span style="color:#a1a1aa;font-size:13px;">Approuvez la 2FA sur votre téléphone.</span>
                    </div>
                    <p style="color:#71717a;font-size:12px;margin-top:16px;">
                        Si vous n'avez pas déclenché cette action, vérifiez vos paramètres IBKR.
                    </p>
                </div>""",
                "text": "Momentum App: démarrage IB Gateway. 2FA IBKR requise sur votre téléphone. C'est bien votre application."
            }
            resend.Emails.send(params)
            return {'success': True}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # =====================================================================
    # GABARIT COMMUN — identité visuelle cohérente & soignée
    # =====================================================================
    APP_BASE_URL = os.environ.get('APP_BASE_URL', '').rstrip('/')

    def _send(self, subject, html, text=None):
        """Envoi mutualisé via Resend avec gestion d'erreur uniforme."""
        if not self.is_configured():
            return {'success': False, 'message': 'Service email non configuré'}
        try:
            params = {
                "from": self.from_email,
                "to": [self.to_email],
                "subject": subject,
                "html": html,
                "text": text or subject,
            }
            response = resend.Emails.send(params)
            return {
                'success': True,
                'message': f'Email envoyé à {self.to_email}',
                'email_id': response.get('id') if isinstance(response, dict) else str(response),
            }
        except Exception as e:
            return {'success': False, 'message': f'Erreur: {str(e)}'}

    def _html_shell(self, title, subtitle, body_html, accent='#1d4ed8,#7c3aed', emoji='📈'):
        """
        Enveloppe HTML commune (thème sombre, gradient, header + footer).
        `accent` = 'couleur1,couleur2' pour le gradient du bandeau.
        Styles inline uniquement → compatible Gmail / Apple Mail.
        """
        c1, c2 = accent.split(',')
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#09090b;">
  <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
              color:#fafafa;background:#09090b;max-width:600px;margin:0 auto;padding:24px;">
    <div style="background:linear-gradient(135deg,{c1},{c2});color:#fff;padding:30px 24px;
                border-radius:14px;text-align:center;margin-bottom:24px;
                box-shadow:0 6px 20px rgba(0,0,0,0.35);">
      <div style="font-size:30px;line-height:1;margin-bottom:8px;">{emoji}</div>
      <h1 style="margin:0;font-size:22px;font-weight:700;letter-spacing:-0.3px;">{title}</h1>
      <p style="margin:10px 0 0;opacity:0.92;font-size:14px;">{subtitle}</p>
    </div>
    {body_html}
    <div style="margin-top:28px;padding-top:18px;border-top:1px solid #27272a;
                text-align:center;color:#71717a;font-size:12px;">
      <p style="margin:0 0 4px;">Momentum Strategy App · Généré automatiquement</p>
      <p style="margin:0;">⚠️ Ceci n'est pas un conseil financier</p>
    </div>
  </div>
</body></html>"""

    @staticmethod
    def _kpi_card(label, value, color='#fafafa'):
        """Carte KPI réutilisable (label + grande valeur)."""
        return f"""<td style="padding:6px;" width="33%">
          <div style="background:#18181b;border:1px solid #27272a;padding:16px;border-radius:10px;text-align:center;">
            <div style="color:#a1a1aa;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;">{label}</div>
            <div style="font-size:20px;font-weight:700;color:{color};margin-top:4px;">{value}</div>
          </div></td>"""

    @staticmethod
    def _badge(text, bg, fg):
        return (f'<span style="background:{bg};color:{fg};padding:3px 12px;border-radius:9999px;'
                f'font-size:12px;font-weight:600;">{text}</span>')

    @staticmethod
    def _indicator_legend():
        return (
            'Mom 1M/3M = performance sur 21/63 jours glissants · '
            'vs SMA50/200 = écart % par rapport aux moyennes mobiles · '
            'RSI14 : &gt;70 suracheté (rouge), &lt;30 survendu (vert)'
        )

    @staticmethod
    def _md_inline(text):
        """Convertit le markdown inline (**gras**, *italique*, [lien](url)) en HTML."""
        text = html_lib.escape(text)
        text = re.sub(r'\[(.+?)\]\((https?://[^)\s]+)\)',
                      r'<a href="\2" style="color:#93c5fd;text-decoration:none;">\1</a>', text)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
        text = re.sub(r'(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])', r'<em>\1</em>', text)
        return text

    def _md_to_email_html(self, text):
        """
        Rend un sous-ensemble de markdown (titres ##, listes -/*, numéros, gras)
        en HTML stylé pour email sombre. Évite d'afficher les ** bruts.
        """
        if not text:
            return ''
        out = []
        for raw in text.split('\n'):
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                out.append('<div style="height:8px;"></div>')
                continue
            m = re.match(r'^#{1,6}\s+(.*)$', stripped)
            if m:
                out.append(f'<div style="font-weight:700;color:#e4e4e7;font-size:14px;'
                           f'margin:14px 0 6px;border-bottom:1px solid #27272a;padding-bottom:4px;">'
                           f'{self._md_inline(m.group(1))}</div>')
                continue
            m = re.match(r'^[-*•]\s+(.*)$', stripped)
            if m:
                out.append(f'<div style="margin:3px 0 3px 6px;">'
                           f'<span style="color:#7c3aed;">•</span> {self._md_inline(m.group(1))}</div>')
                continue
            m = re.match(r'^(\d+)[.)]\s+(.*)$', stripped)
            if m:
                out.append(f'<div style="margin:3px 0 3px 6px;">'
                           f'<strong>{m.group(1)}.</strong> {self._md_inline(m.group(2))}</div>')
                continue
            out.append(f'<div style="margin:4px 0;">{self._md_inline(stripped)}</div>')
        return ''.join(out)

    def _cta_button(self, label, path):
        url = f"{self.APP_BASE_URL}{path}" if self.APP_BASE_URL else path
        return f"""<div style="text-align:center;margin:24px 0;">
          <a href="{url}" style="display:inline-block;background:linear-gradient(135deg,#1d4ed8,#7c3aed);
             color:#fff;text-decoration:none;padding:14px 28px;border-radius:10px;font-weight:700;
             font-size:15px;box-shadow:0 4px 14px rgba(124,58,237,0.4);">{label}</a></div>"""

    # =====================================================================
    # ALERTES MARCHÉ
    # =====================================================================
    def envoyer_alerte_marche(self, event: dict) -> dict:
        """Alerte à l'OUVERTURE d'un épisode (seuil franchi)."""
        sev = event.get('severity', 'warning')
        crit = sev == 'critical'
        accent = '#dc2626,#7f1d1d' if crit else '#ea580c,#9a3412'
        emoji = '🚨' if crit else '⚠️'
        sev_label = 'CRITIQUE' if crit else 'Avertissement'
        sev_bg, sev_fg = ('#7f1d1d', '#fecaca') if crit else ('#7c2d12', '#fed7aa')

        val = event.get('trigger_value')
        thr = event.get('threshold')
        now = datetime.now().strftime('%Y-%m-%d %H:%M ET')

        body = f"""
    <div style="margin-bottom:6px;">{self._badge(sev_label, sev_bg, sev_fg)}</div>
    <div style="background:#18181b;border:1px solid #27272a;border-left:4px solid {'#dc2626' if crit else '#ea580c'};
                padding:20px;border-radius:10px;margin:14px 0;">
      <p style="margin:0 0 14px;font-size:16px;line-height:1.5;">{event.get('message','')}</p>
      <table style="width:100%;border-collapse:collapse;"><tr>
        {self._kpi_card('Valeur', f'{val:+.2f}' if isinstance(val,(int,float)) else '—',
                        '#ef4444' if crit else '#f97316')}
        {self._kpi_card('Seuil', f'{thr:.2f}' if isinstance(thr,(int,float)) else '—')}
        {self._kpi_card('Détecté', now.split(' ')[1] + ' ET')}
      </tr></table>
    </div>
    <p style="color:#a1a1aa;font-size:13px;">Tu recevras un email de clôture lorsque la métrique
    repassera sous le seuil. Reste en gestion passive — pas d'action précipitée.</p>"""

        html = self._html_shell('Alerte marché', now, body, accent=accent, emoji=emoji)
        text = f"[{sev_label}] {event.get('message','')} — {now}"
        return self._send(f"{emoji} Alerte marché — {event.get('event_type','')}", html, text)

    def envoyer_alerte_resolue(self, event: dict) -> dict:
        """Email court de CLÔTURE d'un épisode."""
        dur = event.get('duration_min')
        dur_str = f"{dur:.0f} min" if isinstance(dur, (int, float)) else '—'
        peak = event.get('peak_value')
        now = datetime.now().strftime('%Y-%m-%d %H:%M ET')

        body = f"""
    <div style="background:#18181b;border:1px solid #27272a;border-left:4px solid #22c55e;
                padding:20px;border-radius:10px;margin:14px 0;">
      <p style="margin:0 0 14px;font-size:16px;">L'épisode est terminé : la métrique est repassée
      sous son seuil.</p>
      <p style="margin:0 0 14px;color:#a1a1aa;font-size:14px;">{event.get('message','')}</p>
      <table style="width:100%;border-collapse:collapse;"><tr>
        {self._kpi_card('Durée', dur_str)}
        {self._kpi_card('Valeur pic', f'{peak:+.2f}' if isinstance(peak,(int,float)) else '—', '#f97316')}
        {self._kpi_card('Clôturé', now.split(' ')[1] + ' ET', '#22c55e')}
      </tr></table>
    </div>"""

        html = self._html_shell('Alerte résolue', now, body, accent='#16a34a,#065f46', emoji='✅')
        text = f"[RÉSOLU] {event.get('message','')} — durée {dur_str}"
        return self._send(f"✅ Alerte résolue — {event.get('event_type','')}", html, text)

    # =====================================================================
    # BRIEFING (ouverture / mi-séance / clôture)
    # =====================================================================
    def envoyer_briefing(self, payload: dict) -> dict:
        """
        Briefing de séance. payload attendu :
        {
            'session': 'open'|'mid'|'close',
            'regime': {'regime','pct_vs_sma200',...} | None,
            'vix': float|None, 'vix_pct': float|None,
            'stats': {'total_value','total_pnl','return_pct','positions_count'} | None,
            'positions': [{'ticker','market_value','return_pct','unrealized_pnl','allocation_pct'}],
            'news_summary': str, 'news_items': [{'title','link','source','ticker'}],
        }
        """
        session = payload.get('session', 'open')
        titles = {'open': "Briefing d'ouverture", 'mid': 'Briefing mi-séance',
                  'close': 'Briefing de clôture'}
        emojis = {'open': '🔔', 'mid': '🕛', 'close': '🌙'}
        now = datetime.now().strftime('%Y-%m-%d %H:%M ET')

        # ── Helpers ──────────────────────────────────────────────────────
        def _pct_span(v, bold=False, suffix='%'):
            if not isinstance(v, (int, float)):
                return '<span style="color:#71717a;">—</span>'
            c = '#22c55e' if v >= 0 else '#ef4444'
            w = 'font-weight:700;' if bold else ''
            return f'<span style="color:{c};{w}">{v:+.1f}{suffix}</span>'

        def _num(v, fmt='.2f', prefix='', suffix='', fallback='—'):
            if not isinstance(v, (int, float)):
                return f'<span style="color:#71717a;">{fallback}</span>'
            return f'{prefix}{v:{fmt}}{suffix}'

        def _rsi_color(v):
            if not isinstance(v, (int, float)): return '#71717a'
            if v >= 70: return '#ef4444'
            if v <= 30: return '#22c55e'
            return '#a1a1aa'

        # ── Régime / VIX / indices ────────────────────────────────────
        regime = payload.get('regime') or {}
        reg = regime.get('regime', 'UNKNOWN')
        reg_color = {'BULL': '#22c55e', 'BEAR': '#ef4444'}.get(reg, '#a1a1aa')
        reg_badge = self._badge(reg, '#052e16' if reg == 'BULL' else '#450a0a', reg_color)
        vix = payload.get('vix')
        vix_str = f"{vix:.1f}" if isinstance(vix, (int, float)) else '—'
        vix_pct = payload.get('vix_pct')
        vix_color = '#ef4444' if isinstance(vix, (int, float)) and vix >= 25 else '#fafafa'
        stats = payload.get('stats') or {}
        pnl = stats.get('total_pnl')
        pnl_color = '#22c55e' if isinstance(pnl, (int, float)) and pnl >= 0 else '#ef4444'
        port_intra = payload.get('portfolio_intraday_pct')
        spy_intra  = payload.get('spy_intraday_pct')
        qqq_intra  = payload.get('qqq_intraday_pct')

        # Ligne 1 : régime, VIX, P&L
        cards_row1 = f"""<table style="width:100%;border-collapse:collapse;margin-bottom:6px;"><tr>
          {self._kpi_card('Régime', reg_badge, reg_color)}
          {self._kpi_card('VIX', vix_str + (f' <span style="color:{vix_color};font-size:12px;">({vix_pct:+.1f}%)</span>' if isinstance(vix_pct,(int,float)) else ''), vix_color)}
          {self._kpi_card('P&L total', f'<span style="color:{pnl_color};font-weight:700;">{pnl:+,.0f}$</span>' if isinstance(pnl,(int,float)) else '—', pnl_color)}
        </tr></table>"""

        # Ligne 2 : indices intraday
        spy_cell = self._kpi_card('S&P 500', _pct_span(spy_intra, bold=True) + (f'<br><span style="font-size:11px;color:#52525b;">SPY ${payload.get("spy","—")}</span>' if payload.get("spy") else ''), '#fafafa')
        qqq_cell = self._kpi_card('Nasdaq', _pct_span(qqq_intra, bold=True) + (f'<br><span style="font-size:11px;color:#52525b;">QQQ ${payload.get("qqq","—")}</span>' if payload.get("qqq") else ''), '#fafafa')
        ptf_cell = self._kpi_card('Portef. intraday', _pct_span(port_intra, bold=True), '#fafafa')
        cards_row2 = f'<table style="width:100%;border-collapse:collapse;margin-bottom:8px;"><tr>{spy_cell}{qqq_cell}{ptf_cell}</tr></table>'

        # ── Indicateurs techniques ────────────────────────────────────
        techs = payload.get('technicals') or {}
        tech_rows = ''
        for sym in ('SPY', 'QQQ'):
            t = techs.get(sym)
            if not t:
                continue
            rsi = t.get('rsi14')
            rsi_str = f'<span style="color:{_rsi_color(rsi)};font-weight:600;">{rsi}</span>' if rsi else '—'
            tech_rows += f"""<tr>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;font-family:monospace;font-weight:700;">{sym}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;">${t.get('last','—')}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;">{_pct_span(t.get('mom_1m'))}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;">{_pct_span(t.get('mom_3m'))}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;">{_pct_span(t.get('vs_sma50'))}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;">{_pct_span(t.get('vs_sma200'))}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;">{rsi_str}</td>
            </tr>"""
        technicals_block = ''
        if tech_rows:
            tech_legend = self._indicator_legend()
            technicals_block = f"""
    <h2 style="font-size:15px;color:#e4e4e7;margin:20px 0 8px;">📈 Indicateurs techniques</h2>
    <table style="width:100%;border-collapse:collapse;background:#18181b;border-radius:10px;overflow:hidden;">
      <thead><tr style="background:#27272a;">
        <th style="padding:8px 12px;text-align:left;font-size:11px;color:#a1a1aa;">INDEX</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">Cours</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">Mom 1M</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">Mom 3M</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">vs SMA50</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">vs SMA200</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">RSI 14</th>
      </tr></thead><tbody>{tech_rows}</tbody>
    </table>
    <p style="font-size:11px;color:#52525b;margin:6px 0 0;">{tech_legend}</p>"""

        # ── Positions ─────────────────────────────────────────────────
        positions = payload.get('positions') or []
        pos_rows = ''
        for p in sorted(positions, key=lambda x: abs(x.get('market_value') or 0), reverse=True):
            mv  = p.get('market_value') or 0
            rp  = p.get('return_pct')        # perf depuis PRU
            ip  = p.get('intraday_pct')      # perf intraday du jour
            lp  = p.get('last_price')
            pru = p.get('avg_cost')
            alloc = p.get('allocation_pct', '—')
            pos_rows += f"""<tr>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;font-family:monospace;font-weight:700;">{p.get('ticker','')}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;color:#a1a1aa;">{f'${lp:.2f}' if isinstance(lp,(int,float)) else '—'}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;">${mv:,.0f}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;color:#a1a1aa;">{alloc}%</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;">{_pct_span(ip, bold=True)}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #27272a;text-align:right;">{_pct_span(rp)}</td>
            </tr>"""
        positions_block = f"""
    <h2 style="font-size:15px;color:#e4e4e7;margin:20px 0 8px;">📊 Positions</h2>
    <table style="width:100%;border-collapse:collapse;background:#18181b;border-radius:10px;overflow:hidden;">
      <thead><tr style="background:#27272a;">
        <th style="padding:8px 12px;text-align:left;font-size:11px;color:#a1a1aa;">Ticker</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">Cours</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">Valeur</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">Alloc.</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">Aujourd'hui</th>
        <th style="padding:8px 12px;text-align:right;font-size:11px;color:#a1a1aa;">vs PRU</th>
      </tr></thead><tbody>{pos_rows}</tbody>
    </table>""" if positions else '<p style="color:#a1a1aa;font-size:13px;">Aucune position ouverte.</p>'

        # ── News ──────────────────────────────────────────────────────
        summary = (payload.get('news_summary') or '').strip()
        summary_html = self._md_to_email_html(summary) if summary else 'Aucune actualité notable.'
        news_items = payload.get('news_items') or []
        links = ''
        for it in news_items[:8]:
            tag = it.get('ticker') or 'MARCHÉ'
            links += f"""<li style="margin-bottom:8px;">
          <span style="font-size:11px;color:#a1a1aa;">[{tag}]</span>
          <a href="{it.get('link','#')}" style="color:#93c5fd;text-decoration:none;">{it.get('title','')}</a>
          <span style="font-size:11px;color:#52525b;"> · {it.get('source','')}</span></li>"""
        news_block = f"""
    <h2 style="font-size:15px;color:#e4e4e7;margin:20px 0 8px;">📰 News & analyse</h2>
    <div style="background:#18181b;border:1px solid #27272a;padding:16px;border-radius:10px;
                font-size:14px;line-height:1.6;color:#d4d4d8;">{summary_html}</div>
    {('<ul style="padding-left:18px;margin:12px 0 0;font-size:13px;">' + links + '</ul>') if links else ''}"""

        body = cards_row1 + cards_row2 + technicals_block + positions_block + news_block
        html = self._html_shell(titles.get(session, 'Briefing'), now, body,
                                accent='#1d4ed8,#7c3aed', emoji=emojis.get(session, '📈'))

        # Texte plain
        def _pct_txt(v): return f'{v:+.1f}%' if isinstance(v, (int, float)) else '—'
        txt_lines = [
            f"{titles.get(session,'Briefing')} — {now}",
            f"Régime: {reg}  VIX: {vix_str} ({_pct_txt(vix_pct)})  P&L: {_pct_txt(pnl)}",
            f"SPY: {_pct_txt(spy_intra)}  QQQ: {_pct_txt(qqq_intra)}  Portef.: {_pct_txt(port_intra)}",
            '',
        ]
        for sym in ('SPY', 'QQQ'):
            t = (techs or {}).get(sym, {})
            if t:
                txt_lines.append(
                    f"{sym}: 1M {_pct_txt(t.get('mom_1m'))} | 3M {_pct_txt(t.get('mom_3m'))} | "
                    f"SMA50 {_pct_txt(t.get('vs_sma50'))} | SMA200 {_pct_txt(t.get('vs_sma200'))} | RSI {t.get('rsi14','—')}"
                )
        txt_lines.append('')
        for p in positions:
            txt_lines.append(
                f"{p.get('ticker',''):8} | {_pct_txt(p.get('intraday_pct'))} intraday | {_pct_txt(p.get('return_pct'))} vs PRU"
            )
        txt_lines += ['', summary]
        text = '\n'.join(txt_lines)
        return self._send(f"{emojis.get(session,'📈')} {titles.get(session,'Briefing')} — {now}", html, text)

    # =====================================================================
    # RAPPEL DE RÉÉQUILIBRAGE MENSUEL
    # =====================================================================
    def envoyer_rebalance_reminder(self, recommandations_data: dict, history_id=None) -> dict:
        """« C'est le moment de rééquilibrer ! » + bouton de téléchargement du momentum."""
        date_calcul = recommandations_data.get('date_calcul', datetime.now().strftime('%Y-%m-%d'))
        recos = recommandations_data.get('recommandations', [])
        investir = [r for r in recos if r.get('signal') == 'Investir']

        def _card_cell(r):
            mom = r.get('momentum', 0)
            mom_color = '#22c55e' if mom >= 0 else '#ef4444'
            return f"""<td style="padding:6px;" width="50%">
            <div style="background:#18181b;border:1px solid #27272a;padding:14px 16px;border-radius:10px;">
              <table style="width:100%;"><tr>
                <td style="text-align:left;"><div style="font-family:monospace;font-weight:700;font-size:15px;">{r.get('ticker','')}</div>
                   <div style="color:#a1a1aa;font-size:12px;">Alloc. {r.get('allocation','—')}%</div></td>
                <td style="text-align:right;color:{mom_color};font-weight:700;font-size:15px;">{mom:+.1f}%</td>
              </tr></table>
            </div></td>"""

        # Regrouper les cartes par lignes de 2
        cards_rows = ''
        top = investir[:10]
        for i in range(0, len(top), 2):
            pair = top[i:i + 2]
            cells = ''.join(_card_cell(r) for r in pair)
            if len(pair) == 1:
                cells += '<td width="50%"></td>'
            cards_rows += f'<tr>{cells}</tr>'
        cards_block = (f'<table style="width:100%;border-collapse:collapse;margin-top:10px;">{cards_rows}</table>'
                       if cards_rows else '<p style="color:#a1a1aa;">Aucune action à investir ce mois-ci.</p>')

        dl_path = f"/api/history/{history_id}/download" if history_id else "/api/history/latest/download"
        body = f"""
    <div style="background:#18181b;border:1px solid #27272a;padding:20px;border-radius:10px;
                text-align:center;margin-bottom:8px;">
      <p style="margin:0;font-size:17px;font-weight:600;">📅 C'est le moment de rééquilibrer !</p>
      <p style="margin:8px 0 0;color:#a1a1aa;font-size:14px;">
        Nouveau calcul de momentum disponible ({date_calcul}). Voici le top à conserver/acheter.</p>
    </div>
    {cards_block}
    {self._cta_button('⬇️ Télécharger le momentum (CSV)', dl_path)}
    <p style="color:#a1a1aa;font-size:13px;text-align:center;">
      Connecte-toi à l'app pour lancer le rééquilibrage IBKR en un clic.</p>"""

        html = self._html_shell("C'est le moment de rééquilibrer !",
                                f"Momentum du {date_calcul}", body,
                                accent='#7c3aed,#1d4ed8', emoji='🔄')
        text = (f"C'est le moment de rééquilibrer ! ({date_calcul})\n" +
                "\n".join(f"{r.get('ticker'):8} {r.get('momentum',0):+.1f}%  alloc {r.get('allocation','—')}%"
                          for r in investir[:10]) +
                f"\n\nTéléchargement: {self.APP_BASE_URL}{dl_path}")
        return self._send(f"🔄 C'est le moment de rééquilibrer ! — {date_calcul}", html, text)

    # =====================================================================
    # ÉCHEC DE COLLECTE DE PRIX (yfinance)
    # =====================================================================
    def envoyer_echec_collecte(self, summary: dict) -> dict:
        """
        Alerte quand ≥ 25 % des tickers échouent lors de la collecte de prix nocturne.
        `summary` = dict renvoyé par PriceDataService.collect().
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        n = summary.get('tickers', 0)
        m = summary.get('monthly', {}) or {}
        d = summary.get('daily', {}) or {}
        fr_m = summary.get('fail_ratio_monthly', 0) * 100
        fr_d = summary.get('fail_ratio_daily', 0) * 100

        failed = sorted(set((m.get('failed') or []) + (d.get('failed') or [])))
        sample = ', '.join(failed[:40]) + (f' … (+{len(failed) - 40})' if len(failed) > 40 else '')

        body = f"""
    <div style="background:#18181b;border:1px solid #27272a;border-left:4px solid #dc2626;
                padding:20px;border-radius:10px;margin:14px 0;">
      <p style="margin:0 0 14px;font-size:16px;">La collecte de prix yfinance a rencontré
      un taux d'échec élevé. Les backtests pourraient être incomplets.</p>
      <table style="width:100%;border-collapse:collapse;"><tr>
        {self._kpi_card('Tickers visés', str(n))}
        {self._kpi_card('Échec mensuel', f'{fr_m:.0f}%', '#ef4444' if fr_m >= 25 else '#fafafa')}
        {self._kpi_card('Échec daily', f'{fr_d:.0f}%', '#ef4444' if fr_d >= 25 else '#fafafa')}
      </tr></table>
      <p style="margin:14px 0 4px;color:#a1a1aa;font-size:13px;">
        Nouvelles barres : {m.get('new_bars', 0)} mensuelles · {d.get('new_bars', 0)} journalières
        · durée {summary.get('elapsed_s', '—')}s</p>
    </div>
    <div style="background:#0f0f12;border:1px solid #27272a;padding:14px;border-radius:10px;">
      <div style="color:#a1a1aa;font-size:11px;text-transform:uppercase;margin-bottom:6px;">
        Tickers en échec ({len(failed)})</div>
      <div style="font-family:monospace;font-size:12px;color:#fca5a5;line-height:1.6;">
        {sample or '—'}</div>
    </div>
    <p style="color:#a1a1aa;font-size:13px;margin-top:14px;">
      Causes fréquentes : rate-limit Yahoo, symboles renommés/délistés, coupure réseau.
      La prochaine collecte nocturne réessaiera automatiquement (incrémental).</p>"""

        html = self._html_shell('Échec collecte de prix', now, body,
                                accent='#dc2626,#7f1d1d', emoji='⚠️')
        text = (f"Échec collecte yfinance — {now}\n"
                f"Tickers: {n} | échec mensuel {fr_m:.0f}% | échec daily {fr_d:.0f}%\n"
                f"En échec ({len(failed)}): {', '.join(failed[:60])}")
        return self._send(f"⚠️ Échec collecte de prix — {fr_m:.0f}% mensuel / {fr_d:.0f}% daily", html, text)

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

