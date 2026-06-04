        async function calculatePutSpread() {
            const spot = parseFloat(document.getElementById('opt-spot').value);
            const iv = parseFloat(document.getElementById('opt-iv').value) || 30;
            const dte = parseInt(document.getElementById('opt-dte').value) || 45;
            const strikeLong = parseFloat(document.getElementById('opt-strike-long').value) || null;
            const strikeShort = parseFloat(document.getElementById('opt-strike-short').value) || null;
            
            if (!spot || spot <= 0) {
                showToast('Entrez un prix valide', 'error');
                return;
            }
            
            const payload = {
                spot_price: spot,
                iv: iv,
                dte: dte
            };
            
            if (strikeLong) payload.strike_long = strikeLong;
            if (strikeShort) payload.strike_short = strikeShort;
            
            const data = await api('/options/quick-calc', {
                method: 'POST',
                body: JSON.stringify(payload)
            });
            
            if (data && data.success) {
                displayOptionsResult(data.result);
            } else {
                showToast(data?.error || 'Erreur de calcul', 'error');
            }
        }
        
        function displayOptionsResult(result) {
            document.getElementById('options-result').style.display = 'block';
            
            // Type
            document.getElementById('opt-result-type').textContent = result.type || 'PUT SPREAD';
            
            // Strikes et prix
            document.getElementById('res-strike-long').textContent = '$' + result.strike_long;
            document.getElementById('res-strike-short').textContent = '$' + result.strike_short;
            document.getElementById('res-price-long').textContent = '$' + result.price_long;
            document.getElementById('res-price-short').textContent = '$' + result.price_short;
            document.getElementById('res-delta-long').textContent = result.delta_long || result.delta_long_actual || '-';
            document.getElementById('res-delta-short').textContent = result.delta_short || result.delta_short_actual || '-';
            
            // Métriques principales
            document.getElementById('res-net-debit').textContent = '$' + result.net_debit;
            document.getElementById('res-max-profit').textContent = '$' + result.max_profit;
            
            const rr = result.risk_reward || result.risk_reward_ratio || 0;
            document.getElementById('res-risk-reward').textContent = rr.toFixed(1) + ':1';
            
            // Détails
            document.getElementById('res-breakeven').textContent = '$' + result.breakeven;
            document.getElementById('res-max-loss').textContent = '$' + result.max_loss;
            document.getElementById('res-expiration').textContent = result.expiration_date || '-';
            document.getElementById('res-dte').textContent = result.dte || '-';
        }
        
        async function loadSavedOptionRecommendations() {
            const container = document.getElementById('options-recommendations-list');
            container.innerHTML = '<div class="card"><div class="empty-state">Chargement...</div></div>';
            
            const data = await api('/options/saved');
            
            if (!data || !data.success) {
                container.innerHTML = `
                    <div class="card">
                        <div class="empty-state" style="color: var(--accent-short);">
                            ${data?.error || 'Erreur lors du chargement'}
                        </div>
                    </div>`;
                return;
            }
            
            if (!data.recommendations || data.recommendations.length === 0) {
                container.innerHTML = `
                    <div class="card">
                        <div class="empty-state">
                            Aucune recommandation sauvegardée.<br>
                            <span style="font-size: 12px;">Cliquez sur "Générer nouvelles recommandations".</span>
                        </div>
                    </div>`;
                return;
            }
            
            displayOptionRecommendations(data);
        }
        
        async function generateNewOptionRecommendations() {
            const container = document.getElementById('options-recommendations-list');
            container.innerHTML = '<div class="card"><div class="empty-state">Génération en cours... (peut prendre quelques secondes)</div></div>';
            
            const data = await api('/options/bulk-recommendations');
            
            if (!data || !data.success) {
                container.innerHTML = `
                    <div class="card">
                        <div class="empty-state" style="color: var(--accent-short);">
                            ${data?.error || 'Erreur lors du chargement'}
                        </div>
                    </div>`;
                return;
            }
            
            if (!data.recommendations || data.recommendations.length === 0) {
                container.innerHTML = `
                    <div class="card">
                        <div class="empty-state">
                            Aucune recommandation disponible.<br>
                            <span style="font-size: 12px;">Lancez d'abord un calcul Short.</span>
                        </div>
                    </div>`;
                return;
            }
            
            displayOptionRecommendations(data);
            showToast(`${data.recommendations.length} recommandations générées et sauvegardées`);
        }
        
        function displayOptionRecommendations(data) {
            const container = document.getElementById('options-recommendations-list');
            
            let header = '';
            if (data.calculation_date) {
                header = `
                    <div class="info-box" style="margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center;">
                        <span><strong>${data.recommendations.length}</strong> recommandations</span>
                        <span style="color: var(--text-muted);">Calculé le ${data.calculation_date}</span>
                    </div>
                `;
            }
            
            let html = header + data.recommendations.map(rec => `
                <div class="card" style="margin-bottom: 12px;">
                    <div class="card-header">
                        <span class="card-title" style="font-size: 16px;">${rec.ticker}</span>
                        <span class="status-badge ${rec.all_conditions_met ? 'success' : 'warning'}">
                            ${rec.signal}
                        </span>
                    </div>
                    
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px;">
                        <div style="font-size: 13px;">
                            <strong>Momentum</strong>: ${rec.momentum_score}%<br>
                            <span style="color: var(--text-muted);">Perf 63-5j: ${rec.perf_63_5}%</span><br>
                            <span style="color: var(--text-muted);">Perf 5j: ${rec.perf_5_0}%</span>
                        </div>
                        <div style="font-size: 13px;">
                            <strong>Prix</strong>: $${rec.spot_price}<br>
                            <span style="color: var(--text-muted);">IV: ${rec.iv_pct}%</span>
                        </div>
                    </div>
                    
                    <div class="info-box" style="margin-bottom: 12px;">
                        <strong style="color: var(--accent-short);">PUT SPREAD recommandé</strong><br>
                        Strike Long: $${rec.put_spread.strike_long} (Δ ${rec.put_spread.delta_long_actual})<br>
                        Strike Short: $${rec.put_spread.strike_short} (Δ ${rec.put_spread.delta_short_actual})<br>
                        Prime: <strong>$${rec.put_spread.net_debit}</strong> | 
                        Profit max: <strong style="color: var(--accent-long);">$${rec.put_spread.max_profit}</strong> |
                        R/R: ${rec.put_spread.risk_reward_ratio}:1
                    </div>
                    
                    <div style="display: flex; gap: 8px;">
                        <button class="btn btn-secondary" style="flex: 1;" onclick="prefillCalculator('${rec.ticker}', ${rec.spot_price}, ${rec.iv_pct}, ${rec.put_spread.strike_long}, ${rec.put_spread.strike_short})">
                            Éditer dans calculateur
                        </button>
                    </div>
                </div>
            `).join('');
            
            container.innerHTML = html;
        }
        
        function prefillCalculator(ticker, spot, iv, strikeLong, strikeShort) {
            document.getElementById('opt-spot').value = spot;
            document.getElementById('opt-iv').value = iv;
            document.getElementById('opt-strike-long').value = strikeLong;
            document.getElementById('opt-strike-short').value = strikeShort;
            
            // Scroll vers le calculateur
            document.querySelector('#page-options').scrollTo({ top: 0, behavior: 'smooth' });
            
            // Calculer automatiquement
            calculatePutSpread();
            
            showToast(`${ticker} chargé dans le calculateur`);
        }
        
        // =================================================================
        // INIT
        // =================================================================
        
        document.addEventListener('DOMContentLoaded', async () => {
            // Vérifier l'authentification d'abord
            await checkAuth();
            
            // Charger les données Long
            loadLatest();
            
            // Charger les données Short
            loadShortLatest();
            
            // Allow enter key on ticker input
            document.getElementById('input-ticker').addEventListener('keypress', (e) => {
                if (e.key === 'Enter') addTicker();
            });
            
            // Allow enter key on short ticker input
            document.getElementById('input-short-ticker')?.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') addShortTicker();
            });
        });
        
        // Register service worker for PWA
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/static/sw.js');
        }

