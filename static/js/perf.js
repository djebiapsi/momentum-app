        // =================================================================
        // PERFORMANCE DASHBOARD v2.0 (IBKR / Flex)
        // =================================================================

        function fmtPct(v) { return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'; }
        function fmtUsd(v) { return (v >= 0 ? '+$' : '-$') + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}); }
        function colorClass(v) { return v >= 0 ? 'perf-pos' : 'perf-neg'; }

        let perfRange = '1Y';
        const _perfCharts = {};
        const PERF_GRID = 'rgba(255,255,255,.05)';

        function _destroyChart(key) { if (_perfCharts[key]) { _perfCharts[key].destroy(); delete _perfCharts[key]; } }

        // Brancher les pills de période (une seule fois)
        function _initPerfPills() {
            document.querySelectorAll('.perf-pill').forEach(pill => {
                if (pill._bound) return;
                pill._bound = true;
                pill.addEventListener('click', () => {
                    document.querySelectorAll('.perf-pill').forEach(p => p.classList.remove('active'));
                    pill.classList.add('active');
                    perfRange = pill.dataset.range;
                    loadPerfData();
                });
            });
        }

        async function loadPerfData() {
            _initPerfPills();
            document.getElementById('perf-date-info').textContent = 'Chargement…';

            const data = await api(`/perf/dashboard?range=${perfRange}`);
            if (!data || !data.success) {
                document.getElementById('perf-date-info').textContent = data?.error || 'Connexion IBKR requise';
                return;
            }
            if (data.empty) {
                document.getElementById('perf-date-info').textContent = data.message || 'Données insuffisantes';
                return;
            }

            const k = data.kpis;
            const src = data.nav_source === 'flex' ? 'données réelles (Flex)' : 'reconstruction buy & hold';
            document.getElementById('perf-date-info').textContent = `${perfRange} · ${src}`;
            document.getElementById('perf-last-update-v2').textContent = 'Mis à jour à ' + new Date().toLocaleTimeString('fr-FR');

            // ---- KPIs ----
            const setKpi = (id, valueText, deltaText, deltaPos) => {
                const card = document.getElementById(id);
                if (!card) return;
                card.querySelector('.perf-kpi-value').textContent = valueText;
                const d = card.querySelector('.perf-kpi-delta');
                if (d) {
                    d.textContent = deltaText || '';
                    d.style.color = deltaText ? (deltaPos ? 'var(--accent-long)' : '#f87171') : '';
                }
            };
            setKpi('kpi-total-value', '$' + (k.total_value||0).toLocaleString('en-US',{maximumFractionDigits:0}),
                   fmtPct(k.total_return_pct||0), (k.total_return_pct||0) >= 0);
            setKpi('kpi-cagr', fmtPct(k.cagr_pct||0),
                   k.cagr_vs_bench_pct != null ? `${k.cagr_vs_bench_pct >= 0 ? '+' : ''}${k.cagr_vs_bench_pct}% vs S&P` : '',
                   (k.cagr_vs_bench_pct||0) >= 0);
            document.querySelector('#kpi-sharpe .perf-kpi-value').textContent = (k.sharpe!=null?k.sharpe.toFixed(2):'—');
            const ddEl = document.querySelector('#kpi-max-drawdown .perf-kpi-value');
            ddEl.textContent = fmtPct(k.max_drawdown_pct||0); ddEl.style.color = '#f87171';
            const ugEl = document.querySelector('#kpi-unrealized-gains .perf-kpi-value');
            ugEl.textContent = fmtUsd(k.unrealized_pnl||0); ugEl.style.color = (k.unrealized_pnl||0)>=0?'var(--accent-long)':'#f87171';
            document.querySelector('#kpi-dividends .perf-kpi-value').textContent = '$' + (k.dividends_total||0).toLocaleString('en-US',{maximumFractionDigits:0});

            // ---- Chart évolution (portefeuille + benchmark) ----
            const ts = data.timeseries;
            const portfolioPts = (ts.portfolio||[]).map(p => ({x:p.date, y:p.value}));
            const benchPts = (ts.benchmark||[]).map(p => ({x:p.date, y:p.value}));
            _destroyChart('evolution');
            _perfCharts.evolution = new Chart(document.getElementById('chart-evolution'), {
                type: 'line',
                data: { datasets: [
                    { label:'Portefeuille', data:portfolioPts, borderColor:'#378ADD', backgroundColor:'rgba(55,138,221,.08)', fill:true, borderWidth:1.5, tension:.3, pointRadius:0 },
                    { label:'S&P 500', data:benchPts, borderColor:'#1D9E75', borderDash:[2,3], borderWidth:1, tension:.3, pointRadius:0, fill:false },
                ]},
                options: { responsive:true, maintainAspectRatio:false, interaction:{mode:'index',intersect:false},
                    plugins:{ legend:{labels:{color:'#aaa',boxWidth:12}}, tooltip:{callbacks:{label:c=>` ${c.dataset.label}: $${Math.round(c.parsed.y).toLocaleString()}`}} },
                    scales:{ x:{type:'time',time:{unit:_timeUnit(perfRange)},grid:{color:PERF_GRID},ticks:{color:'#888',maxTicksLimit:6}},
                             y:{type:'logarithmic', grid:{color:PERF_GRID},ticks:{color:'#888',callback:v=>'$'+(v/1000).toFixed(0)+'k'}} } }
            });

            // ---- Chart performance relative (% — TWR pour le portefeuille) ----
            const benchBase0 = benchPts.length ? benchPts[0].y : 1;
            // TWR : neutralise les dépôts/retraits (sinon la perf est faussée)
            const portRet = (ts.portfolio_twr_pct||[]).map(p => ({x:p.date, y:p.value}));
            const benchRet = benchPts.map(p => ({x:p.x, y:(p.y/benchBase0-1)*100}));
            _destroyChart('relperf');
            _perfCharts.relperf = new Chart(document.getElementById('chart-relative-perf'), {
                type:'line',
                data:{ datasets:[
                    { label:'Portefeuille', data:portRet, borderColor:'#534AB7', borderWidth:1.5, tension:.3, pointRadius:0 },
                    { label:'S&P 500', data:benchRet, borderColor:'#1D9E75', borderWidth:1, tension:.3, pointRadius:0 },
                ]},
                options:{ responsive:true, maintainAspectRatio:false, interaction:{mode:'index',intersect:false},
                    plugins:{ legend:{labels:{color:'#aaa',boxWidth:12}}, tooltip:{callbacks:{label:c=>` ${c.dataset.label}: ${c.parsed.y>=0?'+':''}${c.parsed.y.toFixed(1)}%`}} },
                    scales:{ x:{type:'time',time:{unit:_timeUnit(perfRange)},grid:{color:PERF_GRID},ticks:{color:'#888',maxTicksLimit:5}},
                             y:{grid:{color:PERF_GRID},ticks:{color:'#888',callback:v=>v+'%'}} } }
            });

            // ---- Chart drawdown ----
            const ddPts = (data.drawdown||[]).map(p => ({x:p.date, y:p.value}));
            _destroyChart('ddv2');
            _perfCharts.ddv2 = new Chart(document.getElementById('chart-drawdown-v2'), {
                type:'line',
                data:{ datasets:[{ label:'Drawdown', data:ddPts, borderColor:'#D85A30', backgroundColor:'rgba(216,90,48,.12)', fill:true, borderWidth:1.5, tension:.3, pointRadius:0 }]},
                options:{ responsive:true, maintainAspectRatio:false,
                    plugins:{ legend:{display:false}, tooltip:{callbacks:{label:c=>` ${c.parsed.y.toFixed(1)}%`}} },
                    scales:{ x:{type:'time',time:{unit:_timeUnit(perfRange)},grid:{color:PERF_GRID},ticks:{color:'#888',maxTicksLimit:5}},
                             y:{max:0,grid:{color:PERF_GRID},ticks:{color:'#888',callback:v=>v+'%'}} } }
            });

            // ---- Chart P&L par position (horizontal) ----
            const positions = data.positions||[];
            const pnlSorted = [...positions].map(p=>({t:p.ticker, v:(p.unrealized_pnl||0)+(p.realized_pnl||0)}))
                .sort((a,b)=>Math.abs(b.v)-Math.abs(a.v));
            _destroyChart('pnlpos');
            _perfCharts.pnlpos = new Chart(document.getElementById('chart-pnl-by-pos'), {
                type:'bar',
                data:{ labels:pnlSorted.map(p=>p.t), datasets:[{ data:pnlSorted.map(p=>parseFloat(p.v.toFixed(2))),
                    backgroundColor:pnlSorted.map(p=>p.v>=0?'rgba(34,197,94,.75)':'rgba(239,68,68,.75)'), borderRadius:3 }]},
                options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false,
                    plugins:{ legend:{display:false}, tooltip:{callbacks:{label:c=>` $${c.parsed.x.toLocaleString()}`}} },
                    scales:{ x:{grid:{color:PERF_GRID},ticks:{color:'#888',callback:v=>'$'+(v/1000).toFixed(0)+'k'}},
                             y:{grid:{display:false},ticks:{color:'#ccc',font:{family:'IBM Plex Mono',size:11}}} } }
            });

            // ---- Heatmap rendements mensuels ----
            _renderHeatmap(data.monthly_returns||[]);

            // ---- Chart dividendes ----
            const divs = data.dividends_by_period||[];
            _destroyChart('divv2');
            _perfCharts.divv2 = new Chart(document.getElementById('chart-dividends-v2'), {
                type:'bar',
                data:{ labels:divs.map(d=>d.period), datasets:[{ data:divs.map(d=>d.amount),
                    backgroundColor:'rgba(29,158,117,.3)', borderColor:'#1D9E75', borderWidth:1, borderRadius:3 }]},
                options:{ responsive:true, maintainAspectRatio:false,
                    plugins:{ legend:{display:false}, tooltip:{callbacks:{label:c=>` $${c.parsed.y.toFixed(2)}`}} },
                    scales:{ x:{grid:{display:false},ticks:{color:'#888',maxTicksLimit:8}},
                             y:{grid:{color:PERF_GRID},ticks:{color:'#888',callback:v=>'$'+v}} } }
            });
        }

        function _timeUnit(range) {
            if (['1W','1M'].includes(range)) return 'day';
            if (['3M','6M','YTD','1Y'].includes(range)) return 'month';
            return 'year';
        }

        function _renderHeatmap(monthly) {
            const cont = document.getElementById('container-heatmap');
            if (!monthly.length) { cont.innerHTML = '<div class="empty-state">Données insuffisantes</div>'; return; }
            const MONTHS = ['J','F','M','A','M','J','J','A','S','O','N','D'];
            const byYear = {};
            let maxAbs = 0;
            monthly.forEach(m => { byYear[m.year] = byYear[m.year]||{}; byYear[m.year][m.month] = m.return_pct; maxAbs = Math.max(maxAbs, Math.abs(m.return_pct)); });
            const years = Object.keys(byYear).sort();
            const cell = (v) => {
                if (v == null) return `<td style="background:var(--bg-secondary);border-radius:3px;"></td>`;
                const op = Math.max(0.15, Math.min(0.85, Math.abs(v)/(maxAbs||1)));
                const col = v >= 0 ? `rgba(29,158,117,${op})` : `rgba(216,90,48,${op})`;
                return `<td style="background:${col};border-radius:3px;text-align:center;font-size:9px;color:#fff;padding:3px;">${v>=0?'+':''}${v.toFixed(0)}</td>`;
            };
            let html = '<table style="width:100%;border-collapse:separate;border-spacing:2px;font-size:10px;"><thead><tr><th></th>'
                + MONTHS.map(m=>`<th style="color:var(--text-muted);font-weight:400;">${m}</th>`).join('') + '</tr></thead><tbody>';
            years.forEach(y => {
                html += `<tr><td style="color:var(--text-muted);padding-right:6px;">${y}</td>`
                    + Array.from({length:12},(_,i)=>cell(byYear[y][i+1])).join('') + '</tr>';
            });
            html += '</tbody></table>';
            cont.innerHTML = html;
        }

