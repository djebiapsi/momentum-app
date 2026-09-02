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
