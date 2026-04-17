// OI Tracker Dashboard JavaScript
// NOTE: All state is server-controlled. Frontend is a pure display layer.

// ===== Design token bridge — read palette from CSS custom properties =====
// Keeps chart colours in sync with static/styles/tokens.css. Re-evaluated
// each time getComputedStyle is called, so a future theme switch picks up
// the change without rebuilding charts. Fallbacks match Variant A defaults.
function token(name, fallback) {
  if (typeof document === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const CHART_COLORS = {
  up:    token('--color-accent-up',    '#60e0a8'),
  down:  token('--color-accent-dn',    '#ff7a82'),
  warn:  token('--color-accent-warn',  '#f5b963'),
  info:  token('--color-accent-info',  '#7cb7ff'),
  text:  token('--color-text-primary', '#eef1f8'),
  muted: token('--color-text-muted',   '#6b7289'),
  grid:  token('--color-border',       '#1e2430'),
  bg:    token('--color-bg-raised',    '#131721'),
};

const socket = io();
let oiChart = null;
let otmChart = null;
let itmChart = null;
let cumulativeChart = null;
let futuresBasisChart = null;
let fullChartHistory = [];
let chartPeriod = 'all';

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initChart();
    initCumulativeChart();
    initOTMChart();
    initITMChart();
    initFuturesBasisChart();
    initToggles();
    setupSocketListeners();

    setTimeout(fetchMarketStatus, 500);
    setTimeout(fetchLatestData, 1000);
    setTimeout(checkKiteStatus, 1500);
    setTimeout(fetchFullHistory, 2000);
    setTimeout(fetchRegime, 2500);

    setInterval(fetchMarketStatus, 60000);
    setInterval(checkKiteStatus, 300000); // Check Kite auth every 5 min
});

// Initialize toggle switches
function initToggles() {
    const autoToggle = document.getElementById('auto-toggle');

    // Add event listener for auto-fetch toggle (server-controlled, no localStorage)
    if (autoToggle) {
        autoToggle.addEventListener('change', (e) => {
            socket.emit('set_force_fetch', { enabled: e.target.checked });
        });
    }
}

// Switch table tabs
function switchTableTab(btn, tabName) {
    document.querySelectorAll('.table-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.table-tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    const panel = document.getElementById('tab-' + tabName);
    if (panel) panel.classList.add('active');
}

// Chart period toggle
function setChartPeriod(period) {
    chartPeriod = period;
    document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
    document.querySelector(`.period-btn[onclick*="${period}"]`)?.classList.add('active');
    applyChartPeriod();
}

function applyChartPeriod() {
    if (!fullChartHistory.length || !oiChart) return;
    let filtered = fullChartHistory;
    if (chartPeriod !== 'all') {
        const mins = chartPeriod === '30m' ? 30 : 60;
        const cutoff = Date.now() - mins * 60 * 1000;
        filtered = fullChartHistory.filter(h => new Date(h.timestamp).getTime() > cutoff);
    }
    if (!filtered.length) filtered = fullChartHistory.slice(-3);

    const labels = filtered.map(item =>
        new Date(item.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
    );

    // Main OI chart
    oiChart.data.labels = labels;
    oiChart.data.datasets[0].data = filtered.map(item => item.call_oi_change);
    oiChart.data.datasets[1].data = filtered.map(item => item.put_oi_change);
    oiChart.update('none');

    // OTM chart
    if (otmChart) {
        otmChart.data.labels = labels;
        otmChart.data.datasets[0].data = filtered.map(item => item.otm_put_force || 0);
        otmChart.data.datasets[1].data = filtered.map(item => item.otm_call_force || 0);
        otmChart.update('none');
    }

    // ITM chart
    if (itmChart) {
        itmChart.data.labels = labels;
        itmChart.data.datasets[0].data = filtered.map(item => item.itm_put_force || 0);
        itmChart.data.datasets[1].data = filtered.map(item => item.itm_call_force || 0);
        itmChart.update('none');
    }

    // OI Momentum chart (per-interval rate of change)
    if (cumulativeChart) {
        cumulativeChart.data.labels = labels;
        const callCumulative = filtered.map(item => item.cumulative_call_oi_change || 0);
        const putCumulative = filtered.map(item => item.cumulative_put_oi_change || 0);
        // Compute interval-to-interval delta (momentum/acceleration)
        const callMomentum = callCumulative.map((v, i) => i === 0 ? 0 : v - callCumulative[i - 1]);
        const putMomentum = putCumulative.map((v, i) => i === 0 ? 0 : v - putCumulative[i - 1]);
        cumulativeChart.data.datasets[0].data = callMomentum;
        cumulativeChart.data.datasets[1].data = putMomentum;
        cumulativeChart.update('none');
    }

    // Futures Basis chart
    if (futuresBasisChart) {
        futuresBasisChart.data.labels = labels;
        futuresBasisChart.data.datasets[0].data = filtered.map(item => item.futures_basis || 0);
        futuresBasisChart.update('none');
    }

    // Reset zoom on all charts so new data is always visible
    oiChart?.resetZoom();
    otmChart?.resetZoom();
    itmChart?.resetZoom();
    cumulativeChart?.resetZoom();
    futuresBasisChart?.resetZoom();
}

// Update score gauge visualization
function updateScoreGauge(score) {
    const marker = document.getElementById('score-gauge-marker');
    if (marker && score != null) {
        // Score is -100 to +100, normalize to 0-100%
        const normalized = Math.max(0, Math.min(100, (score + 100) / 2));
        marker.style.left = `${normalized}%`;
    }
}

// Initialize Chart.js
function initChart() {
    const ctx = document.getElementById('oi-chart').getContext('2d');

    // Gradient fills
    const bearGrad = ctx.createLinearGradient(0, 0, 0, 360);
    bearGrad.addColorStop(0, CHART_COLORS.down + '40');  // ~25% alpha
    bearGrad.addColorStop(1, CHART_COLORS.down + '05');  // ~2% alpha
    const bullGrad = ctx.createLinearGradient(0, 0, 0, 360);
    bullGrad.addColorStop(0, CHART_COLORS.up + '40');    // ~25% alpha
    bullGrad.addColorStop(1, CHART_COLORS.up + '05');    // ~2% alpha

    oiChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Call OI Change',
                    data: [],
                    borderColor: CHART_COLORS.down,
                    backgroundColor: bearGrad,
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointBackgroundColor: CHART_COLORS.down
                },
                {
                    label: 'Put OI Change',
                    data: [],
                    borderColor: CHART_COLORS.up,
                    backgroundColor: bullGrad,
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointBackgroundColor: CHART_COLORS.up
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            plugins: {
                legend: {
                    position: 'top',
                    align: 'end',
                    labels: {
                        color: CHART_COLORS.muted,
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 20,
                        font: { size: 12, family: 'Inter' }
                    }
                },
                tooltip: {
                    backgroundColor: CHART_COLORS.bg,
                    titleColor: CHART_COLORS.text,
                    bodyColor: CHART_COLORS.muted,
                    borderColor: CHART_COLORS.grid,
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    titleFont: { size: 13, family: 'Inter' },
                    bodyFont: { size: 12, family: 'Inter' }
                },
                zoom: {
                    pan: { enabled: true, mode: 'x' },
                    zoom: {
                        wheel: { enabled: true },
                        pinch: { enabled: true },
                        mode: 'x'
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: CHART_COLORS.grid + '0d', drawBorder: false },  // ~5% alpha
                    ticks: { color: CHART_COLORS.muted, font: { size: 11, family: 'Inter' }, maxRotation: 0 }
                },
                y: {
                    grid: {
                        color: (ctx) => ctx.tick.value === 0 ? CHART_COLORS.muted + '33' : CHART_COLORS.grid + '0d',
                        lineWidth: (ctx) => ctx.tick.value === 0 ? 1.5 : 1,
                        drawBorder: false
                    },
                    ticks: {
                        color: CHART_COLORS.muted,
                        font: { size: 11, family: 'Inter' },
                        callback: value => formatCompact(value)
                    }
                }
            }
        }
    });
}

// Initialize OI Momentum Chart (per-interval rate of change)
function initCumulativeChart() {
    const canvas = document.getElementById('cumulative-oi-chart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');

    cumulativeChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Call OI Momentum',
                    data: [],
                    backgroundColor: CHART_COLORS.down + 'b3',  // ~70% alpha
                    borderColor: CHART_COLORS.down,
                    borderWidth: 1,
                    borderRadius: 2
                },
                {
                    label: 'Put OI Momentum',
                    data: [],
                    backgroundColor: CHART_COLORS.up + 'b3',    // ~70% alpha
                    borderColor: CHART_COLORS.up,
                    borderWidth: 1,
                    borderRadius: 2
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: {
                    position: 'top',
                    align: 'end',
                    labels: { color: CHART_COLORS.muted, usePointStyle: true, pointStyle: 'circle', padding: 20, font: { size: 12, family: 'Inter' } }
                },
                tooltip: {
                    backgroundColor: CHART_COLORS.bg,
                    titleColor: CHART_COLORS.text,
                    bodyColor: CHART_COLORS.muted,
                    borderColor: CHART_COLORS.grid,
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    titleFont: { size: 13, family: 'Inter' },
                    bodyFont: { size: 12, family: 'Inter' }
                },
                zoom: {
                    pan: { enabled: true, mode: 'x' },
                    zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' }
                }
            },
            scales: {
                x: {
                    grid: { color: CHART_COLORS.grid + '0d', drawBorder: false },  // ~5% alpha
                    ticks: { color: CHART_COLORS.muted, font: { size: 11, family: 'Inter' }, maxRotation: 0 }
                },
                y: {
                    grid: {
                        color: (ctx) => ctx.tick.value === 0 ? CHART_COLORS.muted + '33' : CHART_COLORS.grid + '0d',
                        lineWidth: (ctx) => ctx.tick.value === 0 ? 1.5 : 1,
                        drawBorder: false
                    },
                    ticks: {
                        color: CHART_COLORS.muted,
                        font: { size: 11, family: 'Inter' },
                        callback: value => formatCompact(value)
                    }
                }
            }
        }
    });
}

// Initialize OTM Chart (Put vs Call OI trend)
function initOTMChart() {
    const canvas = document.getElementById('otm-chart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const otmBullGrad = ctx.createLinearGradient(0, 0, 0, 260);
    otmBullGrad.addColorStop(0, CHART_COLORS.up + '33');    // ~20% alpha
    otmBullGrad.addColorStop(1, CHART_COLORS.up + '05');    // ~2% alpha
    const otmBearGrad = ctx.createLinearGradient(0, 0, 0, 260);
    otmBearGrad.addColorStop(0, CHART_COLORS.down + '33');  // ~20% alpha
    otmBearGrad.addColorStop(1, CHART_COLORS.down + '05'); // ~2% alpha

    otmChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'OTM Put Force',
                    data: [],
                    borderColor: CHART_COLORS.up,
                    backgroundColor: otmBullGrad,
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4
                },
                {
                    label: 'OTM Call Force',
                    data: [],
                    borderColor: CHART_COLORS.down,
                    backgroundColor: otmBearGrad,
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: { position: 'top', align: 'end', labels: { color: CHART_COLORS.muted, usePointStyle: true, font: { size: 11 } } },
                zoom: {
                    pan: { enabled: true, mode: 'x' },
                    zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' }
                }
            },
            scales: {
                x: { grid: { color: CHART_COLORS.grid + '0d' }, ticks: { color: CHART_COLORS.muted, font: { size: 10 }, maxRotation: 0 } },
                y: { grid: { color: CHART_COLORS.grid + '0d' }, ticks: { color: CHART_COLORS.muted, font: { size: 10 }, callback: v => formatCompact(v) } }
            }
        }
    });
}

// Initialize ITM Chart (Put vs Call OI trend)
function initITMChart() {
    const canvas = document.getElementById('itm-chart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const itmBullGrad = ctx.createLinearGradient(0, 0, 0, 260);
    itmBullGrad.addColorStop(0, CHART_COLORS.up + '33');    // ~20% alpha
    itmBullGrad.addColorStop(1, CHART_COLORS.up + '05');    // ~2% alpha
    const itmBearGrad = ctx.createLinearGradient(0, 0, 0, 260);
    itmBearGrad.addColorStop(0, CHART_COLORS.down + '33');  // ~20% alpha
    itmBearGrad.addColorStop(1, CHART_COLORS.down + '05'); // ~2% alpha

    itmChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'ITM Put Force',
                    data: [],
                    borderColor: CHART_COLORS.up,
                    backgroundColor: itmBullGrad,
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4
                },
                {
                    label: 'ITM Call Force',
                    data: [],
                    borderColor: CHART_COLORS.down,
                    backgroundColor: itmBearGrad,
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: { position: 'top', align: 'end', labels: { color: CHART_COLORS.muted, usePointStyle: true, font: { size: 11 } } },
                zoom: {
                    pan: { enabled: true, mode: 'x' },
                    zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' }
                }
            },
            scales: {
                x: { grid: { color: CHART_COLORS.grid + '0d' }, ticks: { color: CHART_COLORS.muted, font: { size: 10 }, maxRotation: 0 } },
                y: { grid: { color: CHART_COLORS.grid + '0d' }, ticks: { color: CHART_COLORS.muted, font: { size: 10 }, callback: v => formatCompact(v) } }
            }
        }
    });
}

// Initialize Futures Basis Chart
function initFuturesBasisChart() {
    const canvas = document.getElementById('futures-basis-chart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const basisGrad = ctx.createLinearGradient(0, 0, 0, 200);
    basisGrad.addColorStop(0, '#6366f1' + '40');  // indigo series — TODO: token
    basisGrad.addColorStop(1, '#6366f1' + '05');  // indigo series — TODO: token

    futuresBasisChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Futures Basis',
                    data: [],
                    borderColor: '#6366f1',  // indigo series — TODO: token
                    backgroundColor: basisGrad,
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointBackgroundColor: '#6366f1'  // indigo series — TODO: token
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: { position: 'top', align: 'end', labels: { color: CHART_COLORS.muted, usePointStyle: true, font: { size: 11 } } },
                tooltip: {
                    backgroundColor: CHART_COLORS.bg,
                    titleColor: CHART_COLORS.text,
                    bodyColor: CHART_COLORS.muted,
                    borderColor: CHART_COLORS.grid,
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    callbacks: {
                        label: function(context) {
                            const val = context.parsed.y;
                            return 'Basis: ' + (val > 0 ? '+' : '') + val.toFixed(2);
                        }
                    }
                },
                zoom: {
                    pan: { enabled: true, mode: 'x' },
                    zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' }
                }
            },
            scales: {
                x: { grid: { color: CHART_COLORS.grid + '0d' }, ticks: { color: CHART_COLORS.muted, font: { size: 10 }, maxRotation: 0 } },
                y: {
                    grid: {
                        color: (ctx) => ctx.tick.value === 0 ? CHART_COLORS.muted + '33' : CHART_COLORS.grid + '0d',
                        lineWidth: (ctx) => ctx.tick.value === 0 ? 1.5 : 1
                    },
                    ticks: { color: CHART_COLORS.muted, font: { size: 10 }, callback: v => v.toFixed(1) }
                }
            }
        }
    });
}

// Socket listeners
function setupSocketListeners() {
    socket.on('connect', () => {
        console.log('Connected');
        updateConnectionStatus(true);
    });

    socket.on('disconnect', () => {
        console.log('Disconnected');
        updateConnectionStatus(false);
    });

    socket.on('oi_update', data => {
        console.log('OI update received');
        // Single source of truth - update everything from server data
        updateDashboard(data);
        // Replace chart with full history from server
        if (data.chart_history) {
            updateChartFromServer(data.chart_history);
        }
    });

    socket.on('pnl_update', data => {
        updateLivePnl(data);
    });
}

// Fetch functions
function fetchMarketStatus() {
    fetch('/api/market-status')
        .then(r => r.ok ? r.json() : Promise.reject('API error'))
        .then(updateMarketStatus)
        .catch(e => console.error('Market status error:', e));
}

function fetchLatestData() {
    fetch('/api/latest')
        .then(r => r.ok ? r.json() : Promise.reject('No data'))
        .then(data => {
            console.log('Latest data:', data);
            updateDashboard(data);
            // Server sends chart_history with latest data
            if (data.chart_history) {
                updateChartFromServer(data.chart_history);
            }
        })
        .catch(e => console.log('Waiting for data:', e));
}

function fetchFullHistory() {
    fetch('/api/history')
        .then(r => r.ok ? r.json() : Promise.reject('No history'))
        .then(history => {
            if (history?.length) {
                fullChartHistory = history;
                applyChartPeriod();
                updateZoneChartsFromHistory(history);
            }
        })
        .catch(() => {});
}

// Update functions
function updateMarketStatus(data) {
    const badge = document.getElementById('market-badge');
    const text = document.getElementById('market-text');

    if (badge && text) {
        text.textContent = data.is_open ? 'Market Open' : 'Market Closed';
        badge.classList.remove('badge-open', 'badge-closed');
        badge.classList.add(data.is_open ? 'badge-open' : 'badge-closed');
    }
}

function updateDashboard(data) {
    // Update timestamp
    if (data.timestamp) {
        const time = new Date(data.timestamp).toLocaleTimeString('en-IN', {
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });
        setText('last-update-time', time);
    }

    // Update verdict card
    const verdictCard = document.getElementById('verdict-card');
    const verdictText = document.getElementById('verdict-text');

    if (verdictText) {
        verdictText.textContent = data.verdict || 'Loading...';
    }

    if (verdictCard) {
        verdictCard.classList.remove('bullish', 'bearish', 'neutral');
        if (data.verdict?.toLowerCase().includes('bull')) {
            verdictCard.classList.add('bullish');
        } else if (data.verdict?.toLowerCase().includes('bear')) {
            verdictCard.classList.add('bearish');
        } else {
            verdictCard.classList.add('neutral');
        }
    }

    // Update scores and gauge
    updateScore('combined-score', data.combined_score);
    updateScoreGauge(data.combined_score);

    // Smoothed (EMA) score
    updateScore('smoothed-score', data.smoothed_score, true);

    // Update weight displays and individual scores for new zone structure
    if (data.weights) {
        setText('below-spot-weight', Math.round((data.weights.below_spot || 0.5) * 100) + '%');
        setText('above-spot-weight', Math.round((data.weights.above_spot || 0.5) * 100) + '%');
        setText('momentum-weight', Math.round(data.weights.momentum * 100) + '%');

        // Show/hide momentum breakdown based on weight
        const momentumBreakdown = document.getElementById('momentum-breakdown');
        if (momentumBreakdown) {
            momentumBreakdown.style.display = data.weights.momentum > 0 ? 'flex' : 'none';
        }
    }

    // Update zone scores
    updateScore('below-spot-score-display', data.below_spot_score, true);
    updateScore('above-spot-score-display', data.above_spot_score, true);
    updateScore('momentum-score-display', data.momentum_score, true);

    // Update confirmation indicator
    const confirmIcon = document.getElementById('confirmation-icon');
    const confirmText = document.getElementById('confirmation-text');
    const confirmIndicator = document.getElementById('confirmation-indicator');

    if (confirmIndicator && data.confirmation_status) {
        // Reset classes
        confirmIndicator.className = 'confirmation-indicator';

        if (data.confirmation_status === 'CONFIRMED') {
            confirmIcon.textContent = '[OK]';
            confirmIndicator.classList.add('confirmed');
        } else if (data.confirmation_status === 'CONFLICT') {
            confirmIcon.textContent = '[!]';
            confirmIndicator.classList.add('conflict');
        } else if (data.confirmation_status === 'REVERSAL_ALERT') {
            confirmIcon.textContent = '[!!]';
            confirmIndicator.classList.add('reversal-alert');
        } else {
            confirmIcon.textContent = '[~]';
            confirmIndicator.classList.add('neutral');
        }

        confirmText.textContent = data.confirmation_message || '--';
    }

    // Update metrics
    setText('spot-price', formatNumber(data.spot_price));
    setText('atm-strike', formatNumber(data.atm_strike));
    setText('expiry-date', data.expiry_date || '--');
    setText('pcr-value', data.pcr ?? '--');

    // Update momentum
    const momentumElem = document.getElementById('momentum-value');
    if (momentumElem && data.price_change_pct !== undefined) {
        const pct = data.price_change_pct;
        const arrow = pct > 0 ? '↑' : pct < 0 ? '↓' : '→';
        const color = pct > 0 ? CHART_COLORS.up : pct < 0 ? CHART_COLORS.down : CHART_COLORS.muted;
        momentumElem.textContent = `${arrow} ${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
        momentumElem.style.color = color;
    } else if (momentumElem) {
        momentumElem.textContent = '--';
        momentumElem.style.color = CHART_COLORS.muted;
    }

    // Update Market Trend
    const trendElem = document.getElementById('market-trend');
    if (trendElem && data.market_trend) {
        const trend = data.market_trend;
        trendElem.textContent = trend.display || '--';
        trendElem.className = 'metric-value trend-indicator';
        trendElem.classList.add(`trend-${trend.trend}`, `strength-${trend.strength}`);
        trendElem.title = trend.description || '';
    } else if (trendElem) {
        trendElem.textContent = '--';
        trendElem.className = 'metric-value trend-indicator';
        trendElem.title = '';
    }

    // Update volume metrics
    setText('volume-pcr', data.volume_pcr ?? '--');

    // Display average conviction with color coding
    const avgConviction = data.avg_call_conviction || 0;
    const convictionElem = document.getElementById('avg-conviction');
    if (convictionElem) {
        convictionElem.textContent = avgConviction > 0 ? avgConviction.toFixed(2) + 'x' : '--';
        convictionElem.style.color = avgConviction > 1.2 ? CHART_COLORS.up :
                                     avgConviction < 0.8 ? CHART_COLORS.down : CHART_COLORS.muted;
    }

    // Update Zone Force comparison
    const belowSpot = data.below_spot || {};
    const aboveSpot = data.above_spot || {};

    // Calculate max for bar widths
    const maxForce = Math.max(
        Math.abs(belowSpot.net_force || 0),
        Math.abs(aboveSpot.net_force || 0),
        1
    );

    // Below Spot (Support)
    setText('below-spot-net', formatNumber(belowSpot.net_force));
    setWidth('below-spot-bar', Math.abs(belowSpot.net_force || 0) / maxForce * 100);
    updateChange('below-spot-change', belowSpot.score, false);

    // Above Spot (Resistance)
    setText('above-spot-net', formatNumber(aboveSpot.net_force));
    setWidth('above-spot-bar', Math.abs(aboveSpot.net_force || 0) / maxForce * 100);
    updateChange('above-spot-change', aboveSpot.score, true);

    // Net Force
    const netElem = document.getElementById('net-oi-change');
    if (netElem) {
        netElem.textContent = formatSigned(data.net_oi_change);
        netElem.classList.remove('positive', 'negative');
        netElem.classList.add(data.net_oi_change >= 0 ? 'positive' : 'negative');
    }

    // Tug-of-War bar
    const bearForce = Math.abs(aboveSpot.net_force || 0);
    const bullForce = Math.abs(belowSpot.net_force || 0);
    const totalForce = bearForce + bullForce || 1;
    setWidth('tow-bear-fill', (bearForce / totalForce) * 100);
    setWidth('tow-bull-fill', (bullForce / totalForce) * 100);
    setText('tow-bear-value', formatNumber(bearForce));
    setText('tow-bull-value', formatNumber(bullForce));

    // Update Zone tables with new strike-level data
    updateZoneTable('below-spot-tbody', belowSpot.strikes || []);
    updateZoneTable('above-spot-tbody', aboveSpot.strikes || []);

    // Update totals for Below Spot
    setText('below-spot-total-bullish', formatNumber(belowSpot.total_bullish_force));
    setText('below-spot-total-bearish', formatNumber(belowSpot.total_bearish_force));
    setText('below-spot-total-net', formatSigned(belowSpot.net_force));

    // Update totals for Above Spot
    setText('above-spot-total-bullish', formatNumber(aboveSpot.total_bullish_force));
    setText('above-spot-total-bearish', formatNumber(aboveSpot.total_bearish_force));
    setText('above-spot-total-net', formatSigned(aboveSpot.net_force));

    // Update new metrics: Max Pain, IV Skew
    setText('max-pain', data.max_pain ? formatNumber(data.max_pain) : '--');

    const ivSkewElem = document.getElementById('iv-skew');
    if (ivSkewElem && data.iv_skew !== undefined) {
        const skew = data.iv_skew;
        ivSkewElem.textContent = (skew > 0 ? '+' : '') + skew.toFixed(2) + '%';
        ivSkewElem.style.color = skew > 2 ? CHART_COLORS.down : skew < -2 ? CHART_COLORS.up : CHART_COLORS.muted;
    }

    // PCR Trend arrow
    const pcrArrow = document.getElementById('pcr-trend-arrow');
    if (pcrArrow && data.pcr_trend) {
        const trend = data.pcr_trend.trend;
        if (trend === 'rising') { pcrArrow.textContent = ' \u2191'; pcrArrow.style.color = 'var(--bullish)'; }
        else if (trend === 'falling') { pcrArrow.textContent = ' \u2193'; pcrArrow.style.color = 'var(--bearish)'; }
        else { pcrArrow.textContent = ' \u2192'; pcrArrow.style.color = 'var(--text-muted)'; }
    }

    // Max Pain Drift
    const driftElem = document.getElementById('max-pain-drift');
    if (driftElem && data.max_pain_drift != null) {
        const drift = data.max_pain_drift.drift_points;
        if (drift !== 0) {
            const arrow = drift > 0 ? '\u2191' : '\u2193';
            driftElem.textContent = `${arrow} ${drift > 0 ? '+' : ''}${drift} pts`;
            driftElem.style.color = drift > 0 ? 'var(--bullish)' : 'var(--bearish)';
        } else {
            driftElem.textContent = '\u2192 0 pts';
            driftElem.style.color = 'var(--text-muted)';
        }
    }

    // 2-Candle Confirmation badge
    const twoCandleBadge = document.getElementById('two-candle-badge');
    if (twoCandleBadge) {
        twoCandleBadge.style.display = data.two_candle_confirmed ? 'inline-flex' : 'none';
    }

    // Primary S/R (with OI context)
    if (data.primary_sr) {
        if (data.primary_sr.support) {
            const s = data.primary_sr.support;
            setText('primary-support', `${formatNumber(s.strike)} (${formatCompact(s.put_oi)})`);
        } else {
            setText('primary-support', '--');
        }
        if (data.primary_sr.resistance) {
            const r = data.primary_sr.resistance;
            setText('primary-resistance', `${formatNumber(r.strike)} (${formatCompact(r.call_oi)})`);
        } else {
            setText('primary-resistance', '--');
        }
    }

    // OI Flow Summary
    if (data.oi_flow_summary) updateFlowCard(data.oi_flow_summary);

    // Update Futures Data Card
    updateFuturesData(data);

    // Update V-Shape Recovery Alert
    updateVShapeAlert(data);

    // Update Win Rate Card
    updateWinRate(data.trade_stats);

    // Update Trap Warning Card
    updateTrapWarning(data.trap_warning);

    // Update Self-Learning Status
    updateLearningStatus(data.self_learning);

    // Update OTM/ITM tables
    updateOTMITMTables(data);

    // Update Strength Analysis
    updateStrengthAnalysis(data.strength_analysis);

    // Update zone charts
    updateZoneCharts(data);

    // Update trade dock after all trade cards have been updated
    updateTradeDock();
}

function updateOTMITMTables(data) {
    // OTM Puts table (below spot - support)
    const otmPutsTbody = document.getElementById('otm-puts-tbody');
    if (otmPutsTbody && data.otm_puts?.strikes) {
        otmPutsTbody.innerHTML = data.otm_puts.strikes.map(s => `
            <tr>
                <td>${formatNumber(s.strike)}</td>
                <td>${formatNumber(s.put_oi)}</td>
                <td class="positive-change">${formatSigned(s.put_oi_change)}</td>
                <td class="positive-change">${formatNumber(s.put_force)}</td>
                <td>${flowBadge(s.flow_type)}</td>
            </tr>
        `).join('');
    }

    // OTM Puts totals
    if (data.otm_puts) {
        setText('otm-puts-total-oi', formatNumber(data.otm_puts.total_oi));
        setText('otm-puts-total-change', formatSigned(data.otm_puts.total_oi_change));
        setText('otm-puts-total-force', formatNumber(data.otm_puts.total_force));
    }

    // ITM Calls table (below spot - trapped longs)
    const itmCallsTbody = document.getElementById('itm-calls-tbody');
    if (itmCallsTbody && data.itm_calls?.strikes) {
        itmCallsTbody.innerHTML = data.itm_calls.strikes.map(s => `
            <tr>
                <td>${formatNumber(s.strike)}</td>
                <td>${formatNumber(s.call_oi)}</td>
                <td class="negative-change">${formatSigned(s.call_oi_change)}</td>
                <td class="negative-change">${formatNumber(s.call_force)}</td>
                <td>${flowBadge(s.flow_type)}</td>
            </tr>
        `).join('');
    }

    // ITM Calls totals
    if (data.itm_calls) {
        setText('itm-calls-total-oi', formatNumber(data.itm_calls.total_oi));
        setText('itm-calls-total-change', formatSigned(data.itm_calls.total_oi_change));
        setText('itm-calls-total-force', formatNumber(data.itm_calls.total_force));
    }

    // OTM Calls table (above spot - resistance)
    const otmCallsTbody = document.getElementById('otm-calls-tbody');
    if (otmCallsTbody && data.otm_calls?.strikes) {
        otmCallsTbody.innerHTML = data.otm_calls.strikes.map(s => `
            <tr>
                <td>${formatNumber(s.strike)}</td>
                <td>${formatNumber(s.call_oi)}</td>
                <td class="negative-change">${formatSigned(s.call_oi_change)}</td>
                <td class="negative-change">${formatNumber(s.call_force)}</td>
                <td>${flowBadge(s.flow_type)}</td>
            </tr>
        `).join('');
    }

    // OTM Calls totals
    if (data.otm_calls) {
        setText('otm-calls-total-oi', formatNumber(data.otm_calls.total_oi));
        setText('otm-calls-total-change', formatSigned(data.otm_calls.total_oi_change));
        setText('otm-calls-total-force', formatNumber(data.otm_calls.total_force));
    }

    // ITM Puts table (above spot - trapped shorts)
    const itmPutsTbody = document.getElementById('itm-puts-tbody');
    if (itmPutsTbody && data.itm_puts?.strikes) {
        itmPutsTbody.innerHTML = data.itm_puts.strikes.map(s => `
            <tr>
                <td>${formatNumber(s.strike)}</td>
                <td>${formatNumber(s.put_oi)}</td>
                <td class="positive-change">${formatSigned(s.put_oi_change)}</td>
                <td class="positive-change">${formatNumber(s.put_force)}</td>
                <td>${flowBadge(s.flow_type)}</td>
            </tr>
        `).join('');
    }

    // ITM Puts totals
    if (data.itm_puts) {
        setText('itm-puts-total-oi', formatNumber(data.itm_puts.total_oi));
        setText('itm-puts-total-change', formatSigned(data.itm_puts.total_oi_change));
        setText('itm-puts-total-force', formatNumber(data.itm_puts.total_force));
    }

    // CALL Alert Card (PM Strong Reversal)
    updateCallAlert(data.call_alert);
}

function updateCallAlert(alert) {
    const card = document.getElementById('call-alert-card');
    if (!card) return;

    if (alert && alert.active) {
        // Show the alert card
        card.style.display = 'block';

        // Update alert content
        setText('alert-message', alert.message || 'PM Reversal Detected');
        setText('alert-pm-score', '+' + (alert.pm_score || 0).toFixed(1));
        setText('alert-pm-change', '+' + (alert.pm_change || 0).toFixed(1));
        setText('alert-strike', (alert.strike || '--') + ' CE');
        setText('alert-spot', formatNumber(alert.spot_price));
        setText('alert-entry', '₹' + (alert.entry_premium || 0).toFixed(2));
        setText('alert-target', '₹' + (alert.target_premium || 0).toFixed(2));
        setText('alert-sl', '₹' + (alert.sl_premium || 0).toFixed(2));

        // Update time
        if (alert.timestamp) {
            const time = new Date(alert.timestamp).toLocaleTimeString('en-IN', {
                hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
            setText('alert-time', time);
        }
    } else {
        // Hide the alert card
        card.style.display = 'none';
    }
}

function updateStrengthAnalysis(strength) {
    if (!strength) return;

    // Put Strength (Support)
    setText('put-strength-ratio', strength.put_strength?.ratio?.toFixed(2) || '--');
    const putScoreElem = document.getElementById('put-strength-score');
    if (putScoreElem) {
        const score = strength.put_strength?.score || 0;
        putScoreElem.textContent = (score >= 0 ? '+' : '') + score.toFixed(1);
        putScoreElem.classList.remove('positive', 'negative');
        putScoreElem.classList.add(score >= 0 ? 'positive' : 'negative');
    }

    // Call Strength (Resistance)
    setText('call-strength-ratio', strength.call_strength?.ratio?.toFixed(2) || '--');
    const callScoreElem = document.getElementById('call-strength-score');
    if (callScoreElem) {
        const score = strength.call_strength?.score || 0;
        callScoreElem.textContent = (score >= 0 ? '+' : '') + score.toFixed(1);
        callScoreElem.classList.remove('positive', 'negative');
        callScoreElem.classList.add(score >= 0 ? 'positive' : 'negative');
    }

    // Direction indicator
    const dirElem = document.getElementById('strength-direction');
    if (dirElem) {
        dirElem.textContent = strength.direction || '--';
        dirElem.classList.remove('bullish', 'bearish', 'neutral');
        dirElem.classList.add(strength.direction?.toLowerCase() || 'neutral');
    }

    // Net Strength
    const netElem = document.getElementById('net-strength');
    if (netElem) {
        const net = strength.net_strength || 0;
        netElem.textContent = (net >= 0 ? '+' : '') + net.toFixed(1);
        netElem.classList.remove('positive', 'negative');
        netElem.classList.add(net >= 0 ? 'positive' : 'negative');
    }
}

function updateLivePnl(data) {
    // Lightweight P&L update from 30-second WebSocket LTP cache.
    // Only touches P&L numbers — card visibility handled by oi_update.

    // Refresh dock P&L after live update
    updateTradeDock();
}




function updateWinRate(tradeStats) {
    // Update win rate display - handle missing data gracefully
    const winRateElem = document.getElementById('win-rate');
    if (winRateElem) {
        if (tradeStats?.win_rate != null) {
            const rate = tradeStats.win_rate;
            winRateElem.textContent = rate.toFixed(1) + '%';
            winRateElem.classList.remove('good', 'bad');
            winRateElem.classList.add(rate >= 50 ? 'good' : 'bad');
        } else {
            winRateElem.textContent = '--';
            winRateElem.classList.remove('good', 'bad');
        }
    }

    setText('total-trades', tradeStats?.total ?? '--');
    setText('trade-wins', tradeStats?.wins ?? '--');
    setText('trade-losses', tradeStats?.losses ?? '--');
    setText('avg-win', tradeStats?.avg_win ? `+${tradeStats.avg_win.toFixed(1)}` : '--');
    setText('avg-loss', tradeStats?.avg_loss ? tradeStats.avg_loss.toFixed(1) : '--');
}

function updateTrapWarning(trapWarning) {
    const card = document.getElementById('trap-warning-card');
    if (!card) return;

    if (!trapWarning) {
        card.style.display = 'none';
        return;
    }

    card.style.display = 'flex';

    setText('trap-type', trapWarning.type.replace('_', ' '));
    setText('trap-message', trapWarning.message);
}

function updateLearningStatus(learning) {
    // Update accuracy - handle missing data gracefully
    const accElem = document.getElementById('learning-accuracy');
    if (accElem) {
        if (learning?.ema_accuracy != null) {
            accElem.textContent = learning.ema_accuracy + '%';
            accElem.style.color = learning.ema_accuracy >= 55 ? CHART_COLORS.up :
                                 learning.ema_accuracy < 50 ? CHART_COLORS.down : CHART_COLORS.muted;
        } else {
            accElem.textContent = '--';
            accElem.style.color = CHART_COLORS.muted;
        }
    }

    // Update status
    const statusElem = document.getElementById('learning-status');
    if (statusElem) {
        if (learning) {
            statusElem.textContent = learning.is_paused ? 'PAUSED' : 'ACTIVE';
            statusElem.classList.remove('active', 'paused');
            statusElem.classList.add(learning.is_paused ? 'paused' : 'active');
        } else {
            statusElem.textContent = '--';
            statusElem.classList.remove('active', 'paused');
        }
    }

    // Update errors
    setText('learning-errors', learning?.consecutive_errors ?? '--');
}

function updateScore(id, value, addColorClass = false) {
    const elem = document.getElementById(id);
    if (!elem) return;

    if (value != null) {
        elem.textContent = (value > 0 ? '+' : '') + value;
        if (addColorClass) {
            elem.classList.remove('positive', 'negative');
            elem.classList.add(value >= 0 ? 'positive' : 'negative');
        }
    } else {
        elem.textContent = '--';
        elem.classList.remove('positive', 'negative');
    }
}

function updateChange(id, value, isNegativeGood) {
    const elem = document.getElementById(id);
    if (!elem) return;

    elem.textContent = formatSigned(value);
    elem.classList.remove('positive', 'negative');
    elem.classList.add(value >= 0 ? (isNegativeGood ? 'negative' : 'positive') : (isNegativeGood ? 'positive' : 'negative'));
}


function updateZoneTable(tbodyId, strikes) {
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return;

    if (!strikes?.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="loading-cell">No data</td></tr>';
        return;
    }

    tbody.innerHTML = strikes.map(s => {
        const netClass = s.net_force >= 0 ? 'positive-change' : 'negative-change';

        return `
            <tr>
                <td>${formatNumber(s.strike)}</td>
                <td class="positive-change">${formatNumber(s.bullish_force)}</td>
                <td class="negative-change">${formatNumber(s.bearish_force)}</td>
                <td class="${netClass}">${formatSigned(s.net_force)}</td>
            </tr>
        `;
    }).join('');
}

// Chart functions - Server is the single source of truth

/**
 * Update main OI chart with full history from server.
 * No client-side accumulation or deduplication needed.
 */
function updateChartFromServer(history) {
    if (!history?.length || !oiChart) return;

    // Filter out previous day's data before merging
    const today = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD
    if (fullChartHistory.length > 0) {
        fullChartHistory = fullChartHistory.filter(h => {
            const d = new Date(h.timestamp).toLocaleDateString('en-CA');
            return d === today;
        });
    }

    // Merge new points (deduplicate by timestamp)
    if (fullChartHistory.length > 0) {
        const existingTs = new Set(fullChartHistory.map(h => h.timestamp));
        const newPoints = history.filter(h => !existingTs.has(h.timestamp));
        if (newPoints.length > 0) {
            fullChartHistory = fullChartHistory.concat(newPoints);
        }
    } else {
        fullChartHistory = history;
    }
    applyChartPeriod();
}

/**
 * Update OTM/ITM charts with zone force data from server history.
 * Called on page load and socket updates to restore chart state.
 */
function updateZoneChartsFromHistory(history) {
    if (!history?.length) return;

    const labels = history.map(item =>
        new Date(item.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
    );

    // Populate OTM chart (OTM Puts vs OTM Calls)
    if (otmChart) {
        otmChart.data.labels = labels;
        otmChart.data.datasets[0].data = history.map(item => item.otm_put_force || 0);
        otmChart.data.datasets[1].data = history.map(item => item.otm_call_force || 0);
        otmChart.update('none');
    }

    // Populate ITM chart (ITM Puts vs ITM Calls)
    if (itmChart) {
        itmChart.data.labels = labels;
        itmChart.data.datasets[0].data = history.map(item => item.itm_put_force || 0);
        itmChart.data.datasets[1].data = history.map(item => item.itm_call_force || 0);
        itmChart.update('none');
    }

    // Populate OI Momentum chart (per-interval rate of change)
    if (cumulativeChart) {
        cumulativeChart.data.labels = labels;
        const callCumulative = history.map(item => item.cumulative_call_oi_change || 0);
        const putCumulative = history.map(item => item.cumulative_put_oi_change || 0);
        const callMomentum = callCumulative.map((v, i) => i === 0 ? 0 : v - callCumulative[i - 1]);
        const putMomentum = putCumulative.map((v, i) => i === 0 ? 0 : v - putCumulative[i - 1]);
        cumulativeChart.data.datasets[0].data = callMomentum;
        cumulativeChart.data.datasets[1].data = putMomentum;
        cumulativeChart.update('none');
    }

    // Populate Futures Basis chart
    if (futuresBasisChart) {
        futuresBasisChart.data.labels = labels;
        futuresBasisChart.data.datasets[0].data = history.map(item => item.futures_basis || 0);
        futuresBasisChart.update('none');
    }
}

/**
 * Update OTM/ITM charts with zone force data from current analysis.
 * Only adds new data point if chart_history wasn't provided (live update only).
 * When chart_history is available, updateZoneChartsFromHistory handles the full refresh.
 */
function updateZoneCharts(data) {
    // Skip if we have chart_history - that's handled by updateZoneChartsFromHistory
    // This function is only for adding single live updates when no history is provided
    if (data.chart_history?.length > 0) return;

    if (!otmChart || !itmChart) return;

    const timestamp = data.timestamp ?
        new Date(data.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) : '';

    if (!timestamp) return;

    // Add to OTM chart (OTM Puts vs OTM Calls)
    const otmPutForce = data.otm_puts?.total_force || 0;
    const otmCallForce = data.otm_calls?.total_force || 0;

    if (otmChart.data.labels.length >= 30) {
        otmChart.data.labels.shift();
        otmChart.data.datasets[0].data.shift();
        otmChart.data.datasets[1].data.shift();
    }

    // Avoid duplicate timestamps
    if (!otmChart.data.labels.includes(timestamp)) {
        otmChart.data.labels.push(timestamp);
        otmChart.data.datasets[0].data.push(otmPutForce);
        otmChart.data.datasets[1].data.push(otmCallForce);
        otmChart.update('none');
    }

    // Add to ITM chart (ITM Puts vs ITM Calls)
    const itmPutForce = data.itm_puts?.total_force || 0;
    const itmCallForce = data.itm_calls?.total_force || 0;

    if (itmChart.data.labels.length >= 30) {
        itmChart.data.labels.shift();
        itmChart.data.datasets[0].data.shift();
        itmChart.data.datasets[1].data.shift();
    }

    if (!itmChart.data.labels.includes(timestamp)) {
        itmChart.data.labels.push(timestamp);
        itmChart.data.datasets[0].data.push(itmPutForce);
        itmChart.data.datasets[1].data.push(itmCallForce);
        itmChart.update('none');
    }
}

// ===== Futures Data Display =====

function updateFuturesData(data) {
    const basis = data.futures_basis;
    const basisElem = document.getElementById('futures-basis-val');
    if (basisElem && basis != null) {
        basisElem.textContent = (basis > 0 ? '+' : '') + Number(basis).toFixed(2);
        basisElem.className = 'futures-basis-value ' +
            (basis > 50 ? 'basis-bullish' : basis < -20 ? 'basis-bearish' : 'basis-neutral');
    }

    const arrow = document.getElementById('futures-basis-arrow');
    if (arrow && basis != null) {
        arrow.textContent = basis > 50 ? '\u2191 Bullish' : basis < -20 ? '\u2193 Bearish' : '\u2192 Neutral';
        arrow.style.color = basis > 50 ? 'var(--bullish)' : basis < -20 ? 'var(--bearish)' : 'var(--text-muted)';
    }

    const oiChange = document.getElementById('futures-oi-change');
    if (oiChange && data.futures_oi_change != null) {
        const c = data.futures_oi_change;
        oiChange.textContent = (c > 0 ? '+' : '') + formatCompact(c);
        oiChange.style.color = c > 0 ? 'var(--bullish)' : c < 0 ? 'var(--bearish)' : 'var(--text-muted)';
    }

    const oiElem = document.getElementById('futures-oi-val');
    if (oiElem && data.futures_oi) oiElem.textContent = formatCompact(data.futures_oi);

    const priceElem = document.getElementById('futures-price-val');
    if (priceElem && data.futures_price) priceElem.textContent = Number(data.futures_price).toFixed(2);

    updateBasisSparkline(data);
}

function updateBasisSparkline(data) {
    const canvas = document.getElementById('futures-basis-sparkline');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;

    // Get basis values from fullChartHistory
    let values = fullChartHistory.map(item => item.futures_basis || 0);
    // Add current value if available
    if (data.futures_basis != null) {
        values = values.concat([data.futures_basis]);
    }

    // Need at least 2 points
    if (values.length < 2) return;

    // Take last 20 values
    values = values.slice(-20);

    ctx.clearRect(0, 0, w, h);

    const min = Math.min(...values, 0);
    const max = Math.max(...values, 0);
    const range = max - min || 1;
    const stepX = w / (values.length - 1);

    // Draw zero line (dashed)
    const zeroY = h - ((0 - min) / range) * h;
    ctx.beginPath();
    ctx.setLineDash([3, 3]);
    ctx.moveTo(0, zeroY);
    ctx.lineTo(w, zeroY);
    ctx.strokeStyle = CHART_COLORS.muted + '26';  // ~15% alpha zero-line
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.setLineDash([]);

    // Draw basis line
    const lastVal = values[values.length - 1];
    const lineColor = lastVal >= 0 ? CHART_COLORS.up : CHART_COLORS.down;

    ctx.beginPath();
    values.forEach((v, i) => {
        const x = i * stepX;
        const y = h - ((v - min) / range) * h;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Draw end dot
    const lastX = (values.length - 1) * stepX;
    const lastY = h - ((lastVal - min) / range) * h;
    ctx.beginPath();
    ctx.arc(lastX, lastY, 3, 0, Math.PI * 2);
    ctx.fillStyle = lineColor;
    ctx.fill();
}

// ===== RR Regime Capsule =====

function fetchRegime() {
    fetch('/api/rr-regime')
        .then(r => r.json())
        .then(regime => {
            const capsule = document.getElementById('regime-capsule');
            if (!capsule || !regime || !regime.name) {
                if (capsule) capsule.style.display = 'none';
                return;
            }
            capsule.style.display = 'flex';
            const dirMap = {'CE_ONLY': 'CE Only', 'PE_ONLY': 'PE Only', 'BOTH': 'CE + PE'};
            setText('regime-name', regime.name.replace(/_/g, ' '));
            setText('regime-direction', dirMap[regime.direction] || regime.direction);
            setText('regime-window', regime.time_start + ' - ' + regime.time_end);
            setText('regime-signals', (regime.signals || []).join(', '));
            setText('regime-max-trades', 'Max ' + regime.max_trades + ' trades');
        })
        .catch(() => {});
}

// ===== V-Shape Recovery Alert =====

function updateVShapeAlert(data) {
    const alert = document.getElementById('v-shape-alert');
    if (!alert) return;

    const vs = data.v_shape_status;
    if (!vs || vs.signal_level === 'NONE' || !vs.signal_level) {
        alert.style.display = 'none';
        return;
    }

    // Backend controls visibility via 'display' field
    if (vs.display === false) {
        alert.style.display = 'none';
        return;
    }

    alert.style.display = 'block';
    alert.className = 'v-shape-alert';

    const level = vs.signal_level;
    const levelClasses = {
        'FORMING': 'v-forming', 'LIKELY': 'v-likely', 'CONFIRMED': 'v-confirmed',
        'V_SUCCEEDED': 'v-succeeded', 'V_PARTIAL': 'v-partial', 'V_FAILED': 'v-failed'
    };
    if (levelClasses[level]) alert.classList.add(levelClasses[level]);

    const titles = {
        'FORMING': 'V-Shape Recovery Forming',
        'LIKELY': 'V-Shape Recovery Likely!',
        'CONFIRMED': 'V-Shape Recovery Confirmed!',
        'V_SUCCEEDED': 'V-Shape Succeeded',
        'V_PARTIAL': 'V-Shape Partial Recovery',
        'V_FAILED': 'V-Shape Failed'
    };
    document.getElementById('v-shape-title').textContent = titles[level] || level;

    const confEl = document.getElementById('v-shape-confidence');
    if (vs.notes && level.startsWith('V_')) {
        confEl.textContent = vs.notes;
    } else {
        confEl.textContent = vs.conditions_count ? vs.conditions_count + ' signals' : '';
    }

    // Update condition checks from conditions_met array
    const conditions = vs.conditions_met || [];
    const checks = [
        ['vsc-basis-check', conditions.includes('futures_basis_positive')],
        ['vsc-score-check', conditions.includes('capitulation_snapback') || conditions.includes('score_near_zero')],
        ['vsc-pm-check', conditions.includes('pm_reversal_cluster')],
        ['vsc-confidence-check', conditions.includes('low_confidence')]
    ];
    checks.forEach(([id, met]) => {
        const el = document.getElementById(id);
        if (el) {
            el.innerHTML = met ? '<i data-lucide="check-square"></i>' : '<i data-lucide="square"></i>';
            el.className = 'vsc-check' + (met ? ' vsc-met' : '');
        }
    });
    if (window.lucide) { lucide.createIcons(); }

    // Hide conditions grid for resolution states
    const condGrid = alert.querySelector('.v-shape-conditions');
    if (condGrid) condGrid.style.display = level.startsWith('V_') ? 'none' : 'grid';
}

// UI functions
function requestRefresh() {
    const btn = document.getElementById('refresh-btn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spinning"><path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg> Refreshing...';
    }

    socket.emit('request_refresh');

    setTimeout(() => {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg> Refresh';
        }
    }, 5000);
}

function updateConnectionStatus(connected) {
    const elem = document.getElementById('connection-status');
    if (elem) {
        elem.textContent = connected ? 'Connected' : 'Disconnected';
        elem.classList.remove('connected', 'disconnected');
        elem.classList.add(connected ? 'connected' : 'disconnected');
    }
}

// Helper functions
function setText(id, value) {
    const elem = document.getElementById(id);
    if (!elem) return;
    const text = value ?? '--';
    const prev = elem.textContent;
    elem.textContent = text;
    if (prev !== '--' && text !== '--' && prev !== text) {
        const cls = parseFloat(String(text).replace(/[^0-9.\-+]/g, '')) >= parseFloat(String(prev).replace(/[^0-9.\-+]/g, '')) ? 'flash-green' : 'flash-red';
        elem.classList.remove('flash-green', 'flash-red');
        void elem.offsetWidth;
        elem.classList.add(cls);
    }
}

function setWidth(id, percent) {
    const elem = document.getElementById(id);
    if (elem) elem.style.width = `${percent}%`;
}

function formatNumber(num) {
    if (num == null) return '--';
    return num.toLocaleString('en-IN');
}

function formatSigned(num) {
    if (num == null) return '--';
    return (num >= 0 ? '+' : '') + num.toLocaleString('en-IN');
}

function formatCompact(num) {
    if (Math.abs(num) >= 100000) return (num / 100000).toFixed(1) + 'L';
    if (Math.abs(num) >= 1000) return (num / 1000).toFixed(0) + 'K';
    return num.toString();
}

// ===== Chart Zoom Controls =====
function zoomIn() { oiChart?.zoom(1.2); otmChart?.zoom(1.2); itmChart?.zoom(1.2); }
function zoomOut() { oiChart?.zoom(0.8); otmChart?.zoom(0.8); itmChart?.zoom(0.8); }
function resetZoom() { oiChart?.resetZoom(); otmChart?.resetZoom(); itmChart?.resetZoom(); }

// ===== OI Flow Card =====
function updateFlowCard(flow) {
    if (!flow) return;

    const dominantEl = document.getElementById('flow-dominant');
    if (dominantEl) {
        const raw = flow.dominant_flow || 'mixed';
        const dom = raw.charAt(0).toUpperCase() + raw.slice(1);
        const icons = {
            'Writing': '<i data-lucide="pen-line"></i>',
            'Buying': '<i data-lucide="trending-up"></i>',
            'Mixed': '<i data-lucide="shuffle"></i>'
        };
        const subtitles = {
            'Writing': 'Sellers in control \u2014 range-bound',
            'Buying': 'Buyers in control \u2014 trending',
            'Mixed': 'No clear dominance'
        };
        dominantEl.innerHTML = `${icons[dom] || ''} ${dom}<span class="flow-dominant-sub">${subtitles[dom] || ''}</span>`;
        dominantEl.className = 'flow-dominant';
        if (dom === 'Writing') dominantEl.classList.add('flow-writing');
        else if (dom === 'Buying') dominantEl.classList.add('flow-buying');
        if (window.lucide) { lucide.createIcons(); }
    }

    setText('flow-bullish', flow.net_bullish_flow || 0);
    setText('flow-bearish', flow.net_bearish_flow || 0);

    // Zone mini bars — build from zone flow count dicts
    const zonesEl = document.getElementById('flow-zones');
    if (zonesEl) {
        const zoneKeys = [
            { key: 'otm_puts', name: 'OTM Puts', tip: 'Put options above spot price. Writing here = sellers expect support to hold (bullish).' },
            { key: 'otm_calls', name: 'OTM Calls', tip: 'Call options above spot price. Writing here = sellers expect resistance to hold (bearish).' },
            { key: 'itm_calls', name: 'ITM Calls', tip: 'Call options below spot price (already in profit). Unwinding = longs taking profit.' },
            { key: 'itm_puts', name: 'ITM Puts', tip: 'Put options below spot price (already in profit). Unwinding = shorts taking profit.' }
        ];
        zonesEl.innerHTML = zoneKeys.map(({ key, name, tip }) => {
            const z = flow[key];
            if (!z) return '';
            const writing = z.fresh_writing || 0;
            const buying = z.fresh_buying || 0;
            const unwinding = z.long_unwinding || 0;
            const covering = z.short_covering || 0;
            const total = writing + buying + unwinding + covering;
            if (!total) return '';
            const wPct = (writing / total * 100).toFixed(0);
            const bPct = (buying / total * 100).toFixed(0);
            const uPct = (unwinding / total * 100).toFixed(0);
            const cPct = (covering / total * 100).toFixed(0);
            const segLabel = (pct) => pct >= 25 ? `<span class="flow-seg-label">${pct}%</span>` : '';
            return `<div class="flow-zone-row" title="${tip}">
                <span class="flow-zone-label">${name}</span>
                <div class="flow-zone-bar">
                    <div class="flow-seg flow-seg-fw" style="width:${wPct}%" title="Writing: ${writing} strikes (${wPct}%) — new sellers opening positions">${segLabel(wPct)}</div>
                    <div class="flow-seg flow-seg-fb" style="width:${bPct}%" title="Buying: ${buying} strikes (${bPct}%) — new buyers opening positions">${segLabel(bPct)}</div>
                    <div class="flow-seg flow-seg-lu" style="width:${uPct}%" title="Unwinding: ${unwinding} strikes (${uPct}%) — longs exiting positions">${segLabel(uPct)}</div>
                    <div class="flow-seg flow-seg-sc" style="width:${cPct}%" title="Covering: ${covering} strikes (${cPct}%) — shorts exiting positions">${segLabel(cPct)}</div>
                </div>
            </div>`;
        }).join('');
    }
}

function flowBadge(flowType) {
    if (!flowType) return '';
    const map = {
        'fresh_writing': ['FW', 'flow-fw'],
        'fresh_buying': ['FB', 'flow-fb'],
        'long_unwinding': ['LU', 'flow-lu'],
        'short_covering': ['SC', 'flow-sc']
    };
    const [abbr, cls] = map[flowType] || [flowType.substring(0, 2).toUpperCase(), 'flow-n'];
    return `<span class="flow-badge ${cls}">${abbr}</span>`;
}

// ===== Kite Auth (Header) =====
function checkKiteStatus() {
    fetch('/kite/status')
        .then(r => r.json())
        .then(data => {
            const dot = document.getElementById('kite-dot');
            const headerStatus = document.getElementById('kite-header-status');
            const dropdownText = document.getElementById('kite-dropdown-text');
            if (data.authenticated) {
                if (dot) dot.classList.add('connected');
                if (dot) dot.classList.remove('disconnected');
                if (headerStatus) headerStatus.textContent = 'Kite';
                if (dropdownText) dropdownText.textContent = 'Connected — ' + (data.token_preview || '');
            } else {
                if (dot) dot.classList.remove('connected');
                if (dot) dot.classList.add('disconnected');
                if (headerStatus) headerStatus.textContent = 'Kite';
                if (dropdownText) dropdownText.textContent = 'Not connected';
            }
        })
        .catch(() => {
            const dot = document.getElementById('kite-dot');
            if (dot) { dot.classList.remove('connected'); dot.classList.add('disconnected'); }
        });
}

function toggleKiteDropdown() {
    const dd = document.getElementById('kite-dropdown');
    if (dd) dd.classList.toggle('open');
}

function toggleKitePasteInput() {
    const row = document.getElementById('kite-paste-row');
    if (row) row.style.display = row.style.display === 'none' ? 'flex' : 'none';
}

// Close Kite dropdown on outside click
document.addEventListener('click', function(e) {
    const kh = document.getElementById('kite-header');
    const dd = document.getElementById('kite-dropdown');
    if (kh && dd && !kh.contains(e.target)) {
        dd.classList.remove('open');
    }
});

function saveKiteToken() {
    const token = document.getElementById('kite-token-field').value.trim();
    if (!token) { alert('Please paste a token'); return; }

    fetch('/kite/save-token', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token: token})
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            document.getElementById('kite-paste-row').style.display = 'none';
            document.getElementById('kite-token-field').value = '';
            document.getElementById('kite-dropdown').classList.remove('open');
            checkKiteStatus();
        } else {
            alert('Error: ' + (data.error || 'Unknown'));
        }
    });
}

// ===== Premium Bar Visualization =====

/**
 * Position the premium bar fill and entry marker between SL and Target.
 * @param {string} prefix - DOM ID prefix (e.g., 'scalp')
 * @param {number} sl - Stop loss premium
 * @param {number} entry - Entry premium
 * @param {number} current - Current premium
 * @param {number} target - Target premium
 * @param {object} opts - Options: { slLabel, targetLabel }
 */
function positionPremiumBar(prefix, sl, entry, current, target, opts = {}) {
    const barTrack = document.getElementById(prefix + '-premium-bar');
    if (!barTrack || !sl || !entry || !target) return;

    if (!current || current <= 0) {
        barTrack.style.display = 'none';
        return;
    }

    barTrack.style.display = 'block';

    setText(prefix + '-bar-sl', (opts.slLabel || 'SL') + ' ' + sl.toFixed(1));
    setText(prefix + '-bar-current', current.toFixed(1));
    setText(prefix + '-bar-target', (opts.targetLabel || 'T') + ' ' + target.toFixed(1));

    const range = target - sl;
    if (range === 0) return;

    const currentPct = Math.max(0, Math.min(100, ((current - sl) / range) * 100));
    const entryPct = Math.max(0, Math.min(100, ((entry - sl) / range) * 100));

    const fill = document.getElementById(prefix + '-bar-fill');
    const entryMark = document.getElementById(prefix + '-bar-entry-mark');

    if (fill) fill.style.width = currentPct + '%';
    if (entryMark) entryMark.style.left = entryPct + '%';
}

// ===== Trade Dock =====

function updateTradeDock() {
    const dock = document.getElementById('trade-dock');
    if (!dock) return;

    let anyVisible = false;
    const cards = [
    ];

    cards.forEach(({ card, pill, pnl, pnlSrc }) => {
        const cardEl = document.getElementById(card);
        const pillEl = document.getElementById(pill);
        const pnlEl = document.getElementById(pnl);
        const srcEl = document.getElementById(pnlSrc);

        if (cardEl && pillEl) {
            const visible = cardEl.style.display !== 'none';
            pillEl.style.display = visible ? 'flex' : 'none';
            if (visible) anyVisible = true;

            if (pnlEl && srcEl && visible) {
                pnlEl.textContent = srcEl.textContent || '--';
                pnlEl.style.color = srcEl.classList.contains('positive') || srcEl.classList.contains('pnl-positive') ? 'var(--bullish)' :
                                    srcEl.classList.contains('negative') || srcEl.classList.contains('pnl-negative') ? 'var(--bearish)' : '';
            }
        }
    });

    dock.style.display = anyVisible ? 'flex' : 'none';
}

// ===== Trade Cards Carousel =====

const CAROUSEL_CARDS = [
];

let _carouselActive = [];   // subset of CAROUSEL_CARDS currently visible
let _carouselIdx = 0;       // current index in _carouselActive
let _carouselRafPending = false;

function carouselInit() {
    // Observe style changes on each trade card to detect show/hide
    CAROUSEL_CARDS.forEach(({ cardId }) => {
        const el = document.getElementById(cardId);
        if (!el) return;
        const obs = new MutationObserver(() => carouselScheduleRefresh());
        obs.observe(el, { attributes: true, attributeFilter: ['style'] });
    });
    carouselRefreshActiveList();
}

function carouselScheduleRefresh() {
    if (_carouselRafPending) return;
    _carouselRafPending = true;
    requestAnimationFrame(() => {
        _carouselRafPending = false;
        carouselRefreshActiveList();
    });
}

function carouselRefreshActiveList() {
    const prev = _carouselActive.map(c => c.cardId);
    _carouselActive = CAROUSEL_CARDS.filter(({ cardId }) => {
        const el = document.getElementById(cardId);
        return el && el.style.display !== 'none';
    });

    const curr = _carouselActive.map(c => c.cardId);
    const changed = prev.length !== curr.length || prev.some((id, i) => id !== curr[i]);

    if (changed) {
        // Keep current card if still active, else reset to 0
        if (_carouselIdx >= _carouselActive.length) _carouselIdx = 0;
        carouselRenderDots();
    }

    const carousel = document.getElementById('trade-carousel');
    if (carousel) {
        carousel.style.display = _carouselActive.length > 0 ? '' : 'none';
    }

    carouselShowCurrent();
    carouselUpdateArrows();
    carouselHighlightDockPill();
}

function carouselShowCurrent() {
    const activeIds = new Set(_carouselActive.map(c => c.cardId));
    const currentId = _carouselActive[_carouselIdx]?.cardId;

    CAROUSEL_CARDS.forEach(({ cardId }) => {
        const el = document.getElementById(cardId);
        if (!el) return;

        if (!activeIds.has(cardId)) {
            // Not active — keep original display:none from update functions
            el.classList.remove('carousel-visible', 'carousel-hidden');
            return;
        }

        if (cardId === currentId) {
            el.classList.add('carousel-visible');
            el.classList.remove('carousel-hidden');
        } else {
            el.classList.add('carousel-hidden');
            el.classList.remove('carousel-visible');
        }
    });
}

function carouselNav(dir) {
    if (_carouselActive.length <= 1) return;
    _carouselIdx = (_carouselIdx + dir + _carouselActive.length) % _carouselActive.length;
    carouselShowCurrent();
    carouselUpdateArrows();
    carouselRenderDots();
    carouselHighlightDockPill();
}

function carouselGoTo(cardId) {
    const idx = _carouselActive.findIndex(c => c.cardId === cardId);
    if (idx === -1) return;
    _carouselIdx = idx;
    carouselShowCurrent();
    carouselUpdateArrows();
    carouselRenderDots();
    carouselHighlightDockPill();
    // Scroll carousel into view
    const carousel = document.getElementById('trade-carousel');
    if (carousel) carousel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function carouselRenderDots() {
    const container = document.getElementById('carousel-dots');
    if (!container) return;
    container.innerHTML = '';
    _carouselActive.forEach((card, i) => {
        const dot = document.createElement('span');
        dot.className = 'carousel-dot ' + card.dotClass + (i === _carouselIdx ? ' active' : '');
        dot.onclick = () => {
            _carouselIdx = i;
            carouselShowCurrent();
            carouselUpdateArrows();
            carouselRenderDots();
            carouselHighlightDockPill();
        };
        container.appendChild(dot);
    });
}

function carouselUpdateArrows() {
    const left = document.getElementById('carousel-arrow-left');
    const right = document.getElementById('carousel-arrow-right');
    const show = _carouselActive.length > 1;
    if (left) left.style.display = show ? '' : 'none';
    if (right) right.style.display = show ? '' : 'none';
}

function carouselHighlightDockPill() {
    const currentPill = _carouselActive[_carouselIdx]?.pillId;
    CAROUSEL_CARDS.forEach(({ pillId }) => {
        const el = document.getElementById(pillId);
        if (el) el.classList.toggle('dock-pill-active', pillId === currentPill);
    });
}

// Init after DOM is ready and update functions have had a chance to run
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(carouselInit, 100);
});
