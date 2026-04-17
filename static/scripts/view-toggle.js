/**
 * View toggle — Novice ⇄ Expert. Persists to localStorage.
 * Spec ref: section 3.1 (default novice on first load).
 *
 * TASK 1 NOTE: DEFAULT_MODE is 'expert' in this initial scaffolding so the
 * dashboard looks identical to pre-Plan-3 during build-up. Plan 3 Task 5
 * flips it to 'novice' after the novice content is fully populated.
 */
(function () {
  const STORAGE_KEY = 'dashboard_view_mode';
  const DEFAULT_MODE = 'expert';   // Task 5 flips to 'novice'

  function getMode() {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_MODE;
  }

  function applyMode(mode) {
    document.body.dataset.view = mode;
    const btn = document.getElementById('view-toggle');
    if (!btn) return;
    const otherMode = mode === 'novice' ? 'expert' : 'novice';
    btn.setAttribute('aria-label', `Switch to ${otherMode} view`);
    const labelEl = btn.querySelector('.view-toggle-label');
    if (labelEl) labelEl.textContent = otherMode.charAt(0).toUpperCase() + otherMode.slice(1);
    if (window.lucide) lucide.createIcons();
  }

  function toggle() {
    const next = getMode() === 'novice' ? 'expert' : 'novice';
    localStorage.setItem(STORAGE_KEY, next);
    applyMode(next);
  }

  document.addEventListener('DOMContentLoaded', () => {
    applyMode(getMode());
    const btn = document.getElementById('view-toggle');
    if (btn) btn.addEventListener('click', toggle);
  });
})();
