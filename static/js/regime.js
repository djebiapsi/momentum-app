        // =================================================================
        // RÉGIME DE MARCHÉ (SPY + QQQ, 4 états) — décision du jour, KPI,
        // page Marché, réglages. Chargé après core.js (scope global partagé).
        // =================================================================

        let currentRegime = null;

        // L'état ne passe jamais par la couleur seule : icône + mot + couleur.
        const STATE_META = {
            BULL:       { icon: '↑', label: 'BULL',       rule: 'investi, levier' },
            CORRECTION: { icon: '↘', label: 'CORRECTION', rule: 'rester investi' },
            REBOUND:    { icon: '↗', label: 'REBOUND',    rule: 'rentrer' },
            BEAR:       { icon: '↓', label: 'BEAR',       rule: 'cash' },
        };
        const INDEX_NAMES = { SPY: 'S&P 500', QQQ: 'Nasdaq-100' };
        const MONTHS_FR = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

        function rgSigned(v, d = 1) {
            if (v == null || isNaN(v)) return '—';
            return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d).replace('.', ',') + ' %';
        }
        function rgPct(x) { return x == null ? '—' : Math.round(x * 100) + ' %'; }
        function rgDate(iso, withYear = false) {
            if (!iso) return '—';
            const [y, m, d] = iso.split('-').map(Number);
            return `${d} ${MONTHS_FR[m - 1]}` + (withYear ? ` ${y}` : '');
        }
        function rgMonth(iso) {
            if (!iso) return '—';
            const [y, m] = iso.split('-').map(Number);
            return `${MONTHS_FR[m - 1]} ${y}`;
        }
        function rgDaysSince(iso) {
            if (!iso) return null;
            return Math.max(0, Math.round((Date.now() - new Date(iso + 'T12:00:00').getTime()) / 86400000));
        }
        function stateChip(state, extraClass = '') {
            const m = STATE_META[state];
            if (!m) return '<span class="state-chip">—</span>';
            return `<span class="state-chip ${extraClass}" data-state="${state}"><span aria-hidden="true">${m.icon}</span> ${m.label}</span>`;
        }

        async function loadRegime() {
            const reg = await api('/regime');
            if (reg && !reg.error) {
                currentRegime = reg;
                renderDecision();
                renderMarketRegime();
                renderNavDot();
            } else {
                renderDecisionError(reg?.error);
            }
            return reg;
        }

        // ----------------------------------------------------------------
        // 1. Décision du jour (page Aujourd'hui)
        // ----------------------------------------------------------------
        function _sleeveLine(key, sl, reg) {
            const idx = reg.indexes[sl.index] || {};
            const scope = reg.settings.regime_scope;
            const follows = scope === 'sleeve' ? idx.symbol : (scope === 'either' ? 'SPY + QQQ' : 'SPY');
            return `
                <div class="sleeve" data-expo="${sl.exposure <= 0 ? 'zero' : sl.exposure < 1 ? 'cut' : sl.exposure > 1 ? 'lever' : 'full'}">
                    <div class="sleeve-head">
                        <span class="sleeve-name">Poche ${sl.label}</span>
                        <span class="sleeve-expo">${rgPct(sl.exposure)}</span>
                    </div>
                    <div class="sleeve-meta">${stateChip(sl.state, 'sm')}<span>suit ${follows}</span></div>
                </div>`;
        }

        function _actionLines(reg, reco) {
            // Ce qu'il faut faire concrètement, poche par poche.
            const lines = [];
            const sl = reg.sleeves;
            for (const [k, s] of Object.entries(sl)) {
                const idx = reg.indexes[s.index] || {};
                if (s.exposure <= 0) {
                    lines.push(`<b>Poche ${s.label} en cash</b> : ${idx.symbol || ''} en BEAR confirmé. Vends les positions de cette poche.`);
                } else if (s.exposure < 1) {
                    lines.push(`<b>Poche ${s.label} réduite à ${rgPct(s.exposure)}</b> : le signal quotidien du ${idx.symbol || ''} est en BEAR. Sortie totale si la clôture du ${rgDate(reg.next_decision)} confirme.`);
                }
            }
            if (reg.month_end_watch) {
                for (const v of Object.values(reg.indexes)) {
                    if (v.month_end_watch) {
                        lines.push(`<b>Fin de mois dans ${v.trading_days_to_decision} séance(s)</b> : si le mois finissait aujourd'hui, le ${v.symbol} passerait de ${v.state} à ${v.provisional_state}.`);
                    }
                }
            }
            return lines;
        }

        function renderDecision() {
            const box = document.getElementById('decision');
            const reg = currentRegime;
            if (!box || !reg) return;
            const reco = (typeof lastRecoSummary !== 'undefined') ? lastRecoSummary : null;
            box.dataset.status = reg.status;

            const titles = {
                INVESTI: 'Rester investi',
                LEVIER: 'Rester investi, avec levier',
                REDUIT: 'Réduire l\'exposition',
                CASH: 'Passer en cash',
            };
            document.getElementById('decision-kicker').textContent =
                `Régime de marché · ${rgDate(reg.as_of, true)}`;
            const expo = reco && reco.exposure != null && !reco.stale ? reco.exposure : null;
            document.getElementById('decision-title').textContent =
                titles[reg.status] + (expo != null ? ` · ${Math.round(expo)} % investi` : '');
            const nd = reg.next_decision;
            const anyIdx = Object.values(reg.indexes).find(v => v.state);
            document.getElementById('decision-sub').innerHTML =
                `Prochaine décision mensuelle : clôture du <b>${rgDate(nd)}</b>` +
                (anyIdx ? ` (${anyIdx.trading_days_to_decision} séance${anyIdx.trading_days_to_decision > 1 ? 's' : ''})` : '') +
                '. Contrôle automatique chaque soir après la clôture de New York.';

            document.getElementById('decision-sleeves').innerHTML =
                Object.entries(reg.sleeves).map(([k, s]) => _sleeveLine(k, s, reg)).join('');

            // Contrainte : pas de rééquilibrage IBKR sur des cibles périmées
            const btnReb = document.getElementById('btn-rebalance-main');
            const btnCalc = document.getElementById('btn-refresh');
            if (btnReb && btnCalc) {
                const stale = !!(reco && reco.stale);
                btnReb.disabled = stale || !(typeof lastRecoData !== 'undefined' && lastRecoData);
                btnReb.title = stale ? "Recalcule d'abord les cibles avec le régime" : '';
                btnReb.className = 'btn ' + (stale ? 'btn-ghost' : 'btn-primary');
                btnCalc.className = 'btn ' + (stale ? 'btn-primary' : 'btn-ghost');
            }

            const alertBox = document.getElementById('decision-alert');
            const lines = _actionLines(reg, reco);
            if (reco && reco.stale) {
                lines.unshift(`<b>Cibles à recalculer</b> : celles affichées datent du ${rgDate(reco.date, true)}, avant le régime de marché. `
                    + `Lance « Recalculer les cibles » pour appliquer les expositions ci-dessus.`);
            }
            if (lines.length) {
                alertBox.innerHTML = lines.map(l => `<p>${l}</p>`).join('');
                alertBox.hidden = false;
            } else {
                alertBox.hidden = true;
            }
            renderKpis();
        }

        function renderDecisionError(msg) {
            const box = document.getElementById('decision');
            if (!box) return;
            box.dataset.status = 'error';
            document.getElementById('decision-title').textContent = 'Régime indisponible';
            document.getElementById('decision-sub').textContent =
                (msg || 'Les cours du SPY et du QQQ n\'ont pas pu être chargés.') + ' Les cibles affichées sont celles du dernier calcul.';
        }

        function renderKpis() {
            const reg = currentRegime;
            const reco = (typeof lastRecoSummary !== 'undefined') ? lastRecoSummary : null;
            const set = (id, v) => { const el = document.getElementById(id); if (el) el.innerHTML = v; };
            if (reco) {
                const e = reco.exposure;
                set('kpi-exposure', e != null ? Math.round(e) + ' %' : '—');
                set('kpi-exposure-sub', e == null ? '' : e > 100.5 ? `levier ${(e / 100).toFixed(2).replace('.', ',')}×`
                    : e < 99.5 ? `${Math.round(100 - e)} % en cash` : 'sans levier');
                set('kpi-positions', reco.positions);
                set('kpi-positions-sub', reco.bySleeve);
            }
            if (reg) {
                const any = Object.values(reg.indexes).find(v => v.state);
                set('kpi-next', rgDate(reg.next_decision));
                set('kpi-next-sub', any ? `dans ${any.trading_days_to_decision} séance${any.trading_days_to_decision > 1 ? 's' : ''}` : '');
                // Marge = indice investi le plus proche de son pivot
                const invested = Object.values(reg.sleeves).filter(s => s.exposure > 0)
                    .map(s => reg.indexes[s.index]).filter(v => v && v.state && v.pct_vs_pivot != null);
                if (invested.length) {
                    // Baisse encaissable avant le pivot = pivot / prix − 1 (indice le plus proche)
                    const fall = v => (v.pivot / v.price - 1) * 100;
                    const near = invested.reduce((a, b) => (fall(a) >= fall(b) ? a : b));
                    const f = fall(near);
                    set('kpi-margin', f < 0 ? rgSigned(f) : 'sous le pivot');
                    set('kpi-margin-sub', `${near.symbol} · pivot ${near.pivot.toLocaleString('fr-FR', { maximumFractionDigits: 0 })} $`);
                } else {
                    set('kpi-margin', '—');
                    set('kpi-margin-sub', 'tout en cash');
                }
            }
        }

        function renderNavDot() {
            const dot = document.getElementById('nav-dot-today');
            if (!dot || !currentRegime) return;
            const needsAction = currentRegime.status === 'REDUIT' || currentRegime.status === 'CASH' || currentRegime.month_end_watch;
            dot.hidden = !needsAction;
        }

        // ----------------------------------------------------------------
        // 2. Page Marché : un panneau par indice
        // ----------------------------------------------------------------
        function _gauge(v) {
            // Repères : pivot (sortie) · seuil BULL · prix. Échelle autour des trois valeurs.
            const pts = [v.pivot, v.bull_level, v.price].filter(x => x != null);
            if (pts.length < 3) return '';
            const lo = Math.min(...pts) * 0.97, hi = Math.max(...pts) * 1.03;
            const pos = x => ((x - lo) / (hi - lo) * 100).toFixed(1) + '%';
            const fmt = x => x.toLocaleString('fr-FR', { maximumFractionDigits: 0 }) + ' $';
            return `
                <div class="gauge" role="img" aria-label="${v.symbol} à ${fmt(v.price)}, pivot de sortie ${fmt(v.pivot)}, seuil BULL ${fmt(v.bull_level)}">
                    <div class="gauge-zone bear" style="width:${pos(v.pivot)}"></div>
                    <div class="gauge-zone bull" style="left:${pos(v.bull_level)};right:0"></div>
                    <div class="gauge-mark" style="left:${pos(v.pivot)}"><span>Pivot ${fmt(v.pivot)}</span></div>
                    <div class="gauge-mark" style="left:${pos(v.bull_level)}"><span>BULL ${fmt(v.bull_level)}</span></div>
                    <div class="gauge-price" style="left:${pos(v.price)}"><span>${fmt(v.price)}</span></div>
                </div>`;
        }

        function _ribbon(history) {
            if (!history || !history.length) return '';
            const cells = history.map(h => `<span class="rb-cell" data-state="${h.state}"
                title="${rgMonth(h.month + '-01')} · ${h.state} · 12 m ${rgSigned(h.slow_ret_pct)} · 1 m ${rgSigned(h.fast_ret_pct)}">${h.state[0]}</span>`).join('');
            return `<div class="ribbon" aria-label="États mensuels des 24 derniers mois">${cells}</div>
                    <div class="ribbon-axis"><span>${rgMonth(history[0].month + '-01')}</span><span>${rgMonth(history[history.length - 1].month + '-01')}</span></div>`;
        }

        function _indexCard(v, reg) {
            if (!v.state) {
                return `<div class="index-card"><div class="index-head"><span class="index-symbol">${v.symbol}</span></div>
                        <p class="muted-sm">${v.error || 'Données indisponibles'}</p></div>`;
            }
            const sleeve = Object.values(reg.sleeves).find(s => s.index === v.key);
            const scopeNote = reg.settings.regime_scope === 'sleeve'
                ? (sleeve ? `Pilote la poche ${sleeve.label} : <b>${rgPct(sleeve.exposure)}</b>` : '')
                : `Exposition du portefeuille : <b>${rgPct(Object.values(reg.sleeves)[0].exposure)}</b>`;
            const sinceDays = rgDaysSince(v.daily_since);
            const provisional = v.provisional_state && v.provisional_state !== v.state
                ? `<div class="index-row ${v.provisional_state === 'BEAR' ? 'warn' : ''}"><span>Si le mois finissait aujourd'hui</span>${stateChip(v.provisional_state, 'sm')}</div>` : '';
            const lastExit = v.last_exit ? `Dernière sortie : ${rgMonth(v.last_exit.exit)}${v.last_exit.reentry ? ', retour ' + rgMonth(v.last_exit.reentry) : ' (en cours)'}` : 'Aucune sortie sur l\'historique chargé';
            return `
                <article class="index-card" data-state="${v.state}">
                    <div class="index-head">
                        <div>
                            <span class="index-symbol">${v.symbol}</span>
                            <span class="index-name">${INDEX_NAMES[v.symbol] || ''}</span>
                        </div>
                        <span class="index-price">${v.price.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} $</span>
                    </div>
                    <div class="index-state">
                        ${stateChip(v.state, 'lg')}
                        <span class="index-since">depuis ${rgMonth(v.state_since)} · ${v.months_in_state} mois</span>
                    </div>
                    <p class="index-scope">${scopeNote}</p>
                    <div class="index-rows">
                        <div class="index-row"><span>Momentum 12 mois</span><b class="${v.slow_ret_pct >= 0 ? 'up' : 'down'}">${rgSigned(v.slow_ret_pct)}</b></div>
                        <div class="index-row"><span>Dernier mois clos (${rgMonth(v.as_of)})</span><b class="${v.fast_ret_pct >= 0 ? 'up' : 'down'}">${rgSigned(v.fast_ret_pct)}</b></div>
                        <div class="index-row"><span>Signal quotidien</span><span class="row-right">${stateChip(v.daily_state, 'sm')}<small>${sinceDays != null ? 'depuis ' + rgDate(v.daily_since) : ''}</small></span></div>
                        ${provisional}
                        <div class="index-row"><span>Distance au pivot de sortie</span><b>${rgSigned(v.pct_vs_pivot)}</b></div>
                    </div>
                    ${_gauge(v)}
                    <div class="index-sub-h">24 derniers mois</div>
                    ${_ribbon(v.history)}
                    <p class="index-foot">${lastExit}</p>
                </article>`;
        }

        function renderMarketRegime() {
            const reg = currentRegime;
            const grid = document.getElementById('index-grid');
            if (!grid || !reg) return;
            grid.innerHTML = ['spy', 'qqq'].map(k => _indexCard(reg.indexes[k], reg)).join('');
            const asof = document.getElementById('regime-asof');
            if (asof) asof.textContent = `Clôture du ${rgDate(reg.as_of, true)} · décision mensuelle le ${rgDate(reg.next_decision)}`;
            renderJournal(reg.journal || []);
        }

        function renderJournal(items) {
            const el = document.getElementById('regime-journal');
            if (!el) return;
            if (!items.length) {
                el.innerHTML = '<div class="empty-state">Aucune bascule enregistrée pour l\'instant. Chaque changement d\'exposition apparaîtra ici, avec l\'alerte envoyée.</div>';
                return;
            }
            el.innerHTML = items.map(e => {
                const d = new Date(e.at);
                const when = d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
                const [title, ...rest] = (e.message || '').split(' — ');
                return `<div class="journal-item" data-sev="${e.severity}">
                    <span class="journal-date">${when}</span>
                    <div><div class="journal-title">${title}</div><div class="journal-body">${rest.join(' — ')}</div></div>
                </div>`;
            }).join('');
        }

        async function runRegimeCheck() {
            const btn = document.getElementById('btn-regime-check');
            if (btn) { btn.disabled = true; btn.textContent = 'Vérification…'; }
            const res = await api('/regime/check', { method: 'POST', body: JSON.stringify({ notify: true }) });
            if (btn) { btn.disabled = false; btn.textContent = 'Vérifier maintenant'; }
            if (!res) return;
            if (!res.success) { showToast(res.error || 'Vérification impossible', 'error'); return; }
            if (res.initialized) showToast('Suivi du régime initialisé');
            else if (!res.events.length) showToast('Aucun changement d\'exposition');
            else showToast(`${res.events.length} changement(s) détecté(s) : alertes envoyées`, 'warning');
            await loadRegime();
            if (res.events.length) loadLatest();
        }

        // ----------------------------------------------------------------
        // 3. Réglages du régime (page Config)
        // ----------------------------------------------------------------
        async function loadRegimeSettings() {
            const s = await api('/regime/settings');
            if (!s) return;
            const scope = document.getElementById('input-regime-scope');
            const mode = document.getElementById('input-regime-mode');
            const bull = document.getElementById('input-regime-bull');
            const alerts = document.getElementById('input-regime-alerts');
            if (scope) scope.value = s.regime_scope;
            if (mode) mode.value = s.regime_mode;
            if (bull) bull.value = s.regime_bull_exposure;
            if (alerts) alerts.checked = !!s.regime_alerts;
        }

        async function saveRegimeSettings() {
            const body = {
                regime_scope: document.getElementById('input-regime-scope').value,
                regime_mode: document.getElementById('input-regime-mode').value,
                regime_bull_exposure: parseFloat(document.getElementById('input-regime-bull').value),
                regime_alerts: document.getElementById('input-regime-alerts').checked,
            };
            const res = await api('/regime/settings', { method: 'POST', body: JSON.stringify(body) });
            if (res && res.success) {
                showToast('Régime enregistré. Recalcule les cibles pour l\'appliquer.');
                currentRegime = null;
            } else {
                showToast(res?.error || 'Erreur', 'error');
            }
        }
