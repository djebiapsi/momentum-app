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

function _qvBar(pct, color) {
    const p = Math.max(0, Math.min(100, pct || 0));
    return '<div style="background:var(--bg-subtle,#21262d);border-radius:4px;height:6px;overflow:hidden;">' +
           '<div style="width:' + p + '%;height:100%;background:' + color + ';"></div></div>';
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

    let rows = '';
    holdings.forEach(h => {
        rows +=
            '<tr>' +
            '<td style="padding:8px 6px;color:var(--text-muted,#8b949e);">' + h.rank + '</td>' +
            '<td style="padding:8px 6px;font-family:\'IBM Plex Mono\',monospace;font-weight:600;">' + h.ticker + '</td>' +
            '<td style="padding:8px 6px;text-align:right;font-weight:700;color:#22c55e;">' + h.composite + '</td>' +
            '<td style="padding:8px 6px;width:70px;">' + _qvBar(h.quality, '#0891b2') + '</td>' +
            '<td style="padding:8px 6px;width:70px;">' + _qvBar(h.value, '#7c3aed') + '</td>' +
            '<td style="padding:8px 6px;text-align:right;">' + (h.allocation != null ? h.allocation + '%' : '—') + '</td>' +
            '<td style="padding:8px 6px;font-size:11px;color:var(--text-muted,#8b949e);">' + (h.sector || '—') + '</td>' +
            '</tr>';
    });
    document.getElementById('qv-list').innerHTML =
        '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:13px;">' +
        '<thead><tr style="color:var(--text-muted,#8b949e);font-size:11px;text-transform:uppercase;text-align:left;">' +
        '<th style="padding:6px;">#</th><th style="padding:6px;">Ticker</th>' +
        '<th style="padding:6px;text-align:right;">Score</th>' +
        '<th style="padding:6px;">Qualité</th><th style="padding:6px;">Value</th>' +
        '<th style="padding:6px;text-align:right;">Alloc</th><th style="padding:6px;">Secteur</th></tr></thead>' +
        '<tbody>' + rows + '</tbody></table></div>';

    // Répartition sectorielle
    const sb = data.sector_breakdown || {};
    const keys = Object.keys(sb);
    if (keys.length) {
        document.getElementById('qv-sectors').innerHTML = keys.map(k =>
            '<div style="display:flex;justify-content:space-between;padding:5px 2px;border-bottom:1px solid var(--border,rgba(255,255,255,0.06));">' +
            '<span>' + k + '</span><span style="color:var(--text-muted,#8b949e);">' + sb[k] + '%</span></div>').join('');
    } else {
        document.getElementById('qv-sectors').innerHTML = '<div class="empty-state">—</div>';
    }
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
    const badge = (label, v, color) =>
        '<div style="flex:1;text-align:center;padding:10px;background:var(--bg-subtle,#21262d);border-radius:8px;">' +
        '<div style="font-size:22px;font-weight:700;color:' + color + ';">' + v + '</div>' +
        '<div style="font-size:11px;color:var(--text-muted,#8b949e);text-transform:uppercase;">' + label + '</div></div>';
    let metricsRows = '';
    Object.keys(d.metrics || {}).forEach(k => {
        const m = d.metrics[k];
        const pct = m.pct != null ? m.pct : '—';
        const col = m.group === 'quality' ? '#0891b2' : '#7c3aed';
        metricsRows += '<div style="display:flex;justify-content:space-between;padding:4px 2px;font-size:12px;border-bottom:1px solid var(--border,rgba(255,255,255,0.05));">' +
            '<span style="color:var(--text-muted,#8b949e);">' + (QV_METRIC_LABELS[k] || k) + '</span>' +
            '<span style="color:' + col + ';font-weight:600;">' + pct + '</span></div>';
    });
    res.innerHTML =
        '<div style="display:flex;gap:8px;margin-bottom:10px;">' +
        badge('Composite', d.composite, '#22c55e') +
        badge('Qualité', d.quality, '#0891b2') +
        badge('Value', d.value, '#7c3aed') + '</div>' +
        '<p style="font-size:13px;margin:0 0 8px;">' +
        '<strong>' + d.ticker + '</strong> · ' + (d.sector || '—') +
        ' · percentile composite : <strong>' + (d.composite_percentile != null ? d.composite_percentile + '%' : '—') + '</strong>' +
        (d.fetched_live ? ' <span style="color:#f59e0b;">(récupéré en direct)</span>' :
         (d.in_universe ? '' : ' <span style="color:var(--text-muted,#8b949e);">(hors univers)</span>')) + '</p>' +
        '<div style="font-size:11px;color:var(--text-muted,#8b949e);text-transform:uppercase;margin-bottom:4px;">Percentiles par métrique</div>' +
        metricsRows;
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

    const labels = (r.equity || []).map(p => p.t);
    const strat = (r.equity || []).map(p => p.v);
    const bench = (r.benchmark_equity || []).map(p => p.v);
    if (_qvChart) _qvChart.destroy();
    _qvChart = new Chart(document.getElementById('qv-bt-chart'), {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                { label: 'Quality-Value', data: strat, borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)', borderWidth: 2, pointRadius: 0, fill: true, tension: 0.1 },
                { label: 'Univers (équipondéré)', data: bench, borderColor: '#8b949e', borderWidth: 1.5, pointRadius: 0, borderDash: [5, 4], fill: false, tension: 0.1 },
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { type: 'time', time: { unit: 'year' }, ticks: { maxTicksLimit: 12 } },
                y: { type: 'logarithmic', title: { display: true, text: 'Équité (base 1)' } }
            },
            plugins: { legend: { position: 'top' } }
        }
    });
}
