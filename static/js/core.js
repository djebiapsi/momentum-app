        // =================================================================
        // DONNÉES GLOBALES
        // =================================================================
        
        let currentRecommendations = [];  // Stocke toutes les recommandations Long
        let currentShortRecommendations = [];  // Stocke toutes les recommandations Short
        let showAllStocks = false;        // Toggle pour voir toutes les actions Long
        let showAllShortStocks = false;   // Toggle pour voir toutes les actions Short
        let isAdmin = false;              // Mode admin actif
        let adminToken = localStorage.getItem('adminToken') || '';  // Token stocké
        let generatedShortTickers = [];   // Stocke les tickers Short générés
        
        // =================================================================
        // AUTHENTIFICATION
        // =================================================================
        
        async function checkAuth() {
            try {
                const response = await fetch('/api/auth/check', {
                    headers: adminToken ? { 'X-Admin-Token': adminToken } : {}
                });
                const data = await response.json();
                
                if (!data.auth_required) {
                    // Pas de mot de passe configuré - accès complet
                    isAdmin = true;
                    document.getElementById('admin-badge').style.display = 'none';
                } else {
                    isAdmin = data.is_admin;
                    updateAdminUI();
                }
            } catch (e) {
                console.error('Auth check failed:', e);
                isAdmin = false;
                updateAdminUI();
            }
        }
        
        function updateAdminUI() {
            const badge = document.getElementById('admin-badge');
            const iconSvg = document.getElementById('admin-icon-svg');
            const text = document.getElementById('admin-text');
            
            if (isAdmin) {
                badge.className = 'admin-badge logged-in';
                iconSvg.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>';
                text.textContent = 'Actif';
            } else {
                badge.className = 'admin-badge logged-out';
                iconSvg.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path>';
                text.textContent = 'Accès';
            }
            
            // Afficher/masquer les éléments admin-only
            document.querySelectorAll('.admin-only').forEach(el => {
                if (isAdmin) {
                    el.classList.remove('hidden');
                } else {
                    el.classList.add('hidden');
                }
            });
        }
        
        function toggleLoginModal() {
            if (isAdmin) {
                // Déconnexion
                if (confirm('Se déconnecter du mode admin ?')) {
                    adminToken = '';
                    localStorage.removeItem('adminToken');
                    isAdmin = false;
                    updateAdminUI();
                    showToast('Déconnecté');
                }
            } else {
                // Ouvrir le modal de connexion
                openLoginModal();
            }
        }
        
        function openLoginModal() {
            document.getElementById('login-modal-overlay').classList.add('active');
            document.getElementById('admin-password').value = '';
            document.getElementById('login-error').style.display = 'none';
            document.getElementById('admin-password').focus();
        }
        
        function closeLoginModal(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('login-modal-overlay').classList.remove('active');
        }
        
        async function doLogin() {
            const password = document.getElementById('admin-password').value;
            const errorEl = document.getElementById('login-error');
            
            if (!password) {
                errorEl.textContent = 'Entrez un mot de passe';
                errorEl.style.display = 'block';
                return;
            }
            
            try {
                const response = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    adminToken = data.token || password;
                    localStorage.setItem('adminToken', adminToken);
                    isAdmin = true;
                    updateAdminUI();
                    closeLoginModal();
                    showToast('Connecté');
                } else {
                    errorEl.textContent = data.message || 'Mot de passe incorrect';
                    errorEl.style.display = 'block';
                }
            } catch (e) {
                errorEl.textContent = 'Erreur de connexion';
                errorEl.style.display = 'block';
            }
        }
        
        // =================================================================
        // TRADINGVIEW LINK
        // =================================================================
        
        function getTradingViewUrl(ticker) {
            return `https://www.tradingview.com/symbols/${ticker}/`;
        }
        
        // =================================================================
        // MODAL
        // =================================================================
        
        function openModal(ticker) {
            const rec = currentRecommendations.find(r => r.ticker === ticker);
            if (!rec) return;
            
            document.getElementById('modal-title').innerHTML = `
                <a href="${getTradingViewUrl(ticker)}" target="_blank" rel="noopener" style="color: white; text-decoration: none;">
                    ${ticker} ↗
                </a>
            `;
            
            const details = rec.details_mensuels || [];
            const momentumClass = rec.momentum >= 0 ? 'positive' : 'negative';
            
            const signalColor = rec.signal === 'Investir' ? 'var(--accent-long)'
                              : rec.signal === 'Cash'    ? 'var(--accent-blue)'
                              : 'var(--text-muted)';
            const perfRecentStat = rec.perf_recent_1m != null ? `
                    <div class="detail-stat">
                        <div class="detail-stat-value ${rec.perf_recent_1m >= 0 ? 'positive' : 'negative'}">
                            ${rec.perf_recent_1m >= 0 ? '+' : ''}${rec.perf_recent_1m}%
                        </div>
                        <div class="detail-stat-label">Mois récent</div>
                    </div>` : '';
            const volStat = rec.vol_annualisee != null ? `
                    <div class="detail-stat">
                        <div class="detail-stat-value" style="color:var(--text-secondary)">${rec.vol_annualisee}%</div>
                        <div class="detail-stat-label">Vol annuelle</div>
                    </div>` : '';

            let html = `
                <div class="detail-summary">
                    <div class="detail-stat">
                        <div class="detail-stat-value ${momentumClass}">${rec.momentum >= 0 ? '+' : ''}${rec.momentum.toFixed(2)}%</div>
                        <div class="detail-stat-label">Momentum 12-1</div>
                    </div>
                    ${perfRecentStat}
                    ${volStat}
                    <div class="detail-stat">
                        <div class="detail-stat-value">#${rec.rank}</div>
                        <div class="detail-stat-label">Classement</div>
                    </div>
                    <div class="detail-stat">
                        <div class="detail-stat-value" style="color: ${signalColor}">${rec.signal}</div>
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
                `;
                
                details.forEach(d => {
                    const mensuelClass = d.rendement_mensuel >= 0 ? 'positive' : 'negative';
                    const cumuleClass = d.rendement_cumule >= 0 ? 'positive' : 'negative';
                    html += `
                        <tr>
                            <td>${d.mois}</td>
                            <td>$${d.prix}</td>
                            <td class="${mensuelClass}">${d.rendement_mensuel >= 0 ? '+' : ''}${d.rendement_mensuel}%</td>
                            <td class="${cumuleClass}">${d.rendement_cumule >= 0 ? '+' : ''}${d.rendement_cumule}%</td>
                        </tr>
                    `;
                });
                
                html += `
                        </tbody>
                    </table>
                `;
            } else {
                html += `<p style="color: var(--text-muted); text-align: center; padding: 20px;">Détails mensuels non disponibles pour cet historique</p>`;
            }
            
            document.getElementById('modal-body').innerHTML = html;
            document.getElementById('modal-overlay').classList.add('active');
            document.body.style.overflow = 'hidden';
        }
        
        function closeModal(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('modal-overlay').classList.remove('active');
            document.body.style.overflow = '';
        }
        
        // Fermer avec Escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeModal();
                closeGenerateModal();
            }
        });
        
        // =================================================================
        // GÉNÉRATION DE PANEL
        // =================================================================
        
        let generatedTickers = [];  // Stocke les tickers générés
        
        function openGenerateModal() {
            document.getElementById('generate-modal-overlay').classList.add('active');
            document.body.style.overflow = 'hidden';
        }
        
        function closeGenerateModal(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('generate-modal-overlay').classList.remove('active');
            document.body.style.overflow = '';
        }
        
        function confirmGeneratePanel() {
            // Afficher le modal de confirmation
            const body = document.getElementById('generate-modal-body');
            document.getElementById('generate-modal-title').textContent = 'Panel Long';
            document.getElementById('generate-modal-header').classList.add('long');
            document.getElementById('generate-modal-header').classList.remove('short');
            body.innerHTML = `
                <div style="text-align: center; padding: 20px;">
                    <h3 style="margin-bottom: 16px;">Générer panel Long</h3>
                    <p class="info-box" style="margin-bottom: 16px; text-align: left;">
                        <strong>Critères du screening via Finviz :</strong><br><br>
                        • MarketCap ≥ $10B (grandes capitalisations)<br>
                        • Avg Volume ≥ 1M actions<br>
                        • ADV ≥ $5M (Prix × Volume)<br>
                        • Score = log(MarketCap) × log(ADV)<br>
                        • Top 50 par score décroissant
                    </p>
                    <p style="color: var(--accent-long); font-size: 12px; margin-bottom: 20px;">
                        0 appel API Tiingo consommé
                    </p>
                    <div class="confirm-buttons">
                        <button class="btn btn-secondary" onclick="closeGenerateModal()">Annuler</button>
                        <button class="btn btn-primary" onclick="startGeneratePanel()">Lancer</button>
                    </div>
                </div>
            `;
            openGenerateModal();
        }
        
        async function startGeneratePanel() {
            const body = document.getElementById('generate-modal-body');
            
            // Afficher la progress bar
            body.innerHTML = `
                <div style="text-align: center; padding: 20px;">
                    <h3 style="margin-bottom: 20px;">Analyse en cours</h3>
                    <div class="progress-container">
                        <div class="progress-bar">
                            <div class="progress-fill" id="generate-progress" style="width: 0%"></div>
                        </div>
                        <p class="progress-text" id="generate-status">Initialisation...</p>
                    </div>
                    <p style="color: var(--text-muted); font-size: 12px; margin-top: 20px;">
                        Ne fermez pas cette fenêtre pendant l'analyse
                    </p>
                </div>
            `;
            
            // Simuler une progression (l'API ne retourne pas de progression en temps réel)
            let progress = 0;
            const progressInterval = setInterval(() => {
                if (progress < 90) {
                    progress += Math.random() * 5;
                    document.getElementById('generate-progress').style.width = progress + '%';
                }
            }, 1000);
            
            try {
                const data = await api('/screener/finviz/generate', { method: 'POST' });
                
                clearInterval(progressInterval);
                
                if (data && data.success) {
                    generatedTickers = data.tickers;
                    displayGeneratedResults(data);
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
                        <p style="color: var(--text-secondary);">Erreur de connexion au serveur</p>
                        <button class="btn btn-secondary" style="margin-top: 20px;" onclick="closeGenerateModal()">Fermer</button>
                    </div>
                `;
            }
        }
        
        function displayGeneratedResults(data) {
            const body = document.getElementById('generate-modal-body');
            const tickers = data.tickers;
            const stats = data.stats;
            
            let html = `
                <div class="stats-summary">
                    <div class="stat-card">
                        <div class="stat-value">${(stats.total_tickers || stats.total_tickers_us)?.toLocaleString() || '-'}</div>
                        <div class="stat-label">Tickers analysés</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${stats.above_adv_threshold || '-'}</div>
                        <div class="stat-label">ADV ≥ 5M$</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${stats.selected || '-'}</div>
                        <div class="stat-label">Sélectionnés</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value" style="color: var(--accent-blue);">${stats.api_calls_used || '1'}</div>
                        <div class="stat-label">Appel API</div>
                    </div>
                </div>
                
                <div style="text-align: center; margin-bottom: 16px; padding: 8px; background: var(--bg-secondary); border-radius: var(--radius-sm); font-size: 12px; color: var(--text-muted);">
                    Score = log(ADV) • ADV max: ${stats.max_adv || '-'} • ADV min: ${stats.min_adv || '-'}
                </div>
                
                <div class="search-container">
                    <svg class="search-icon" width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                    </svg>
                    <input type="text" class="search-input" id="search-generated" placeholder="Rechercher..." oninput="filterGeneratedTickers()">
                </div>
                
                <div style="max-height: 300px; overflow-y: auto;">
                    <table class="screener-table" id="generated-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Ticker</th>
                                <th>Prix</th>
                                <th>Volume</th>
                                <th>ADV</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${tickers.map(t => `
                                <tr onclick="openTickerDetail('${t.ticker}')" style="cursor: pointer;">
                                    <td class="rank-cell">${t.rank}</td>
                                    <td class="ticker-cell">
                                        <a href="${getTradingViewUrl(t.ticker)}" target="_blank" rel="noopener" class="ticker-link" onclick="event.stopPropagation()">${t.ticker}</a>
                                    </td>
                                    <td class="number-cell">$${t.price}</td>
                                    <td class="number-cell">${t.volume_display}</td>
                                    <td class="number-cell">${t.adv_display}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
                
                <div class="confirm-buttons">
                    <button class="btn btn-secondary" onclick="closeGenerateModal()">Annuler</button>
                    <button class="btn btn-primary" onclick="applyGeneratedPanel()">
                        Appliquer au panel
                    </button>
                </div>
            `;
            
            body.innerHTML = html;
            document.getElementById('generate-progress')?.remove();
        }
        
        function filterGeneratedTickers() {
            const query = document.getElementById('search-generated').value.toUpperCase();
            const rows = document.querySelectorAll('#generated-table tbody tr');
            
            rows.forEach(row => {
                const ticker = row.querySelector('.ticker-cell')?.textContent || '';
                row.style.display = ticker.includes(query) ? '' : 'none';
            });
        }
        
        function openTickerDetail(ticker) {
            const t = generatedTickers.find(x => x.ticker === ticker);
            if (!t) return;
            
            // Afficher les détails du ticker généré
            document.getElementById('modal-title').innerHTML = `
                <a href="${getTradingViewUrl(ticker)}" target="_blank" rel="noopener" style="color: white; text-decoration: none;">
                    ${ticker} ↗
                </a>
            `;
            
            document.getElementById('modal-body').innerHTML = `
                <div class="detail-summary">
                    <div class="detail-stat">
                        <div class="detail-stat-value">#${t.rank}</div>
                        <div class="detail-stat-label">Rang</div>
                    </div>
                    <div class="detail-stat">
                        <div class="detail-stat-value">${t.score}</div>
                        <div class="detail-stat-label">Score</div>
                    </div>
                    <div class="detail-stat">
                        <div class="detail-stat-value">$${t.price}</div>
                        <div class="detail-stat-label">Prix</div>
                    </div>
                    <div class="detail-stat">
                        <div class="detail-stat-value">${t.adv_display}</div>
                        <div class="detail-stat-label">ADV</div>
                    </div>
                </div>
                
                <h4 style="margin: 16px 0 12px; color: var(--text-secondary);">Données de marché</h4>
                <table class="details-table">
                    <tr>
                        <td>Prix actuel</td>
                        <td style="text-align: right;">$${t.price}</td>
                    </tr>
                    <tr>
                        <td>Volume du jour</td>
                        <td style="text-align: right;">${t.volume_display}</td>
                    </tr>
                    <tr>
                        <td>ADV (Average Daily $ Volume)</td>
                        <td style="text-align: right;">${t.adv_display}</td>
                    </tr>
                </table>
                
                <p style="margin-top: 16px; font-size: 12px; color: var(--text-muted); text-align: center;">
                    Score = log(ADV) = ${t.score}
                </p>
            `;
            
            document.getElementById('modal-overlay').classList.add('active');
        }
        
        async function applyGeneratedPanel() {
            if (generatedTickers.length === 0) {
                showToast('Aucun ticker à appliquer', 'error');
                return;
            }
            
            const body = document.getElementById('generate-modal-body');
            body.innerHTML = `
                <div style="text-align: center; padding: 40px;">
                    <span class="spinner" style="width: 40px; height: 40px; border-width: 3px;"></span>
                    <p style="margin-top: 16px; color: var(--text-secondary);">Application au panel...</p>
                </div>
            `;
            
            const data = await api('/screener/apply', {
                method: 'POST',
                body: JSON.stringify({ tickers: generatedTickers })
            });
            
            if (data && data.success) {
                showToast(`${data.count} tickers ajoutés`);
                closeGenerateModal();
                loadPanel();
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
        // TOGGLE & SEARCH
        // =================================================================
        
        function toggleAllStocks() {
            showAllStocks = !showAllStocks;
            const card = document.getElementById('all-stocks-card');
            const toggleText = document.getElementById('toggle-text');
            
            if (showAllStocks) {
                card.style.display = 'block';
                toggleText.textContent = '🔼 Masquer toutes les actions';
                renderAllStocks();
            } else {
                card.style.display = 'none';
                toggleText.textContent = 'Voir tout le classement';
            }
        }
        
        function filterStocks() {
            const query = document.getElementById('search-ticker').value.toUpperCase();
            renderAllStocks(query);
        }
        
        function renderAllStocks(filter = '') {
            const list = document.getElementById('all-stocks-list');
            let filtered = currentRecommendations;
            
            if (filter) {
                filtered = currentRecommendations.filter(r => r.ticker.includes(filter));
            }
            
            document.getElementById('all-count').textContent = filtered.length;
            
            if (filtered.length === 0) {
                list.innerHTML = '<div class="empty-state"><p>Aucun ticker trouvé</p></div>';
                return;
            }
            
            list.innerHTML = filtered.map(r => {
                const signalClass = r.signal === 'Investir' ? 'buy' : r.signal === 'Cash' ? 'cash' : 'sell';
                const signalLabel = r.signal === 'Investir' ? 'Acheter' : r.signal === 'Cash' ? 'Cash' : 'Éviter';
                const perfRecent = r.perf_recent_1m != null
                    ? `<div class="perf-recent ${r.perf_recent_1m >= 0 ? 'positive' : 'negative'}"
                            title="Perf mois récent (exclu du momentum)">1m: ${r.perf_recent_1m >= 0 ? '+' : ''}${r.perf_recent_1m}%</div>`
                    : '';
                return `
                <div class="stock-item clickable" onclick="openModal('${r.ticker}')">
                    <div class="stock-rank ${r.signal === 'Investir' ? 'top' : ''}">${r.rank}</div>
                    <div class="stock-info">
                        <div class="stock-ticker">
                            <a href="${getTradingViewUrl(r.ticker)}" target="_blank" rel="noopener" class="ticker-link" onclick="event.stopPropagation()">${r.ticker}</a>
                        </div>
                        <div class="stock-signal ${signalClass}">${signalLabel}</div>
                    </div>
                    <div class="stock-momentum">
                        <div class="momentum-value ${r.momentum >= 0 ? 'positive' : 'negative'}">
                            ${r.momentum >= 0 ? '+' : ''}${r.momentum.toFixed(2)}%
                        </div>
                        ${perfRecent}
                        ${r.allocation > 0 ? `<div class="allocation-badge">${r.allocation}%</div>` : ''}
                    </div>
                </div>`;
            }).join('');
        }
        
        // =================================================================
        // NAVIGATION
        // =================================================================
        
        function showPage(pageName) {
            // Hide all pages
            document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
            // Show selected page
            document.getElementById('page-' + pageName).classList.add('active');
            // Update nav
            document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
            event.currentTarget.classList.add('active');
            
            // Load data for specific pages
            if (pageName === 'dashboard') loadLatest();
            if (pageName === 'short') loadShortLatest();
            if (pageName === 'panel') loadPanel();
            if (pageName === 'history') loadHistory();
            if (pageName === 'options') loadSavedOptionRecommendations();
            if (pageName === 'settings') {
                loadSettings();
                loadShortSettings();
                loadIBKRStatus();
                loadFlexStatus();
            }
            if (pageName === 'perf') loadPerfData();
            if (pageName === 'backtest') loadBacktestDefaults();
            if (pageName === 'market') loadMarketPage();
        }
        
        // =================================================================
        // TOAST NOTIFICATIONS
        // =================================================================
        
        function showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = 'toast ' + type + ' show';
            setTimeout(() => toast.classList.remove('show'), 3000);
        }
        
        // =================================================================
        // ANIMATION HELPERS
        // =================================================================

        let _pending = 0, _progTimer = null;

        function _progStart() {
            _pending++;
            const b = document.getElementById('progress-bar');
            clearTimeout(_progTimer);
            b.style.transition = 'width 0.25s ease, opacity 0.3s ease';
            b.style.width = '40%';
            b.classList.add('running');
        }

        function _progEnd() {
            _pending = Math.max(0, _pending - 1);
            if (_pending > 0) return;
            const b = document.getElementById('progress-bar');
            b.style.width = '100%';
            _progTimer = setTimeout(() => {
                b.style.opacity = '0';
                setTimeout(() => {
                    b.classList.remove('running');
                    b.style.width = '0';
                    b.style.opacity = '';
                }, 300);
            }, 180);
        }

        function skelStocks(n = 4) {
            return Array.from({length: n}, () => `
                <div class="skel-stock">
                    <div class="skel skel-circle"></div>
                    <div style="flex:1;display:flex;flex-direction:column;gap:7px;">
                        <div class="skel skel-line" style="width:35%;"></div>
                        <div class="skel skel-line" style="width:55%;height:10px;"></div>
                    </div>
                    <div class="skel skel-line" style="width:46px;"></div>
                </div>`).join('');
        }

        function skelCards(n = 3) {
            return Array.from({length: n}, () => `
                <div style="background:var(--bg-card);border:1px solid var(--border-subtle);
                            border-radius:var(--radius-lg);padding:20px;margin-bottom:12px;">
                    <div class="skel skel-line" style="width:40%;height:14px;margin-bottom:16px;"></div>
                    <div class="skel skel-line" style="width:100%;margin-bottom:10px;"></div>
                    <div class="skel skel-line" style="width:70%;margin-bottom:10px;"></div>
                    <div class="skel skel-line" style="width:50%;"></div>
                </div>`).join('');
        }

        function animIn(container) {
            if (!container) return;
            Array.from(container.children).forEach((el, i) => {
                el.style.setProperty('--i', i);
                el.classList.add('anim-in');
            });
        }

        function popEl(id) {
            const el = document.getElementById(id);
            if (!el) return;
            el.classList.remove('stat-pop');
            void el.offsetWidth;
            el.classList.add('stat-pop');
        }

        // =================================================================
        // API CALLS
        // =================================================================

        async function api(endpoint, options = {}) {
            _progStart();
            try {
                const headers = {
                    'Content-Type': 'application/json',
                    ...options.headers
                };

                if (adminToken) {
                    headers['X-Admin-Token'] = adminToken;
                }

                const response = await fetch('/api' + endpoint, {
                    ...options,
                    headers
                });

                const data = await response.json();

                if (response.status === 401 && data.auth_required) {
                    showToast('🔒 Connexion requise', 'error');
                    openLoginModal();
                    _progEnd();
                    return null;
                }

                _progEnd();
                return data;
            } catch (error) {
                console.error('API Error:', error);
                showToast('Erreur de connexion', 'error');
                _progEnd();
                return null;
            }
        }
        
        // =================================================================
        // DASHBOARD
        // =================================================================
        
        async function loadLatest() {
            document.getElementById('invest-list').innerHTML = skelStocks(4);
            document.getElementById('sell-list').innerHTML = skelStocks(3);
            const [data, regime] = await Promise.all([
                api('/history/latest'),
                api('/market-regime')
            ]);
            const dlBtn = document.getElementById('btn-download-momentum');
            if (data && data.details) {
                if (regime) data.market_regime = regime;
                displayRecommendations(data);
                if (dlBtn) dlBtn.style.display = data.details.length ? 'block' : 'none';
            } else if (regime) {
                displayRecommendations({ recommandations: [], market_regime: regime });
                if (dlBtn) dlBtn.style.display = 'none';
            }
        }

        function downloadMomentum() {
            // Route publique (GET) — téléchargement direct du CSV
            window.location.href = '/api/history/latest/download';
        }

        // =================================================================
        // PAGE MARCHÉ — pulse, évènements, seuils, briefings
        // =================================================================

        const EVENT_LABELS = {
            VIX_HIGH: 'VIX élevé', VIX_SPIKE: 'Bond du VIX', SPY_DRAWDOWN: 'Chute S&P 500',
            PORTFOLIO_DRAWDOWN: 'Chute portefeuille', POSITION_DROP: 'Chute position',
        };

        function fmtPct(v) {
            return (typeof v === 'number') ? (v >= 0 ? '+' : '') + v.toFixed(2) + '%' : '—';
        }
        function pctColor(v) {
            if (typeof v !== 'number') return 'var(--text-secondary)';
            return v >= 0 ? '#22c55e' : '#ef4444';
        }

        async function loadMarketPage() {
            await Promise.all([loadMarketPulse(), loadMarketEvents(), loadThresholds()]);
        }

        async function loadMarketPulse() {
            const data = await api('/market/pulse');
            if (!data) return;
            const reg = (data.regime && data.regime.regime) || '—';
            const regEl = document.getElementById('pulse-regime');
            regEl.textContent = reg;
            regEl.style.color = reg === 'BULL' ? '#22c55e' : (reg === 'BEAR' ? '#ef4444' : 'var(--text-primary)');

            const vixEl = document.getElementById('pulse-vix');
            vixEl.textContent = (typeof data.vix === 'number') ? data.vix.toFixed(1) : '—';
            vixEl.style.color = (typeof data.vix === 'number' && data.vix >= 25) ? '#ef4444' : 'var(--text-primary)';

            const spyEl = document.getElementById('pulse-spy');
            spyEl.textContent = fmtPct(data.spy_intraday_pct);
            spyEl.style.color = pctColor(data.spy_intraday_pct);

            const pEl = document.getElementById('pulse-portfolio');
            pEl.textContent = fmtPct(data.portfolio_intraday_pct);
            pEl.style.color = pctColor(data.portfolio_intraday_pct);

            document.getElementById('pulse-disconnected').style.display = data.connected ? 'none' : 'block';
            document.getElementById('market-pulse-update').textContent =
                'Mise à jour : ' + new Date().toLocaleTimeString('fr-FR');
        }

        function renderEvent(e) {
            const crit = e.severity === 'critical';
            const accent = crit ? '#ef4444' : (e.is_open ? '#f97316' : '#22c55e');
            const label = EVENT_LABELS[e.event_type] || e.event_type;
            const started = e.started_at ? new Date(e.started_at).toLocaleString('fr-FR') : '—';
            const dur = (typeof e.duration_min === 'number')
                ? (e.duration_min >= 60 ? (e.duration_min / 60).toFixed(1) + ' h' : e.duration_min.toFixed(0) + ' min')
                : '—';
            const statusBadge = e.is_open
                ? '<span style="background:#450a0a;color:#f87171;padding:2px 8px;border-radius:8px;font-size:10px;">EN COURS</span>'
                : '<span style="background:#14532d;color:#4ade80;padding:2px 8px;border-radius:8px;font-size:10px;">RÉSOLU</span>';
            return `<div style="padding:12px;border-left:3px solid ${accent};background:var(--bg-secondary);
                        border-radius:8px;margin-bottom:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                    <strong>${label}${e.ticker ? ' · ' + e.ticker : ''}</strong> ${statusBadge}
                </div>
                <div style="font-size:13px;color:var(--text-secondary);">${e.message || ''}</div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:6px;">
                    ${started} · durée ${dur}${typeof e.peak_value === 'number' ? ' · pic ' + e.peak_value : ''}</div>
            </div>`;
        }

        async function loadMarketEvents() {
            const data = await api('/market/events?status=all');
            if (!data) return;
            const open = data.events.filter(e => e.is_open);
            const closed = data.events.filter(e => !e.is_open);
            document.getElementById('market-open-events').innerHTML =
                open.length ? open.map(renderEvent).join('') : '<div class="empty-state">Aucune alerte active</div>';
            document.getElementById('market-event-history').innerHTML =
                closed.length ? closed.slice(0, 30).map(renderEvent).join('') : '<div class="empty-state">Aucun évènement</div>';
        }

        const THRESHOLD_LABELS = {
            vix_warn: 'VIX — alerte', vix_crit: 'VIX — critique', vix_spike_pct: 'Bond VIX (%)',
            spy_dd_warn: 'SPY baisse — alerte (%)', spy_dd_crit: 'SPY baisse — critique (%)',
            portfolio_dd_warn: 'Portef. baisse — alerte (%)', portfolio_dd_crit: 'Portef. baisse — critique (%)',
            position_drop: 'Chute position (%)',
        };

        async function loadThresholds() {
            const data = await api('/market/thresholds');
            if (!data) return;
            const form = document.getElementById('thresholds-form');
            form.innerHTML = Object.entries(data.thresholds).map(([k, v]) => `
                <label style="font-size:12px;color:var(--text-secondary);">
                    ${THRESHOLD_LABELS[k] || k}
                    <input type="number" step="0.5" id="thr-${k}" value="${v}"
                        style="width:100%;margin-top:4px;padding:8px;border-radius:8px;
                        background:var(--bg-primary);border:1px solid var(--border-color);color:var(--text-primary);">
                </label>`).join('');
        }

        async function saveThresholds() {
            const inputs = document.querySelectorAll('[id^="thr-"]');
            const thresholds = {};
            inputs.forEach(i => { thresholds[i.id.replace('thr-', '')] = parseFloat(i.value); });
            const data = await api('/market/thresholds', {
                method: 'POST', body: JSON.stringify({ thresholds })
            });
            if (data && data.success) showToast('Seuils enregistrés', 'success');
            else if (data) showToast(data.error || 'Erreur', 'error');
        }

        async function runMonitorNow() {
            showToast('Vérification du marché...', 'info');
            const data = await api('/market/monitor/run', { method: 'POST' });
            if (!data) return;
            if (data.success) {
                showToast(`Monitor OK — ${data.opened} ouverte(s), ${data.closed} clôturée(s)`, 'success');
                loadMarketPulse(); loadMarketEvents();
            } else {
                showToast(data.error || 'Erreur monitor', 'error');
            }
        }

        async function sendBriefing(session) {
            showToast('Envoi du briefing...', 'info');
            const data = await api('/briefing/send', {
                method: 'POST', body: JSON.stringify({ session })
            });
            if (!data) return;
            showToast(data.success ? 'Briefing envoyé !' : (data.message || data.error || 'Erreur'),
                      data.success ? 'success' : 'error');
        }
        
