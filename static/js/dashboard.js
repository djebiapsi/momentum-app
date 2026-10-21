        async function calculateMomentum() {
            const btn = document.getElementById('btn-refresh');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Calcul en cours...';
            
            const data = await api('/calculate', { method: 'POST' });
            
            btn.disabled = false;
            btn.innerHTML = 'Recalculer les cibles';
            
            if (data && data.success) {
                displayRecommendations(data);
                showToast('Cibles mises à jour');
            } else {
                showToast(data?.error || 'Erreur lors du calcul', 'error');
            }
        }
        
        async function calculateAndNotify() {
            const btn = event.currentTarget;
            const label = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Calcul et envoi...';
            
            const data = await api('/calculate-and-notify', { method: 'POST' });
            
            btn.disabled = false;
            btn.innerHTML = label;
            
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
        
        // Score qualité (indicatif) à côté de chaque action momentum — non bloquant
        async function _fillMomentumQuality(tickers) {
            if (!tickers || !tickers.length) return;
            try {
                const d = await api('/qv/quality-scores?market=us&tickers=' + encodeURIComponent(tickers.join(',')));
                if (!d || !d.scores) return;
                Object.keys(d.scores).forEach(t => {
                    const el = document.getElementById('qvq-' + t);
                    if (!el) return;
                    const q = d.scores[t];
                    if (q == null) { el.textContent = ''; return; }
                    el.className = 'qv-q-badge ' + (q >= 66 ? 'good' : (q >= 33 ? 'mid' : 'low'));
                    el.title = 'Score qualité ' + Math.round(q) + '/100 (indicatif · Quality-Value)';
                    el.textContent = 'Q' + Math.round(q);
                });
            } catch (e) { /* indicatif : silencieux */ }
        }

        // Résumé du dernier calcul, partagé avec regime.js (KPI de la décision)
        let lastRecoSummary = null;

        const SLEEVE_LABELS = { nasdaq: 'Nasdaq', other: 'S&P 500' };

        function _sleeveBadge(r) {
            if (!r.sleeve) return '';
            const m = r.regime_mult;
            const tag = m != null && m !== 1 ? ` · ×${String(m).replace('.', ',')}` : '';
            return `<span class="sleeve-badge" data-sleeve="${r.sleeve}">${SLEEVE_LABELS[r.sleeve] || r.sleeve}${tag}</span>`;
        }

        function dateStrFor(data) { return data.calculation_date || data.date_calcul || null; }

        function displayRecommendations(data) {
            const investList = document.getElementById('invest-list');
            const sellList = document.getElementById('sell-list');

            const recs = data.recommandations || data.details || [];
            const invest = recs.filter(r => r.signal === 'Investir');
            const cash   = recs.filter(r => r.signal === 'Cash');
            // Titres écartés par le régime : leur poche est à 0 % (BEAR)
            const lastInvestRank = Math.max(0, ...invest.map(r => r.rank || 0), ...cash.map(r => r.rank || 0));
            const blocked = recs.filter(r => r.regime_blocked
                || (r.regime_mult === 0 && r.signal === 'Sortir' && (r.rank || 999) < Math.max(lastInvestRank, (data.nb_top || 5) + 1)));
            const blockedSet = new Set(blocked.map(r => r.ticker));
            const sell = recs.filter(r => r.signal === 'Sortir' && !blockedSet.has(r.ticker));

            currentRecommendations = recs;

            // Export TWS / IBKR : cibles du dernier calcul
            lastRecoData = { recommendations: recs.map(r => ({ ticker: r.ticker, signal: r.signal, allocation: r.allocation })) };
            const btnExport = document.getElementById('btn-export-tws');
            if (btnExport) btnExport.style.display = invest.length > 0 ? '' : 'none';
            const btnMain = document.getElementById('btn-rebalance-main');
            if (btnMain) btnMain.disabled = recs.length === 0;

            // Résumé (KPI + décision)
            const exposure = invest.reduce((s, r) => s + (r.allocation || 0), 0);
            const bySleeve = {};
            invest.forEach(r => { if (r.sleeve) bySleeve[r.sleeve] = (bySleeve[r.sleeve] || 0) + 1; });
            lastRecoSummary = {
                // Cibles calculées avant le régime (aucune poche renseignée) : à recalculer
                stale: invest.length > 0 && !invest.some(r => r.sleeve),
                date: dateStrFor(data),
                exposure: recs.length ? exposure : null,
                positions: invest.length,
                bySleeve: Object.entries(bySleeve).map(([k, n]) => `${n} ${SLEEVE_LABELS[k] || k}`).join(' · '),
            };
            const total = document.getElementById('target-total');
            if (total) {
                total.textContent = !recs.length ? '—'
                    : exposure <= 0.5 ? '100 % cash'
                    : `${Math.round(exposure)} % investi` + (exposure > 100.5 ? ' (levier)' : exposure < 99.5 ? ` · ${Math.round(100 - exposure)} % cash` : '');
            }

            const dateStr = data.calculation_date || data.date_calcul;
            const upd = document.getElementById('last-update');
            if (upd) upd.textContent = dateStr ? `Cibles calculées le ${rgDate(dateStr, true)}` : '—';

            if (recs.length > 0) document.getElementById('toggle-all').style.display = 'flex';

            // Perf du mois récent (exclu du momentum 12-1)
            function perfRecentHtml(r) {
                if (r.perf_recent_1m == null) return '';
                const cls = r.perf_recent_1m >= 0 ? 'positive' : 'negative';
                const sign = r.perf_recent_1m >= 0 ? '+' : '';
                return `<div class="perf-recent ${cls}" title="Perf du dernier mois (exclu du momentum 12-1)">1 m : ${sign}${r.perf_recent_1m}%</div>`;
            }

            // --- Portefeuille cible ---
            const investAndCash = [...invest, ...cash];
            const maxAlloc = Math.max(1, ...invest.map(r => r.allocation || 0));
            if (investAndCash.length > 0) {
                investList.innerHTML = investAndCash.map(r => {
                    const isCash = r.signal === 'Cash';
                    const barPct = isCash ? 0 : Math.round(((r.allocation || 0) / maxAlloc) * 100);
                    return `
                    <div class="stock-item clickable" onclick="openModal('${r.ticker}')">
                        <div class="stock-rank ${isCash ? '' : 'top'}" title="Rang dans le classement momentum">${r.rank ?? ''}</div>
                        <div class="stock-info">
                            <div class="stock-ticker">
                                <a href="${getTradingViewUrl(r.ticker)}" target="_blank" rel="noopener" class="ticker-link" onclick="event.stopPropagation()">${r.ticker}</a>
                                ${_sleeveBadge(r)}
                                <span class="qv-q-badge" id="qvq-${r.ticker}" title="Score qualité (indicatif · Quality-Value)"></span>
                            </div>
                            <div class="stock-signal ${isCash ? 'cash' : 'buy'}">${isCash ? 'Cash : momentum négatif' : 'Détenir'}</div>
                            ${!isCash ? `<div class="alloc-bar" title="Part du portefeuille : ${r.allocation}%"><span style="width:${barPct}%"></span></div>` : ''}
                        </div>
                        <div class="stock-momentum">
                            <div class="momentum-value ${r.momentum >= 0 ? 'positive' : 'negative'}">
                                ${r.momentum >= 0 ? '+' : ''}${r.momentum.toFixed(1)}%
                            </div>
                            ${perfRecentHtml(r)}
                            ${!isCash ? `<div class="allocation-badge">${r.allocation}%</div>` : ''}
                        </div>
                    </div>`;
                }).join('');
                _fillMomentumQuality(invest.map(r => r.ticker));
            } else if (recs.length > 0) {
                investList.innerHTML = `<div class="empty-state cash-state">
                    <p class="empty-title">100 % cash</p>
                    <p>Les deux marchés sont en BEAR : aucune action à détenir. Le retour se fera dès que le dernier mois d'un des indices redevient positif.</p></div>`;
            } else {
                investList.innerHTML = '<div class="empty-state"><p class="empty-title">Pas encore de cibles</p><p>Lance « Recalculer les cibles ».</p></div>';
            }
            animIn(investList);

            // --- Écartés par le régime ---
            const blockedBox = document.getElementById('blocked-list');
            if (blockedBox) {
                if (blocked.length) {
                    const groups = {};
                    blocked.forEach(r => { (groups[r.sleeve || 'other'] = groups[r.sleeve || 'other'] || []).push(r.ticker); });
                    blockedBox.innerHTML = Object.entries(groups).map(([k, t]) =>
                        `<p><b>Écartés par le régime</b> (poche ${SLEEVE_LABELS[k] || k} en BEAR) : ${t.join(', ')}</p>`).join('');
                    blockedBox.hidden = false;
                } else {
                    blockedBox.hidden = true;
                }
            }

            // --- Juste après dans le classement (5) ---
            if (sell.length > 0) {
                sellList.innerHTML = sell.slice(0, 5).map(r => `
                    <div class="stock-item clickable" onclick="openModal('${r.ticker}')">
                        <div class="stock-rank">${r.rank ?? ''}</div>
                        <div class="stock-info">
                            <div class="stock-ticker">
                                <a href="${getTradingViewUrl(r.ticker)}" target="_blank" rel="noopener" class="ticker-link" onclick="event.stopPropagation()">${r.ticker}</a>
                                ${_sleeveBadge({ ...r, regime_mult: null })}
                            </div>
                        </div>
                        <div class="stock-momentum">
                            <div class="momentum-value ${r.momentum >= 0 ? 'positive' : 'negative'}">
                                ${r.momentum >= 0 ? '+' : ''}${r.momentum.toFixed(1)}%
                            </div>
                            ${perfRecentHtml(r)}
                        </div>
                    </div>
                `).join('');
            } else {
                sellList.innerHTML = '<div class="empty-state"><p>—</p></div>';
            }
            animIn(sellList);

            if (showAllStocks) renderAllStocks();
            if (typeof renderDecision === 'function') renderDecision();
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

        async function exportTWSFromReco() {
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
            const investTickers = new Set(invest.map(r => r.ticker));
            const lines = invest.map(r => {
                const pct = Math.max(0, r.allocation - CASH_BUFFER_PCT);
                return `DES,${r.ticker},STK,SMART/AMEX,,,,,,${pct.toFixed(6)}`;
            });

            // Récupérer le portefeuille IBKR pour inclure les liquidations (positions à 0%)
            try {
                const data = await api('/ibkr/positions');
                if (data && data.success && data.positions && data.positions.length > 0) {
                    let liquidations = 0;
                    for (const p of data.positions) {
                        const ticker = (p.ticker || '').toUpperCase().trim();
                        if (ticker && !investTickers.has(ticker)) {
                            lines.push(`DES,${ticker},STK,SMART/AMEX,,,,,,0.000000`);
                            liquidations++;
                        }
                    }
                    if (liquidations > 0)
                        showToast(`${invest.length} position(s) cible + ${liquidations} liquidation(s) incluse(s)`);
                } else {
                    showToast('IBKR non connecté — liquidations non incluses', 'warning');
                }
            } catch (e) {
                showToast('IBKR non connecté — liquidations non incluses', 'warning');
            }

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

            // Positions cibles > 0 ET positions actuelles à ramener à 0 (liquidations)
            // const CASH_BUFFER_PCT = 1.0; // 1% de marge par ticker gardé en cash
            const CASH_BUFFER_PCT = 0.0;
            const lines = [];
            for (const a of lastRebalanceActions) {
                if (a.target <= 0 && a.current <= 0) continue;
                const pct = a.target <= 0
                    ? 0
                    : Math.max(0, a.target / lastRebalanceTotalValue * 100 - CASH_BUFFER_PCT);
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
