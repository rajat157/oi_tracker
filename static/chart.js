// OI Tracker Dashboard JavaScript
// NOTE: All state is server-controlled. Frontend is a pure display layer.

const socket = io();
let oiChart = null;
let otmChart = null;
let itmChart = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initChart();
    initOTMChart();
    initITMChart();
    initToggles();
    setupSocketListeners();

    setTimeout(fetchMarketStatus, 500);
    setTimeout(fetchLatestData, 1000);

    setInterval(fetchMarketStatus, 60000);
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

// Initialize OTM Chart (Put vs Call OI trend)
function initOTMChart() {
    const canvas = document.getElementById('otm-chart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    otmChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'OTM Put Force',
                    data: [],
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 2
                },
                {
                    label: 'OTM Call Force',
                    data: [],
                    borderColor: '#f87171',
                    backgroundColor: 'rgba(248, 113, 113, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 2
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { position: 'top', align: 'end', labels: { color: '#a1a1b5', usePointStyle: true, font: { size: 11 } } }
            },
            scales: {
                x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#6b6b7f', font: { size: 10 }, maxRotation: 0 } },
                y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#6b6b7f', font: { size: 10 }, callback: v => formatCompact(v) } }
            }
        }
    });
}

// Initialize ITM Chart (Put vs Call OI trend)
function initITMChart() {
    const canvas = document.getElementById('itm-chart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    itmChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'ITM Put Force',
                    data: [],
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 2
                },
                {
                    label: 'ITM Call Force',
                    data: [],
                    borderColor: '#f87171',
                    backgroundColor: 'rgba(248, 113, 113, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 2
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { position: 'top', align: 'end', labels: { color: '#a1a1b5', usePointStyle: true, font: { size: 11 } } }
            },
            scales: {
                x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#6b6b7f', font: { size: 10 }, maxRotation: 0 } },
                y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#6b6b7f', font: { size: 10 }, callback: v => formatCompact(v) } }
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
        const color = pct > 0 ? '#22c55e' : pct < 0 ? '#f87171' : '#a1a1b5';
        momentumElem.textContent = `${arrow} ${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
        momentumElem.style.color = color;
    } else if (momentumElem) {
        momentumElem.textContent = '--';
        momentumElem.style.color = '#a1a1b5';
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
    const avgConviction = ((data.avg_call_conviction || 0) + (data.avg_put_conviction || 0)) / 2;
    const convictionElem = document.getElementById('avg-conviction');
    if (convictionElem) {
        convictionElem.textContent = avgConviction > 0 ? avgConviction.toFixed(2) + 'x' : '--';
        convictionElem.style.color = avgConviction > 1.2 ? '#22c55e' :
                                     avgConviction < 0.8 ? '#f87171' : '#a1a1b5';
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

    // Update OTM/ITM tables
    updateOTMITMTables(data);

    // Update Strength Analysis
    updateStrengthAnalysis(data.strength_analysis);

    // Update zone charts
    updateZoneCharts(data);
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
            </tr>
        `).join('');
    }

    // ITM Puts totals
    if (data.itm_puts) {
        setText('itm-puts-total-oi', formatNumber(data.itm_puts.total_oi));
        setText('itm-puts-total-change', formatSigned(data.itm_puts.total_oi_change));
        setText('itm-puts-total-force', formatNumber(data.itm_puts.total_force));
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
            accElem.style.color = learning.ema_accuracy >= 55 ? '#22c55e' :
                                 learning.ema_accuracy < 50 ? '#f87171' : '#a1a1b5';
        } else {
            accElem.textContent = '--';
            accElem.style.color = '#a1a1b5';
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

    const limited = history.slice(-30);

    // Replace all chart data with server data
    oiChart.data.labels = limited.map(item =>
        new Date(item.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
    );
    oiChart.data.datasets[0].data = limited.map(item => item.call_oi_change);
    oiChart.data.datasets[1].data = limited.map(item => item.put_oi_change);
    oiChart.update('none');

    // Also update OTM/ITM charts from history (zone force data)
    updateZoneChartsFromHistory(limited);
}

/**
 * Update OTM/ITM charts with zone force data from server history.
 * Called on page load and socket updates to restore chart state.
 */
function updateZoneChartsFromHistory(history) {
    if (!history?.length || !otmChart || !itmChart) return;

    // Debug: log the zone force data
    console.log('Zone chart history sample:', history.slice(0, 2).map(h => ({
        ts: h.timestamp,
        otm_put: h.otm_put_force,
        otm_call: h.otm_call_force,
        itm_put: h.itm_put_force,
        itm_call: h.itm_call_force
    })));

    // Populate OTM chart (OTM Puts vs OTM Calls)
    otmChart.data.labels = history.map(item =>
        new Date(item.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
    );
    otmChart.data.datasets[0].data = history.map(item => item.otm_put_force || 0);
    otmChart.data.datasets[1].data = history.map(item => item.otm_call_force || 0);
    otmChart.update('none');

    // Populate ITM chart (ITM Puts vs ITM Calls)
    itmChart.data.labels = history.map(item =>
        new Date(item.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
    );
    itmChart.data.datasets[0].data = history.map(item => item.itm_put_force || 0);
    itmChart.data.datasets[1].data = history.map(item => item.itm_call_force || 0);
    itmChart.update('none');
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
