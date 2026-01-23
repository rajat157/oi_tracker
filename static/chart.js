// OI Tracker Dashboard JavaScript

const socket = io();
let oiChart = null;
let lastChartTimestamp = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initChart();
    setupSocketListeners();

    setTimeout(fetchMarketStatus, 500);
    setTimeout(fetchLatestData, 1000);
    setTimeout(fetchHistory, 1500);

    setInterval(fetchMarketStatus, 60000);
});

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
                    borderColor: '#ef4444',
                    backgroundColor: 'rgba(239, 68, 68, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: '#ef4444'
                },
                {
                    label: 'Put OI Change',
                    data: [],
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: '#10b981'
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
                        color: '#8b8b9e',
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 20,
                        font: { size: 12, family: 'Inter' }
                    }
                },
                tooltip: {
                    backgroundColor: '#1a1a24',
                    titleColor: '#ffffff',
                    bodyColor: '#8b8b9e',
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
                    ticks: { color: '#5c5c6f', font: { size: 11, family: 'Inter' }, maxRotation: 0 }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false },
                    ticks: {
                        color: '#5c5c6f',
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
    });

    socket.on('disconnect', () => {
        console.log('Disconnected');
        updateConnectionStatus(false);
    });

    socket.on('oi_update', data => {
        console.log('OI update received');
        updateDashboard(data);
        addChartDataPoint(data);
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

    // Update scores
    updateScore('combined-score', data.combined_score);
    updateScore('change-score', data.change_score, true);
    updateScore('total-oi-score', data.total_oi_score, true);

    // Update metrics
    setText('spot-price', formatNumber(data.spot_price));
    setText('atm-strike', formatNumber(data.atm_strike));
    setText('expiry-date', data.expiry_date || '--');
    setText('pcr-value', data.pcr ?? '--');

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
    setText('puts-total-oi', formatNumber(data.total_put_oi));
    setText('puts-total-change', formatSigned(data.put_oi_change));
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
    }
}

function updateChange(id, value, isNegativeGood) {
    const elem = document.getElementById(id);
    if (!elem) return;

    elem.textContent = formatSigned(value);
    elem.classList.remove('positive', 'negative');
    elem.classList.add(value >= 0 ? (isNegativeGood ? 'negative' : 'positive') : (isNegativeGood ? 'positive' : 'negative'));
}

function updateTable(tbodyId, strikes) {
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return;

    if (!strikes?.length) {
        tbody.innerHTML = '<tr><td colspan="3" class="loading-cell">No data</td></tr>';
        return;
    }

    tbody.innerHTML = strikes.map(s => `
        <tr>
            <td>${formatNumber(s.strike)}</td>
            <td>${formatNumber(s.oi)}</td>
            <td class="${s.oi_change >= 0 ? 'positive-change' : 'negative-change'}">${formatSigned(s.oi_change)}</td>
        </tr>
    `).join('');
}

// Chart functions
function updateChartHistory(history) {
    if (!history?.length) return;

    const limited = history.slice(-30);

    oiChart.data.labels = limited.map(item =>
        new Date(item.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
    );
    oiChart.data.datasets[0].data = limited.map(item => item.call_oi_change);
    oiChart.data.datasets[1].data = limited.map(item => item.put_oi_change);

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

    if (oiChart.data.labels.length >= 30) {
        oiChart.data.labels.shift();
        oiChart.data.datasets[0].data.shift();
        oiChart.data.datasets[1].data.shift();
    }

    oiChart.data.labels.push(label);
    oiChart.data.datasets[0].data.push(data.call_oi_change);
    oiChart.data.datasets[1].data.push(data.put_oi_change);
    oiChart.update('none');
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
