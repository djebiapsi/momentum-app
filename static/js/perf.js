        // =================================================================
        // PERFORMANCE (compte IBKR, relevés Flex)
        // Ordre de lecture : combien vaut le compte et combien il a gagné face
        // au S&P → avec quel risque → ce que je détiens → d'où vient le P&L.
        // =================================================================

        function fmtPct(v, d = 2) { return v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(d) + '%'; }
        function fmtUsd(v) {
            if (v == null) return '—';
            return (v >= 0 ? '+$' : '-$') + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
        }
        function fmtDollar(v) {
            if (v == null) return '—';
            return '$' + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
        }
        function _frDate(iso, withYear = true) {
            if (!iso) return '—';
            const d = new Date(iso.length <= 10 ? iso + 'T12:00:00' : iso);
            return d.toLocaleDateString('fr-FR', withYear ? { day: 'numeric', month: 'short', year: 'numeric' } : { day: 'numeric', month: 'short' });
        }
        function _signClass(v) { return v == null ? '' : v >= 0 ? 'pos' : 'neg'; }

        let perfRange = '1Y';
        let _perfData = null;
        let _pnlData = null;
        let _pnlMode = 'open';
        const _perfCharts = {};
        const RANGE_LABELS = { '1W': '1 semaine', '1M': '1 mois', '3M': '3 mois', '6M': '6 mois', 'YTD': 'depuis le 1er janvier', '1Y': '1 an', '3Y': '3 ans', '5Y': '5 ans', 'ALL': 'tout l\'historique' };

        // Couleurs des graphiques tirées du thème (lisibles en clair et en sombre)
        function _chartTheme() {
            const cs = getComputedStyle(document.documentElement);
            const v = n => cs.getPropertyValue(n).trim();
            return { text: v('--text-muted') || '#888', grid: v('--border-subtle') || 'rgba(128,128,128,.15)',
                     main: v('--accent-blue') || '#5b63f0', bench: v('--text-muted') || '#888',
                     neg: v('--accent-short') || '#e23857', pos: v('--accent-long') || '#0aa06a' };
        }
        // Compatibilité avec les graphiques secondaires
        let PERF_GRID = 'rgba(128,128,128,.15)';

        function _destroyChart(key) { if (_perfCharts[key]) { _perfCharts[key].destroy(); delete _perfCharts[key]; } }

        function _initPerfPills() {
            document.querySelectorAll('#perf-range .perf-pill').forEach(pill => {
                if (pill._bound) return;
                pill._bound = true;
                pill.addEventListener('click', () => {
                    document.querySelectorAll('#perf-range .perf-pill').forEach(p => p.classList.remove('active'));
                    pill.classList.add('active');
                    perfRange = pill.dataset.range;
                    loadPerfData();
                });
            });
        }

        function perfChartView(view) {
            document.querySelectorAll('[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
            document.getElementById('box-growth').hidden = view !== 'growth';
            document.getElementById('box-dd').hidden = view !== 'dd';
        }

        async function syncFlexFromPerf() {
            const btn = document.getElementById('btn-perf-sync');
            if (btn) { btn.disabled = true; btn.textContent = 'Synchronisation…'; }
            const data = await api('/flex/sync', { method: 'POST' });
            if (btn) { btn.disabled = false; btn.textContent = 'Synchroniser Flex'; }
            if (!data) return;
            if (data.success) {
                const i = data.imported || {};
                showToast(`Flex synchronisé : ${i.transactions ?? 0} transactions, ${i.snapshots ?? 0} nouveaux jours`);
                loadPerfData();
            } else {
                showToast(data.error || 'Synchronisation Flex impossible', 'error');
            }
        }

        function _renderSources(data) {
            const fs = data.flex_stats || {};
            const parts = [];
            parts.push(`NAV Flex jusqu'au <b>${_frDate(fs.last_snapshot_date)}</b>`);
            if (data.positions_source === 'ibkr') parts.push('positions <b>IBKR en direct</b>');
            else if (data.positions_source === 'flex') parts.push(`positions du relevé Flex du <b>${_frDate(data.positions_as_of)}</b> (IBKR déconnecté)`);
            else parts.push('<span class="neg">positions indisponibles</span> (IBKR déconnecté, aucun relevé Flex)');
            if (fs.last_sync) parts.push(`dernière synchro ${_frDate(fs.last_sync.slice(0, 10), false)}`);
            document.getElementById('perf-date-info').innerHTML = parts.join(' · ');
        }

        async function loadPerfData() {
            _initPerfPills();
            const info = document.getElementById('perf-date-info');
            info.textContent = 'Chargement…';

            const data = await api(`/perf/dashboard?range=${perfRange}`);
            if (!data || !data.success) {
                info.textContent = data?.error || 'Données de performance indisponibles';
                return;
            }
            if (data.empty) {
                info.textContent = data.message || 'Données insuffisantes';
                document.getElementById('perf-sentence').textContent = 'Pas assez de relevés sur cette période.';
                _loadPositionsPnl();
                return;
            }
            _perfData = data;
            const k = data.kpis;
            _renderSources(data);

            const paper = document.getElementById('perf-paper-banner');
            if (data.is_paper) {
                paper.innerHTML = `<b>Données d'un compte PAPER</b> (${data.flex_account_id}) : la Flex Query pointe sur le compte de simulation. Crée-la sur le compte LIVE puis resynchronise.`;
                paper.hidden = false;
            } else {
                paper.hidden = true;
            }

            // 1. Résumé
            document.getElementById('perf-total-value').textContent =
                k.total_value != null ? '$' + k.total_value.toLocaleString('en-US', { maximumFractionDigits: 0 }) : '—';
            const per = RANGE_LABELS[perfRange] || perfRange;
            let sentence = `Sur ${per} : <b class="${_signClass(k.total_return_pct)}">${fmtPct(k.total_return_pct, 1)}</b>`;
            if (k.bench_total_pct != null) {
                sentence += ` contre <b>${fmtPct(k.bench_total_pct, 1)}</b> pour le S&amp;P 500`;
                if (k.excess_total_pct != null) {
                    sentence += k.excess_total_pct >= 0
                        ? `, soit <b class="pos">${fmtPct(k.excess_total_pct, 1)}</b> d'avance.`
                        : `, soit <b class="neg">${fmtPct(k.excess_total_pct, 1)}</b> de retard.`;
                }
            } else {
                sentence += '.';
            }
            document.getElementById('perf-sentence').innerHTML = sentence;
            const chips = [];
            if (data.period) chips.push(`du ${_frDate(data.period.start)} au ${_frDate(data.period.end)}`);
            if (k.max_drawdown_pct != null) chips.push(`pire perte ${fmtPct(k.max_drawdown_pct, 1)}`);
            document.getElementById('perf-hero-chips').innerHTML = chips.map(c => `<span class="chip">${c}</span>`).join('');

            // 2. Chiffres clés
            const set = (id, html, cls) => { const el = document.getElementById(id); if (el) { el.innerHTML = html; el.className = cls || ''; } };
            const annual = data.period?.annualized;
            set('k-total', fmtPct(k.total_return_pct), _signClass(k.total_return_pct));
            set('k-bench', fmtPct(k.bench_total_pct));
            set('k-cagr', annual ? fmtPct(k.cagr_pct) + (k.cagr_vs_bench_pct != null ? ` <small>(${fmtPct(k.cagr_vs_bench_pct, 1)} vs S&amp;P)</small>` : '')
                : '<small>à partir de 3 mois</small>', annual ? _signClass(k.cagr_pct) : 'muted');
            set('k-div', k.dividends_total != null ? fmtDollar(k.dividends_total) : '—');
            set('k-dd', fmtPct(k.max_drawdown_pct), 'neg');
            set('k-vol', k.vol_annual_pct != null ? k.vol_annual_pct.toFixed(1) + ' %' : '—');
            set('k-sharpe', k.sharpe != null ? k.sharpe.toFixed(2) : '<small>à partir de 3 mois</small>', k.sharpe != null ? '' : 'muted');
            set('k-conc', k.top5_concentration_pct != null ? k.top5_concentration_pct + ' %' : '—');
            set('k-pos', k.positions_count != null ? `${k.positions_count}${k.winners_count != null ? ` <small>· ${k.winners_count} en gain</small>` : ''}` : '—');
            set('k-cash', k.cash != null ? fmtDollar(k.cash) + (k.cash_pct != null ? ` <small>· ${k.cash_pct} %</small>` : '') : '—', k.cash != null && k.cash < 0 ? 'neg' : '');
            set('k-unreal', fmtUsd(k.unrealized_pnl), _signClass(k.unrealized_pnl));
            set('k-real', fmtUsd(k.realized_pnl), _signClass(k.realized_pnl));

            // 3. Graphiques
            _renderEvolution(data);
            // 4. Positions détenues
            _renderPositionsTable(data.positions || [], data);
            // 6. Rendements par période
            const useDaily = ['1W', '1M'].includes(perfRange);
            const useWeekly = ['3M', '6M', 'YTD'].includes(perfRange);
            document.getElementById('perf-heatmap-title').textContent =
                'Rendements ' + (useDaily ? 'quotidiens' : useWeekly ? 'hebdomadaires' : 'mensuels');
            if (useDaily) _renderHeatmapDaily(data.daily_returns || []);
            else if (useWeekly) _renderHeatmapWeekly(data.weekly_returns || []);
            else _renderHeatmap(data.monthly_returns || []);
            // 7. Détails
            _renderCashFlows(data.cash_flows_monthly || [], data.cash_flows || []);
            _loadPositionsTimeline();
            _loadPositionsPnl();
            document.getElementById('perf-last-update-v2').textContent = 'Mis à jour à ' + new Date().toLocaleTimeString('fr-FR');
        }

        function _renderEvolution(data) {
            const th = _chartTheme();
            PERF_GRID = th.grid;
            const unit = _timeUnit(perfRange);
            const portPts = (data.timeseries?.portfolio || []).map(p => ({ x: p.date, y: p.value }));
            const benchPts = (data.timeseries?.benchmark || []).map(p => ({ x: p.date, y: p.value }));
            const axes = (yCb) => ({
                x: { type: 'time', time: { unit }, grid: { display: false }, ticks: { color: th.text, maxTicksLimit: 6 } },
                y: { grid: { color: th.grid }, border: { display: false }, ticks: { color: th.text, callback: yCb } },
            });
            _destroyChart('evolution');
            _perfCharts.evolution = new Chart(document.getElementById('chart-evolution'), {
                type: 'line',
                data: { datasets: [
                    { label: 'Mon compte', data: portPts, borderColor: th.main, backgroundColor: th.main + '14',
                      fill: true, borderWidth: 2, tension: .25, pointRadius: 0 },
                    { label: 'S&P 500', data: benchPts, borderColor: th.bench, borderDash: [3, 3],
                      borderWidth: 1.5, tension: .25, pointRadius: 0, fill: false },
                ]},
                options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
                    plugins: { legend: { labels: { color: th.text, boxWidth: 12 } },
                        tooltip: { callbacks: { label: c => ` ${c.dataset.label} : ${(c.parsed.y - 100) >= 0 ? '+' : ''}${(c.parsed.y - 100).toFixed(1)} %` } } },
                    scales: axes(v => v.toFixed(0)) }
            });
            const ddPts = (data.drawdown || []).map(p => ({ x: p.date, y: p.value }));
            _destroyChart('ddv2');
            _perfCharts.ddv2 = new Chart(document.getElementById('chart-drawdown-v2'), {
                type: 'line',
                data: { datasets: [{ label: 'Drawdown', data: ddPts, borderColor: th.neg,
                    backgroundColor: th.neg + '22', fill: true, borderWidth: 1.5, tension: .25, pointRadius: 0 }] },
                options: { responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => ` ${c.parsed.y.toFixed(1)} %` } } },
                    scales: { ...axes(v => v + ' %'), y: { max: 0, grid: { color: th.grid }, border: { display: false }, ticks: { color: th.text, callback: v => v + ' %' } } } }
            });
        }

        // --- Positions détenues ------------------------------------------
        function _renderPositionsTable(positions, data) {
            const body = document.getElementById('perf-positions-body');
            const empty = document.getElementById('perf-positions-empty');
            const src = document.getElementById('positions-source');
            if (!body) return;
            if (src) src.textContent = data.positions_source === 'ibkr' ? 'IBKR en direct'
                : data.positions_source === 'flex' ? `relevé Flex du ${_frDate(data.positions_as_of)}` : 'source indisponible';
            if (!positions.length) {
                body.innerHTML = '';
                empty.textContent = data.positions_source ? 'Aucune position : le compte est en cash.'
                    : 'Positions indisponibles : IBKR est déconnecté et aucun relevé Flex ne contient les positions ouvertes. Ajoute la section « Open Positions » à ta Flex Query puis synchronise.';
                empty.hidden = false;
                return;
            }
            empty.hidden = true;
            const alloc = p => p.allocation_pct_nav ?? 0;
            const sorted = [...positions].sort((a, b) => alloc(b) - alloc(a));
            const maxAlloc = Math.max(1, ...sorted.map(alloc));
            body.innerHTML = sorted.map(p => {
                const price = p.market_price ?? (p.market_value && p.qty ? p.market_value / p.qty : null);
                const costTotal = p.avg_cost != null && p.qty ? p.avg_cost * p.qty : null;
                const ret = costTotal && p.unrealized_pnl != null ? p.unrealized_pnl / costTotal * 100 : null;
                return `<tr>
                    <td class="tk">${p.ticker}</td>
                    <td class="num muted">${p.qty != null ? (+p.qty).toLocaleString('fr-FR', { maximumFractionDigits: 2 }) : '—'}</td>
                    <td class="num muted">${p.avg_cost != null ? '$' + p.avg_cost.toFixed(2) : '—'}</td>
                    <td class="num">${price != null ? '$' + price.toFixed(2) : '—'}</td>
                    <td class="num">${fmtDollar(p.market_value)}</td>
                    <td class="col-alloc"><div class="mini-bar"><span style="width:${(alloc(p) / maxAlloc * 100).toFixed(0)}%"></span></div><small>${alloc(p).toFixed(1)} %</small></td>
                    <td class="num ${_signClass(p.unrealized_pnl)}">${fmtUsd(p.unrealized_pnl)}</td>
                    <td class="num ${_signClass(ret)}">${ret != null ? fmtPct(ret, 1) : '—'}</td>
                </tr>`;
            }).join('');
        }

        // --- Impact par position (cycle de vie complet, réconcilié) -------
        const RECON_LABELS = {
            missing_exit: 'soldée chez le courtier mais une vente manque dans l\'historique : P&L incomplet',
            missing_entry: 'détenue sans achat dans l\'historique : coût repris du courtier',
            qty_mismatch: 'quantité corrigée sur celle du courtier (division d\'action probable)',
        };

        function pnlFilter(mode) {
            _pnlMode = mode;
            document.querySelectorAll('[data-pnl]').forEach(b => b.classList.toggle('active', b.dataset.pnl === mode));
            _renderPnlRows();
        }

        async function _loadPositionsPnl() {
            const body = document.getElementById('perf-pnl-lifecycle-body');
            const emptyEl = document.getElementById('pnl-lifecycle-empty');
            const summaryEl = document.getElementById('pnl-lifecycle-summary');
            if (!body) return;
            const data = await api('/perf/positions-pnl');
            if (!data || !data.success || !(data.positions || []).length) {
                _pnlData = null;
                body.innerHTML = '';
                summaryEl.innerHTML = '';
                emptyEl.textContent = data?.error || data?.message || 'Aucune position à afficher. Synchronise Flex.';
                emptyEl.hidden = false;
                return;
            }
            emptyEl.hidden = true;
            _pnlData = data;
            const t = data.totals || {};
            document.getElementById('pnl-count-open').textContent = t.open_count ?? '';
            document.getElementById('pnl-count-closed').textContent = t.closed_count ?? '';
            const item = (lbl, val, cls) => `<div><span>${lbl}</span><b class="${cls || ''}">${val}</b></div>`;
            summaryEl.innerHTML =
                item('P&L total', fmtUsd(t.total_pnl), _signClass(t.total_pnl))
                + item('Réalisé', fmtUsd(t.realized_pnl), _signClass(t.realized_pnl))
                + item('Latent', fmtUsd(t.unrealized_pnl), _signClass(t.unrealized_pnl))
                + item('Dividendes nets', fmtDollar(t.dividends));

            const rec = data.reconciliation || {};
            const reconEl = document.getElementById('pnl-recon');
            if (rec.fixes && rec.fixes.length) {
                const srcTxt = rec.source === 'ibkr' ? 'les positions IBKR en direct' : `le relevé Flex du ${_frDate(rec.as_of)}`;
                reconEl.innerHTML = `<b>${rec.fixes.length} écart(s) corrigé(s)</b> en confrontant l'historique à ${srcTxt} : `
                    + rec.fixes.map(f => `<b>${f.ticker}</b> (${RECON_LABELS[f.fix] || f.fix})`).join(' · ');
                reconEl.hidden = false;
            } else if (!rec.source) {
                reconEl.innerHTML = 'Statuts reconstruits depuis l\'historique seul : IBKR est déconnecté et aucun relevé Flex ne contient les positions ouvertes. Une position peut apparaître ouverte à tort.';
                reconEl.hidden = false;
            } else {
                reconEl.hidden = true;
            }
            _renderPnlRows();
        }

        function _renderPnlRows() {
            const body = document.getElementById('perf-pnl-lifecycle-body');
            if (!body || !_pnlData) return;
            const dur = d => d == null ? '—' : d >= 365 ? (d / 365).toFixed(1).replace('.', ',') + ' an' + (d >= 730 ? 's' : '') : d + ' j';
            const rows = _pnlData.positions.filter(p => _pnlMode === 'all' || p.status === _pnlMode);
            if (!rows.length) {
                body.innerHTML = `<tr><td colspan="10" class="muted center">Aucune position ${_pnlMode === 'open' ? 'ouverte' : 'soldée'}.</td></tr>`;
                return;
            }
            body.innerHTML = rows.map(p => {
                const isOpen = p.status === 'open';
                const periode = `${_frDate(p.open_date)} → ${isOpen ? '<span class="pos">en cours</span>' : _frDate(p.close_date)}`;
                const flags = [];
                if (p.reconciled) flags.push(`<span class="flag" title="${RECON_LABELS[p.reconciled] || ''}">corrigée</span>`);
                if (p.unknown_cost && !p.reconciled) flags.push('<span class="flag" title="Achat antérieur au début de l\'historique : coût inconnu">coût inconnu</span>');
                return `<tr class="${isOpen ? '' : 'row-closed'}">
                    <td class="tk">${p.ticker} ${flags.join(' ')}</td>
                    <td class="muted">${periode}</td>
                    <td class="num muted">${dur(p.holding_days)}</td>
                    <td class="num muted">${p.buys} / ${p.sells}${p.profit_takings ? ` <small title="ventes partielles">(${p.profit_takings} partielle${p.profit_takings > 1 ? 's' : ''})</small>` : ''}</td>
                    <td class="num">${fmtDollar(p.invested)}</td>
                    <td class="num ${_signClass(p.realized_pnl)}">${p.realized_pnl ? fmtUsd(p.realized_pnl) : '—'}</td>
                    <td class="num ${_signClass(p.unrealized_pnl)}">${isOpen ? fmtUsd(p.unrealized_pnl) : '—'}</td>
                    <td class="num muted">${p.dividends ? fmtDollar(p.dividends) : '—'}</td>
                    <td class="num strong ${_signClass(p.total_pnl)}">${fmtUsd(p.total_pnl)}${p.incomplete ? ' <small title="Une vente manque dans l\'historique">*</small>' : ''}</td>
                    <td class="num ${_signClass(p.perf_pct)}">${p.perf_pct == null ? '—' : fmtPct(p.perf_pct, 1)}</td>
                </tr>`;
            }).join('');
        }

        // --- Évolution par position (aires empilées) ---------------------
        const _POS_PALETTE = ['#7C5CFF','#378ADD','#1D9E75','#E0A030','#D85A30',
                              '#534AB7','#3BBFA3','#E87040','#9B59B6','#2ECC71',
                              '#E74C3C','#F39C12','#1ABC9C'];

        async function _loadPositionsTimeline() {
            const emptyEl   = document.getElementById('positions-timeline-empty');
            const contentEl = document.getElementById('positions-timeline-content');
            const subtitle  = document.getElementById('positions-timeline-subtitle');

            const data = await api(`/perf/positions-timeline?range=${perfRange}`);
            if (!data || !data.success || data.empty || !(data.series || []).length) {
                _destroyChart('postimeline');
                if (emptyEl)   { emptyEl.style.display = ''; emptyEl.textContent = data?.message || 'Aucune position à afficher sur cette période.'; }
                if (contentEl) contentEl.style.display = 'none';
                return;
            }
            if (emptyEl)   emptyEl.style.display   = 'none';
            if (contentEl) contentEl.style.display = '';
            if (subtitle) {
                const src = data.anchor === 'ibkr' ? 'ancrée sur IBKR' : data.anchor === 'flex' ? 'ancrée sur le relevé Flex' : 'reconstruite depuis les transactions';
                subtitle.textContent = `valeur de marché ${src}`;
            }

            const datasets = data.series.map((s, i) => {
                const col = _POS_PALETTE[i % _POS_PALETTE.length];
                const isOthers = s.ticker.startsWith('Autres');
                const c = isOthers ? '150,150,150' : null;
                return {
                    label: s.ticker + (s.held_now ? '' : ' (soldée)'),
                    data: s.values.map((v, j) => ({ x: data.dates[j], y: v })),
                    borderColor: isOthers ? 'rgba(150,150,150,0.9)' : col,
                    backgroundColor: isOthers ? 'rgba(150,150,150,0.35)' : col + '55',
                    borderWidth: 1, tension: 0.25, pointRadius: 0, fill: true,
                };
            });

            const unit = _timeUnit(perfRange);
            _destroyChart('postimeline');
            _perfCharts.postimeline = new Chart(document.getElementById('chart-positions-timeline'), {
                type: 'line',
                data: { datasets },
                options: { responsive: true, maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { labels: { color: _chartTheme().text, boxWidth: 12, font: { family: 'IBM Plex Mono', size: 11 } } },
                        tooltip: {
                            itemSort: (a, b) => b.parsed.y - a.parsed.y,
                            filter: it => it.parsed.y > 0.5,
                            callbacks: {
                                label: c => ` ${c.dataset.label}: $${c.parsed.y.toLocaleString('en-US', {maximumFractionDigits:0})}`,
                                footer: items => ' Total: $' + items.reduce((s, it) => s + it.parsed.y, 0)
                                    .toLocaleString('en-US', {maximumFractionDigits:0}),
                            },
                        },
                    },
                    scales: {
                        x: { type: 'time', time: { unit }, stacked: true,
                             grid: { color: PERF_GRID }, ticks: { color: _chartTheme().text, maxTicksLimit: 7 } },
                        y: { stacked: true, grid: { color: PERF_GRID },
                             ticks: { color: _chartTheme().text, callback: v => '$' + (v / 1000).toFixed(0) + 'k' },
                             title: { display: true, text: 'Valeur de marché', color: _chartTheme().text, font: { size: 9 } } },
                    },
                },
            });
        }

        // --- Heatmap mensuelle -------------------------------------------
        function _renderHeatmap(monthly) {
            const cont = document.getElementById('container-heatmap');
            if (!monthly.length) { cont.innerHTML = '<div class="empty-state">Données insuffisantes</div>'; return; }
            const MONTHS = ['J','F','M','A','M','J','J','A','S','O','N','D'];
            const byYear = {};
            let maxAbs = 0;
            monthly.forEach(m => {
                byYear[m.year] = byYear[m.year] || {};
                byYear[m.year][m.month] = m.return_pct;
                maxAbs = Math.max(maxAbs, Math.abs(m.return_pct));
            });
            const years = Object.keys(byYear).sort();
            const cell = v => {
                if (v == null) return `<td style="background:var(--bg-secondary);border-radius:3px;"></td>`;
                const op = Math.max(0.15, Math.min(0.85, Math.abs(v) / (maxAbs || 1)));
                const col = v >= 0 ? `rgba(29,158,117,${op})` : `rgba(216,90,48,${op})`;
                return `<td title="${v>=0?'+':''}${v.toFixed(2)}%" style="background:${col};border-radius:3px;text-align:center;font-size:9px;color:#fff;padding:3px;">${v>=0?'+':''}${v.toFixed(0)}</td>`;
            };
            let html = '<table style="width:100%;border-collapse:separate;border-spacing:2px;font-size:10px;"><thead><tr><th></th>'
                + MONTHS.map(m=>`<th style="color:var(--text-muted);font-weight:400;">${m}</th>`).join('') + '</tr></thead><tbody>';
            years.forEach(y => {
                const annual = Object.values(byYear[y]).reduce((acc,v)=>acc*(1+v/100),1)-1;
                html += `<tr><td style="color:var(--text-muted);padding-right:6px;white-space:nowrap;">${y}</td>`
                    + Array.from({length:12},(_,i)=>cell(byYear[y][i+1])).join('')
                    + `<td style="padding-left:6px;font-size:9px;${annual>=0?'color:var(--accent-long)':'color:var(--accent-short)'};">${annual>=0?'+':''}${(annual*100).toFixed(0)}%</td></tr>`;
            });
            html += '</tbody></table>';
            cont.innerHTML = html;
        }

        // --- Heatmap hebdomadaire ----------------------------------------
        function _renderHeatmapWeekly(weekly) {
            const cont = document.getElementById('container-heatmap');
            if (!weekly.length) { cont.innerHTML = '<div class="empty-state">Données insuffisantes</div>'; return; }
            // Organiser par année+mois pour l'affichage
            const byYearMonth = {};
            let maxAbs = 0;
            weekly.forEach(w => {
                const key = `${w.year}-${String(w.week).padStart(2,'0')}`;
                byYearMonth[key] = w;
                maxAbs = Math.max(maxAbs, Math.abs(w.return_pct));
            });
            const keys = Object.keys(byYearMonth).sort();
            const cell = w => {
                const v = w.return_pct;
                const op = Math.max(0.15, Math.min(0.85, Math.abs(v) / (maxAbs || 1)));
                const col = v >= 0 ? `rgba(29,158,117,${op})` : `rgba(216,90,48,${op})`;
                return `<div title="S${w.week} ${w.year} · ${v>=0?'+':''}${v.toFixed(2)}%" style="background:${col};border-radius:3px;display:inline-flex;align-items:center;justify-content:center;width:36px;height:28px;font-size:9px;color:#fff;margin:1px;">${v>=0?'+':''}${v.toFixed(0)}</div>`;
            };
            // Grouper par mois (4-5 semaines par ligne)
            const byMonth = {};
            keys.forEach(k => {
                const w = byYearMonth[k];
                const mo = w.date ? w.date.slice(0,7) : k.slice(0,4)+'-??';
                (byMonth[mo] = byMonth[mo] || []).push(w);
            });
            let html = '<div style="font-size:10px;overflow-y:auto;max-height:100%;">';
            Object.entries(byMonth).sort(([a],[b])=>a.localeCompare(b)).forEach(([mo, weeks]) => {
                html += `<div style="display:flex;align-items:center;gap:4px;margin-bottom:2px;">
                    <span style="color:var(--text-muted);width:52px;flex-shrink:0;">${mo.slice(5,7)}/${mo.slice(0,4)}</span>
                    <div style="display:flex;flex-wrap:wrap;gap:0;">${weeks.map(cell).join('')}</div></div>`;
            });
            html += '</div>';
            cont.innerHTML = html;
        }

        function _timeUnit(range) {
            if (['1W','1M'].includes(range)) return 'day';
            if (['3M','6M','YTD','1Y'].includes(range)) return 'month';
            return 'year';
        }

        // --- Heatmap journalière (1W / 1M) --------------------------------
        function _renderHeatmapDaily(daily) {
            const cont = document.getElementById('container-heatmap');
            if (!daily.length) { cont.innerHTML = '<div class="empty-state">Données insuffisantes</div>'; return; }
            const DAYS = ['L','M','M','J','V'];  // lundi→vendredi
            // Organiser par semaine ISO
            const byWeek = {};
            let maxAbs = 0;
            daily.forEach(d => {
                const dt = new Date(d.date);
                const dow = dt.getDay(); // 0=dim
                if (dow === 0 || dow === 6) return; // ignorer week-end
                const monday = new Date(dt);
                monday.setDate(dt.getDate() - (dow - 1));
                const wk = monday.toISOString().slice(0, 10);
                if (!byWeek[wk]) byWeek[wk] = {};
                byWeek[wk][dow - 1] = d; // 0=lundi, 4=vendredi
                maxAbs = Math.max(maxAbs, Math.abs(d.return_pct));
            });
            const weeks = Object.keys(byWeek).sort();
            const cell = d => {
                if (!d) return `<td style="background:var(--bg-secondary);border-radius:3px;width:36px;height:28px;"></td>`;
                const v = d.return_pct;
                const op = Math.max(0.15, Math.min(0.9, Math.abs(v) / (maxAbs || 1)));
                const col = v >= 0 ? `rgba(29,158,117,${op})` : `rgba(216,90,48,${op})`;
                const day = new Date(d.date).toLocaleDateString('fr-FR', {day:'numeric',month:'short'});
                return `<td title="${day} · ${v>=0?'+':''}${v.toFixed(2)}%" style="background:${col};border-radius:3px;text-align:center;font-size:9px;color:#fff;padding:3px 2px;white-space:nowrap;">${v>=0?'+':''}${v.toFixed(1)}</td>`;
            };
            let html = '<table style="width:100%;border-collapse:separate;border-spacing:3px;font-size:10px;"><thead><tr>'
                + `<th style="color:var(--text-muted);font-weight:400;width:52px;"></th>`
                + DAYS.map(d=>`<th style="color:var(--text-muted);font-weight:400;text-align:center;">${d}</th>`).join('')
                + '</tr></thead><tbody>';
            weeks.forEach(wk => {
                const wkDate = new Date(wk);
                const label = wkDate.toLocaleDateString('fr-FR', {day:'numeric',month:'short'});
                html += `<tr><td style="color:var(--text-muted);padding-right:6px;font-size:9px;white-space:nowrap;">${label}</td>`
                    + Array.from({length:5}, (_,i) => cell(byWeek[wk][i])).join('')
                    + '</tr>';
            });
            html += '</tbody></table>';
            cont.innerHTML = html;
        }

        // --- Graphique flux de capitaux ------------------------------------
        function _renderCashFlows(monthly, events) {
            const emptyEl   = document.getElementById('cashflows-empty');
            const contentEl = document.getElementById('cashflows-content');
            const eventsEl  = document.getElementById('cashflows-events');

            if (!monthly.length) {
                if (emptyEl)   emptyEl.style.display   = '';
                if (contentEl) contentEl.style.display = 'none';
                return;
            }
            if (emptyEl)   emptyEl.style.display   = 'none';
            if (contentEl) contentEl.style.display = '';

            // ── Chart : barres individuelles + ligne cumulative ──────────
            const labels = monthly.map(m => m.month);
            const values = monthly.map(m => m.amount);
            const cumul  = values.reduce((acc, v, i) => {
                acc.push((acc[i - 1] || 0) + v);
                return acc;
            }, []);

            _destroyChart('cashflows');
            _perfCharts.cashflows = new Chart(document.getElementById('chart-cashflows'), {
                type: 'bar',
                data: { labels, datasets: [
                    {
                        type: 'bar',
                        label: 'Flux mensuel',
                        data: values,
                        backgroundColor: values.map(v => v >= 0 ? 'rgba(29,158,117,0.65)' : 'rgba(216,90,48,0.65)'),
                        borderRadius: 3,
                        yAxisID: 'y',
                    },
                    {
                        type: 'line',
                        label: 'Capital investi cumulé',
                        data: cumul,
                        borderColor: '#7C5CFF',
                        backgroundColor: 'rgba(124,92,255,0.08)',
                        fill: true,
                        borderWidth: 2,
                        tension: 0.3,
                        pointRadius: 3,
                        yAxisID: 'y2',
                    },
                ]},
                options: { responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: _chartTheme().text, boxWidth: 12 } },
                        tooltip: { callbacks: { label: c =>
                            ` ${c.dataset.label}: ${c.parsed.y >= 0 ? '+' : ''}$${Math.abs(c.parsed.y).toLocaleString()}`
                        }} },
                    scales: {
                        x:  { grid: { color: PERF_GRID }, ticks: { color: _chartTheme().text } },
                        y:  { grid: { color: PERF_GRID }, ticks: { color: _chartTheme().text,
                              callback: v => (v >= 0 ? '+' : '') + '$' + Math.abs(v / 1000).toFixed(0) + 'k' },
                              title: { display: true, text: 'Flux mensuel', color: _chartTheme().text, font: { size: 9 } } },
                        y2: { position: 'right', grid: { drawOnChartArea: false },
                              ticks: { color: '#7C5CFF',
                                callback: v => '$' + (v / 1000).toFixed(0) + 'k' },
                              title: { display: true, text: 'Cumul investi', color: '#7C5CFF', font: { size: 9 } } },
                    }
                }
            });

            // ── Liste des événements individuels ────────────────────────
            if (eventsEl && events && events.length) {
                const sorted = [...events].sort((a, b) => b.date.localeCompare(a.date));
                eventsEl.innerHTML = `
                    <div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;
                                letter-spacing:0.6px;margin-bottom:8px;">Détail des mouvements</div>
                    <div style="display:flex;flex-direction:column;gap:4px;">
                    ${sorted.map(e => {
                        const sign = e.amount >= 0 ? '+' : '';
                        const col  = e.amount >= 0 ? 'var(--accent-long)' : 'var(--accent-short)';
                        const desc = (e.description || '').slice(0, 60);
                        return `<div style="display:flex;align-items:center;gap:12px;padding:5px 8px;
                                            background:var(--bg-secondary);border-radius:6px;font-size:12px;">
                            <span style="color:var(--text-muted);white-space:nowrap;font-family:'IBM Plex Mono';">${e.date}</span>
                            <span style="font-weight:700;color:${col};font-family:'IBM Plex Mono';">${sign}$${Math.abs(e.amount).toLocaleString('en-US',{minimumFractionDigits:0})}</span>
                            <span style="color:var(--text-muted);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${desc}</span>
                        </div>`;
                    }).join('')}
                    </div>`;
            } else if (eventsEl) {
                eventsEl.innerHTML = '';
            }
        }

