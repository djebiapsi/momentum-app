// =====================================================================
// BACKTEST — courbe d'équité composée + stats (quantstats côté serveur)
// =====================================================================

let btYears = 5;
const _btCharts = {};
const BT_GRID = 'rgba(255,255,255,.05)';

function _btDestroy(key) {
    if (_btCharts[key]) { _btCharts[key].destroy(); delete _btCharts[key]; }
}

function _btFmtUsd(v) {
    return '$' + Math.round(v).toLocaleString('en-US');
}
function _btFmtPct(v, dec = 1) {
    if (v == null || isNaN(v)) return '—';
    return (v >= 0 ? '+' : '') + (v * 100).toFixed(dec) + '%';
}
function _btSetKpi(id, value, deltaText, deltaPos) {
    const card = document.getElementById(id);
    if (!card) return;
    card.querySelector('.perf-kpi-value').textContent = value;
    const d = card.querySelector('.perf-kpi-delta');
    if (d && deltaText != null) {
        d.textContent = deltaText;
        d.style.color = deltaPos == null ? 'var(--text-muted)'
            : (deltaPos ? 'var(--accent-long)' : 'var(--accent-short)');
    }
}

// Branche les pills de période (une fois)
function _btInitPills() {
    document.querySelectorAll('#bt-years .perf-pill').forEach(pill => {
        if (pill._bound) return;
        pill._bound = true;
        pill.addEventListener('click', () => {
            document.querySelectorAll('#bt-years .perf-pill').forEach(p => p.classList.remove('active'));
            pill.classList.add('active');
            btYears = parseInt(pill.dataset.years, 10);
        });
    });
}

async function loadBacktestDefaults() {
    _btInitPills();
    const d = await api('/backtest/defaults');
    if (!d) return;
    document.getElementById('bt-capital').value = d.capital;
    btYears = d.years;
    document.querySelectorAll('#bt-years .perf-pill').forEach(p => {
        p.classList.toggle('active', parseInt(p.dataset.years, 10) === d.years);
    });
    // Pré-remplissage des hypothèses de réalisme
    const a = d.assumptions || {};
    const setVal = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
    setVal('bt-tx-cost', a.tx_cost_bps);
    setVal('bt-margin-rate', a.margin_rate_pct);
    setVal('bt-cash-yield', a.cash_yield_pct);
    const mc = document.getElementById('bt-margin-call');
    if (mc) mc.checked = a.margin_call_enabled !== false;

    const c = d.config || {};
    const layers = [];
    if (c.vol_scaling) layers.push(`vol scaling (cible ${c.vol_target_pct}%, max ${c.max_exposure_pct}%)`);
    if (c.portfolio_filter) layers.push(`frein anti-krach (seuil ${c.portfolio_vol_threshold_pct}%)`);
    document.getElementById('bt-live-config').innerHTML =
        `Config live appliquée : <b>top ${d.nb_top}</b>, pondération inverse-volatilité`
        + (layers.length ? ' + ' + layers.join(' + ') : '')
        + (d.tiingo_configured ? '' : ' <span style="color:var(--accent-short)">· clé Tiingo manquante</span>');
}

async function runBacktest() {
    const btn = document.getElementById('bt-run-btn');
    const statusEl = document.getElementById('bt-status');
    const results = document.getElementById('bt-results');

    btn.disabled = true;
    btn.textContent = 'Calcul en cours…';
    results.style.display = 'none';
    statusEl.style.display = 'block';
    statusEl.textContent = '⏳ Lancement du backtest…';

    const numOrNull = id => {
        const el = document.getElementById(id);
        if (!el) return null;
        return el.value === '' ? null : parseFloat(el.value);
    };
    const body = JSON.stringify({
        capital: parseFloat(document.getElementById('bt-capital').value) || 10000,
        years: btYears,
        benchmark: document.getElementById('bt-benchmark').value,
        tx_cost_bps: numOrNull('bt-tx-cost'),
        margin_rate_pct: numOrNull('bt-margin-rate'),
        cash_yield_pct: numOrNull('bt-cash-yield'),
        dca_amount: parseFloat(document.getElementById('bt-dca')?.value) || 0,
        margin_call_enabled: document.getElementById('bt-margin-call')?.checked ?? true,
    });

    // Lance en async — retourne job_id instantanément (pas de timeout gunicorn)
    const launch = await api('/backtest/run', { method: 'POST', body });
    if (!launch) { _btReset(btn, statusEl); return; }
    if (launch.error) { _btShowError(btn, statusEl, launch.error); return; }

    const jobId = launch.job_id;
    let elapsed = 0;

    const poll = setInterval(async () => {
        elapsed += 3000;
        const mins = Math.floor(elapsed / 60000);
        const secs = Math.floor((elapsed % 60000) / 1000);
        statusEl.textContent = `⏳ Backtest en cours… ${mins > 0 ? mins + 'min ' : ''}${secs}s`
            + (elapsed < 20000 ? ' · récupération des données…' : '');

        if (elapsed > 20 * 60 * 1000) {
            clearInterval(poll);
            _btShowError(btn, statusEl, 'Timeout : le backtest dépasse 20 minutes.');
            return;
        }

        const st = await api('/backtest/status/' + jobId);
        if (!st) { clearInterval(poll); _btReset(btn, statusEl); return; }
        if (st.status === 'running') return;

        clearInterval(poll);
        btn.disabled = false;
        btn.textContent = 'Lancer le backtest';
        statusEl.style.display = 'none';

        if (st.status === 'error' || st.error) {
            _btShowError(btn, statusEl, st.error || 'Erreur inconnue');
            return;
        }
        _btRender(st.result);
        results.style.display = 'block';
        showToast('Backtest terminé', 'success');
    }, 3000);
}

function _btReset(btn, statusEl) {
    btn.disabled = false;
    btn.textContent = 'Lancer le backtest';
    if (statusEl) statusEl.style.display = 'none';
}
function _btShowError(btn, statusEl, msg) {
    btn.disabled = false;
    btn.textContent = 'Lancer le backtest';
    if (statusEl) { statusEl.style.display = 'block'; statusEl.textContent = '⚠️ ' + msg; }
    showToast(msg, 'error');
}

function _btRender(res) {
    const s = res.stats || {};
    const m = res.meta || {};

    // KPI
    const benchFinal = (res.benchmark_equity && res.benchmark_equity.length)
        ? res.benchmark_equity[res.benchmark_equity.length - 1].v : null;
    _btSetKpi('bt-kpi-final', _btFmtUsd(s.final_value), _btFmtPct(s.total_return) + ' total', s.total_return >= 0);
    _btSetKpi('bt-kpi-cagr', _btFmtPct(s.cagr));
    _btSetKpi('bt-kpi-sharpe', s.sharpe != null ? s.sharpe.toFixed(2) : '—');
    _btSetKpi('bt-kpi-sortino', s.sortino != null ? s.sortino.toFixed(2) : '—');
    _btSetKpi('bt-kpi-maxdd', _btFmtPct(s.max_drawdown));
    _btSetKpi('bt-kpi-vol', _btFmtPct(s.volatility, 1));

    // Avertissements
    const warn = document.getElementById('bt-warnings');
    if (m.warnings && m.warnings.length) {
        warn.style.display = 'block';
        warn.innerHTML = '⚠️ ' + m.warnings.join('<br>⚠️ ');
    } else { warn.style.display = 'none'; }

    // Courbe d'équité
    const eq = (res.equity || []).map(p => ({ x: p.t, y: p.v }));
    const bench = (res.benchmark_equity || []).map(p => ({ x: p.t, y: p.v }));
    const inv = (res.invested || []).map(p => ({ x: p.t, y: p.v }));
    const unit = btYears <= 3 ? 'month' : 'year';
    const eqDatasets = [
        { label: 'Stratégie', data: eq, borderColor: '#7C5CFF', backgroundColor: 'rgba(124,92,255,.08)', fill: true, borderWidth: 1.5, tension: .25, pointRadius: 0 },
        { label: m.benchmark || 'Benchmark', data: bench, borderColor: '#1D9E75', borderDash: [2, 3], borderWidth: 1, tension: .25, pointRadius: 0, fill: false },
    ];
    if (inv.length) eqDatasets.push(
        { label: 'Capital investi (DCA)', data: inv, borderColor: '#888', borderDash: [4, 4], borderWidth: 1, tension: 0, pointRadius: 0, fill: false });
    _btDestroy('equity');
    _btCharts.equity = new Chart(document.getElementById('bt-chart-equity'), {
        type: 'line',
        data: { datasets: eqDatasets },
        options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
            plugins: { legend: { labels: { color: '#aaa', boxWidth: 12 } },
                tooltip: { callbacks: { label: c => ` ${c.dataset.label}: $${Math.round(c.parsed.y).toLocaleString()}` } } },
            scales: { x: { type: 'time', time: { unit }, grid: { color: BT_GRID }, ticks: { color: '#888', maxTicksLimit: 7 } },
                y: { type: 'logarithmic', grid: { color: BT_GRID }, ticks: { color: '#888', callback: v => '$' + (v / 1000).toFixed(0) + 'k' } } } }
    });

    // Drawdown
    const dd = (res.drawdown || []).map(p => ({ x: p.t, y: p.v * 100 }));
    _btDestroy('drawdown');
    _btCharts.drawdown = new Chart(document.getElementById('bt-chart-drawdown'), {
        type: 'line',
        data: { datasets: [{ label: 'Drawdown', data: dd, borderColor: '#D85A30', backgroundColor: 'rgba(216,90,48,.12)', fill: true, borderWidth: 1.5, tension: .25, pointRadius: 0 }]},
        options: { responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => ` ${c.parsed.y.toFixed(1)}%` } } },
            scales: { x: { type: 'time', time: { unit }, grid: { color: BT_GRID }, ticks: { color: '#888', maxTicksLimit: 5 } },
                y: { max: 0, grid: { color: BT_GRID }, ticks: { color: '#888', callback: v => v + '%' } } } }
    });

    // Heatmap mensuelle
    _btHeatmap(res.monthly_returns || [], 'bt-container-heatmap');

    // Levier dans le temps
    _btLeverage(res.leverage || [], res.margin_calls || [], unit);

    // Frictions & coûts
    _btCosts(s);

    // Bloc investisseur (DCA / TWR vs MWR)
    _btDcaBlock(s);

    // Appels de marge
    _btMarginCalls(res.margin_calls || []);

    // Rendements annuels + top drawdowns
    _btYearly(res.yearly_returns || [], m.benchmark);
    _btDrawdownPeriods(res.drawdown_periods || []);

    // Grille de stats détaillée (strat vs benchmark)
    _btStatsGrid(s, res.benchmark_stats || {}, m.benchmark);

    // Méta
    const cfg = m.config || {};
    const a = m.assumptions || {};
    const alphaTxt = s.alpha != null ? `${_btFmtPct(s.alpha)} alpha · β ${s.beta != null ? s.beta.toFixed(2) : '—'}` : '—';
    document.getElementById('bt-meta').innerHTML = [
        `Période : <b>${m.start}</b> → <b>${m.end}</b>`,
        `Benchmark : <b>${m.benchmark}</b> · valeur finale benchmark : <b>${benchFinal != null ? _btFmtUsd(benchFinal) : '—'}</b>`,
        `vs benchmark : <b>${alphaTxt}</b>`,
        `Rééquilibrages : <b>${s.n_rebalances}</b> · changements d'univers : <b>${s.n_universe_changes}</b> · mois en cash (risk-off) : <b>${m.n_riskoff_months ?? '—'}</b>`,
        `Univers candidat : <b>${m.pool_size}</b> tickers · top ${cfg.nb_top} sélectionné chaque mois`,
        `Hypothèses : coût <b>${a.tx_cost_bps}bps</b> · marge <b>${a.margin_rate_pct}%</b> · cash <b>${a.cash_yield_pct}%</b>`
        + ` · appel de marge <b>${a.margin_call_enabled ? 'ON' : 'OFF'}</b> (maintenance ${a.maintenance_margin_pct}%)`
        + (a.dca_amount > 0 ? ` · DCA <b>$${a.dca_amount}/mois</b>` : ''),
    ].join('<br>');
}

// --- Levier dans le temps + marqueurs d'appel de marge ----------------
function _btLeverage(lev, calls, unit) {
    const row = document.getElementById('bt-row-leverage');
    if (!lev.length) { row.style.display = 'none'; _btDestroy('leverage'); return; }
    row.style.display = '';
    const data = lev.map(p => ({ x: p.t, y: p.v }));
    const callSet = new Set(calls.map(c => c.date));
    const pts = lev.map(p => ({ x: p.t, y: callSet.has(p.t) ? p.v : null }));
    _btDestroy('leverage');
    _btCharts.leverage = new Chart(document.getElementById('bt-chart-leverage'), {
        type: 'line',
        data: { datasets: [
            { label: 'Levier (brut/equity)', data, borderColor: '#E0A030', backgroundColor: 'rgba(224,160,48,.08)', fill: true, borderWidth: 1.3, tension: .2, pointRadius: 0 },
            { label: 'Appel de marge', data: pts, borderColor: '#D85A30', backgroundColor: '#D85A30', showLine: false, pointRadius: 5, pointStyle: 'triangle' },
        ]},
        options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
            plugins: { legend: { labels: { color: '#aaa', boxWidth: 12 } },
                tooltip: { callbacks: { label: c => ` ${c.dataset.label}: ${c.parsed.y != null ? c.parsed.y.toFixed(2) + '×' : '—'}` } } },
            scales: { x: { type: 'time', time: { unit }, grid: { color: BT_GRID }, ticks: { color: '#888', maxTicksLimit: 7 } },
                y: { grid: { color: BT_GRID }, ticks: { color: '#888', callback: v => v + '×' } } } }
    });
}

// --- Frictions & coûts -------------------------------------------------
function _btCosts(s) {
    const usd = v => v != null ? _btFmtUsd(v) : '—';
    const rows = [
        ['Coûts de transaction', usd(s.tx_costs)],
        ['Intérêts de marge (financement)', usd(s.financing_costs)],
        ['Total frictions', `<b>${usd(s.total_costs)}</b>`],
        ['Levier moyen / max', `${s.avg_leverage != null ? s.avg_leverage.toFixed(2) + '×' : '—'} / ${s.max_leverage != null ? s.max_leverage.toFixed(2) + '×' : '—'}`],
        ['Temps à levier > 1×', s.pct_time_levered != null ? (s.pct_time_levered * 100).toFixed(0) + '%' : '—'],
        ['Temps investi', s.pct_time_invested != null ? (s.pct_time_invested * 100).toFixed(0) + '%' : '—'],
        ['Appels de marge', `<b style="color:${s.n_margin_calls ? 'var(--accent-short)' : 'inherit'}">${s.n_margin_calls}</b>`],
    ];
    document.getElementById('bt-costs').innerHTML = rows.map(
        r => `<div style="display:flex;justify-content:space-between;gap:12px;"><span>${r[0]}</span><span style="color:var(--text-primary)">${r[1]}</span></div>`
    ).join('');
}

// --- Bloc investisseur : capital investi, TWR vs MWR -------------------
function _btDcaBlock(s) {
    const usd = v => v != null ? _btFmtUsd(v) : '—';
    const title = document.getElementById('bt-dca-title');
    const hasDca = s.contributions > 0;
    title.textContent = hasDca ? 'Investisseur (DCA)' : 'Investisseur (apport unique)';
    const rows = [
        ['Capital total investi', usd(s.total_invested)],
        ['Valeur finale', `<b>${usd(s.final_value)}</b>`],
        ['Plus-value nette', `<b style="color:${s.profit >= 0 ? 'var(--accent-long)' : 'var(--accent-short)'}">${usd(s.profit)}</b>`],
    ];
    if (hasDca) rows.splice(1, 0, ['dont apports DCA', usd(s.contributions)]);
    rows.push(['Rendement pondéré-temps (TWR)', _btFmtPct(s.twr_total_return)]);
    rows.push(['Rendement actuariel (MWR / XIRR, annualisé)',
        s.money_weighted_return != null ? `<b>${_btFmtPct(s.money_weighted_return)}</b>` : '—']);
    document.getElementById('bt-dca-block').innerHTML = rows.map(
        r => `<div style="display:flex;justify-content:space-between;gap:12px;"><span>${r[0]}</span><span style="color:var(--text-primary)">${r[1]}</span></div>`
    ).join('');
}

// --- Tableau des appels de marge --------------------------------------
function _btMarginCalls(calls) {
    const card = document.getElementById('bt-card-margin');
    if (!calls.length) { card.style.display = 'none'; return; }
    card.style.display = '';
    let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
        + '<thead><tr style="color:var(--text-muted);text-align:left;">'
        + '<th style="padding:4px 8px;">Date</th><th style="padding:4px 8px;">Levier avant</th>'
        + '<th style="padding:4px 8px;">Liquidé</th><th style="padding:4px 8px;">Equity après</th></tr></thead><tbody>';
    calls.forEach(c => {
        html += `<tr style="border-top:1px solid var(--border);">`
            + `<td style="padding:4px 8px;">${c.date}</td>`
            + `<td style="padding:4px 8px;color:var(--accent-short);">${c.leverage_before}×</td>`
            + `<td style="padding:4px 8px;">${_btFmtUsd(c.liquidated)}</td>`
            + `<td style="padding:4px 8px;">${_btFmtUsd(c.equity)}</td></tr>`;
    });
    html += '</tbody></table>';
    document.getElementById('bt-margin-calls').innerHTML = html;
}

// --- Rendements annuels strat vs benchmark ----------------------------
function _btYearly(yearly, benchName) {
    const cont = document.getElementById('bt-yearly');
    if (!yearly.length) { cont.innerHTML = '<div class="empty-state">—</div>'; return; }
    const bar = (v) => {
        if (v == null) return '—';
        const col = v >= 0 ? 'var(--accent-long)' : 'var(--accent-short)';
        return `<span style="color:${col};">${v >= 0 ? '+' : ''}${v.toFixed(1)}%</span>`;
    };
    let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
        + `<thead><tr style="color:var(--text-muted);text-align:right;"><th style="text-align:left;padding:4px 8px;">Année</th>`
        + `<th style="padding:4px 8px;">Stratégie</th><th style="padding:4px 8px;">${benchName || 'Bench'}</th>`
        + `<th style="padding:4px 8px;">Écart</th></tr></thead><tbody>`;
    yearly.forEach(y => {
        const diff = (y.benchmark_pct != null) ? y.strategy_pct - y.benchmark_pct : null;
        html += `<tr style="border-top:1px solid var(--border);text-align:right;">`
            + `<td style="text-align:left;padding:4px 8px;">${y.year}</td>`
            + `<td style="padding:4px 8px;">${bar(y.strategy_pct)}</td>`
            + `<td style="padding:4px 8px;">${bar(y.benchmark_pct)}</td>`
            + `<td style="padding:4px 8px;">${bar(diff)}</td></tr>`;
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
}

// --- Top drawdowns ----------------------------------------------------
function _btDrawdownPeriods(periods) {
    const cont = document.getElementById('bt-dd-periods');
    if (!periods.length) { cont.innerHTML = '<div class="empty-state">—</div>'; return; }
    let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
        + '<thead><tr style="color:var(--text-muted);text-align:left;">'
        + '<th style="padding:4px 6px;">Profondeur</th><th style="padding:4px 6px;">Creux</th>'
        + '<th style="padding:4px 6px;">Durée</th><th style="padding:4px 6px;">Récupéré</th></tr></thead><tbody>';
    periods.forEach(p => {
        html += `<tr style="border-top:1px solid var(--border);">`
            + `<td style="padding:4px 6px;color:var(--accent-short);">${p.depth_pct.toFixed(1)}%</td>`
            + `<td style="padding:4px 6px;">${p.valley}</td>`
            + `<td style="padding:4px 6px;">${p.days} j</td>`
            + `<td style="padding:4px 6px;">${p.recovered ? '✓' : '<span style="color:var(--accent-short)">en cours</span>'}</td></tr>`;
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
}

// --- Grille de stats détaillée ----------------------------------------
function _btStatsGrid(s, b, benchName) {
    const pct = (v, d = 1) => v == null ? '—' : (v * 100).toFixed(d) + '%';
    const num = (v, d = 2) => v == null ? '—' : v.toFixed(d);
    const items = [
        ['CAGR', pct(s.cagr), pct(b.cagr)],
        ['Volatilité', pct(s.volatility), pct(b.volatility)],
        ['Sharpe', num(s.sharpe), num(b.sharpe)],
        ['Sortino', num(s.sortino), num(b.sortino)],
        ['Calmar', num(s.calmar), num(b.calmar)],
        ['Max drawdown', pct(s.max_drawdown), pct(b.max_drawdown)],
        ['Ulcer index', num(s.ulcer_index, 3), null],
        ['VaR 95% (jour)', pct(s.var_95), null],
        ['CVaR 95% (jour)', pct(s.cvar_95), null],
        ['Omega', num(s.omega), null],
        ['Gain/Pain', num(s.gain_to_pain), null],
        ['Tail ratio', num(s.tail_ratio), null],
        ['Skew', num(s.skew), null],
        ['Kurtosis', num(s.kurtosis), null],
        ['Meilleur jour', pct(s.best_day), null],
        ['Pire jour', pct(s.worst_day), null],
        ['Meilleur mois', pct(s.best_month), null],
        ['Pire mois', pct(s.worst_month), null],
        ['% jours gagnants', pct(s.win_rate_daily, 0), null],
        ['% mois positifs', pct(s.pct_positive_months, 0), null],
        ['Profit factor', num(s.profit_factor), null],
        ['Alpha (vs bench)', pct(s.alpha), null],
        ['Beta (vs bench)', num(s.beta), null],
    ];
    document.getElementById('bt-stats-grid').innerHTML = items.map(it => {
        const benchTxt = it[2] != null ? `<span style="color:var(--text-muted);font-size:10px;"> · ${benchName || 'B'} ${it[2]}</span>` : '';
        return `<div style="display:flex;flex-direction:column;gap:1px;">`
            + `<span style="color:var(--text-muted);font-size:10px;">${it[0]}</span>`
            + `<span style="color:var(--text-primary);">${it[1]}${benchTxt}</span></div>`;
    }).join('');
}

// Heatmap locale (réutilise le format {year, month, return_pct} du serveur)
function _btHeatmap(monthly, containerId) {
    const cont = document.getElementById(containerId);
    if (!monthly.length) { cont.innerHTML = '<div class="empty-state">Données insuffisantes</div>'; return; }
    const MONTHS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
    const byYear = {};
    let maxAbs = 0;
    monthly.forEach(x => { byYear[x.year] = byYear[x.year] || {}; byYear[x.year][x.month] = x.return_pct; maxAbs = Math.max(maxAbs, Math.abs(x.return_pct)); });
    const years = Object.keys(byYear).sort();
    const cell = (v) => {
        if (v == null) return `<td style="background:var(--bg-secondary);border-radius:3px;"></td>`;
        const op = Math.max(0.15, Math.min(0.85, Math.abs(v) / (maxAbs || 1)));
        const col = v >= 0 ? `rgba(29,158,117,${op})` : `rgba(216,90,48,${op})`;
        return `<td style="background:${col};border-radius:3px;text-align:center;font-size:9px;color:#fff;padding:3px;">${v >= 0 ? '+' : ''}${v.toFixed(0)}</td>`;
    };
    let html = '<table style="width:100%;border-collapse:separate;border-spacing:2px;font-size:10px;"><thead><tr><th></th>'
        + MONTHS.map(mo => `<th style="color:var(--text-muted);font-weight:400;">${mo}</th>`).join('') + '</tr></thead><tbody>';
    years.forEach(y => {
        html += `<tr><td style="color:var(--text-muted);padding-right:6px;">${y}</td>`
            + Array.from({ length: 12 }, (_, i) => cell(byYear[y][i + 1])).join('') + '</tr>';
    });
    html += '</tbody></table>';
    cont.innerHTML = html;
}

// =====================================================================
// OPTIMISATION PARAMÈTRES (grid search vol_target × max_exposure)
// =====================================================================

let _optPollTimer = null;
let _optYears = 10;

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('#opt-years .perf-pill').forEach(pill => {
        pill.addEventListener('click', () => {
            document.querySelectorAll('#opt-years .perf-pill').forEach(p => p.classList.remove('active'));
            pill.classList.add('active');
            _optYears = parseInt(pill.dataset.years, 10);
        });
    });
});

async function launchOptimize(quick) {
    const nbTop = parseInt(document.getElementById('opt-nb-top')?.value || 5, 10);
    document.getElementById('opt-results').style.display = 'none';
    document.getElementById('opt-error').style.display = 'none';
    _optSetProgress(0, 0, 'Démarrage…');
    document.getElementById('opt-progress').style.display = 'block';
    ['btn-opt-full','btn-opt-quick'].forEach(id => {
        const b = document.getElementById(id); if (b) b.disabled = true;
    });

    const res = await api('/backtest/optimize', {
        method: 'POST',
        body: JSON.stringify({ years: _optYears, nb_top: nbTop, quick: !!quick }),
    });

    if (!res || !res.success) {
        _optDone();
        const errEl = document.getElementById('opt-error');
        errEl.textContent = res?.message || 'Erreur lors du lancement';
        errEl.style.display = 'block';
        return;
    }
    if (_optPollTimer) clearInterval(_optPollTimer);
    _optPollTimer = setInterval(_optPoll, 2000);
}

async function _optPoll() {
    const st = await api('/backtest/optimize/status');
    if (!st) return;
    _optSetProgress(st.done, st.total, st.current || '…');
    if (!st.running) {
        clearInterval(_optPollTimer); _optPollTimer = null;
        _optDone();
        if (st.error) {
            const errEl = document.getElementById('opt-error');
            errEl.textContent = '❌ ' + st.error;
            errEl.style.display = 'block';
        } else if (st.results) {
            _optRenderResults(st.results, st.elapsed_s);
        }
    }
}

function _optSetProgress(done, total, label) {
    const pct = total > 0 ? Math.round(done / total * 100) : 0;
    const bar = document.getElementById('opt-progress-bar');
    if (bar) bar.style.width = pct + '%';
    const lbl = document.getElementById('opt-progress-label');
    if (lbl) lbl.textContent = label;
    const pctEl = document.getElementById('opt-progress-pct');
    if (pctEl) pctEl.textContent = total > 0 ? `${done}/${total}` : '';
}

function _optDone() {
    ['btn-opt-full','btn-opt-quick'].forEach(id => {
        const b = document.getElementById(id); if (b) b.disabled = false;
    });
    document.getElementById('opt-progress').style.display = 'none';
}

function _optRenderResults(results, elapsedS) {
    const body = document.getElementById('opt-results-body');
    const meta = document.getElementById('opt-results-meta');
    const el = document.getElementById('opt-results');
    if (!body || !el) return;

    const eligible = results.filter(r => r.eligible);
    const best = eligible[0];
    meta.innerHTML = `${results.length} combinaisons testées · ${eligible.length} éligibles (MaxDD ≥ −40 %) · ${elapsedS}s`
        + (best ? ` · <strong style="color:var(--accent-long);">★ Meilleure : ${best.label}</strong>` : '');

    body.innerHTML = results.map(r => {
        const isBest = r.rank === 1 && r.eligible;
        const bg = isBest ? 'background:rgba(34,197,94,.07);' : '';
        const ddColor = r.max_dd < -35 ? 'color:#ef4444;' : r.max_dd < -28 ? 'color:#f97316;' : '';
        const eligBadge = r.eligible ? ''
            : '<span style="font-size:9px;color:#ef4444;opacity:.7;margin-left:4px;">hors limite</span>';
        const applyBtn = r.eligible && r.vol_scaling
            ? `<button class="btn btn-secondary" style="padding:3px 10px;font-size:11px;"
                 onclick="optApply(${r.vol_target_pct},${r.max_exposure_pct})">Appliquer</button>`
            : '';
        return `<tr style="${bg}border-bottom:1px solid var(--border);">
            <td style="padding:7px 10px;color:var(--text-muted);">${isBest ? '★' : r.rank}</td>
            <td style="padding:7px 10px;font-family:'IBM Plex Mono';white-space:nowrap;">${r.label}${eligBadge}</td>
            <td style="padding:7px 10px;text-align:right;font-weight:600;">${r.sharpe}</td>
            <td style="padding:7px 10px;text-align:right;">${r.sortino}</td>
            <td style="padding:7px 10px;text-align:right;">${r.cagr}%</td>
            <td style="padding:7px 10px;text-align:right;${ddColor}">${r.max_dd}%</td>
            <td style="padding:7px 10px;text-align:right;">${r.volatility}%</td>
            <td style="padding:7px 10px;text-align:right;">${r.avg_leverage}×</td>
            <td style="padding:7px 10px;text-align:right;">${r.n_margin_calls}</td>
            <td style="padding:7px 10px;text-align:center;">${applyBtn}</td>
        </tr>`;
    }).join('');
    el.style.display = 'block';
}

async function optApply(volTarget, maxExposure) {
    if (!confirm(`Appliquer vol_target=${volTarget}%, max_exposure=${maxExposure}% aux paramètres live ?`)) return;
    const res = await api('/settings', {
        method: 'POST',
        body: JSON.stringify({ vol_scaling_enabled: true, vol_target: volTarget, max_exposure: maxExposure }),
    });
    if (res && res.success) {
        showToast(`Paramètres mis à jour : vol_target=${volTarget}% · max_exposure=${maxExposure}%`);
    } else {
        showToast(res?.error || 'Erreur', 'error');
    }
}
