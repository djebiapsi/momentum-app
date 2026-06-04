        async function calculateMomentum() {
            const btn = document.getElementById('btn-refresh');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Calcul en cours...';
            
            const data = await api('/calculate', { method: 'POST' });
            
            btn.disabled = false;
            btn.innerHTML = 'Calculer le momentum';
            
            if (data && data.success) {
                displayRecommendations(data);
                showToast('Recommandations mises à jour !');
            } else {
                showToast(data?.error || 'Erreur lors du calcul', 'error');
            }
        }
        
        async function calculateAndNotify() {
            const btn = event.currentTarget;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Calcul et envoi...';
            
            const data = await api('/calculate-and-notify', { method: 'POST' });
            
            btn.disabled = false;
            btn.innerHTML = 'Calculer et notifier';
            
            if (data && data.success) {
                displayRecommendations(data);
                if (data.email_sent) {
                    showToast('Email envoyé avec succès !');
                } else {
                    showToast('Calcul OK mais email non envoyé: ' + data.email_message, 'warning');
                }
            } else {
                showToast(data?.error || 'Erreur', 'error');
            }
        }
        
        function displayRecommendations(data) {
            const investList = document.getElementById('invest-list');
            const sellList = document.getElementById('sell-list');

            const recs = data.recommandations || data.details || [];
            const invest = recs.filter(r => r.signal === 'Investir');
            const cash   = recs.filter(r => r.signal === 'Cash');
            const sell   = recs.filter(r => r.signal === 'Sortir');

            currentRecommendations = recs;

            // Stocker pour export TWS et afficher le bouton si positions Investir
            lastRecoData = { recommendations: recs.map(r => ({ ticker: r.ticker, signal: r.signal, allocation: r.allocation })) };
            const btnExport = document.getElementById('btn-export-tws');
            if (btnExport) btnExport.style.display = invest.length > 0 ? '' : 'none';

            // --- Stats (4 cartes) ---
            document.getElementById('stat-top').textContent = invest.length;
            document.getElementById('stat-allocation').textContent = invest.length > 0
                ? invest[0].allocation + '%' : '—';

            const avgMomentum = invest.length > 0
                ? (invest.reduce((s, r) => s + r.momentum, 0) / invest.length).toFixed(1)
                : null;
            document.getElementById('stat-momentum-avg').textContent =
                avgMomentum != null ? (avgMomentum >= 0 ? '+' : '') + avgMomentum + '%' : '—';

            const vols = invest.filter(r => r.vol_annualisee != null).map(r => r.vol_annualisee);
            const avgVol = vols.length > 0
                ? (vols.reduce((s, v) => s + v, 0) / vols.length).toFixed(1)
                : null;
            document.getElementById('stat-vol-avg').textContent =
                avgVol != null ? avgVol + '%' : '—';

            // --- Bannière exposition brute (vol scaling et/ou filtre portefeuille) ---
            const expoBanner = document.getElementById('exposure-banner');
            if (expoBanner) {
                if ((data.vol_scaling || data.portfolio_filter) && data.exposition_brute != null) {
                    const brute = data.exposition_brute;
                    const levier = brute > 100;
                    let html = `<span style="color:${levier ? 'var(--accent-short)' : 'var(--text-primary,#fff)'}">${brute.toFixed(1)}%</span>`
                        + (levier ? ' <span style="font-size:11px;color:var(--accent-short);">⚡ levier</span>' : '');
                    // Frein anti-krach actif ?
                    if (data.portfolio_filter && data.portfolio_factor != null) {
                        const f = data.portfolio_factor;
                        const pvol = data.portfolio_vol != null ? data.portfolio_vol + '%' : '—';
                        if (f < 1) {
                            html += ` <span style="font-size:11px;color:var(--accent-short);" title="Vol panier ${pvol} > seuil ${data.portfolio_threshold_pct}%">🛡️ frein ×${f}</span>`;
                        } else {
                            html += ` <span style="font-size:11px;color:var(--text-secondary,#888);" title="Vol panier ${pvol} ≤ seuil ${data.portfolio_threshold_pct}%">🛡️ veille (vol ${pvol})</span>`;
                        }
                    }
                    document.getElementById('exposure-value').innerHTML = html;
                    expoBanner.style.display = 'flex';
                } else {
                    expoBanner.style.display = 'none';
                }
            }

            // Couleur momentum moyen
            const statMom = document.getElementById('stat-momentum-avg');
            statMom.style.color = avgMomentum != null
                ? (parseFloat(avgMomentum) >= 0 ? 'var(--accent-long)' : 'var(--accent-short)')
                : '';

            // Update date
            const dateStr = data.calculation_date || data.date_calcul;
            document.getElementById('last-update').textContent = 'Dernière MAJ: ' + dateStr;

            if (recs.length > 0) document.getElementById('toggle-all').style.display = 'flex';

            // --- Carte état du marché ---
            const marketCard = document.getElementById('market-card');
            const regime = data.market_regime;
            if (regime && regime.regime !== 'UNKNOWN' && regime.spy_price != null) {
                const isBear = regime.regime === 'BEAR';
                const sign = regime.pct_vs_sma200 >= 0 ? '+' : '';
                const gapColor = isBear ? 'var(--accent-short)' : 'var(--accent-long)';

                // Badge
                const badge = document.getElementById('market-regime-badge');
                badge.className = 'market-badge ' + (isBear ? 'bear' : 'bull');
                badge.textContent = isBear ? '⚠ BEAR' : '↑ BULL';

                // Métriques
                document.getElementById('market-spy-price').textContent = '$' + regime.spy_price;
                document.getElementById('market-sma200').textContent    = '$' + regime.sma200;
                const gapEl = document.getElementById('market-gap');
                gapEl.textContent = sign + regime.pct_vs_sma200 + '%';
                gapEl.style.color = gapColor;

                // Jauge : SPY vs SMA200
                // On mappe l'écart [-15%, +15%] sur [0%, 100%] (50% = neutre = SMA200)
                const pct = regime.pct_vs_sma200;
                const clampedPct = Math.max(-15, Math.min(15, pct));
                const fillPct = 50 + (clampedPct / 15) * 50;          // 0→100
                const markerLeft = 50;                                  // SMA200 toujours au centre

                const fill = document.getElementById('market-gauge-fill');
                if (isBear) {
                    // Barre de gauche (rouge) : de fillPct jusqu'au centre
                    fill.style.width  = (50 - fillPct) + '%';
                    fill.style.marginLeft = fillPct + '%';
                    fill.style.background = 'var(--accent-short)';
                } else {
                    // Barre de droite (verte) : du centre jusqu'à fillPct
                    fill.style.width      = (fillPct - 50) + '%';
                    fill.style.marginLeft = '50%';
                    fill.style.background = 'var(--accent-long)';
                }
                document.getElementById('market-gauge-marker').style.left = markerLeft + '%';

                // Explication contextuelle
                const explications = {
                    bull_strong:  `<strong>Tendance haussière confirmée</strong> — SPY nettement au-dessus de sa SMA200 (+${pct}%). Le momentum 12-1 est statistiquement plus fiable dans ce régime. Les signaux <em>Acheter</em> ont une probabilité de succès historiquement plus élevée.`,
                    bull_weak:    `<strong>Tendance haussière modérée</strong> — SPY légèrement au-dessus de sa SMA200 (+${pct}%). Signal positif, mais surveiller un éventuel retournement vers la SMA200.`,
                    bear_weak:    `<strong>Attention — marché légèrement baissier</strong> (${pct}% sous SMA200). Les drawdowns sont plus fréquents. Envisager de réduire la taille des positions.`,
                    bear_strong:  `<strong>Marché baissier — prudence maximale</strong> (${pct}% sous SMA200). Historiquement, le momentum génère plus de faux signaux en bear market et les pertes peuvent être sévères. Considérer de rester en cash sur une partie du portefeuille.`
                };
                let key;
                if (!isBear) key = pct >= 5 ? 'bull_strong' : 'bull_weak';
                else         key = pct <= -5 ? 'bear_strong' : 'bear_weak';

                document.getElementById('market-explication').innerHTML = explications[key];

                // Bordure de la carte selon régime
                marketCard.className = 'market-card o-2 ' + (isBear ? 'bear' : 'bull');
                marketCard.style.display = 'block';
            } else {
                marketCard.style.display = 'none';
            }

            // Helper : affiche la perf du mois récent (exclu du momentum 12-1)
            function perfRecentHtml(r) {
                if (r.perf_recent_1m == null) return '';
                const cls = r.perf_recent_1m >= 0 ? 'positive' : 'negative';
                const sign = r.perf_recent_1m >= 0 ? '+' : '';
                return `<div class="perf-recent ${cls}" title="Perf mois récent (exclu du momentum 12-1)">1m récent: ${sign}${r.perf_recent_1m}%</div>`;
            }

            // --- Render invest list ---
            const investAndCash = [...invest, ...cash];
            if (investAndCash.length > 0) {
                investList.innerHTML = investAndCash.map((r, i) => {
                    const isCash = r.signal === 'Cash';
                    return `
                    <div class="stock-item clickable" onclick="openModal('${r.ticker}')">
                        <div class="stock-rank ${isCash ? '' : 'top'}">${i + 1}</div>
                        <div class="stock-info">
                            <div class="stock-ticker">
                                <a href="${getTradingViewUrl(r.ticker)}" target="_blank" rel="noopener" class="ticker-link" onclick="event.stopPropagation()">${r.ticker}</a>
                            </div>
                            <div class="stock-signal ${isCash ? 'cash' : 'buy'}">${isCash ? 'Cash (momentum <0)' : 'Acheter'}</div>
                        </div>
                        <div class="stock-momentum">
                            <div class="momentum-value ${r.momentum >= 0 ? 'positive' : 'negative'}">
                                ${r.momentum >= 0 ? '+' : ''}${r.momentum.toFixed(2)}%
                            </div>
                            ${perfRecentHtml(r)}
                            ${r.vol_annualisee != null ? `<div class="perf-recent" style="opacity:0.6;" title="Volatilité annualisée du titre">vol: ${r.vol_annualisee}%${r.multiplier != null ? ` · ×${r.multiplier}` : ''}</div>` : ''}
                            ${!isCash ? `<div class="allocation-badge">${r.allocation}%</div>` : ''}
                        </div>
                    </div>`;
                }).join('');
            } else {
                investList.innerHTML = '<div class="empty-state"><p>Aucune action sélectionnée</p></div>';
            }
            animIn(investList);

            // --- Stat pop ---
            ['stat-top','stat-allocation','stat-momentum','stat-vol'].forEach(popEl);

            // --- Render sell list (top 5 visibles) ---
            if (sell.length > 0) {
                sellList.innerHTML = sell.slice(0, 5).map((r, i) => `
                    <div class="stock-item clickable" onclick="openModal('${r.ticker}')">
                        <div class="stock-rank">${investAndCash.length + i + 1}</div>
                        <div class="stock-info">
                            <div class="stock-ticker">
                                <a href="${getTradingViewUrl(r.ticker)}" target="_blank" rel="noopener" class="ticker-link" onclick="event.stopPropagation()">${r.ticker}</a>
                            </div>
                            <div class="stock-signal sell">Éviter</div>
                        </div>
                        <div class="stock-momentum">
                            <div class="momentum-value ${r.momentum >= 0 ? 'positive' : 'negative'}">
                                ${r.momentum >= 0 ? '+' : ''}${r.momentum.toFixed(2)}%
                            </div>
                            ${perfRecentHtml(r)}
                        </div>
                    </div>
                `).join('');

                if (sell.length > 5) {
                    sellList.innerHTML += `<p style="text-align: center; color: var(--text-muted); font-size: 12px; padding: 8px;">+ ${sell.length - 5} autres actions</p>`;
                }
            } else {
                sellList.innerHTML = '<div class="empty-state"><p>--</p></div>';
            }
            animIn(sellList);

            // Si le toggle est ouvert, mettre à jour la liste
            if (showAllStocks) {
                renderAllStocks();
            }
        }
        
        // =================================================================
        // RÉÉQUILIBRAGE
        // =================================================================

        let rebalanceRows = []; // [{ticker, value}]
        let lastRebalanceActions = []; // sauvegarde pour export TWS
        let lastRebalanceTotalValue = 0;

        function renderRebalancePositions() {
            const list = document.getElementById('rebalance-positions-list');
            if (rebalanceRows.length === 0) {
                list.innerHTML = '';
                return;
            }
            list.innerHTML = rebalanceRows.map((row, i) => `
                <div class="rb-position-row">
                    <span class="rb-position-ticker">${row.ticker}</span>
                    <span style="font-size:11px; color:var(--text-muted); flex:0.6;">Valeur actuelle</span>
                    <input type="number" class="rb-position-value" min="0"
                        value="${row.value || ''}" placeholder="0"
                        oninput="rebalanceRows[${i}].value = parseFloat(this.value)||0">
                    <button class="rb-remove-btn" onclick="removeRebalanceRow(${i})">✕</button>
                </div>
            `).join('');
        }

        function addRebalanceRow() {
            const tickerInput = document.getElementById('rb-ticker-input');
            const valueInput  = document.getElementById('rb-value-input');
            const ticker = tickerInput.value.trim().toUpperCase();
            const value  = parseFloat(valueInput.value) || 0;
            if (!ticker) { showToast('Entrez un ticker', 'error'); return; }
            if (rebalanceRows.find(r => r.ticker === ticker)) {
                showToast(ticker + ' déjà dans la liste', 'error'); return;
            }
            rebalanceRows.push({ ticker, value });
            tickerInput.value = '';
            valueInput.value  = '';
            tickerInput.focus();
            renderRebalancePositions();
        }

        function removeRebalanceRow(i) {
            rebalanceRows.splice(i, 1);
            renderRebalancePositions();
        }

        function fmt(n) {
            return n.toLocaleString('fr-FR', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
        }

        // Export TWS depuis les recommandations directement (sans portfolio)
        let lastRecoData = null;

        function exportTWSFromReco() {
            if (!lastRecoData || !lastRecoData.recommendations) {
                alert('Aucune recommandation disponible.');
                return;
            }
            const invest = lastRecoData.recommendations.filter(r => r.signal === 'Investir');
            if (invest.length === 0) {
                alert('Aucune position "Investir" à exporter.');
                return;
            }
            const CASH_BUFFER_PCT = 1.0; // 1% de marge par ticker gardé en cash
            const lines = invest.map(r => {
                const pct = Math.max(0, r.allocation - CASH_BUFFER_PCT);
                return `DES,${r.ticker},STK,SMART/AMEX,,,,,,${pct.toFixed(6)}`;
            });
            const csv  = lines.join('\n') + '\n';
            const blob = new Blob([csv], { type: 'text/csv' });
            const url  = URL.createObjectURL(blob);
            const el   = document.createElement('a');
            el.href    = url;
            el.download = 'tws_rebalance.csv';
            el.click();
            URL.revokeObjectURL(url);
        }

        function exportTWS() {
            if (!lastRebalanceActions || lastRebalanceActions.length === 0) {
                alert('Calculez d\'abord le rééquilibrage.');
                return;
            }
            if (!lastRebalanceTotalValue || lastRebalanceTotalValue <= 0) return;

            // Toutes les positions avec une cible > 0 (on exclut les liquidations totales)
            const CASH_BUFFER_PCT = 1.0; // 1% de marge par ticker gardé en cash
            const lines = [];
            for (const a of lastRebalanceActions) {
                if (a.target <= 0) continue;
                const pct = Math.max(0, a.target / lastRebalanceTotalValue * 100 - CASH_BUFFER_PCT);
                lines.push(`DES,${a.ticker},STK,SMART/AMEX,,,,,,${pct.toFixed(6)}`);
            }

            if (lines.length === 0) {
                alert('Aucune position cible à exporter.');
                return;
            }

            const csv = lines.join('\n') + '\n';
            const blob = new Blob([csv], { type: 'text/csv' });
            const url  = URL.createObjectURL(blob);
            const el   = document.createElement('a');
            el.href    = url;
            el.download = 'tws_rebalance.csv';
            el.click();
            URL.revokeObjectURL(url);
        }

        function importIBKR(input) {
            const file = input.files[0];
            if (!file) return;
            const status = document.getElementById('ibkr-import-status');
            status.textContent = 'Lecture…';
            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    const lines = e.target.result.split('\n');
                    let positions = [];
                    let cash = null;

                    for (const line of lines) {
                        const cols = line.split(',');

                        // Positions: "Positions ouvertes,Data,Summary,Actions,USD,TICKER,QTY,...,CLOSE,VALUE,..."
                        if (cols[0] === 'Positions ouvertes' && cols[1] === 'Data' && cols[2] === 'Summary' && cols[3] === 'Actions') {
                            const ticker = (cols[5] || '').trim();
                            const qty    = parseFloat((cols[6] || '').trim());
                            const price  = parseFloat((cols[10] || '').trim());
                            const value  = parseFloat((cols[11] || '').trim());
                            if (ticker && !isNaN(value) && value > 0) {
                                positions.push({ ticker, value, price: (!isNaN(price) && price > 0) ? price : null });
                            }
                        }

                        // Cash: "Actif net,Data,Trésorerie,VALUE,..."
                        if (cols[0] === 'Actif net' && cols[1] === 'Data' && (cols[2] || '').trim().startsWith('Trésorerie')) {
                            const v = parseFloat((cols[3] || '').trim());
                            if (!isNaN(v)) cash = v;
                        }
                    }

                    if (positions.length === 0 && cash === null) {
                        status.textContent = '❌ Aucune donnée trouvée. Vérifiez le fichier.';
                        return;
                    }

                    // Clear & repopulate
                    rebalanceRows = [];
                    for (const p of positions) {
                        if (!rebalanceRows.find(r => r.ticker === p.ticker)) {
                            rebalanceRows.push({ ticker: p.ticker, value: p.value, price: p.price });
                        }
                    }
                    renderRebalancePositions();

                    if (cash !== null) {
                        document.getElementById('rb-cash').value = cash.toFixed(2);
                    }

                    status.textContent = `✅ ${positions.length} position(s) importée(s)` + (cash !== null ? ` + cash $${cash.toFixed(2)}` : '');
                    // Reset file input so same file can be re-imported
                    input.value = '';
                } catch(err) {
                    status.textContent = '❌ Erreur de lecture: ' + err.message;
                }
            };
            reader.readAsText(file, 'UTF-8');
        }

        function calculateRebalance() {
            const invest = currentRecommendations.filter(r => r.signal === 'Investir');
            if (invest.length === 0) {
                showToast('Lancez d\'abord un calcul pour obtenir des recommandations', 'error');
                return;
            }

            // Lire les valeurs depuis les inputs (au cas où l'user a modifié sans déclencher oninput)
            document.querySelectorAll('.rb-position-value').forEach((el, i) => {
                rebalanceRows[i].value = parseFloat(el.value) || 0;
            });

            const cash = parseFloat(document.getElementById('rb-cash').value) || 0;
            const portfolioMap = {};
            for (const row of rebalanceRows) portfolioMap[row.ticker] = row.value;

            const totalValue = Object.values(portfolioMap).reduce((s, v) => s + v, 0) + cash;
            if (totalValue <= 0) {
                showToast('Entrez au moins une valeur dans votre portefeuille', 'error');
                return;
            }

            const THRESHOLD_PCT = 2; // ignorer les écarts < 2% de la cible (évite micro-trades)
            const actions = [];

            // Positions recommandées : calculer la cible et l'écart
            for (const rec of invest) {
                const target  = totalValue * rec.allocation / 100;
                const current = portfolioMap[rec.ticker] || 0;
                const diff    = target - current;
                const threshold = target * THRESHOLD_PCT / 100;
                actions.push({
                    ticker:  rec.ticker,
                    current: current,
                    target:  target,
                    diff:    diff,
                    type:    Math.abs(diff) <= threshold ? 'hold' : (diff > 0 ? 'buy' : 'sell'),
                    alloc:   rec.allocation
                });
            }

            // Positions actuelles non recommandées → vendre entièrement
            for (const [ticker, value] of Object.entries(portfolioMap)) {
                if (value > 0 && !invest.find(r => r.ticker === ticker)) {
                    actions.push({
                        ticker:  ticker,
                        current: value,
                        target:  0,
                        diff:    -value,
                        type:    'sell',
                        alloc:   0
                    });
                }
            }

            // Trier : ventes d'abord (libèrent du cash), puis achats, puis garder
            const order = { sell: 0, buy: 1, hold: 2 };
            actions.sort((a, b) => order[a.type] - order[b.type] || Math.abs(b.diff) - Math.abs(a.diff));

            // Cash résultant théorique après opérations
            const cashChange = actions.reduce((s, a) => s + (a.type === 'sell' ? -a.diff : a.type === 'buy' ? -a.diff : 0), 0);
            const cashAfter  = cash + cashChange;

            lastRebalanceActions = actions;
            lastRebalanceTotalValue = totalValue;
            renderRebalanceResults(actions, totalValue, cashAfter);
        }

        function renderRebalanceResults(actions, totalValue, cashAfter) {
            const sells = actions.filter(a => a.type === 'sell');
            const buys  = actions.filter(a => a.type === 'buy');
            const holds = actions.filter(a => a.type === 'hold');

            const labels = { buy: 'ACHETER', sell: 'VENDRE', hold: 'GARDER' };

            const rowHtml = (a) => {
                const sign = a.diff > 0 ? '+' : '';
                const amountStr = a.type === 'hold'
                    ? '$' + fmt(a.current)
                    : (a.type === 'buy' ? '+$' + fmt(a.diff) : '-$' + fmt(Math.abs(a.diff)));
                const detail = a.type === 'hold'
                    ? `Actuel $${fmt(a.current)} · Cible $${fmt(a.target)} · écart &lt; 2%`
                    : `Actuel $${fmt(a.current)} → Cible $${fmt(a.target)}${a.alloc > 0 ? ' (' + a.alloc + '%)' : ''}`;
                return `
                <div class="rb-action-row">
                    <span class="rb-action-badge ${a.type}">${labels[a.type]}</span>
                    <div class="rb-action-body">
                        <div class="rb-action-ticker">${a.ticker}</div>
                        <div class="rb-action-detail">${detail}</div>
                    </div>
                    <div class="rb-action-amount ${a.type}">${amountStr}</div>
                </div>`;
            };

            const allRows = [...sells, ...buys, ...holds].map(rowHtml).join('');

            const cashColor = cashAfter >= 0 ? 'var(--accent-long)' : 'var(--accent-short)';

            document.getElementById('rebalance-results').innerHTML = `
                <div style="border-top: 1px solid var(--border-subtle); padding-top: 4px;">
                    ${allRows}
                </div>
                <div class="rb-summary">
                    Portefeuille total : <strong>$${fmt(totalValue)}</strong><br>
                    Ventes : <strong style="color:var(--accent-short)">${sells.length} positions</strong>
                    &nbsp;·&nbsp; Achats : <strong style="color:var(--accent-long)">${buys.length} positions</strong>
                    &nbsp;·&nbsp; Inchangé : <strong>${holds.length}</strong><br>
                    Cash théorique après opérations : <strong style="color:${cashColor}">$${fmt(cashAfter)}</strong>
                </div>
                <button class="btn btn-primary btn-block" onclick="exportTWS()"
                    style="margin-top: 12px; background: var(--bg-secondary); border: 1px solid var(--border-color);
                           color: var(--text-primary);">
                    ⬇ Exporter pour TWS (BasketTrader)
                </button>`;
        }

        // =================================================================
        // PANEL
        // =================================================================

