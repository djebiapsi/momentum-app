        async function loadShortPanel() {
            const data = await api('/short/panel');
            if (data) {
                document.getElementById('short-panel-count').textContent = data.count + ' actions';
                
                if (data.actions.length > 0) {
                    document.getElementById('short-panel-list').innerHTML = data.actions.map(a => `
                        <div class="panel-item">
                            <div style="display: flex; flex-direction: column;">
                                <span class="panel-ticker">
                                    <a href="${getTradingViewUrl(a.ticker)}" target="_blank" rel="noopener" class="ticker-link">${a.ticker}</a>
                                </span>
                                ${a.perf_year ? `<span style="font-size: 11px; color: var(--accent-short);">${a.perf_year}% YTD</span>` : ''}
                            </div>
                            <button class="btn-remove" onclick="removeShortTicker('${a.ticker}')">
                                <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                            </button>
                        </div>
                    `).join('');
                } else {
                    document.getElementById('short-panel-list').innerHTML = '<div class="empty-state"><p>Aucune action dans le panel Short</p></div>';
                }
            }
        }
        
        async function addShortTicker() {
            const input = document.getElementById('input-short-ticker');
            const ticker = input.value.trim().toUpperCase();
            
            if (!ticker) {
                showToast('Entrez un symbole', 'error');
                return;
            }
            
            const data = await api('/short/panel', {
                method: 'POST',
                body: JSON.stringify({ ticker })
            });
            
            if (data && data.success) {
                input.value = '';
                showToast(data.message);
                loadShortPanel();
            } else {
                showToast(data?.error || 'Erreur', 'error');
            }
        }
        
        async function removeShortTicker(ticker) {
            if (!confirm(`Retirer ${ticker} du panel Short ?`)) return;
            
            const data = await api('/short/panel/' + ticker, { method: 'DELETE' });
            
            if (data && data.success) {
                showToast(data.message);
                loadShortPanel();
            } else {
                showToast(data?.error || 'Erreur', 'error');
            }
        }
        
        // =================================================================
        // SHORT - SCREENER (via Finviz)
        // =================================================================
        
        function confirmGenerateShortPanel() {
            const body = document.getElementById('generate-modal-body');
            body.innerHTML = `
                <div style="text-align: center; padding: 20px;">
                    <h3 style="margin-bottom: 16px;">Générer panel Short</h3>
                    <p class="info-box" style="margin-bottom: 16px; text-align: left;">
                        <strong>Critères stricts via Finviz :</strong><br><br>
                        • MarketCap ≥ $2B (shortabilité)<br>
                        • Avg Volume ≥ 500K, Price ≥ $5<br>
                        • Perf 1M ≤ -8%, Perf 3M ≤ -15%<br>
                        • Price &lt; SMA50, Price &lt; SMA200<br>
                        • SMA50 &lt; SMA200 (Death Cross)<br><br>
                        <strong>Score :</strong> (Perf_1M × 0.4) + (Perf_3M × 0.6)<br>
                        <span style="color: var(--text-muted);">Les 50 scores les plus négatifs sont sélectionnés.</span>
                    </p>
                    <p style="color: var(--accent-long); font-size: 12px; margin-bottom: 20px;">
                        0 appel API Tiingo consommé
                    </p>
                    <div class="confirm-buttons">
                        <button class="btn btn-secondary" onclick="closeGenerateModal()">Annuler</button>
                        <button class="btn btn-short" onclick="startGenerateShortPanel()">Lancer</button>
                    </div>
                </div>
            `;
            document.getElementById('generate-modal-title').textContent = 'Panel Short';
            document.getElementById('generate-modal-header').classList.remove('long');
            document.getElementById('generate-modal-header').classList.add('short');
            openGenerateModal();
        }
        
        async function startGenerateShortPanel() {
            const body = document.getElementById('generate-modal-body');
            
            body.innerHTML = `
                <div style="text-align: center; padding: 20px;">
                    <h3 style="margin-bottom: 20px;">Analyse Finviz en cours</h3>
                    <div class="progress-container">
                        <div class="progress-bar">
                            <div class="progress-fill" id="generate-short-progress" style="width: 0%"></div>
                        </div>
                        <p class="progress-text" id="generate-short-status">Connexion à Finviz...</p>
                    </div>
                    <p style="color: var(--text-muted); font-size: 12px; margin-top: 20px;">
                        Ne fermez pas cette fenêtre pendant l'analyse
                    </p>
                </div>
            `;
            
            let progress = 0;
            const progressInterval = setInterval(() => {
                if (progress < 90) {
                    progress += Math.random() * 8;
                    const el = document.getElementById('generate-short-progress');
                    if (el) el.style.width = progress + '%';
                }
            }, 500);
            
            try {
                const data = await api('/short/screener/generate', { method: 'POST' });
                
                clearInterval(progressInterval);
                
                if (data && data.success) {
                    generatedShortTickers = data.tickers;
                    displayGeneratedShortResults(data);
                } else {
                    body.innerHTML = `
                        <div style="text-align: center; padding: 20px;">
                            <h3 style="color: var(--accent-short); margin-bottom: 16px;">Erreur</h3>
                            <p style="color: var(--text-secondary);">${data?.error || 'Erreur inconnue'}</p>
                            <button class="btn btn-secondary" style="margin-top: 20px;" onclick="closeGenerateModal()">Fermer</button>
                        </div>
                    `;
                }
            } catch (error) {
                clearInterval(progressInterval);
                body.innerHTML = `
                    <div style="text-align: center; padding: 20px;">
                        <h3 style="color: var(--accent-short); margin-bottom: 16px;">Erreur</h3>
                        <p style="color: var(--text-secondary);">Erreur de connexion</p>
                        <button class="btn btn-secondary" style="margin-top: 20px;" onclick="closeGenerateModal()">Fermer</button>
                    </div>
                `;
            }
        }
        
        function displayGeneratedShortResults(data) {
            const body = document.getElementById('generate-modal-body');
            const tickers = data.tickers;
            const stats = data.stats;
            
            let html = `
                <div class="stats-summary">
                    <div class="stat-card">
                        <div class="stat-value">${stats.total_found || '-'}</div>
                        <div class="stat-label">Actions trouvées</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value short">${stats.selected || '-'}</div>
                        <div class="stat-label">Sélectionnées</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value short">${stats.worst_perf || '-'}</div>
                        <div class="stat-label">Pire Perf</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${stats.best_perf || '-'}</div>
                        <div class="stat-label">Meilleure</div>
                    </div>
                </div>
                
                <div class="search-container">
                    <svg class="search-icon" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                    </svg>
                    <input type="text" class="search-input" id="search-short-generated" placeholder="Rechercher..." oninput="filterGeneratedShortTickers()">
                </div>
                
                <div style="max-height: 300px; overflow-y: auto;">
                    <table class="screener-table" id="generated-short-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Ticker</th>
                                <th>Secteur</th>
                                <th>Prix</th>
                                <th>Perf Year</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${tickers.map(t => `
                                <tr>
                                    <td class="rank-cell">${t.rank}</td>
                                    <td class="ticker-cell">
                                        <a href="${getTradingViewUrl(t.ticker)}" target="_blank" rel="noopener" class="ticker-link">${t.ticker}</a>
                                    </td>
                                    <td style="font-size: 11px;">${t.sector || '-'}</td>
                                    <td class="number-cell">$${t.price || '-'}</td>
                                    <td class="number-cell" style="color: var(--accent-short);">${t.perf_year}%</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
                
                <div class="confirm-buttons">
                    <button class="btn btn-secondary" onclick="closeGenerateModal()">Annuler</button>
                    <button class="btn" onclick="applyGeneratedShortPanel()" style="background: linear-gradient(135deg, #ef4444 0%, #f59e0b 100%); color: white;">
                        Appliquer au panel Short
                    </button>
                </div>
            `;
            
            body.innerHTML = html;
        }
        
        function filterGeneratedShortTickers() {
            const query = document.getElementById('search-short-generated').value.toUpperCase();
            const rows = document.querySelectorAll('#generated-short-table tbody tr');
            
            rows.forEach(row => {
                const ticker = row.querySelector('.ticker-cell')?.textContent || '';
                row.style.display = ticker.includes(query) ? '' : 'none';
            });
        }
        
        async function applyGeneratedShortPanel() {
            if (generatedShortTickers.length === 0) {
                showToast('Aucun ticker à appliquer', 'error');
                return;
            }
            
            const body = document.getElementById('generate-modal-body');
            body.innerHTML = `
                <div style="text-align: center; padding: 40px;">
                    <span class="spinner" style="width: 40px; height: 40px; border-width: 3px;"></span>
                    <p style="margin-top: 16px; color: var(--text-secondary);">Application au panel Short...</p>
                </div>
            `;
            
            const data = await api('/short/screener/apply', {
                method: 'POST',
                body: JSON.stringify({ tickers: generatedShortTickers })
            });
            
            if (data && data.success) {
                showToast(`${data.count} tickers ajoutés`);
                closeGenerateModal();
                loadShortPanel();
            } else {
                body.innerHTML = `
                    <div style="text-align: center; padding: 20px;">
                        <h3 style="color: var(--accent-short); margin-bottom: 16px;">Erreur</h3>
                        <p style="color: var(--text-secondary);">${data?.error || 'Erreur inconnue'}</p>
                        <button class="btn btn-secondary" style="margin-top: 20px;" onclick="closeGenerateModal()">Fermer</button>
                    </div>
                `;
            }
        }
        
        // =================================================================
        // SHORT - MOMENTUM CALCULATION
        // =================================================================
        
        async function loadShortLatest() {
            const data = await api('/short/history/latest');
            if (data && data.details) {
                displayShortRecommendations(data);
            }
        }
        
        async function calculateShortMomentum() {
            const btn = document.getElementById('btn-short-refresh');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Calcul en cours...';
            
            const data = await api('/short/calculate', { method: 'POST' });
            
            btn.disabled = false;
            btn.innerHTML = 'Calculer le momentum';
            
            if (data && data.success) {
                displayShortRecommendations(data);
                showToast('Recommandations Short mises à jour !');
            } else {
                showToast(data?.error || 'Erreur lors du calcul', 'error');
            }
        }
        
        function displayShortRecommendations(data) {
            const shortList = document.getElementById('short-list');
            const coverList = document.getElementById('cover-list');
            
            const recs = data.recommandations || data.details || [];
            const toShort = recs.filter(r => r.signal === 'Shorter');
            const toCover = recs.filter(r => r.signal === 'Couvrir');
            
            currentShortRecommendations = recs;
            
            // Update stats
            document.getElementById('short-stat-top').textContent = toShort.length;
            document.getElementById('short-stat-allocation').textContent = toShort.length > 0 ? 
                (100 / toShort.length).toFixed(1) + '%' : '-';
            
            // Update date
            const dateStr = data.calculation_date || data.date_calcul;
            document.getElementById('short-last-update').textContent = 'Dernière MAJ: ' + dateStr;
            
            // Afficher le toggle si on a des données
            if (recs.length > 0) {
                document.getElementById('toggle-all-short').style.display = 'flex';
            }
            
            // Render short list
            if (toShort.length > 0) {
                shortList.innerHTML = toShort.map((r, i) => `
                    <div class="stock-item clickable" onclick="openShortModal('${r.ticker}')">
                        <div class="stock-rank short">${i + 1}</div>
                        <div class="stock-info">
                            <div class="stock-ticker">
                                <a href="${getTradingViewUrl(r.ticker)}" target="_blank" rel="noopener" class="ticker-link" onclick="event.stopPropagation()">${r.ticker}</a>
                            </div>
                            <div class="stock-signal short-signal">Short</div>
                        </div>
                        <div class="stock-momentum">
                            <div class="momentum-value negative">
                                ${r.momentum >= 0 ? '+' : ''}${r.momentum.toFixed(2)}%
                            </div>
                            <div class="allocation-badge short">${r.allocation}%</div>
                        </div>
                    </div>
                `).join('');
            } else {
                shortList.innerHTML = '<div class="empty-state"><p>Aucune position Short sélectionnée</p></div>';
            }
            
            // Render cover list (top 5 visibles)
            if (toCover.length > 0) {
                coverList.innerHTML = toCover.slice(0, 5).map((r, i) => `
                    <div class="stock-item clickable" onclick="openShortModal('${r.ticker}')">
                        <div class="stock-rank">${toShort.length + i + 1}</div>
                        <div class="stock-info">
                            <div class="stock-ticker">
                                <a href="${getTradingViewUrl(r.ticker)}" target="_blank" rel="noopener" class="ticker-link" onclick="event.stopPropagation()">${r.ticker}</a>
                            </div>
                            <div class="stock-signal" style="color: var(--text-muted);">Ne pas détenir</div>
                        </div>
                        <div class="stock-momentum">
                            <div class="momentum-value ${r.momentum >= 0 ? 'positive' : 'negative'}">
                                ${r.momentum >= 0 ? '+' : ''}${r.momentum.toFixed(2)}%
                            </div>
                        </div>
                    </div>
                `).join('');
                
                if (toCover.length > 5) {
                    coverList.innerHTML += `<p style="text-align: center; color: var(--text-muted); font-size: 12px; padding: 8px;">+ ${toCover.length - 5} autres actions</p>`;
                }
            } else {
                coverList.innerHTML = '<div class="empty-state"><p>--</p></div>';
            }
            
            if (showAllShortStocks) {
                renderAllShortStocks();
            }
        }
        
        function openShortModal(ticker) {
            const rec = currentShortRecommendations.find(r => r.ticker === ticker);
            if (!rec) return;
            
            document.getElementById('modal-title').innerHTML = `
                <a href="${getTradingViewUrl(ticker)}" target="_blank" rel="noopener" style="color: white; text-decoration: none;">
                    ${ticker} ↗
                </a>
            `;
            
            const details = rec.details_mensuels || [];
            const momentumClass = rec.momentum >= 0 ? 'positive' : 'negative';
            
            let html = `
                <div class="detail-summary">
                    <div class="detail-stat">
                        <div class="detail-stat-value ${momentumClass}">${rec.momentum >= 0 ? '+' : ''}${rec.momentum.toFixed(2)}%</div>
                        <div class="detail-stat-label">Momentum 12-1</div>
                    </div>
                    <div class="detail-stat">
                        <div class="detail-stat-value">#${rec.rank}</div>
                        <div class="detail-stat-label">Classement</div>
                    </div>
                    <div class="detail-stat">
                        <div class="detail-stat-value" style="color: ${rec.signal === 'Shorter' ? 'var(--accent-short)' : 'var(--text-muted)'}">${rec.signal}</div>
                        <div class="detail-stat-label">Signal</div>
                    </div>
                    <div class="detail-stat">
                        <div class="detail-stat-value">${rec.allocation}%</div>
                        <div class="detail-stat-label">Allocation</div>
                    </div>
                </div>
            `;
            
            if (details.length > 0) {
                html += `
                    <h4 style="margin-bottom: 12px; color: var(--text-secondary);">Performance mensuelle</h4>
                    <table class="details-table">
                        <thead>
                            <tr>
                                <th>Mois</th>
                                <th>Prix</th>
                                <th>Mensuel</th>
                                <th>Cumulé</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${details.map(d => `
                                <tr>
                                    <td>${d.mois}</td>
                                    <td>$${d.prix}</td>
                                    <td class="${d.rendement_mensuel >= 0 ? 'positive' : 'negative'}">${d.rendement_mensuel >= 0 ? '+' : ''}${d.rendement_mensuel}%</td>
                                    <td class="${d.rendement_cumule >= 0 ? 'positive' : 'negative'}">${d.rendement_cumule >= 0 ? '+' : ''}${d.rendement_cumule}%</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                `;
            } else {
                html += `<p style="color: var(--text-muted); text-align: center; padding: 20px;">Détails mensuels non disponibles</p>`;
            }
            
            document.getElementById('modal-body').innerHTML = html;
            document.getElementById('modal-overlay').classList.add('active');
            document.body.style.overflow = 'hidden';
        }
        
        function toggleAllShortStocks() {
            showAllShortStocks = !showAllShortStocks;
            const card = document.getElementById('all-short-stocks-card');
            const toggleText = document.getElementById('toggle-text-short');
            
            if (showAllShortStocks) {
                card.style.display = 'block';
                toggleText.textContent = '🔼 Masquer toutes les actions';
                renderAllShortStocks();
            } else {
                card.style.display = 'none';
                toggleText.textContent = 'Voir tout le classement';
            }
        }
        
        function filterShortStocks() {
            const query = document.getElementById('search-short-ticker').value.toUpperCase();
            renderAllShortStocks(query);
        }
        
        function renderAllShortStocks(filter = '') {
            const list = document.getElementById('all-short-stocks-list');
            let filtered = currentShortRecommendations;
            
            if (filter) {
                filtered = currentShortRecommendations.filter(r => r.ticker.includes(filter));
            }
            
            document.getElementById('all-short-count').textContent = filtered.length;
            
            if (filtered.length === 0) {
                list.innerHTML = '<div class="empty-state"><p>Aucun ticker trouvé</p></div>';
                return;
            }
            
            list.innerHTML = filtered.map(r => `
                <div class="stock-item clickable" onclick="openShortModal('${r.ticker}')">
                    <div class="stock-rank ${r.signal === 'Shorter' ? 'short' : ''}">${r.rank}</div>
                    <div class="stock-info">
                        <div class="stock-ticker">
                            <a href="${getTradingViewUrl(r.ticker)}" target="_blank" rel="noopener" class="ticker-link" onclick="event.stopPropagation()">${r.ticker}</a>
                        </div>
                        <div class="stock-signal ${r.signal === 'Shorter' ? 'short-signal' : ''}">${r.signal === 'Shorter' ? 'Short' : 'Ne pas détenir'}</div>
                    </div>
                    <div class="stock-momentum">
                        <div class="momentum-value ${r.momentum >= 0 ? 'positive' : 'negative'}">
                            ${r.momentum >= 0 ? '+' : ''}${r.momentum.toFixed(2)}%
                        </div>
                        ${r.allocation > 0 ? `<div class="allocation-badge short">${r.allocation}%</div>` : ''}
                    </div>
                </div>
            `).join('');
        }
        
        // =================================================================
        // SHORT - HISTORY
        // =================================================================
        
        async function loadShortHistory() {
            const data = await api('/short/history');
            if (data && data.history.length > 0) {
                document.getElementById('short-history-list').innerHTML = data.history.map(h => {
                    const shortActions = h.details.filter(d => d.signal === 'Shorter');
                    return `
                        <div class="history-item short" onclick="showShortHistoryDetail(${h.id})">
                            <div class="history-date">${h.calculation_date}</div>
                            <div class="history-summary">
                                Short ${h.nb_top}: ${shortActions.map(a => a.ticker).join(', ')}
                            </div>
                        </div>
                    `;
                }).join('');
            } else {
                document.getElementById('short-history-list').innerHTML = '<div class="empty-state"><p>Aucun historique Short disponible</p></div>';
            }
        }
        
        async function showShortHistoryDetail(id) {
            const data = await api('/short/history/' + id);
            if (data) {
                displayShortRecommendations(data);
                showPage('short');
                document.querySelectorAll('.nav-tab')[1].click();
            }
        }
        
        // =================================================================
        // SHORT - SETTINGS
        // =================================================================
        
        async function loadShortSettings() {
            const data = await api('/short/settings');
            if (data) {
                document.getElementById('input-short-nb-top').value = data.nb_top;
                document.getElementById('input-short-date-calcul').value = data.date_calcul || '';
            }
        }
        
        async function saveShortSettings() {
            const nbTop = document.getElementById('input-short-nb-top').value;
            const dateCalcul = document.getElementById('input-short-date-calcul').value;
            
            const data = await api('/short/settings', {
                method: 'POST',
                body: JSON.stringify({
                    nb_top: parseInt(nbTop),
                    date_calcul: dateCalcul
                })
            });
            
            if (data && data.success) {
                showToast('Paramètres Short enregistrés');
            } else {
                showToast(data?.error || 'Erreur', 'error');
            }
        }
        
        // =================================================================
        // OPTIONS (PUT SPREAD CALCULATOR)
        // =================================================================
        
