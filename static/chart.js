// OI Tracker Dashboard JavaScript

const socket = io();
let oiChart = null;
let atmChart = null;
let itmChart = null;
let lastChartTimestamp = null;

// Toggle state (persisted in localStorage)
let includeATM = localStorage.getItem('includeATM') === 'true';
let includeITM = localStorage.getItem('includeITM') === 'true';
let forceAutoFetch = localStorage.getItem('forceAutoFetch') === 'true';

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initChart();
    initATMChart();
    initITMChart();
    initToggles();
    setupSocketListeners();

    setTimeout(fetchMarketStatus, 500);
    setTimeout(fetchLatestData, 1000);
    setTimeout(fetchHistory, 1500);

    setInterval(fetchMarketStatus, 60000);
});

// Initialize toggle switches
function initToggles() {
    const atmToggle = document.getElementById('atm-toggle');
    const itmToggle = document.getElementById('itm-toggle');
    const autoToggle = document.getElementById('auto-toggle');

    // Set initial state from localStorage
    if (atmToggle) atmToggle.checked = includeATM;
    if (itmToggle) itmToggle.checked = includeITM;
    if (autoToggle) autoToggle.checked = forceAutoFetch;

    // Update UI visibility
    updateSectionVisibility();

    // Add event listeners
    if (atmToggle) {
        atmToggle.addEventListener('change', (e) => {
            includeATM = e.target.checked;
            localStorage.setItem('includeATM', includeATM);
            updateSectionVisibility();
            socket.emit('update_toggles', { include_atm: includeATM, include_itm: includeITM });
        });
    }

    if (itmToggle) {
        itmToggle.addEventListener('change', (e) => {
            includeITM = e.target.checked;
            localStorage.setItem('includeITM', includeITM);
            updateSectionVisibility();
            socket.emit('update_toggles', { include_atm: includeATM, include_itm: includeITM });
        });
    }

    if (autoToggle) {
        autoToggle.addEventListener('change', (e) => {
            forceAutoFetch = e.target.checked;
            localStorage.setItem('forceAutoFetch', forceAutoFetch);
            socket.emit('set_force_fetch', { enabled: forceAutoFetch });
        });
    }
}

// Show/hide sections based on toggle state
function updateSectionVisibility() {
    const atmSection = document.getElementById('atm-section');
    const atmBreakdown = document.getElementById('atm-breakdown');
    const atmChartSection = document.getElementById('atm-chart-section');
    const itmSection = document.getElementById('itm-section');
    const itmBreakdown = document.getElementById('itm-breakdown');
    const itmChartSection = document.getElementById('itm-chart-section');

    if (atmSection) atmSection.style.display = includeATM ? 'block' : 'none';
    if (atmBreakdown) atmBreakdown.style.display = includeATM ? 'flex' : 'none';
    if (atmChartSection) atmChartSection.style.display = includeATM ? 'block' : 'none';
    if (itmSection) itmSection.style.display = includeITM ? 'grid' : 'none';
    if (itmBreakdown) itmBreakdown.style.display = includeITM ? 'flex' : 'none';
    if (itmChartSection) itmChartSection.style.display = includeITM ? 'block' : 'none';
}

// Filter incoming WebSocket data based on toggle state
function filterDataByToggles(data) {
    // Create a shallow copy to avoid mutating original
    const filtered = { ...data };

    // If ATM toggle is OFF, subtract ATM data from totals
    if (!includeATM && data.atm_data) {
        filtered.call_oi_change = (data.call_oi_change || 0) - (data.atm_data.call_oi_change || 0);
        filtered.put_oi_change = (data.put_oi_change || 0) - (data.atm_data.put_oi_change || 0);
        filtered.atm_data = null;
    }

    // If ITM toggle is OFF, subtract ITM data from totals
    if (!includeITM) {
        filtered.call_oi_change = (filtered.call_oi_change || 0) - (data.itm_call_oi_change || 0);
        filtered.put_oi_change = (filtered.put_oi_change || 0) - (data.itm_put_oi_change || 0);
        filtered.itm_call_oi_change = 0;
        filtered.itm_put_oi_change = 0;
        filtered.itm_calls = null;
        filtered.itm_puts = null;
    }

    // Recalculate net OI change
    filtered.net_oi_change = (filtered.put_oi_change || 0) - (filtered.call_oi_change || 0);

    // Recalculate verdict based on filtered OI changes
    filtered.verdict = calculateFilteredVerdict(filtered.call_oi_change, filtered.put_oi_change);

    return filtered;
}

// Calculate verdict for filtered data (matches oi_analyzer.py logic)
function calculateFilteredVerdict(callChange, putChange) {
    const diff = (putChange || 0) - (callChange || 0);
    const total = Math.abs(callChange || 0) + Math.abs(putChange || 0);

    if (total === 0) return "Neutral";

    const ratio = Math.abs(diff) / total * 100;

    if (diff > 0) {
        // Bullish (Put OI > Call OI means writers selling puts = bullish)
        if (ratio > 40) return "Bulls Strongly Winning";
        if (ratio > 15) return "Bulls Winning";
        return "Slightly Bullish";
    } else {
        // Bearish (Call OI > Put OI means writers selling calls = bearish)
        if (ratio > 40) return "Bears Strongly Winning";
        if (ratio > 15) return "Bears Winning";
        return "Slightly Bearish";
    }
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

    oiChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Call OI Change',
                    data: [],
                    borderColor: '#f87171',
                    backgroundColor: 'rgba(248, 113, 113, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: '#f87171'
                },
                {
                    label: 'Put OI Change',
                    data: [],
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: '#22c55e'
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
                        color: '#a1a1b5',
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 20,
                        font: { size: 12, family: 'Inter' }
                    }
                },
                tooltip: {
                    backgroundColor: '#1a1a24',
                    titleColor: '#ffffff',
                    bodyColor: '#a1a1b5',
                    borderColor: '#2a2a3a',
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    titleFont: { size: 13, family: 'Inter' },
                    bodyFont: { size: 12, family: 'Inter' }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false },
                    ticks: { color: '#6b6b7f', font: { size: 11, family: 'Inter' }, maxRotation: 0 }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false },
                    ticks: {
                        color: '#6b6b7f',
                        font: { size: 11, family: 'Inter' },
                        callback: value => formatCompact(value)
                    }
                }
            }
        }
    });
}

// Initialize ATM Chart
function initATMChart() {
    const ctx = document.getElementById('atm-chart').getContext('2d');

    atmChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'ATM Call OI Change',
                    data: [],
                    borderColor: '#f87171',
                    backgroundColor: 'rgba(248, 113, 113, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: '#f87171'
                },
                {
                    label: 'ATM Put OI Change',
                    data: [],
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: '#22c55e'
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
                        color: '#a1a1b5',
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 20,
                        font: { size: 12, family: 'Inter' }
                    }
                },
                tooltip: {
                    backgroundColor: '#1a1a24',
                    titleColor: '#ffffff',
                    bodyColor: '#a1a1b5',
                    borderColor: '#2a2a3a',
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    titleFont: { size: 13, family: 'Inter' },
                    bodyFont: { size: 12, family: 'Inter' }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false },
                    ticks: { color: '#6b6b7f', font: { size: 11, family: 'Inter' }, maxRotation: 0 }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false },
                    ticks: {
                        color: '#6b6b7f',
                        font: { size: 11, family: 'Inter' },
                        callback: value => formatCompact(value)
                    }
                }
            }
        }
    });
}

// Initialize ITM Chart
function initITMChart() {
    const ctx = document.getElementById('itm-chart').getContext('2d');

    itmChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'ITM Call OI Change',
                    data: [],
                    borderColor: '#f87171',
                    backgroundColor: 'rgba(248, 113, 113, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: '#f87171'
                },
                {
                    label: 'ITM Put OI Change',
                    data: [],
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: '#22c55e'
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
                        color: '#a1a1b5',
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 20,
                        font: { size: 12, family: 'Inter' }
                    }
                },
                tooltip: {
                    backgroundColor: '#1a1a24',
                    titleColor: '#ffffff',
                    bodyColor: '#a1a1b5',
                    borderColor: '#2a2a3a',
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    titleFont: { size: 13, family: 'Inter' },
                    bodyFont: { size: 12, family: 'Inter' }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false },
                    ticks: { color: '#6b6b7f', font: { size: 11, family: 'Inter' }, maxRotation: 0 }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false },
                    ticks: {
                        color: '#6b6b7f',
                        font: { size: 11, family: 'Inter' },
                        callback: value => formatCompact(value)
                    }
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
        // Sync force fetch state with server on connect
        socket.emit('set_force_fetch', { enabled: forceAutoFetch });
    });

    socket.on('disconnect', () => {
        console.log('Disconnected');
        updateConnectionStatus(false);
    });

    socket.on('oi_update', data => {
        console.log('OI update received');

        // Filter data based on current toggle state before displaying
        const filteredData = filterDataByToggles(data);

        updateDashboard(filteredData);
        addChartDataPoint(filteredData);
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
    const params = new URLSearchParams({
        include_atm: includeATM,
        include_itm: includeITM
    });
    fetch(`/api/latest?${params}`)
        .then(r => r.ok ? r.json() : Promise.reject('No data'))
        .then(data => {
            console.log('Latest data:', data);
            updateDashboard(data);
        })
        .catch(e => console.log('Waiting for data:', e));
}

function fetchHistory() {
    fetch('/api/history')
        .then(r => r.json())
        .then(updateChartHistory)
        .catch(e => console.error('History error:', e));
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

    // Update weight displays and individual scores
    if (data.weights) {
        setText('otm-weight', Math.round(data.weights.otm * 100) + '%');
        setText('atm-weight', Math.round(data.weights.atm * 100) + '%');
        setText('itm-weight', Math.round(data.weights.itm * 100) + '%');
        setText('momentum-weight', Math.round(data.weights.momentum * 100) + '%');

        // Show/hide momentum breakdown based on weight
        const momentumBreakdown = document.getElementById('momentum-breakdown');
        if (momentumBreakdown) {
            momentumBreakdown.style.display = data.weights.momentum > 0 ? 'flex' : 'none';
        }
    }

    // Update zone scores (simplified - just zone scores without 70/30 breakdown)
    updateScore('otm-score-display', data.otm_score, true);
    updateScore('atm-score-display', data.atm_score, true);
    updateScore('itm-score-display', data.itm_score, true);
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
        const color = pct > 0 ? '#22c55e' : pct < 0 ? '#f87171' : '#a1a1b5';
        momentumElem.textContent = `${arrow} ${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
        momentumElem.style.color = color;
    } else if (momentumElem) {
        momentumElem.textContent = '--';
        momentumElem.style.color = '#a1a1b5';
    }

    // Update volume metrics
    setText('volume-pcr', data.volume_pcr ?? '--');

    // Display average conviction with color coding
    const avgConviction = ((data.avg_call_conviction || 0) + (data.avg_put_conviction || 0)) / 2;
    const convictionElem = document.getElementById('avg-conviction');
    if (convictionElem) {
        convictionElem.textContent = avgConviction > 0 ? avgConviction.toFixed(2) + 'x' : '--';
        convictionElem.style.color = avgConviction > 1.2 ? '#22c55e' :
                                     avgConviction < 0.8 ? '#f87171' : '#a1a1b5';
    }

    // Update OI comparison
    const maxOI = Math.max(data.total_call_oi || 0, data.total_put_oi || 0);

    setText('call-oi-total', formatNumber(data.total_call_oi));
    setText('put-oi-total', formatNumber(data.total_put_oi));

    setWidth('call-oi-bar', maxOI > 0 ? (data.total_call_oi / maxOI * 100) : 0);
    setWidth('put-oi-bar', maxOI > 0 ? (data.total_put_oi / maxOI * 100) : 0);

    updateChange('call-oi-change', data.call_oi_change, true);
    updateChange('put-oi-change', data.put_oi_change, false);

    // Net OI change
    const netElem = document.getElementById('net-oi-change');
    if (netElem) {
        netElem.textContent = formatSigned(data.net_oi_change);
        netElem.classList.remove('positive', 'negative');
        netElem.classList.add(data.net_oi_change >= 0 ? 'positive' : 'negative');
    }

    // Update tables
    updateTable('calls-tbody', data.otm_calls);
    updateTable('puts-tbody', data.otm_puts);

    setText('calls-total-oi', formatNumber(data.total_call_oi));
    setText('calls-total-change', formatSigned(data.call_oi_change));
    setText('calls-total-volume', formatNumber(data.total_call_volume || 0));
    setText('calls-avg-conviction', data.avg_call_conviction ? data.avg_call_conviction.toFixed(2) + 'x' : '--');

    setText('puts-total-oi', formatNumber(data.total_put_oi));
    setText('puts-total-change', formatSigned(data.put_oi_change));
    setText('puts-total-volume', formatNumber(data.total_put_volume || 0));
    setText('puts-avg-conviction', data.avg_put_conviction ? data.avg_put_conviction.toFixed(2) + 'x' : '--');

    // Update ATM section
    if (data.atm_data) {
        setText('atm-strike-value', formatNumber(data.atm_data.strike));
        setText('atm-call-oi', formatNumber(data.atm_data.call_oi));
        setText('atm-put-oi', formatNumber(data.atm_data.put_oi));
        updateATMChange('atm-call-change', data.atm_data.call_oi_change);
        updateATMChange('atm-put-change', data.atm_data.put_oi_change);
    }

    // Update ITM tables
    if (data.itm_calls) {
        updateTable('itm-calls-tbody', data.itm_calls);
        setText('itm-calls-total-oi', formatNumber(data.total_itm_call_oi));
        setText('itm-calls-total-change', formatSigned(data.itm_call_oi_change));
        // Calculate ITM calls volume and conviction
        const itmCallsVolume = data.itm_calls.reduce((sum, s) => sum + (s.volume || 0), 0);
        const itmCallsAvgConviction = data.itm_calls.length > 0 ?
            data.itm_calls.reduce((sum, s) => sum + (s.conviction || 0), 0) / data.itm_calls.length : 0;
        setText('itm-calls-total-volume', formatNumber(itmCallsVolume));
        setText('itm-calls-avg-conviction', itmCallsAvgConviction > 0 ? itmCallsAvgConviction.toFixed(2) + 'x' : '--');
    }

    if (data.itm_puts) {
        updateTable('itm-puts-tbody', data.itm_puts);
        setText('itm-puts-total-oi', formatNumber(data.total_itm_put_oi));
        setText('itm-puts-total-change', formatSigned(data.itm_put_oi_change));
        // Calculate ITM puts volume and conviction
        const itmPutsVolume = data.itm_puts.reduce((sum, s) => sum + (s.volume || 0), 0);
        const itmPutsAvgConviction = data.itm_puts.length > 0 ?
            data.itm_puts.reduce((sum, s) => sum + (s.conviction || 0), 0) / data.itm_puts.length : 0;
        setText('itm-puts-total-volume', formatNumber(itmPutsVolume));
        setText('itm-puts-avg-conviction', itmPutsAvgConviction > 0 ? itmPutsAvgConviction.toFixed(2) + 'x' : '--');
    }

    // Update new metrics: Max Pain, IV Skew
    setText('max-pain', data.max_pain ? formatNumber(data.max_pain) : '--');

    const ivSkewElem = document.getElementById('iv-skew');
    if (ivSkewElem && data.iv_skew !== undefined) {
        const skew = data.iv_skew;
        ivSkewElem.textContent = (skew > 0 ? '+' : '') + skew.toFixed(2) + '%';
        ivSkewElem.style.color = skew > 2 ? '#f87171' : skew < -2 ? '#22c55e' : '#a1a1b5';
    }

    // Update Trade Setup Card (persistent with lifecycle)
    updateTradeSetup(data);

    // Update Win Rate Card
    updateWinRate(data.trade_stats);

    // Update Trap Warning Card
    updateTrapWarning(data.trap_warning);

    // Update Self-Learning Status
    updateLearningStatus(data.self_learning);
}

function updateTradeSetup(data) {
    const card = document.getElementById('trade-setup-card');
    if (!card) return;

    // Use active_trade (persistent setup) if available, else fall back to trade_setup
    const activeTrade = data.active_trade;
    const setup = activeTrade || data.trade_setup;
    const confidence = activeTrade ? activeTrade.signal_confidence : (data.signal_confidence || 0);

    // Show if we have an active/pending trade OR a new setup
    if (!setup) {
        card.style.display = 'none';
        return;
    }

    card.style.display = 'block';

    // Update status badge
    const statusBadge = document.getElementById('trade-status-badge');
    if (statusBadge && activeTrade) {
        const status = activeTrade.status || 'PENDING';
        statusBadge.textContent = status;
        statusBadge.className = 'trade-status-badge status-' + status.toLowerCase();
    } else if (statusBadge) {
        statusBadge.textContent = 'NEW';
        statusBadge.className = 'trade-status-badge status-new';
    }

    // Update direction (BUY_CALL or BUY_PUT)
    const dirElem = document.getElementById('trade-direction');
    if (dirElem) {
        const directionText = setup.direction === 'BUY_CALL' ? 'BUY CALL' : 'BUY PUT';
        dirElem.textContent = directionText;
        dirElem.classList.remove('long', 'short', 'buy-call', 'buy-put');
        dirElem.classList.add(setup.direction === 'BUY_CALL' ? 'buy-call' : 'buy-put');
    }

    // Update strike info
    const strikeElem = document.getElementById('trade-strike');
    if (strikeElem) {
        const optionType = setup.option_type || (setup.direction === 'BUY_CALL' ? 'CE' : 'PE');
        const moneyness = setup.moneyness || 'ATM';
        strikeElem.textContent = `${setup.strike} ${optionType} (${moneyness})`;
    }

    // Update confidence
    setText('trade-confidence', `Confidence: ${Math.round(confidence)}%`);

    // Update Live P/L display (only for ACTIVE trades)
    const pnlDiv = document.getElementById('trade-live-pnl');
    if (pnlDiv && activeTrade && activeTrade.status === 'ACTIVE') {
        pnlDiv.style.display = 'flex';
        const pnlValue = document.getElementById('trade-pnl-value');
        if (pnlValue) {
            const pnl = activeTrade.live_pnl_pct || 0;
            const pnlPoints = activeTrade.live_pnl_points || 0;
            pnlValue.textContent = `${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}% (${pnlPoints >= 0 ? '+' : ''}${pnlPoints.toFixed(2)} pts)`;
            pnlValue.classList.remove('positive', 'negative');
            pnlValue.classList.add(pnl >= 0 ? 'positive' : 'negative');
        }
        setText('trade-current-premium', activeTrade.current_premium?.toFixed(2) || '--');
    } else if (pnlDiv) {
        pnlDiv.style.display = 'none';
    }

    // Update premium-based levels
    setText('trade-entry', setup.entry_premium?.toFixed(2) || '--');
    setText('trade-sl', setup.sl_premium?.toFixed(2) || '--');
    setText('trade-sl-detail', `-${setup.risk_pct}%`);
    setText('trade-target1', setup.target1_premium?.toFixed(2) || '--');
    setText('trade-target2', setup.target2_premium?.toFixed(2) || '--');

    // Update meta
    setText('trade-risk', setup.risk_points?.toFixed(2) || (setup.entry_premium && setup.sl_premium ? (setup.entry_premium - setup.sl_premium).toFixed(2) : '--'));
    setText('trade-risk-pct', setup.risk_pct || '--');
    setText('trade-support', setup.support_ref ? formatNumber(setup.support_ref) : '--');
    setText('trade-resistance', setup.resistance_ref ? formatNumber(setup.resistance_ref) : '--');
    setText('trade-max-pain', setup.max_pain ? formatNumber(setup.max_pain) : '--');
}

function updateWinRate(tradeStats) {
    if (!tradeStats) return;

    // Update win rate display
    const winRateElem = document.getElementById('win-rate');
    if (winRateElem) {
        const rate = tradeStats.win_rate || 0;
        winRateElem.textContent = rate.toFixed(1) + '%';
        winRateElem.classList.remove('good', 'bad');
        winRateElem.classList.add(rate >= 50 ? 'good' : 'bad');
    }

    setText('total-trades', tradeStats.total || 0);
    setText('trade-wins', tradeStats.wins || 0);
    setText('trade-losses', tradeStats.losses || 0);
    setText('avg-win', tradeStats.avg_win ? `+${tradeStats.avg_win.toFixed(1)}` : '--');
    setText('avg-loss', tradeStats.avg_loss ? tradeStats.avg_loss.toFixed(1) : '--');
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
    if (!learning) return;

    // Update accuracy
    const accElem = document.getElementById('learning-accuracy');
    if (accElem) {
        accElem.textContent = learning.ema_accuracy + '%';
        accElem.style.color = learning.ema_accuracy >= 55 ? '#22c55e' :
                             learning.ema_accuracy < 50 ? '#f87171' : '#a1a1b5';
    }

    // Update status
    const statusElem = document.getElementById('learning-status');
    if (statusElem) {
        statusElem.textContent = learning.is_paused ? 'PAUSED' : 'ACTIVE';
        statusElem.classList.remove('active', 'paused');
        statusElem.classList.add(learning.is_paused ? 'paused' : 'active');
    }

    // Update errors
    setText('learning-errors', learning.consecutive_errors);
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

function updateATMChange(id, value) {
    const elem = document.getElementById(id);
    if (!elem) return;

    elem.textContent = formatSigned(value);
    elem.classList.remove('positive', 'negative');
    elem.classList.add(value >= 0 ? 'positive' : 'negative');
}

function updateTable(tbodyId, strikes) {
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return;

    if (!strikes?.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="loading-cell">No data</td></tr>';
        return;
    }

    tbody.innerHTML = strikes.map(s => {
        const volume = s.volume !== undefined ? formatNumber(s.volume) : '--';
        const conviction = s.conviction !== undefined ? s.conviction.toFixed(2) + 'x' : '--';
        const convictionColor = s.conviction > 1.2 ? 'high-conviction' :
                               s.conviction < 0.8 ? 'low-conviction' : '';

        return `
            <tr>
                <td>${formatNumber(s.strike)}</td>
                <td>${formatNumber(s.oi)}</td>
                <td class="${s.oi_change >= 0 ? 'positive-change' : 'negative-change'}">${formatSigned(s.oi_change)}</td>
                <td>${volume}</td>
                <td class="${convictionColor}">${conviction}</td>
            </tr>
        `;
    }).join('');
}

// Chart functions
function updateChartHistory(history) {
    if (!history?.length) return;

    const limited = history.slice(-30);

    // Update OTM chart (existing)
    oiChart.data.labels = limited.map(item =>
        new Date(item.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
    );
    oiChart.data.datasets[0].data = limited.map(item => item.call_oi_change);
    oiChart.data.datasets[1].data = limited.map(item => item.put_oi_change);

    // Update ATM chart
    if (atmChart) {
        atmChart.data.labels = oiChart.data.labels;
        atmChart.data.datasets[0].data = limited.map(item => item.atm_call_oi_change || 0);
        atmChart.data.datasets[1].data = limited.map(item => item.atm_put_oi_change || 0);
        atmChart.update('none');
    }

    // Update ITM chart
    if (itmChart) {
        itmChart.data.labels = oiChart.data.labels;
        itmChart.data.datasets[0].data = limited.map(item => item.itm_call_oi_change || 0);
        itmChart.data.datasets[1].data = limited.map(item => item.itm_put_oi_change || 0);
        itmChart.update('none');
    }

    if (limited.length > 0) {
        lastChartTimestamp = limited[limited.length - 1].timestamp;
    }

    oiChart.update('none');
}

function addChartDataPoint(data) {
    if (!data.timestamp || data.timestamp === lastChartTimestamp) return;

    const label = new Date(data.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });

    if (oiChart.data.labels.includes(label)) return;

    lastChartTimestamp = data.timestamp;

    // Handle rolling window for all charts
    if (oiChart.data.labels.length >= 30) {
        oiChart.data.labels.shift();
        oiChart.data.datasets[0].data.shift();
        oiChart.data.datasets[1].data.shift();

        if (atmChart) {
            atmChart.data.labels.shift();
            atmChart.data.datasets[0].data.shift();
            atmChart.data.datasets[1].data.shift();
        }

        if (itmChart) {
            itmChart.data.labels.shift();
            itmChart.data.datasets[0].data.shift();
            itmChart.data.datasets[1].data.shift();
        }
    }

    // Add new data point to OTM chart
    oiChart.data.labels.push(label);
    oiChart.data.datasets[0].data.push(data.call_oi_change);
    oiChart.data.datasets[1].data.push(data.put_oi_change);
    oiChart.update('none');

    // Add new data point to ATM chart
    if (atmChart) {
        atmChart.data.labels.push(label);
        const atmCallChange = data.atm_data?.call_oi_change || 0;
        const atmPutChange = data.atm_data?.put_oi_change || 0;
        atmChart.data.datasets[0].data.push(atmCallChange);
        atmChart.data.datasets[1].data.push(atmPutChange);
        atmChart.update('none');
    }

    // Add new data point to ITM chart
    if (itmChart) {
        itmChart.data.labels.push(label);
        itmChart.data.datasets[0].data.push(data.itm_call_oi_change || 0);
        itmChart.data.datasets[1].data.push(data.itm_put_oi_change || 0);
        itmChart.update('none');
    }
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
    if (elem) elem.textContent = value ?? '--';
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
