/**
 * Trades Page JavaScript
 * Handles fetching, rendering, and filtering trade history
 */

// State
let currentOffset = 0;
let currentLimit = 20;
let hasMore = false;
let isLoading = false;

// DOM Elements
const tradesList = document.getElementById('trades-list');
const loadMoreContainer = document.getElementById('load-more-container');
const loadMoreBtn = document.getElementById('load-more-btn');
const emptyState = document.getElementById('empty-state');
const applyFiltersBtn = document.getElementById('apply-filters');
const filterDays = document.getElementById('filter-days');
const filterStatus = document.getElementById('filter-status');
const filterDirection = document.getElementById('filter-direction');
const footerDays = document.getElementById('footer-days');
const summaryTotal = document.getElementById('summary-total');
const summaryWins = document.getElementById('summary-wins');
const summaryLosses = document.getElementById('summary-losses');
const tradeCardTemplate = document.getElementById('trade-card-template');

/**
 * Format a number as Indian lakhs notation
 */
function formatLakhs(num) {
    if (num === 0 || num === null || num === undefined) return '0';
    const lakhs = num / 100000;
    if (Math.abs(lakhs) >= 1) {
        return (lakhs >= 0 ? '+' : '') + lakhs.toFixed(1) + 'L';
    }
    return (num >= 0 ? '+' : '') + num.toLocaleString('en-IN');
}

/**
 * Format a date string to readable format
 */
function formatDate(dateStr) {
    if (!dateStr) return '--';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-IN', {
        day: '2-digit',
        month: 'short',
        year: 'numeric'
    });
}

/**
 * Format a time string
 */
function formatTime(dateStr) {
    if (!dateStr) return '--';
    const date = new Date(dateStr);
    return date.toLocaleTimeString('en-IN', {
        hour: '2-digit',
        minute: '2-digit'
    });
}

/**
 * Format datetime for timeline
 */
function formatDateTime(dateStr) {
    if (!dateStr) return '--';
    return formatDate(dateStr) + ' ' + formatTime(dateStr);
}

/**
 * Get current filter values
 */
function getFilters() {
    return {
        days: parseInt(filterDays.value) || 30,
        status: filterStatus.value || null,
        direction: filterDirection.value || null
    };
}

/**
 * Fetch trades from API
 */
async function fetchTrades(reset = false) {
    if (isLoading) return;
    isLoading = true;

    if (reset) {
        currentOffset = 0;
        tradesList.innerHTML = '<div class="loading-trades">Loading trades...</div>';
    }

    const filters = getFilters();
    const params = new URLSearchParams({
        limit: currentLimit,
        offset: currentOffset,
        days: filters.days
    });

    if (filters.status) params.append('status', filters.status);
    if (filters.direction) params.append('direction', filters.direction);

    try {
        const response = await fetch(`/api/trades?${params}`);
        const data = await response.json();

        if (reset) {
            tradesList.innerHTML = '';
        }

        renderTrades(data.trades);
        hasMore = data.has_more;
        currentOffset += data.trades.length;

        updateLoadMoreButton();
        updateSummary(data.trades, reset);
        footerDays.textContent = filters.days;

    } catch (error) {
        console.error('Error fetching trades:', error);
        if (reset) {
            tradesList.innerHTML = '<div class="error-message">Error loading trades. Please try again.</div>';
        }
    } finally {
        isLoading = false;
    }
}

/**
 * Update summary counts
 */
let totalTrades = 0;
let totalWins = 0;
let totalLosses = 0;

function updateSummary(trades, reset) {
    if (reset) {
        totalTrades = 0;
        totalWins = 0;
        totalLosses = 0;
    }

    trades.forEach(trade => {
        totalTrades++;
        if (trade.status === 'WON') totalWins++;
        if (trade.status === 'LOST') totalLosses++;
    });

    summaryTotal.textContent = totalTrades;
    summaryWins.textContent = totalWins;
    summaryLosses.textContent = totalLosses;
}

/**
 * Update load more button visibility
 */
function updateLoadMoreButton() {
    if (hasMore) {
        loadMoreContainer.style.display = 'flex';
    } else {
        loadMoreContainer.style.display = 'none';
    }

    // Show empty state if no trades
    if (currentOffset === 0) {
        emptyState.style.display = 'flex';
    } else {
        emptyState.style.display = 'none';
    }
}

/**
 * Render trades to the DOM
 */
function renderTrades(trades) {
    trades.forEach(trade => {
        const card = createTradeCard(trade);
        tradesList.appendChild(card);
    });
}

/**
 * Create a trade card from template
 */
function createTradeCard(trade) {
    const template = tradeCardTemplate.content.cloneNode(true);
    const card = template.querySelector('.trade-card');

    // Add status class to card
    card.classList.add(`status-${trade.status.toLowerCase()}`);

    // Direction badge
    const directionBadge = card.querySelector('.trade-direction-badge');
    directionBadge.textContent = trade.direction === 'BUY_CALL' ? 'CALL' : 'PUT';
    directionBadge.classList.add(trade.direction === 'BUY_CALL' ? 'bullish' : 'bearish');

    // Strike badge
    const strikeBadge = card.querySelector('.trade-strike-badge');
    strikeBadge.textContent = `${trade.strike} ${trade.option_type} (${trade.moneyness})`;

    // Status badge
    const statusBadge = card.querySelector('.trade-status-badge');
    statusBadge.textContent = trade.status;
    statusBadge.classList.add(`status-${trade.status.toLowerCase()}`);

    // Date
    card.querySelector('.trade-card-date').textContent = formatDate(trade.created_at);

    // Timeline
    card.querySelector('.timeline-item.created .timeline-value').textContent = formatTime(trade.created_at);
    card.querySelector('.timeline-item.activated .timeline-value').textContent = trade.activated_at ? formatTime(trade.activated_at) : '--';
    card.querySelector('.timeline-item.resolved .timeline-value').textContent = trade.resolved_at ? formatTime(trade.resolved_at) : '--';

    // Premium levels
    card.querySelector('.premium-item.entry .premium-value').textContent = trade.entry_premium?.toFixed(2) || '--';
    card.querySelector('.premium-item.activation .premium-value').textContent = trade.activation_premium?.toFixed(2) || '--';
    card.querySelector('.premium-item.exit .premium-value').textContent = trade.exit_premium?.toFixed(2) || '--';
    card.querySelector('.premium-item.sl .premium-value').textContent = trade.sl_premium?.toFixed(2) || '--';
    card.querySelector('.premium-item.target .premium-value').textContent = trade.target1_premium?.toFixed(2) || '--';

    // Max/Min reached
    card.querySelector('.extreme-item.max strong').textContent = trade.max_premium_reached?.toFixed(2) || '--';
    card.querySelector('.extreme-item.min strong').textContent = trade.min_premium_reached?.toFixed(2) || '--';

    // P/L Result
    const resultValue = card.querySelector('.result-value');
    const resultPoints = card.querySelector('.result-points');
    const pnlPct = trade.profit_loss_pct || 0;
    const pnlPoints = trade.profit_loss_points || 0;

    resultValue.textContent = `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%`;
    resultValue.classList.add(pnlPct >= 0 ? 'positive' : 'negative');

    resultPoints.textContent = `(${pnlPoints >= 0 ? '+' : ''}${pnlPoints.toFixed(2)} pts)`;
    resultPoints.classList.add(pnlPct >= 0 ? 'positive' : 'negative');

    // Add hit indicator
    if (trade.hit_sl) {
        resultPoints.textContent += ' SL Hit';
    } else if (trade.hit_target) {
        resultPoints.textContent += ' Target Hit';
    }

    // Technical Analysis
    card.querySelector('.verdict-value').textContent = trade.verdict_at_creation || '--';
    card.querySelector('.confidence-value').textContent = trade.signal_confidence ? `${trade.signal_confidence.toFixed(0)}%` : '--';
    card.querySelector('.call-oi-value').textContent = formatLakhs(trade.call_oi_change_at_creation);
    card.querySelector('.put-oi-value').textContent = formatLakhs(trade.put_oi_change_at_creation);
    card.querySelector('.pcr-value').textContent = trade.pcr_at_creation?.toFixed(2) || '--';
    card.querySelector('.max-pain-value').textContent = trade.max_pain_at_creation || '--';
    card.querySelector('.support-value').textContent = trade.support_at_creation || '--';
    card.querySelector('.resistance-value').textContent = trade.resistance_at_creation || '--';
    card.querySelector('.spot-value').textContent = trade.spot_at_creation?.toFixed(2) || '--';
    card.querySelector('.iv-value').textContent = trade.iv_at_creation ? `${trade.iv_at_creation.toFixed(1)}%` : '--';

    // Trade reasoning
    const reasoningText = card.querySelector('.reasoning-text');
    if (trade.trade_reasoning) {
        reasoningText.textContent = trade.trade_reasoning;
    } else {
        // Generate fallback reasoning from available data
        const direction = trade.direction === 'BUY_PUT' ? 'BUY PUT' : 'BUY CALL';
        reasoningText.textContent = `${direction} at ${trade.strike} ${trade.option_type}. ` +
            `Verdict: ${trade.verdict_at_creation || 'N/A'} ` +
            `(${trade.signal_confidence?.toFixed(0) || 'N/A'}% confidence).`;
    }

    return card;
}

/**
 * Initialize event listeners
 */
function init() {
    // Apply filters button
    applyFiltersBtn.addEventListener('click', () => {
        fetchTrades(true);
    });

    // Load more button
    loadMoreBtn.addEventListener('click', () => {
        fetchTrades(false);
    });

    // Initial load
    fetchTrades(true);
}

// Start when DOM is ready
document.addEventListener('DOMContentLoaded', init);
