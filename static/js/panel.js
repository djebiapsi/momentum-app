        async function loadPanel() {
            const list = document.getElementById('panel-list');
            list.innerHTML = skelStocks(6);
            const data = await api('/panel');
            if (data) {
                document.getElementById('panel-count').textContent = data.count + ' actions';
                if (data.actions.length > 0) {
                    list.innerHTML = data.actions.map(a => `
                        <div class="panel-item">
                            <span class="panel-ticker">
                                <a href="${getTradingViewUrl(a.ticker)}" target="_blank" rel="noopener" class="ticker-link">${a.ticker}</a>
                            </span>
                            <button class="btn-remove" onclick="removeTicker('${a.ticker}')">
                                <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                            </button>
                        </div>
                    `).join('');
                } else {
                    list.innerHTML = '<div class="empty-state"><p>Aucune action dans le panel</p></div>';
                }
                animIn(list);
            }
        }
        
        async function addTicker() {
            const input = document.getElementById('input-ticker');
            const ticker = input.value.trim().toUpperCase();
            
            if (!ticker) {
                showToast('Entrez un symbole', 'error');
                return;
            }
            
            const data = await api('/panel', {
                method: 'POST',
                body: JSON.stringify({ ticker })
            });
            
            if (data && data.success) {
                input.value = '';
                showToast(data.message);
                loadPanel();
            } else {
                showToast(data?.error || 'Erreur', 'error');
            }
        }
        
        async function removeTicker(ticker) {
            if (!confirm(`Retirer ${ticker} du panel ?`)) return;
            
            const data = await api('/panel/' + ticker, { method: 'DELETE' });
            
            if (data && data.success) {
                showToast(data.message);
                loadPanel();
            } else {
                showToast(data?.error || 'Erreur', 'error');
            }
        }
        
        async function clearPanel(strategy) {
            const panelName = strategy === 'long' ? 'panel' : 'panel Short';
            if (!confirm(`Supprimer tous les tickers du ${panelName} ?`)) return;
            
            const endpoint = strategy === 'long' ? '/panel/clear' : '/short/panel/clear';
            const data = await api(endpoint, { method: 'DELETE' });
            
            if (data && data.success) {
                showToast(data.message);
                if (strategy === 'long') {
                    loadPanel();
                } else {
                    loadShortPanel();
                }
            } else {
                showToast(data?.error || 'Erreur', 'error');
            }
        }
        
        // =================================================================
        // EXPORT / IMPORT PANEL
        // =================================================================
        
        function exportPanel(strategy) {
            window.location.href = `/api/panel/export?strategy=${strategy}`;
        }
        
        function triggerImportPanel(strategy) {
            document.getElementById(`import-${strategy}-file`).click();
        }
        
        async function importPanel(strategy, input) {
            const file = input.files[0];
            if (!file) return;
            
            try {
                const text = await file.text();
                let data;
                try {
                    data = JSON.parse(text);
                } catch (e) {
                    showToast('Fichier JSON invalide', 'error');
                    input.value = '';
                    return;
                }
                
                // S'assurer que le strategy est correct
                data.strategy = strategy;
                
                const response = await api('/panel/import', {
                    method: 'POST',
                    body: JSON.stringify(data)
                });
                
                if (response && response.success) {
                    showToast(response.message);
                    if (strategy === 'long') {
                        loadPanel();
                    } else {
                        loadShortPanel();
                    }
                } else {
                    showToast(response?.error || 'Erreur import', 'error');
                }
            } catch (e) {
                showToast('Erreur lecture fichier', 'error');
            }
            
            input.value = '';
        }
        
        // =================================================================
        // HISTORY
        // =================================================================
        
        async function loadHistory() {
            const list = document.getElementById('history-list');
            list.innerHTML = skelCards(4);
            const data = await api('/history');
            if (data && data.history.length > 0) {
                list.innerHTML = data.history.map(h => {
                    const topActions = h.details.filter(d => d.signal === 'Investir');
                    return `
                        <div class="history-item long" onclick="showHistoryDetail(${h.id})">
                            <div class="history-date">${h.calculation_date}</div>
                            <div class="history-summary">
                                Top ${h.nb_top}: ${topActions.map(a => a.ticker).join(', ')}
                            </div>
                        </div>
                    `;
                }).join('');
            } else {
                list.innerHTML = '<div class="empty-state"><p>Aucun historique disponible</p></div>';
            }
            animIn(list);
        }
        
        async function showHistoryDetail(id) {
            const data = await api('/history/' + id);
            if (data) {
                displayRecommendations(data);
                showPage('dashboard');
                document.querySelector('.nav-tab').click(); // Activate dashboard tab
            }
        }
        
        // =================================================================
        // SETTINGS
        // =================================================================
        
        async function loadSettings() {
            const data = await api('/settings');
            if (data) {
                document.getElementById('input-nb-top').value = data.nb_top;
                document.getElementById('input-date-calcul').value = data.date_calcul || '';

                // Email status
                const emailDiv = document.getElementById('email-status');
                if (data.email_configured) {
                    emailDiv.innerHTML = `
                        <div class="status-badge success">✓ Configuré</div>
                        <p style="margin-top: 8px; font-size: 13px; color: var(--text-secondary);">
                            Emails envoyés à: ${data.email_to}
                        </p>
                    `;
                } else {
                    emailDiv.innerHTML = `
                        <div class="status-badge warning">Non configuré</div>
                        <p style="margin-top: 8px; font-size: 13px; color: var(--text-secondary);">
                            Configurez RESEND_API_KEY et EMAIL_TO dans les variables d'environnement.
                        </p>
                    `;
                }
            }
        }
        
        async function saveSettings() {
            const nbTop = document.getElementById('input-nb-top').value;
            const dateCalcul = document.getElementById('input-date-calcul').value;

            const data = await api('/settings', {
                method: 'POST',
                body: JSON.stringify({
                    nb_top: parseInt(nbTop),
                    date_calcul: dateCalcul
                })
            });
            
            if (data && data.success) {
                showToast('Paramètres enregistrés');
            } else {
                showToast(data?.error || 'Erreur', 'error');
            }
        }
        
        async function sendTestEmail() {
            const btn = event.currentTarget;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Envoi...';
            
            const data = await api('/email/test', { method: 'POST' });
            
            btn.disabled = false;
            btn.innerHTML = 'Envoyer un test';
            
            if (data && data.success) {
                showToast(data.message);
            } else {
                showToast(data?.message || 'Erreur', 'error');
            }
        }
        
        // =================================================================
        // COLLECTE DE PRIX (yfinance — SP500 / NDX100)
        // =================================================================

        let _pricesPollTimer = null;

        function _fmtCoverage(c) {
            if (!c) return 'Couverture indisponible.';
            const cst = c.constituents || {};
            const m = c.monthly || {}, d = c.daily || {};
            const range = (x) => (x.start && x.end) ? `${x.start} → ${x.end}` : '—';
            const refreshed = cst.refreshed_at ? cst.refreshed_at.slice(0, 10) : 'jamais';
            return `
                <div><strong>Composition</strong> : ${cst.sp500 || 0} SP500 · ${cst.ndx100 || 0} NDX100
                     <span style="color:var(--text-muted)">(vérifiée ${refreshed})</span></div>
                <div style="margin-top:4px;"><strong>Mensuel</strong> : ${m.tickers || 0} tickers ·
                     ${(m.rows || 0).toLocaleString()} barres · ${range(m)}</div>
                <div style="margin-top:4px;"><strong>Journalier</strong> : ${d.tickers || 0} tickers ·
                     ${(d.rows || 0).toLocaleString()} barres · ${range(d)}</div>`;
        }

        async function loadPricesStatus() {
            const cov = document.getElementById('prices-coverage');
            const prog = document.getElementById('prices-progress');
            const btn = document.getElementById('btn-collect-prices');
            const data = await api('/prices/status');
            if (!data) { if (cov) cov.textContent = 'Erreur de chargement.'; return; }

            if (cov) cov.innerHTML = _fmtCoverage(data.coverage);

            const st = data.state || {};
            const running = st.running;
            if (btn) {
                btn.disabled = running;
                btn.innerHTML = running ? '<span class="spinner"></span> Collecte en cours…'
                                        : 'Lancer la collecte maintenant';
            }
            if (prog) {
                if (running) {
                    const p = st.progress || {};
                    const phase = { constituents: 'Composition des indices', monthly: 'Prix mensuels',
                                    daily: 'Prix journaliers', starting: 'Démarrage' }[st.phase] || st.phase;
                    const pct = p.total ? ` (${p.done}/${p.total})` : '';
                    prog.style.display = 'block';
                    prog.textContent = `⏳ ${phase}${pct}`;
                } else {
                    prog.style.display = 'none';
                    if (st.phase === 'error' && st.error) showToast('Collecte échouée : ' + st.error, 'error');
                }
            }

            // Poll tant que la collecte tourne
            if (running && !_pricesPollTimer) {
                _pricesPollTimer = setInterval(loadPricesStatus, 3000);
            } else if (!running && _pricesPollTimer) {
                clearInterval(_pricesPollTimer); _pricesPollTimer = null;
                if (st.summary) {
                    const s = st.summary;
                    showToast(`Collecte terminée : +${s.monthly?.new_bars || 0} mensuelles, ` +
                              `+${s.daily?.new_bars || 0} journalières`, 'success');
                }
            }
        }

        async function collectPrices(full) {
            if (full && !confirm('Recollecter TOUT l\'historique ? La 1ʳᵉ passe peut prendre plusieurs minutes.')) return;
            const data = await api('/prices/collect', {
                method: 'POST', body: JSON.stringify({ full: !!full })
            });
            if (data && data.success) {
                showToast(data.message || 'Collecte lancée');
                loadPricesStatus();
            } else {
                showToast(data?.message || 'Erreur', 'error');
            }
        }

        // =================================================================
        // IBKR REBALANCE VIA API
        // =================================================================

        async function rebalanceViaAPI() {
            if (!lastRecoData || !lastRecoData.recommendations) {
                showToast('Lancez d\'abord un calcul momentum', 'error'); return;
            }
            const targets = lastRecoData.recommendations
                .filter(r => r.signal === 'Investir' && r.allocation > 0)
                .map(r => ({ ticker: r.ticker, target_pct: r.allocation }));

            // Régime BEAR sur les deux poches : aucune cible → tout vendre (passage en cash)
            const goCash = !targets.length;
            if (goCash && !confirm('Le régime demande de passer 100 % en cash.\n\n'
                    + 'Prévisualiser la vente de TOUTES les positions du compte IBKR ?')) return;

            // Aperçu d'abord
            showToast('Calcul des ordres...', 'info');
            const preview = await api('/ibkr/rebalance', { method: 'POST',
                body: JSON.stringify({ targets, dry_run: true, allow_cash: goCash }) });
            if (!preview || !preview.success) { showToast(preview?.error || 'Erreur IBKR', 'error'); return; }

            const skippedTxt = (preview.skipped || []).length
                ? '\n— Ignorés —\n' + preview.skipped.map(s => `${s.ticker} : ${s.reason}`).join('\n') + '\n' : '';
            if (!preview.orders.length) {
                showToast('Aucun ordre nécessaire (portefeuille déjà équilibré)', 'success');
                if (skippedTxt) alert(`Aucun ordre à passer.${skippedTxt}`);
                return;
            }

            const ajustements = preview.orders.filter(o => !o.liquidation);
            const liquidations = preview.orders.filter(o => o.liquidation);
            const fmtQty = q => (Number.isInteger(q) ? q : q.toFixed(4));
            const fmtUsd = v => v == null ? '?' : '$' + Number(v).toFixed(2);
            // whatIf = simulation serveur : commission estimée ou raison du refus
            const fmtWhatIf = o => o.whatif === 'rejected' ? `\n  ⛔ IBKR refuserait : ${o.whatif_error}`
                : o.commission != null ? ` · comm. ~${fmtUsd(o.commission)}` : '';
            const fmtNote = o => o.note ? `\n  ℹ️ ${o.note}` : '';
            const fmtOrder = o =>
                `${o.action} ${fmtQty(o.qty)} × ${o.ticker} @ ~${fmtUsd(o.est_price)} ≈ ${fmtUsd(o.est_value)}${fmtWhatIf(o)}` +
                `\n  (actuel ${fmtUsd(o.current_value)} → cible ${fmtUsd(o.target_value)})${fmtNote(o)}`;

            let summary = '';
            if (liquidations.length)
                summary += '— Liquidations (hors stratégie) —\n' +
                    liquidations.map(o => `VENDRE ${fmtQty(o.qty)} × ${o.ticker} ≈ ${fmtUsd(o.est_value)}${fmtWhatIf(o)}${fmtNote(o)}`).join('\n') + '\n\n';
            if (ajustements.length)
                summary += '— Ajustements —\n' + ajustements.map(fmtOrder).join('\n') + '\n';
            summary += skippedTxt;
            // Fractionnaire refusé par la TWS API (10243) → compléments à passer dans IBKR Mobile
            const manual = preview.manual_orders || [];
            const fmtManual = m => m.cash != null
                ? `${m.action} ≈ ${fmtUsd(m.cash)} de ${m.ticker}${m.qty ? ` (~${m.qty} act.)` : ''}`
                : `${m.action} ${m.qty} × ${m.ticker} ≈ ${fmtUsd(m.est_value)}`;
            if (manual.length)
                summary += '\n— À passer à la main dans IBKR Mobile (fractionnaire, refusé par l\'API) —\n' +
                    manual.map(fmtManual).join('\n') + '\n';
            renderManualOrders(manual, fmtManual);

            const expo = preview.total_target_pct;
            let warn = '';
            if (expo != null && expo > 100)
                warn += `\n⚠️ EXPOSITION CIBLE : ${expo}% (levier ${(expo/100).toFixed(2)}×)\nNécessite de la marge.\n`;
            if (preview.queued_for_open)
                warn += '\n🕒 Marché US fermé : les ordres seront acceptés maintenant et exécutés au marché à la prochaine ouverture.\n';
            const rejected = preview.orders.filter(o => o.whatif === 'rejected').length;
            if (rejected)
                warn += `\n⛔ ${rejected} ordre(s) seraient refusés par IBKR (voir détails) — corriger avant d'exécuter.\n`;
            const commTotal = preview.orders.reduce((s, o) => s + (o.commission || 0), 0);
            const head = `Base : ${fmtUsd(preview.base_value)} (valeur nette du compte)` +
                (preview.cash != null ? ` · cash ${fmtUsd(preview.cash)}` : '') +
                (commTotal ? ` · commissions ≈ ${fmtUsd(commTotal)}` : '');

            if (!confirm(`${preview.count} ordre(s) à passer — ${head}\n${warn}\n${summary}\nConfirmer l'exécution ?`)) return;

            showToast('Passage des ordres...', 'info');
            const result = await api('/ibkr/rebalance', { method: 'POST',
                body: JSON.stringify({ targets, dry_run: false, allow_cash: goCash }) });
            if (result && result.success) {
                const placed = result.placed_count || 0;
                const failed = result.failed_count || 0;
                const other = result.orders.filter(o => !['placed', 'filled', 'failed'].includes(o.status));
                const line = o => `${o.ticker} ${o.action} ${fmtQty(o.qty)} → ${o.order_status || o.status}` +
                    (o.error ? ` : ${o.error}` : o.note ? ` : ${o.note}` : '');
                if (failed > 0 || other.length) {
                    const details = result.orders.map(line).join('\n');
                    showToast(`${placed} ordre(s) accepté(s), ${failed} échec(s)`, failed >= placed ? 'error' : 'success');
                    alert(`Résultat des ordres :\n\n${details}`);
                } else {
                    showToast(`${placed} ordre(s) accepté(s) par IBKR${result.queued_for_open ? ' (exécution à l\'ouverture)' : ''}`, 'success');
                }
                loadPerfData();  // rafraîchir les positions
                if (typeof loadIBKROrders === 'function') loadIBKROrders();
            } else {
                showToast(result?.error || 'Erreur lors du passage des ordres', 'error');
            }
        }

        // Compléments fractionnaires (hors API) affichés sous la décision, avec copie presse-papiers
        function renderManualOrders(manual, fmtManual) {
            const box = document.getElementById('ibkr-manual-orders');
            if (!box) return;
            if (!manual || !manual.length) { box.hidden = true; box.innerHTML = ''; return; }
            const lines = manual.map(fmtManual);
            box.hidden = false;
            box.innerHTML = `<strong>À passer dans IBKR Mobile</strong> — l'API IBKR refuse le fractionnaire (10243) :` +
                `<ul style="margin:6px 0 8px 18px;">${lines.map(l => `<li>${esc(l)}</li>`).join('')}</ul>` +
                `<button class="btn btn-ghost" style="padding:2px 10px;font-size:12px;" onclick="copyManualOrders()">Copier la liste</button>`;
            box.dataset.text = lines.join('\n');
        }

        function copyManualOrders() {
            const box = document.getElementById('ibkr-manual-orders');
            const text = box?.dataset.text || '';
            if (navigator.clipboard && text) {
                navigator.clipboard.writeText(text).then(() => showToast('Liste copiée', 'success'),
                                                        () => showToast('Copie impossible', 'error'));
            }
        }

        // =================================================================
        // IBKR
        // =================================================================

        // ---- IBKR Flex Web Service ----
        async function loadFlexStatus() {
            const data = await api('/flex/status');
            if (!data) return;
            const badge = document.getElementById('flex-status-badge');
            const info = document.getElementById('flex-sync-info');
            if (badge) {
                badge.textContent = data.configured ? '✓ Configuré' : 'Non configuré';
                badge.style.cssText = data.configured
                    ? 'font-size:10px;background:#14532d;color:#4ade80;'
                    : 'font-size:10px;background:var(--bg-hover);color:var(--text-muted);';
            }
            if (info) {
                const last = data.last_sync ? new Date(data.last_sync).toLocaleString('fr-FR') : 'jamais';
                const acctLine = data.account_id
                    ? `<br>Compte : <code>${data.account_id}</code>`
                      + (data.is_paper
                          ? ' <span style="background:#7c2d12;color:#fca5a5;padding:1px 6px;border-radius:4px;font-size:10px;">⚠ PAPER — créez une Flex Query sur votre compte LIVE</span>'
                          : ' <span style="background:#14532d;color:#4ade80;padding:1px 6px;border-radius:4px;font-size:10px;">LIVE ✓</span>')
                    : '';
                info.innerHTML = `Dernière synchro : ${last}${acctLine}<br>`
                    + `${data.snapshots} jours NAV · ${data.transactions} transactions · ${data.dividends} div · ${data.cash_flows || 0} flux`
                    + (data.last_error ? `<br><span style="color:#f87171">Erreur : ${data.last_error}</span>` : '');
            }
        }

        async function saveFlexCredentials() {
            const token = document.getElementById('flex-token').value.trim();
            const query_id = document.getElementById('flex-query-id').value.trim();
            if (!token || !query_id) { showToast('Token et Query ID requis', 'error'); return; }
            const data = await api('/flex/credentials', { method: 'POST', body: JSON.stringify({ token, query_id }) });
            if (!data) return;
            showToast(data.success ? 'Identifiants Flex sauvegardés' : data.error, data.success ? 'success' : 'error');
            if (data.success) document.getElementById('flex-token').value = '';
            loadFlexStatus();
        }

        async function syncFlex() {
            showToast('Synchronisation Flex en cours (~30s)...', 'info');
            const data = await api('/flex/sync', { method: 'POST' });
            if (!data) return;
            if (data.success) {
                const i = data.imported;
                const paperWarn = data.is_paper ? ' ⚠ Compte PAPER détecté !' : '';
                showToast(
                    `Importé : ${i.snapshots} NAV, ${i.transactions} tx, ${i.dividends} div${paperWarn}`,
                    data.is_paper ? 'error' : 'success'
                );
            } else {
                showToast(data.error || 'Erreur Flex', 'error');
            }
            loadFlexStatus();
        }

        async function sendDigest() {
            const btn = document.getElementById('btn-send-digest');
            const info = document.getElementById('digest-send-info');
            if (btn) { btn.disabled = true; btn.textContent = 'Génération en cours (~30s)…'; }
            if (info) info.textContent = 'Récupération des articles + résumé IA…';

            try {
                const data = await api('/digest/send', { method: 'POST' });
                if (!data) throw new Error('Pas de réponse');
                if (data.success) {
                    showToast(`✅ Digest envoyé (${data.articles || '?'} articles → ${(data.recipients||[]).join(', ')})`, 'success');
                    if (info) info.textContent = `Envoyé · ${data.articles || '?'} articles · ${new Date().toLocaleTimeString('fr-FR')}`;
                } else {
                    showToast(data.message || data.error || 'Erreur lors de l\'envoi', 'error');
                    if (info) info.textContent = data.message || data.error || 'Erreur';
                }
            } catch(e) {
                showToast('Erreur réseau', 'error');
                if (info) info.textContent = 'Erreur réseau';
            } finally {
                if (btn) { btn.disabled = false; btn.textContent = 'Envoyer maintenant'; }
            }
        }

        async function sendPushTest() {
            const info = document.getElementById('push-test-info');
            if (info) info.textContent = 'Envoi…';
            const status = await api('/push/status');
            const nb = status?.subscribers ?? '?';
            try {
                const data = await api('/push/test', { method: 'POST' });
                if (info) info.textContent = data?.success
                    ? `✅ Envoyé à ${data.sent}/${nb} appareil(s)${data.failed ? ` (${data.failed} échec)` : ''}`
                    : `❌ ${data?.message || data?.error || 'Erreur'}`;
            } catch(e) { if (info) info.textContent = 'Erreur réseau'; }
        }

        async function loadPushSubscriptions() {
            const el = document.getElementById('push-subs-list');
            if (!el) return;
            el.textContent = 'Chargement…';
            try {
                const data = await api('/push/subscriptions');
                if (!data || !data.subscriptions) { el.textContent = 'Erreur'; return; }
                const list = data.subscriptions;
                if (!list.length) { el.textContent = 'Aucun appareil enregistré.'; return; }
                el.innerHTML = list.map(s => {
                    const date = s.created_at ? new Date(s.created_at).toLocaleDateString('fr-FR') : '?';
                    return `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid #1f2937;">
                        <span>📱 <b>${s.label}</b> <span style="color:var(--text-muted)">…${s.endpoint_short}</span></span>
                        <span style="color:var(--text-muted);margin-left:8px;">${date}
                        <button onclick="deletePushSub(${s.id})" style="margin-left:6px;background:#7f1d1d;border:none;color:#fca5a5;border-radius:4px;padding:1px 6px;cursor:pointer;font-size:10px;">✕</button>
                        </span></div>`;
                }).join('');
            } catch(e) { el.textContent = 'Erreur réseau'; }
        }

        async function deletePushSub(id) {
            if (!confirm('Supprimer cet abonnement ?')) return;
            const data = await api(`/push/subscriptions/${id}`, { method: 'DELETE' });
            if (data?.success) { showToast('Abonnement supprimé', 'success'); loadPushSubscriptions(); }
        }

        async function loadNotifPrefs() {
            const data = await api('/market/notification-prefs');
            if (!data) return;
            const selOpen  = document.getElementById('notif-open');
            const selClose = document.getElementById('notif-close');
            if (selOpen  && data.open)  selOpen.value  = data.open;
            if (selClose && data.close) selClose.value = data.close;
        }

        async function saveNotifPrefs() {
            const info = document.getElementById('notif-prefs-info');
            const open  = document.getElementById('notif-open')?.value;
            const close = document.getElementById('notif-close')?.value;
            const data = await api('/market/notification-prefs', {
                method: 'POST', body: JSON.stringify({ open, close })
            });
            if (info) info.textContent = data?.success ? '✅ Préférences enregistrées' : '❌ Erreur';
            if (data?.success) showToast('Préférences de notification enregistrées', 'success');
        }

        async function sendDigestTech() {
            const btn  = document.getElementById('btn-send-digest-tech');
            const info = document.getElementById('digest-tech-send-info');
            if (btn)  { btn.disabled = true; btn.textContent = 'Génération en cours (~45s)…'; }
            if (info) info.textContent = 'Récupération des flux tech + résumé IA…';

            try {
                const data = await api('/digest/tech/send', { method: 'POST' });
                if (!data) throw new Error('Pas de réponse');
                if (data.success) {
                    showToast(`✅ Digest Tech envoyé (${data.articles || '?'} articles → ${(data.recipients||[]).join(', ')})`, 'success');
                    if (info) info.textContent = `Envoyé · ${data.articles || '?'} articles · ${new Date().toLocaleTimeString('fr-FR')}`;
                } else {
                    showToast(data.message || data.error || 'Erreur lors de l\'envoi', 'error');
                    if (info) info.textContent = data.message || data.error || 'Erreur';
                }
            } catch(e) {
                showToast('Erreur réseau', 'error');
                if (info) info.textContent = 'Erreur réseau';
            } finally {
                if (btn) { btn.disabled = false; btn.textContent = 'Envoyer maintenant'; }
            }
        }

        async function loadIBKRStatus() {
            const data = await api('/ibkr/status');
            if (!data) return;
            const badge = document.getElementById('ibkr-status-badge');
            const detail = document.getElementById('ibkr-status-detail');
            if (data.connected) {
                badge.style.cssText = 'background:#14532d;color:#4ade80';
                badge.textContent = '✓ Connecté';
                const since = data.connected_at
                    ? new Date(data.connected_at * 1000).toLocaleString('fr-FR')
                    : '—';
                detail.textContent = `Connecté depuis ${since}`;
            } else {
                badge.style.cssText = 'background:#450a0a;color:#f87171';
                badge.textContent = '✗ Déconnecté';
                detail.textContent = data.last_error ? `Dernière erreur : ${data.last_error}` : 'Non connecté à IB Gateway';
            }

            // Mode de trading (live / paper)
            const mode = data.trading_mode || 'live';
            const modeBadge = document.getElementById('ibkr-mode-badge');
            if (modeBadge) {
                const isLive = mode === 'live';
                modeBadge.textContent = isLive ? 'LIVE (réel)' : 'PAPER (simu)';
                modeBadge.style.cssText = isLive
                    ? 'font-size:10px;padding:2px 8px;border-radius:8px;background:#450a0a;color:#f87171;'
                    : 'font-size:10px;padding:2px 8px;border-radius:8px;background:#1e3a5f;color:#60a5fa;';
            }
            const btnLive  = document.getElementById('ibkr-mode-live');
            const btnPaper = document.getElementById('ibkr-mode-paper');
            if (btnLive && btnPaper) {
                const active = 'background:var(--accent-blue);color:white;';
                const idle   = 'background:var(--bg-secondary);border:1px solid var(--border-color);color:var(--text-secondary);';
                btnLive.style.cssText  = 'flex:1;' + (mode === 'live'  ? active : idle);
                btnPaper.style.cssText = 'flex:1;' + (mode === 'paper' ? active : idle);
            }
        }

        async function ibkrConnect() {
            showToast('Connexion en cours...', 'info');
            const data = await api('/ibkr/connect', { method: 'POST' });
            if (!data) return;
            showToast(data.success ? 'IBKR connecté !' : `Erreur : ${data.error}`, data.success ? 'success' : 'error');
            await loadIBKRStatus();
        }

        async function setTradingMode(mode) {
            const label = mode === 'live' ? 'LIVE (réel)' : 'PAPER (simulation)';
            if (!confirm(`Basculer en mode ${label} ?\n\nLe gateway va redémarrer (~90s) et une 2FA sera demandée sur votre téléphone.`)) return;
            showToast(`Bascule en ${mode}...`, 'info');
            const data = await api('/ibkr/trading-mode', { method: 'POST', body: JSON.stringify({ mode }) });
            if (!data) return;
            showToast(data.success ? data.message : `Erreur : ${data.error}`, data.success ? 'success' : 'error');
            await loadIBKRStatus();
        }

        async function saveIBKRCredentials() {
            const username = document.getElementById('ibkr-username').value.trim();
            const password = document.getElementById('ibkr-password').value;
            const trading_mode = document.getElementById('ibkr-trading-mode').value;
            if (!username || !password) { showToast('Remplissez tous les champs', 'error'); return; }
            const data = await api('/ibkr/credentials', {
                method: 'POST',
                body: JSON.stringify({ username, password, trading_mode })
            });
            if (!data) return;
            showToast(data.success ? 'Identifiants sauvegardés' : data.error, data.success ? 'success' : 'error');
            if (data.success) document.getElementById('ibkr-password').value = '';
        }

        // ---- Ordres IBKR (ouverts + exécutions du jour) ----
        async function loadIBKROrders() {
            const container = document.getElementById('ibkr-orders-content');
            if (!container) return;
            container.innerHTML = '<p style="color:var(--text-muted);font-size:13px;text-align:center;padding:16px;">Chargement...</p>';
            const data = await api('/ibkr/orders');
            if (!data || !data.success) {
                container.innerHTML = `<p style="color:#f87171;font-size:13px;text-align:center;padding:16px;">${esc(data?.error || 'Erreur de connexion')}</p>`;
                return;
            }
            const td = 'padding:6px 10px;border-bottom:1px solid var(--border-subtle);';
            const th = 'padding:6px 10px;text-align:left;font-size:10px;color:var(--text-muted);text-transform:uppercase';
            const n = (v, d = 2) => v == null ? '—' : Number(v).toFixed(d);
            const acc = data.account || {};
            let html = `<p style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">
                Valeur nette ${acc.net_liquidation != null ? '$' + n(acc.net_liquidation) : '—'} · cash ${acc.total_cash != null ? '$' + n(acc.total_cash) : '—'}
                · ${new Date().toLocaleTimeString('fr-FR')}</p>`;
            html += `<div style="font-size:12px;font-weight:600;margin:8px 0 4px;">Ordres ouverts (${data.open_orders.length})</div>`;
            if (!data.open_orders.length) {
                html += '<p style="color:var(--text-muted);font-size:13px;">Aucun ordre ouvert</p>';
            } else {
                html += `<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:13px;">
                    <thead><tr style="background:var(--bg-hover);"><th style="${th}">Ticker</th><th style="${th}">Ordre</th><th style="${th}">Statut</th><th style="${th}">Exécuté</th><th style="${th}"></th></tr></thead><tbody>` +
                    data.open_orders.map(o => `<tr>
                        <td style="${td}font-family:monospace;font-weight:600">${esc(o.ticker)}</td>
                        <td style="${td}">${esc(o.action)} ${o.cash_qty ? '$' + n(o.cash_qty) : n(o.qty, 4)} ${esc(o.order_type)}${o.limit_price ? ' @ $' + n(o.limit_price) : ''} ${esc(o.tif || '')}</td>
                        <td style="${td}">${esc(o.status)}${o.why_held ? ' <span style="color:var(--text-muted)">(' + esc(o.why_held) + ')</span>' : ''}${o.message ? '<div style="font-size:11px;color:var(--text-muted)">' + esc(o.message) + '</div>' : ''}</td>
                        <td style="${td}text-align:right">${n(o.filled, 4)}${o.avg_fill_price ? ' @ $' + n(o.avg_fill_price) : ''}</td>
                        <td style="${td}text-align:right">${o.order_id ? `<button class="btn btn-ghost" style="padding:2px 8px;font-size:11px;" onclick="cancelIBKROrder(${o.order_id})">Annuler</button>` : ''}</td>
                    </tr>`).join('') + '</tbody></table></div>';
            }
            html += `<div style="font-size:12px;font-weight:600;margin:12px 0 4px;">Exécutions du jour (${data.executions.length})</div>`;
            if (data.executions_error) html += `<p style="color:#f87171;font-size:12px;">${esc(data.executions_error)}</p>`;
            if (!data.executions.length) {
                html += '<p style="color:var(--text-muted);font-size:13px;">Aucune exécution aujourd\'hui</p>';
            } else {
                html += `<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:13px;">
                    <thead><tr style="background:var(--bg-hover);"><th style="${th}">Heure</th><th style="${th}">Ticker</th><th style="${th}">Sens</th><th style="${th}">Qté</th><th style="${th}">Prix</th><th style="${th}">Comm.</th></tr></thead><tbody>` +
                    data.executions.map(x => `<tr>
                        <td style="${td}">${esc(String(x.time).replace('T', ' ').slice(0, 19))}</td>
                        <td style="${td}font-family:monospace;font-weight:600">${esc(x.ticker)}</td>
                        <td style="${td}">${esc(x.side)}</td>
                        <td style="${td}text-align:right">${n(x.shares, 4)}</td>
                        <td style="${td}text-align:right">$${n(x.price)}</td>
                        <td style="${td}text-align:right">${x.commission != null ? '$' + n(x.commission) : '—'}</td>
                    </tr>`).join('') + '</tbody></table></div>';
            }
            container.innerHTML = html;
        }

        async function cancelIBKROrder(orderId) {
            if (!confirm(`Annuler l'ordre ${orderId} ?`)) return;
            const res = await api(`/ibkr/orders/${orderId}/cancel`, { method: 'POST' });
            showToast(res?.success ? `Ordre ${orderId} annulé (${res.order_status})` : (res?.error || 'Annulation impossible'),
                      res?.success ? 'success' : 'error');
            loadIBKROrders();
        }

        function esc(s) {
            return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
        }

        async function loadIBKRPositions() {
            const container = document.getElementById('ibkr-positions-content');
            container.innerHTML = '<p style="color:var(--text-muted);font-size:13px;text-align:center;padding:16px;">Chargement...</p>';
            const data = await api('/ibkr/positions');
            if (!data || !data.success) {
                container.innerHTML = `<p style="color:#f87171;font-size:13px;text-align:center;padding:16px;">${data?.error || 'Erreur de connexion'}</p>`;
                return;
            }
            if (!data.positions.length) {
                container.innerHTML = '<p style="color:var(--text-muted);font-size:13px;text-align:center;padding:16px;">Aucune position ouverte</p>';
                return;
            }
            const rows = data.positions.map(p => {
                const upl = p.unrealized_pnl;
                const uplColor = (upl != null && upl >= 0) ? 'var(--accent-long)' : '#f87171';
                const uplStr = upl != null ? `<span style="color:${uplColor}">${upl >= 0 ? '+' : ''}${upl.toFixed(2)}</span>` : '—';
                return `<tr>
                    <td style="padding:8px 12px;border-bottom:1px solid var(--border-subtle);font-family:monospace;font-weight:600">${p.ticker}</td>
                    <td style="padding:8px 12px;border-bottom:1px solid var(--border-subtle);text-align:right">${p.qty}</td>
                    <td style="padding:8px 12px;border-bottom:1px solid var(--border-subtle);text-align:right">${p.avg_cost ? '$'+p.avg_cost.toFixed(2) : '—'}</td>
                    <td style="padding:8px 12px;border-bottom:1px solid var(--border-subtle);text-align:right">${p.market_price ? '$'+p.market_price.toFixed(2) : '—'}</td>
                    <td style="padding:8px 12px;border-bottom:1px solid var(--border-subtle);text-align:right">${p.market_value ? '$'+Math.round(p.market_value).toLocaleString() : '—'}</td>
                    <td style="padding:8px 12px;border-bottom:1px solid var(--border-subtle);text-align:right">${uplStr}</td>
                </tr>`;
            }).join('');
            container.innerHTML = `
                <p style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">${data.count} position(s) · ${new Date().toLocaleTimeString('fr-FR')}</p>
                <div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;font-size:13px;">
                    <thead><tr style="background:var(--bg-hover);">
                        <th style="padding:8px 12px;text-align:left;font-size:10px;color:var(--text-muted);text-transform:uppercase">Ticker</th>
                        <th style="padding:8px 12px;text-align:right;font-size:10px;color:var(--text-muted);text-transform:uppercase">Qty</th>
                        <th style="padding:8px 12px;text-align:right;font-size:10px;color:var(--text-muted);text-transform:uppercase">Avg Cost</th>
                        <th style="padding:8px 12px;text-align:right;font-size:10px;color:var(--text-muted);text-transform:uppercase">Prix</th>
                        <th style="padding:8px 12px;text-align:right;font-size:10px;color:var(--text-muted);text-transform:uppercase">Valeur</th>
                        <th style="padding:8px 12px;text-align:right;font-size:10px;color:var(--text-muted);text-transform:uppercase">P&L</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`;
        }

        // =================================================================
        // SHORT - PANEL TABS
        // =================================================================
        
        function showPanelTab(tab) {
            const longSection = document.getElementById('panel-long-section');
            const shortSection = document.getElementById('panel-short-section');
            const longTab = document.getElementById('tab-long-panel');
            const shortTab = document.getElementById('tab-short-panel');
            
            if (tab === 'long') {
                longSection.style.display = 'block';
                shortSection.style.display = 'none';
                longTab.classList.add('active');
                shortTab.classList.remove('active');
                loadPanel();
            } else {
                longSection.style.display = 'none';
                shortSection.style.display = 'block';
                shortTab.classList.add('active');
                longTab.classList.remove('active');
                loadShortPanel();
            }
        }
        
        function showHistoryTab(tab) {
            const longSection = document.getElementById('history-long-section');
            const shortSection = document.getElementById('history-short-section');
            const longTab = document.getElementById('tab-long-history');
            const shortTab = document.getElementById('tab-short-history');
            
            if (tab === 'long') {
                longSection.style.display = 'block';
                shortSection.style.display = 'none';
                longTab.classList.add('active');
                shortTab.classList.remove('active');
                loadHistory();
            } else {
                longSection.style.display = 'none';
                shortSection.style.display = 'block';
                shortTab.classList.add('active');
                longTab.classList.remove('active');
                loadShortHistory();
            }
        }
        
        // =================================================================
        // SHORT - PANEL
        // =================================================================
        
