// =============================================================================
// Stratégie long Quality-Value (US / Europe PEA / transversal)
// Classic script — partage le scope global (api, adminToken, showToast de core.js)
// =============================================================================
let qvMarket = 'us';

const QV_MARKET_LABELS = { us: 'US (S&P 500)', eu: 'Europe (PEA)', all: 'Transversal US + Europe' };

function _qvHighlightMarket(m) {
    document.querySelectorAll('.qv-market-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.market === m));
    const lbl = document.getElementById('qv-market-label');
    if (lbl) lbl.textContent = QV_MARKET_LABELS[m] || '';
}

// Chargé quand on affiche l'onglet (voir showPage dans core.js)
async function loadQV() {
    const cfg = await api('/qv/config', { method: 'GET' });
    if (cfg && cfg.market) qvMarket = cfg.market;
    _qvHighlightMarket(qvMarket);
    await _qvLoadPortfolio(qvMarket);
}

// Change de marché : met à jour la vue (+ persiste le choix si admin)
async function setQVMarket(m, el) {
    qvMarket = m;
    _qvHighlightMarket(m);
    if (adminToken) {
        try { await api('/qv/config', { method: 'POST', body: JSON.stringify({ market: m }) }); }
        catch (e) { /* non bloquant */ }
    }
    await _qvLoadPortfolio(m);
}

// Charge le dernier portefeuille calculé pour un marché
async function _qvLoadPortfolio(m) {
    const data = await api('/qv/portfolio?market=' + m, { method: 'GET' });
    if (data && data.success) {
        renderQV(data);
    } else {
        document.getElementById('qv-list').innerHTML =
            '<div class="empty-state">Aucun portefeuille pour ce marché — cliquez « Générer le portefeuille »</div>';
        document.getElementById('qv-sectors').innerHTML = '<div class="empty-state">—</div>';
        document.getElementById('qv-stat-count').textContent = '—';
        document.getElementById('qv-stat-universe').textContent = '—';
    }
}

// Déclenche le calcul live (admin)
async function runQV() {
    const btn = document.getElementById('btn-qv-run');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Calcul en cours...';
    const data = await api('/qv/run', { method: 'POST', body: JSON.stringify({ market: qvMarket }) });
    btn.disabled = false;
    btn.innerHTML = 'Générer le portefeuille';
    if (data && data.success) {
        renderQV(data);
        showToast('Portefeuille Quality-Value généré');
    } else {
        showToast((data && data.error) || 'Erreur lors du calcul', 'error');
    }
}

function renderQV(data) {
    const holdings = data.holdings || [];
    document.getElementById('qv-stat-count').textContent = holdings.length || '—';
    document.getElementById('qv-stat-universe').textContent = (data.eligible != null ? data.eligible : '—');
    if (data.date) {
        const u = document.getElementById('qv-last-update');
        if (u) u.textContent = 'Calculé le ' + data.date + ' · ' + (QV_MARKET_LABELS[data.market] || '');
    }

    if (!holdings.length) {
        document.getElementById('qv-list').innerHTML = '<div class="empty-state">Aucun titre.</div>';
        return;
    }

    document.getElementById('qv-list').innerHTML = holdings.map(h => `
        <div class="stock-item">
            <div class="stock-rank top">${h.rank}</div>
            <div class="stock-info">
                <div class="stock-ticker">${h.ticker}</div>
                <div class="qv-mini-bars">
                    <span class="qv-mini">Q<span class="qv-mini-track q"><span style="width:${Math.max(0, Math.min(100, h.quality || 0))}%"></span></span></span>
                    <span class="qv-mini">V<span class="qv-mini-track v"><span style="width:${Math.max(0, Math.min(100, h.value || 0))}%"></span></span></span>
                </div>
                <div class="stock-signal sell">${h.sector || '—'}</div>
            </div>
            <div class="qv-row-right">
                <div class="qv-score-pill">${h.composite}</div>
                ${h.allocation != null ? `<div class="allocation-badge">${h.allocation}%</div>` : ''}
            </div>
        </div>`).join('');
    if (typeof animIn === 'function') animIn(document.getElementById('qv-list'));

    // Répartition sectorielle
    const sb = data.sector_breakdown || {};
    const keys = Object.keys(sb);
    document.getElementById('qv-sectors').innerHTML = keys.length
        ? keys.map(k => `<div class="qv-sector-row"><span>${k}</span><span class="pct">${sb[k]}%</span></div>`).join('')
        : '<div class="empty-state">—</div>';
}

// ── Évaluation d'un ticker ───────────────────────────────────────────────────
const QV_METRIC_LABELS = {
    gp_to_assets: 'GP / actifs', return_on_equity: 'ROE', return_on_assets: 'ROA',
    gross_margins: 'Marge brute', operating_margins: 'Marge opé', fcf_margin: 'FCF margin',
    accruals_ratio: 'Accruals (bas=mieux)', debt_to_equity: 'Dette/FP (bas=mieux)',
    current_ratio: 'Current ratio', fcf_yield: 'FCF yield', earnings_yield: 'Earnings yield',
    book_yield: 'Book yield', sales_yield: 'Sales yield', ebitda_ev: 'EBITDA/EV'
};

async function evaluateQVTicker() {
    const inp = document.getElementById('qv-eval-ticker');
    const t = (inp.value || '').trim().toUpperCase();
    if (!t) { showToast('Entrez un ticker', 'error'); return; }
    const btn = document.getElementById('btn-qv-eval');
    const res = document.getElementById('qv-eval-result');
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
    res.innerHTML = '<div class="empty-state">Calcul…</div>';
    const d = await api('/qv/evaluate?ticker=' + encodeURIComponent(t) + '&market=' + qvMarket);
    btn.disabled = false; btn.textContent = 'Évaluer';
    if (!d || !d.success) {
        res.innerHTML = '<div class="empty-state">' + ((d && d.error) || 'Erreur') + '</div>';
        return;
    }
    const tile = (label, v, color) =>
        `<div class="qv-eval-tile"><div class="v" style="color:${color}">${v}</div><div class="l">${label}</div></div>`;
    const metricsRows = Object.keys(d.metrics || {}).map(k => {
        const m = d.metrics[k];
        const cls = m.group === 'quality' ? 'q' : 'v';
        return `<div class="qv-metric-row"><span class="qv-metric-name">${QV_METRIC_LABELS[k] || k}</span>` +
               `<span class="qv-metric-pct ${cls}">${m.pct != null ? m.pct : '—'}</span></div>`;
    }).join('');
    const tag = d.fetched_live ? '<span class="qv-eval-tag live">récupéré en direct</span>'
              : (d.in_universe ? '' : '<span class="qv-eval-tag" style="background:var(--bg-hover);color:var(--text-muted);">hors univers</span>');
    res.innerHTML =
        '<div class="qv-eval-tiles">' +
        tile('Composite', d.composite, 'var(--accent-long)') +
        tile('Qualité', d.quality, 'var(--accent-blue)') +
        tile('Value', d.value, '#a855f7') + '</div>' +
        `<p class="qv-eval-meta"><strong style="font-family:'IBM Plex Mono',monospace;">${d.ticker}</strong> · ${d.sector || '—'}` +
        ` · percentile : <strong>${d.composite_percentile != null ? d.composite_percentile + '%' : '—'}</strong>${tag}</p>` +
        '<div class="card-title" style="margin-bottom:6px;">Percentiles par métrique</div>' + metricsRows;
}

// ── Backtest de la stratégie ─────────────────────────────────────────────────
let _qvChart = null;

async function runQVBacktest() {
    const btn = document.getElementById('btn-qv-bt');
    const statusEl = document.getElementById('qv-bt-status');
    const results = document.getElementById('qv-bt-results');
    btn.disabled = true; btn.textContent = 'Backtest en cours…';
    statusEl.style.display = 'block'; statusEl.textContent = '⏳ Lancement…';

    const launch = await api('/qv/backtest/run', { method: 'POST', body: JSON.stringify({ market: qvMarket }) });
    if (!launch || launch.error) {
        btn.disabled = false; btn.textContent = 'Lancer le backtest';
        statusEl.textContent = (launch && launch.error) || 'Erreur au lancement';
        return;
    }
    const jobId = launch.job_id;
    let elapsed = 0;
    const poll = setInterval(async () => {
        elapsed += 3000;
        statusEl.textContent = '⏳ Backtest en cours… ' + Math.floor(elapsed / 1000) + 's'
            + (elapsed < 20000 ? ' · chargement des données…' : '');
        if (elapsed > 15 * 60 * 1000) { clearInterval(poll); statusEl.textContent = 'Timeout.'; btn.disabled = false; btn.textContent = 'Lancer le backtest'; return; }
        const st = await api('/qv/backtest/status/' + jobId);
        if (!st) { clearInterval(poll); btn.disabled = false; btn.textContent = 'Lancer le backtest'; return; }
        if (st.status === 'running') return;
        clearInterval(poll);
        btn.disabled = false; btn.textContent = 'Lancer le backtest';
        statusEl.style.display = 'none';
        if (st.status === 'error' || st.error || !st.result || !st.result.success) {
            statusEl.style.display = 'block';
            statusEl.textContent = (st.error) || (st.result && st.result.error) || 'Erreur';
            return;
        }
        renderQVBacktest(st.result);
        results.style.display = 'block';
        showToast('Backtest terminé');
    }, 3000);
}

function _qvPct(v) { return v == null ? '—' : (v * 100).toFixed(1) + '%'; }

function renderQVBacktest(r) {
    const s = r.stats || {}, b = r.benchmark_stats || {};
    const card = (label, v, sub) =>
        '<div class="stat-card"><div class="stat-value">' + v + '</div>' +
        '<div class="stat-label">' + label + (sub ? ' <span style="opacity:.6">' + sub + '</span>' : '') + '</div></div>';
    document.getElementById('qv-bt-stats').innerHTML =
        card('CAGR', _qvPct(s.cagr), 'vs ' + _qvPct(b.cagr)) +
        card('Sharpe', s.sharpe != null ? s.sharpe : '—', 'vs ' + (b.sharpe != null ? b.sharpe : '—')) +
        card('Max DD', _qvPct(s.max_drawdown), 'vs ' + _qvPct(b.max_drawdown)) +
        card('Volatilité', _qvPct(s.volatility), '');

    const cs = getComputedStyle(document.documentElement);
    const green = (cs.getPropertyValue('--accent-long') || '#10b981').trim();
    const muted = (cs.getPropertyValue('--text-muted') || '#8b949e').trim();
    const grid = (cs.getPropertyValue('--border-subtle') || 'rgba(128,128,128,0.15)').trim();
    const labels = (r.equity || []).map(p => p.t);
    const strat = (r.equity || []).map(p => p.v);
    const bench = (r.benchmark_equity || []).map(p => p.v);
    if (_qvChart) _qvChart.destroy();
    _qvChart = new Chart(document.getElementById('qv-bt-chart'), {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                { label: 'Quality-Value', data: strat, borderColor: green, backgroundColor: green + '22', borderWidth: 2, pointRadius: 0, fill: true, tension: 0.15 },
                { label: 'Univers (équipondéré)', data: bench, borderColor: muted, borderWidth: 1.5, pointRadius: 0, borderDash: [5, 4], fill: false, tension: 0.15 },
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { type: 'time', time: { unit: 'year' }, grid: { color: grid },
                     ticks: { maxTicksLimit: 10, color: muted, font: { size: 10 } } },
                y: { type: 'logarithmic', grid: { color: grid },
                     ticks: { color: muted, font: { size: 10 } } }
            },
            plugins: { legend: { position: 'top', labels: { color: muted, boxWidth: 12, font: { size: 11 } } } }
        }
    });
}
