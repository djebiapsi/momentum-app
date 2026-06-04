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
    const status = document.getElementById('bt-status');
    const results = document.getElementById('bt-results');
    btn.disabled = true;
    btn.textContent = 'Calcul en cours…';
    results.style.display = 'none';
    status.style.display = 'block';
    status.textContent = '⏳ Backtest en cours… (la 1ʳᵉ exécution peut prendre 1 à 2 min : récupération de l\'historique)';

    const body = JSON.stringify({
        capital: parseFloat(document.getElementById('bt-capital').value) || 10000,
        years: btYears,
        benchmark: document.getElementById('bt-benchmark').value,
    });
    const res = await api('/backtest/run', { method: 'POST', body });

    btn.disabled = false;
    btn.textContent = 'Lancer le backtest';
    status.style.display = 'none';

    if (!res) return;
    if (res.error) { showToast(res.error, 'error'); status.style.display = 'block'; status.textContent = '⚠️ ' + res.error; return; }
    _btRender(res);
    results.style.display = 'block';
    showToast('Backtest terminé', 'success');
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
    const unit = btYears <= 3 ? 'month' : 'year';
    _btDestroy('equity');
    _btCharts.equity = new Chart(document.getElementById('bt-chart-equity'), {
        type: 'line',
        data: { datasets: [
            { label: 'Stratégie', data: eq, borderColor: '#7C5CFF', backgroundColor: 'rgba(124,92,255,.08)', fill: true, borderWidth: 1.5, tension: .25, pointRadius: 0 },
            { label: m.benchmark || 'Benchmark', data: bench, borderColor: '#1D9E75', borderDash: [2, 3], borderWidth: 1, tension: .25, pointRadius: 0, fill: false },
        ]},
        options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
            plugins: { legend: { labels: { color: '#aaa', boxWidth: 12 } },
                tooltip: { callbacks: { label: c => ` ${c.dataset.label}: $${Math.round(c.parsed.y).toLocaleString()}` } } },
            scales: { x: { type: 'time', time: { unit }, grid: { color: BT_GRID }, ticks: { color: '#888', maxTicksLimit: 7 } },
                y: { grid: { color: BT_GRID }, ticks: { color: '#888', callback: v => '$' + (v / 1000).toFixed(0) + 'k' } } } }
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

    // Méta
    const cfg = m.config || {};
    const alphaTxt = s.alpha != null ? `${_btFmtPct(s.alpha)} alpha · β ${s.beta != null ? s.beta.toFixed(2) : '—'}` : '—';
    document.getElementById('bt-meta').innerHTML = [
        `Période : <b>${m.start}</b> → <b>${m.end}</b>`,
        `Benchmark : <b>${m.benchmark}</b> · valeur finale benchmark : <b>${benchFinal != null ? _btFmtUsd(benchFinal) : '—'}</b>`,
        `vs benchmark : <b>${alphaTxt}</b>`,
        `Calmar : <b>${s.calmar != null ? s.calmar.toFixed(2) : '—'}</b> · % mois positifs : <b>${s.pct_positive_months != null ? (s.pct_positive_months * 100).toFixed(0) + '%' : '—'}</b>`,
        `Rééquilibrages : <b>${s.n_rebalances}</b> · changements d'univers : <b>${s.n_universe_changes}</b>`,
        `Univers candidat : <b>${m.pool_size}</b> tickers · top ${cfg.nb_top} sélectionné chaque mois`,
    ].join('<br>');
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
