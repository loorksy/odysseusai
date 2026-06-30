// static/js/tour-core.js
/**
 * Shared tour and hint utilities for onboarding and feature walkthroughs (C30).
 */

export const TOUR_ACTIVE_CLASS = 'tour-active';

export function isTourActive() {
  return document.body.classList.contains(TOUR_ACTIVE_CLASS) || 
         !!document.getElementById('tour-tooltip');
}

export function isElementVisible(el) {
  if (!el || el.classList.contains('hidden')) return false;
  if (el.style.display === 'none') return false;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

export function hasSeenTour(key) {
  try {
    return localStorage.getItem(key) === '1';
  } catch (_) {
    return false;
  }
}

export function markTourSeen(key) {
  try {
    localStorage.setItem(key, '1');
  } catch (_) {}
}

/**
 * Watches a set of modal selectors and triggers onOpenCallback(modalEl) 
 * when any of them transition from hidden to visible.
 */
export function observeModals(modalIds, onOpenCallback) {
  if (typeof MutationObserver === 'undefined') return;

  const watchedIds = new Set(modalIds);

  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.attributeName !== 'class' && m.attributeName !== 'style') continue;
      const el = m.target;
      if (!(el instanceof HTMLElement)) continue;
      if (!watchedIds.has(el.id)) continue;
      
      const wasHidden = !m.oldValue || 
                        /\bhidden\b/.test(m.oldValue) || 
                        /display:\s*none/.test(m.oldValue);
      if (wasHidden && isElementVisible(el)) {
        onOpenCallback(el);
      }
    }
  });

  // Observe already existing modals
  modalIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      observer.observe(el, {
        attributes: true,
        attributeOldValue: true,
        attributeFilter: ['class', 'style']
      });
    }
  });

  // Dynamic observer for new modals appended to document body
  const bodyObserver = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (!m.addedNodes) continue;
      for (const node of m.addedNodes) {
        if (!(node instanceof HTMLElement)) continue;
        if (watchedIds.has(node.id)) {
          observer.observe(node, {
            attributes: true,
            attributeOldValue: true,
            attributeFilter: ['class', 'style']
          });
          if (isElementVisible(node)) {
            onOpenCallback(node);
          }
        }
      }
    }
  });

  bodyObserver.observe(document.body, { childList: true });
}
