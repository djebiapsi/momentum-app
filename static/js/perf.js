        // =================================================================
        // PERFORMANCE DASHBOARD v3.0 (IBKR / Flex)
        // =================================================================

        function fmtPct(v, d=2) { return v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(d) + '%'; }
        function fmtUsd(v) {
            if (v == null) return '—';
            return (v >= 0 ? '+$' : '-$') + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:0, maximumFractionDigits:0});
        }
        function fmtDollar(v) {
            if (v == null) return '—';
            return '$' + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:0, maximumFractionDigits:0});
        }

        let perfRange = '1Y';
        const _perfCharts = {};
        const PERF_GRID = 'rgba(255,255,255,.05)';

        function _destroyChart(key) { if (_perfCharts[key]) { _perfCharts[key].destroy(); delete _perfCharts[key]; } }

        function _initPerfPills() {
            document.querySelectorAll('#page-perf .perf-pill').forEach(pill => {
                if (pill._bound) return;
                pill._bound = true;
                pill.addEventListener('click', () => {
                    document.querySelectorAll('#page-perf .perf-pill').forEach(p => p.classList.remove('active'));
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

            // ---- KPIs Performance ----
            const setKpi = (id, val, delta, deltaPos) => {
                const card = document.getElementById(id);
                if (!card) return;
                card.querySelector('.perf-kpi-value').textContent = val;
                const d = card.querySelector('.perf-kpi-delta');
                if (d) {
                    d.textContent = delta || '';
                    d.style.color = delta ? (deltaPos ? 'var(--accent-long)' : '#f87171') : '';
                }
            };

            setKpi('kpi-total-value',
                '$' + (k.total_value || 0).toLocaleString('en-US', {maximumFractionDigits:0}),
                fmtPct(k.total_return_pct) + ' période (TWR)',
                (k.total_return_pct || 0) >= 0);

            setKpi('kpi-cagr', fmtPct(k.cagr_pct),
                k.cagr_vs_bench_pct != null ? fmtPct(k.cagr_vs_bench_pct) + ' vs S&P' : '',
                (k.cagr_vs_bench_pct || 0) >= 0);

            document.querySelector('#kpi-sharpe .perf-kpi-value').textContent =
                k.sharpe != null ? k.sharpe.toFixed(2) : '—';

            const ddEl = document.querySelector('#kpi-max-drawdown .perf-kpi-value');
            ddEl.textContent = fmtPct(k.max_drawdown_pct);
            ddEl.style.color = '#f87171';

            const ugEl = document.querySelector('#kpi-unrealized-gains .perf-kpi-value');
            ugEl.textContent = fmtUsd(k.unrealized_pnl);
            ugEl.style.color = (k.unrealized_pnl || 0) >= 0 ? 'var(--accent-long)' : '#f87171';
            const ugD = document.querySelector('#kpi-unrealized-gains .perf-kpi-delta');
            if (ugD && k.realized_pnl != null) {
                ugD.textContent = `réalisé : ${fmtUsd(k.realized_pnl)}`;
                ugD.style.color = k.realized_pnl >= 0 ? 'var(--accent-long)' : '#f87171';
            }

            const cashEl = document.querySelector('#kpi-cash .perf-kpi-value');
            if (k.cash != null) {
                cashEl.textContent = fmtDollar(k.cash)
                    + (k.cash_pct != null ? ` · ${k.cash_pct}%` : '');
            } else {
                cashEl.textContent = '—';
            }

            document.querySelector('#kpi-vol .perf-kpi-value').textContent =
                k.vol_annual_pct != null ? k.vol_annual_pct.toFixed(1) + '%' : '—';

            document.querySelector('#kpi-dividends .perf-kpi-value').textContent =
                '$' + (k.dividends_total || 0).toLocaleString('en-US', {maximumFractionDigits:0});

            // ---- KPIs Composition ----
            setKpi('kpi-nb-positions',
                k.positions_count ?? '—',
                k.winners_count != null ? `${k.winners_count} gagnantes` : '',
                true);

            setKpi('kpi-concentration',
                k.top5_concentration_pct != null ? k.top5_concentration_pct + '%' : '—', '', null);

            if (k.best_position) {
                setKpi('kpi-best-pos', k.best_position.ticker,
                    fmtUsd(k.best_position.pnl), true);
            }
            if (k.worst_position) {
                setKpi('kpi-worst-pos', k.worst_position.ticker,
                    fmtUsd(k.worst_position.pnl), false);
            }

            // ---- Courbe d'évolution (base 100) ----
            const portPts = (data.timeseries?.portfolio || []).map(p => ({ x: p.date, y: p.value }));
            const benchPts = (data.timeseries?.benchmark || []).map(p => ({ x: p.date, y: p.value }));
            const unit = _timeUnit(perfRange);
            _destroyChart('evolution');
            _perfCharts.evolution = new Chart(document.getElementById('chart-evolution'), {
                type: 'line',
                data: { datasets: [
                    { label:'Portefeuille', data:portPts, borderColor:'#378ADD', backgroundColor:'rgba(55,138,221,.08)',
                      fill:true, borderWidth:1.5, tension:.3, pointRadius:0 },
                    { label:'S&P 500', data:benchPts, borderColor:'#1D9E75', borderDash:[2,3],
                      borderWidth:1, tension:.3, pointRadius:0, fill:false },
                ]},
                options: { responsive:true, maintainAspectRatio:false, interaction:{mode:'index',intersect:false},
                    plugins:{ legend:{labels:{color:'#aaa',boxWidth:12}},
                        tooltip:{callbacks:{label:c=>` ${c.dataset.label}: ${c.parsed.y.toFixed(1)}`}} },
                    scales:{ x:{type:'time',time:{unit},grid:{color:PERF_GRID},ticks:{color:'#888',maxTicksLimit:6}},
                             y:{grid:{color:PERF_GRID},ticks:{color:'#888',callback:v=>v.toFixed(0)},
                                title:{display:true,text:'Base 100',color:'#555',font:{size:9}}} } }
            });

            // ---- Performance relative ----
            const portRet = portPts.map(p => ({ x: p.x, y: p.y - 100 }));
            const benchRet = benchPts.map(p => ({ x: p.x, y: p.y - 100 }));
            _destroyChart('relperf');
            _perfCharts.relperf = new Chart(document.getElementById('chart-relative-perf'), {
                type:'line',
                data:{ datasets:[
                    { label:'Portefeuille', data:portRet, borderColor:'#534AB7', borderWidth:1.5, tension:.3, pointRadius:0 },
                    { label:'S&P 500', data:benchRet, borderColor:'#1D9E75', borderWidth:1, tension:.3, pointRadius:0 },
                ]},
                options:{ responsive:true, maintainAspectRatio:false, interaction:{mode:'index',intersect:false},
                    plugins:{ legend:{labels:{color:'#aaa',boxWidth:12}},
                        tooltip:{callbacks:{label:c=>` ${c.dataset.label}: ${c.parsed.y>=0?'+':''}${c.parsed.y.toFixed(1)}%`}} },
                    scales:{ x:{type:'time',time:{unit},grid:{color:PERF_GRID},ticks:{color:'#888',maxTicksLimit:5}},
                             y:{grid:{color:PERF_GRID},ticks:{color:'#888',callback:v=>v+'%'}} } }
            });

            // ---- Drawdown ----
            const ddPts = (data.drawdown || []).map(p => ({ x: p.date, y: p.value }));
            _destroyChart('ddv2');
            _perfCharts.ddv2 = new Chart(document.getElementById('chart-drawdown-v2'), {
                type:'line',
                data:{ datasets:[{ label:'Drawdown', data:ddPts, borderColor:'#D85A30',
                    backgroundColor:'rgba(216,90,48,.12)', fill:true, borderWidth:1.5, tension:.3, pointRadius:0 }]},
                options:{ responsive:true, maintainAspectRatio:false,
                    plugins:{ legend:{display:false},
                        tooltip:{callbacks:{label:c=>` ${c.parsed.y.toFixed(1)}%`}} },
                    scales:{ x:{type:'time',time:{unit},grid:{color:PERF_GRID},ticks:{color:'#888',maxTicksLimit:5}},
                             y:{max:0,grid:{color:PERF_GRID},ticks:{color:'#888',callback:v=>v+'%'}} } }
            });

            // ---- Donut composition ----
            const positions = data.positions || [];
            _renderComposition(positions);

            // ---- Heatmap : hebdomadaire si ≤ 1Y, mensuelle sinon ----
            const useWeekly = ['1W','1M','3M','6M','1Y','YTD'].includes(perfRange);
            document.getElementById('perf-heatmap-title').textContent =
                'Heatmap rendements ' + (useWeekly ? 'hebdomadaires' : 'mensuels');
            if (useWeekly) {
                _renderHeatmapWeekly(data.weekly_returns || []);
            } else {
                _renderHeatmap(data.monthly_returns || []);
            }

            // ---- P&L par position ----
            const pnlSorted = [...positions]
                .map(p => ({ t: p.ticker, v: (p.unrealized_pnl || 0) }))
                .sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
            _destroyChart('pnlpos');
            _perfCharts.pnlpos = new Chart(document.getElementById('chart-pnl-by-pos'), {
                type:'bar',
                data:{ labels:pnlSorted.map(p=>p.t), datasets:[{
                    data:pnlSorted.map(p=>parseFloat(p.v.toFixed(2))),
                    backgroundColor:pnlSorted.map(p=>p.v>=0?'rgba(34,197,94,.75)':'rgba(239,68,68,.75)'),
                    borderRadius:3
                }]},
                options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false,
                    plugins:{ legend:{display:false},
                        tooltip:{callbacks:{label:c=>` $${c.parsed.x.toLocaleString()}`}} },
                    scales:{ x:{grid:{color:PERF_GRID},ticks:{color:'#888',callback:v=>'$'+(v>=0?'':'-')+Math.abs(v/1000).toFixed(0)+'k'}},
                             y:{grid:{display:false},ticks:{color:'#ccc',font:{family:'IBM Plex Mono',size:11}}} } }
            });

            // ---- Tableau positions ----
            _renderPositionsTable(positions);
        }

        // --- Donut composition -------------------------------------------
        function _renderComposition(positions) {
            if (!positions.length) return;
            const sorted = [...positions].sort((a,b)=>(b.allocation_pct||0)-(a.allocation_pct||0));
            // Regrouper les petites positions (<2%) en "Autres"
            const THRESHOLD = 2;
            const main = sorted.filter(p => (p.allocation_pct||0) >= THRESHOLD);
            const others = sorted.filter(p => (p.allocation_pct||0) < THRESHOLD);
            const labels = main.map(p => p.ticker);
            const values = main.map(p => p.allocation_pct || 0);
            if (others.length) {
                labels.push('Autres (' + others.length + ')');
                values.push(others.reduce((s,p)=>s+(p.allocation_pct||0),0));
            }
            const PALETTE = ['#7C5CFF','#378ADD','#1D9E75','#E0A030','#D85A30',
                             '#534AB7','#3BBFA3','#E87040','#9B59B6','#2ECC71',
                             '#E74C3C','#F39C12','#1ABC9C','#3498DB','#8E44AD'];
            _destroyChart('composition');
            _perfCharts.composition = new Chart(document.getElementById('chart-composition'), {
                type:'doughnut',
                data:{ labels, datasets:[{
                    data:values,
                    backgroundColor:labels.map((_,i)=>PALETTE[i%PALETTE.length]),
                    borderColor:'#111', borderWidth:2, hoverOffset:6
                }]},
                options:{ responsive:true, maintainAspectRatio:false, cutout:'60%',
                    plugins:{ legend:{position:'right',labels:{color:'#ccc',boxWidth:12,font:{family:'IBM Plex Mono',size:11},
                        generateLabels:chart=>{
                            const ds=chart.data;
                            return ds.labels.map((lbl,i)=>({
                                text:`${lbl} ${ds.datasets[0].data[i].toFixed(1)}%`,
                                fillStyle:ds.datasets[0].backgroundColor[i],
                                strokeStyle:'#111', lineWidth:1, hidden:false, index:i
                            }));
                        }}},
                        tooltip:{callbacks:{label:c=>` ${c.label}: ${c.parsed.toFixed(1)}%`}} } }
            });
        }

        // --- Tableau positions -------------------------------------------
        function _renderPositionsTable(positions) {
            const sorted = [...positions].sort((a,b)=>(b.allocation_pct||0)-(a.allocation_pct||0));
            const body = document.getElementById('perf-positions-body');
            if (!body) return;
            const pnlColor = v => v == null ? '' : v >= 0 ? 'color:var(--accent-long)' : 'color:var(--accent-short)';
            body.innerHTML = sorted.map(p => {
                const mv = p.market_value != null ? '$'+(Math.abs(p.market_value)).toLocaleString('en-US',{maximumFractionDigits:0}) : '—';
                const pnl = p.unrealized_pnl != null ? fmtUsd(p.unrealized_pnl) : '—';
                const ret = p.return_pct != null ? fmtPct(p.return_pct) : '—';
                const cost = p.avg_cost != null ? '$'+p.avg_cost.toFixed(2) : '—';
                const price = p.current_price != null ? '$'+p.current_price.toFixed(2)
                              : (p.market_value && p.qty ? '$'+(p.market_value/p.qty).toFixed(2) : '—');
                return `<tr style="border-top:1px solid var(--border);text-align:right;">
                    <td style="text-align:left;padding:5px 10px;font-weight:600;color:var(--text-primary);">${p.ticker}</td>
                    <td style="padding:5px 8px;color:var(--text-muted);">${p.qty != null ? p.qty.toFixed(2) : '—'}</td>
                    <td style="padding:5px 8px;color:var(--text-muted);">${cost}</td>
                    <td style="padding:5px 8px;color:var(--text-primary);">${price}</td>
                    <td style="padding:5px 8px;color:var(--text-primary);">${mv}</td>
                    <td style="padding:5px 8px;color:var(--text-primary);">${(p.allocation_pct||0).toFixed(1)}%</td>
                    <td style="padding:5px 8px;${pnlColor(p.unrealized_pnl)}">${pnl}</td>
                    <td style="padding:5px 8px;${pnlColor(p.return_pct)}">${ret}</td>
                </tr>`;
            }).join('');
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
