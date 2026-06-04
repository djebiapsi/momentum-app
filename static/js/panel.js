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
        
        function toggleVolScalingInputs() {
            const on = document.getElementById('input-vol-scaling').checked;
            document.getElementById('vol-scaling-params').style.display = on ? 'block' : 'none';
        }

        function togglePortfolioFilterInputs() {
            const on = document.getElementById('input-portfolio-filter').checked;
            document.getElementById('portfolio-filter-params').style.display = on ? 'block' : 'none';
        }

        async function loadSettings() {
            const data = await api('/settings');
            if (data) {
                document.getElementById('input-nb-top').value = data.nb_top;
                document.getElementById('input-date-calcul').value = data.date_calcul || '';
                document.getElementById('input-vol-scaling').checked = !!data.vol_scaling_enabled;
                if (data.vol_target != null) document.getElementById('input-vol-target').value = data.vol_target;
                if (data.max_exposure != null) document.getElementById('input-max-exposure').value = data.max_exposure;
                document.getElementById('input-portfolio-filter').checked = !!data.portfolio_filter_enabled;
                if (data.portfolio_vol_threshold != null) document.getElementById('input-portfolio-threshold').value = data.portfolio_vol_threshold;
                toggleVolScalingInputs();
                togglePortfolioFilterInputs();

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
            const volScaling = document.getElementById('input-vol-scaling').checked;
            const volTarget = parseFloat(document.getElementById('input-vol-target').value);
            const maxExposure = parseFloat(document.getElementById('input-max-exposure').value);
            const portfolioFilter = document.getElementById('input-portfolio-filter').checked;
            const portfolioThreshold = parseFloat(document.getElementById('input-portfolio-threshold').value);

            const data = await api('/settings', {
                method: 'POST',
                body: JSON.stringify({
                    nb_top: parseInt(nbTop),
                    date_calcul: dateCalcul,
                    vol_scaling_enabled: volScaling,
                    vol_target: volTarget,
                    max_exposure: maxExposure,
                    portfolio_filter_enabled: portfolioFilter,
                    portfolio_vol_threshold: portfolioThreshold
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
        // IBKR REBALANCE VIA API
        // =================================================================

        async function rebalanceViaAPI() {
            if (!lastRecoData || !lastRecoData.recommendations) {
                showToast('Lancez d\'abord un calcul momentum', 'error'); return;
            }
            const targets = lastRecoData.recommendations
                .filter(r => r.signal === 'Investir' && r.allocation > 0)
                .map(r => ({ ticker: r.ticker, target_pct: r.allocation }));

            if (!targets.length) { showToast('Aucune position à acheter', 'error'); return; }

            // Aperçu d'abord
            showToast('Calcul des ordres...', 'info');
            const preview = await api('/ibkr/rebalance', { method: 'POST', body: JSON.stringify({ targets, dry_run: true }) });
            if (!preview || !preview.success) { showToast(preview?.error || 'Erreur IBKR', 'error'); return; }

            if (!preview.orders.length) { showToast('Aucun ordre nécessaire (portefeuille déjà équilibré)', 'success'); return; }

            const ajustements = preview.orders.filter(o => !o.liquidation);
            const liquidations = preview.orders.filter(o => o.liquidation);

            const fmtOrder = o =>
                `${o.action} ${o.qty} action(s) ${o.ticker} @ ~$${o.est_price} ≈ $${o.est_value}` +
                `\n  (actuel $${o.current_value} → cible $${o.target_value})`;

            let summary = '';
            if (ajustements.length)
                summary += '— Ajustements —\n' + ajustements.map(fmtOrder).join('\n') + '\n';
            if (liquidations.length)
                summary += '\n— Liquidations (hors stratégie) —\n' +
                    liquidations.map(o => `VENDRE TOUT ${o.qty} × ${o.ticker} ≈ $${o.est_value}`).join('\n') + '\n';

            const expo = preview.total_target_pct;
            let warn = expo != null && expo > 100
                ? `\n⚠️ EXPOSITION CIBLE : ${expo}% (levier ${(expo/100).toFixed(2)}×)\nNécessite de la marge.\n` : '';

            if (!confirm(`${preview.count} ordre(s) à passer :\n${warn}\n${summary}\nConfirmer l'exécution ?`)) return;

            showToast('Passage des ordres...', 'info');
            const result = await api('/ibkr/rebalance', { method: 'POST', body: JSON.stringify({ targets, dry_run: false }) });
            if (result && result.success) {
                const placed = result.placed_count || 0;
                const failed = result.failed_count || 0;
                if (failed > 0) {
                    const fails = result.orders.filter(o => o.status === 'failed')
                        .map(o => `${o.ticker}: ${o.error || 'échec'}`).join('\n');
                    showToast(`${placed} ordre(s) passé(s), ${failed} échec(s)`, failed >= placed ? 'error' : 'success');
                    alert(`Ordres en échec :\n\n${fails}`);
                } else {
                    showToast(`${placed} ordre(s) passé(s) avec succès`, 'success');
                }
                loadPerfData();  // rafraîchir les positions
            } else {
                showToast(result?.error || 'Erreur lors du passage des ordres', 'error');
            }
        }

        // =================================================================
        // ORDRES DE TEST + ORDRE MANUEL
        // =================================================================

        function toggleLimitPrice() {
            const type = document.getElementById('test-order-type').value;
            document.getElementById('test-order-limit-wrap').style.display =
                type === 'LMT' ? '' : 'none';
        }

        async function placeTestOrder() {
            const ticker = (document.getElementById('test-order-ticker').value || '').trim().toUpperCase();
            const action = document.getElementById('test-order-action').value;
            const qty    = parseFloat(document.getElementById('test-order-qty').value) || 1;
            const type   = document.getElementById('test-order-type').value;
            const limit  = type === 'LMT' ? parseFloat(document.getElementById('test-order-limit').value) : null;

            if (!ticker) { showToast('Ticker requis', 'error'); return; }
            if (type === 'LMT' && !limit) { showToast('Prix limite requis pour LMT', 'error'); return; }

            const resultDiv = document.getElementById('test-order-result');
            resultDiv.style.display = 'block';
            resultDiv.style.background = 'rgba(255,255,255,.04)';
            resultDiv.textContent = '⏳ Passage de l\'ordre…';

            const tif  = document.getElementById('test-order-tif').value;
            const body = JSON.stringify({ ticker, action, qty, order_type: type,
                                          limit_price: limit, currency: 'USD', tif });
            const res = await api('/ibkr/order/single', { method: 'POST', body });

            if (!res) { resultDiv.textContent = '⚠️ Pas de réponse'; return; }

            if (res.success) {
                resultDiv.style.background = 'rgba(29,158,117,.12)';
                resultDiv.innerHTML =
                    `✅ <b>Ordre passé</b> — orderId: <b>${res.order_id ?? '?'}</b> · status: <b>${res.order_status ?? '?'}</b><br>` +
                    `${res.action} ${res.qty} × ${res.ticker} @ ${res.order_type}` +
                    (res.limit_price ? ` $${res.limit_price}` : '');
                showToast(`Ordre ${res.action} ${res.qty}×${res.ticker} passé`, 'success');
                setTimeout(loadOpenOrders, 1500);
            } else {
                resultDiv.style.background = 'rgba(216,90,48,.12)';
                resultDiv.innerHTML = `⚠️ <b>Échec</b> : ${res.error || 'erreur inconnue'}`;
                showToast(res.error || 'Échec de l\'ordre', 'error');
            }
        }

        async function loadOpenOrders() {
            const cont = document.getElementById('open-orders-list');
            cont.innerHTML = '<div class="empty-state" style="font-size:12px;">Chargement…</div>';
            const res = await api('/ibkr/orders/open');
            if (!res || !res.success) {
                cont.innerHTML = `<div class="empty-state" style="font-size:12px;color:var(--accent-short);">${res?.error || 'Erreur IBKR'}</div>`;
                return;
            }
            if (!res.orders.length) {
                cont.innerHTML = '<div class="empty-state" style="font-size:12px;">Aucun ordre ouvert</div>';
                return;
            }
            cont.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px;font-family:'IBM Plex Mono';">
                <thead><tr style="color:var(--text-muted);text-align:left;border-bottom:1px solid var(--border);">
                    <th style="padding:5px 8px;">ID</th>
                    <th style="padding:5px 8px;">Ticker</th>
                    <th style="padding:5px 8px;">Sens</th>
                    <th style="padding:5px 8px;">Qté</th>
                    <th style="padding:5px 8px;">Type</th>
                    <th style="padding:5px 8px;">Prix lim.</th>
                    <th style="padding:5px 8px;"></th>
                </tr></thead>
                <tbody>${res.orders.map(o => `
                <tr style="border-top:1px solid var(--border);">
                    <td style="padding:5px 8px;color:var(--text-muted);">#${o.order_id}</td>
                    <td style="padding:5px 8px;font-weight:600;color:var(--text-primary);">${o.ticker || '—'}</td>
                    <td style="padding:5px 8px;color:${o.action==='BUY'?'var(--accent-long)':'var(--accent-short)'};">${o.action}</td>
                    <td style="padding:5px 8px;">${o.qty}</td>
                    <td style="padding:5px 8px;">${o.order_type}</td>
                    <td style="padding:5px 8px;">${o.lmt_price ? '$'+o.lmt_price : '—'}</td>
                    <td style="padding:5px 8px;">
                        <button onclick="cancelOrder(${o.order_id})" style="font-size:10px;padding:2px 8px;border-radius:6px;
                            background:rgba(216,90,48,.15);color:var(--accent-short);border:1px solid rgba(216,90,48,.3);cursor:pointer;">
                            Annuler
                        </button>
                    </td>
                </tr>`).join('')}</tbody>
            </table>`;
        }

        async function cancelOrder(orderId) {
            if (!confirm(`Annuler l'ordre #${orderId} ?`)) return;
            const res = await api(`/ibkr/orders/cancel/${orderId}`, { method: 'POST' });
            if (res?.success) {
                showToast(`Ordre #${orderId} annulé`, 'success');
                setTimeout(loadOpenOrders, 1000);
            } else {
                showToast(res?.error || 'Échec annulation', 'error');
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
                info.innerHTML = `Dernière synchro : ${last}<br>`
                    + `${data.snapshots} jours NAV · ${data.transactions} transactions · ${data.dividends} dividendes`
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
                showToast(`Importé : ${i.snapshots} NAV, ${i.transactions} tx, ${i.dividends} div`, 'success');
            } else {
                showToast(data.error || 'Erreur Flex', 'error');
            }
            loadFlexStatus();
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
        
