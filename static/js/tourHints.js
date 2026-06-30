// tourHints.js — secret continuation of /tour. The first time the user opens
// a tool modal (after the welcome experience), surface a single "pro tip"
// hint pointing out that modals can be snapped to the screen edge or
// fullscreened by dragging the title bar. Shown once globally.

import { isTourActive, hasSeenTour, markTourSeen, observeModals } from './tour-core.js';

const HINT_SEEN_KEY = 'odysseus-hint-drag-to-snap-seen';

// Allow-list of modals where the snap/fullscreen hint makes sense.
const SHOW_MODALS = [
  'email-lib-modal',
  'calendar-modal',
  'compare-modal',
  'cookbook-modal',
  'gallery-modal',
  'doclib-modal',
  'library-modal',
  'memory-modal',
  'tasks-modal',
  'theme-modal',
];

let _shown = false;
let _initialized = false;

function _onModalOpened(modal) {
  if (_shown || hasSeenTour(HINT_SEEN_KEY)) return;
  
  // Don't interrupt the welcome / tour itself
  if (isTourActive()) return;
  // Mobile: skip — snapping is a desktop feature
  if (window.innerWidth <= 768) return;

  _shown = true;
  // Give the modal a moment to settle (some open with their own animation).
  setTimeout(() => _show(modal), 380);
}

function _show(modal) {
  if (hasSeenTour(HINT_SEEN_KEY)) return;
  const content = modal.querySelector('.modal-content') || modal;
  const r = content.getBoundingClientRect();

  const pop = document.createElement('div');
  pop.className = 'tour-hint';
  pop.innerHTML = `
    <div class="tour-hint-visual" aria-hidden="true">
      <svg viewBox="0 0 100 60" width="160" height="96">
        <!-- ambient frame -->
        <rect x="0.5" y="0.5" width="99" height="59" rx="3" fill="none" stroke="currentColor" stroke-opacity="0.18" />
        <!-- snap-zone preview (right half) -->
        <rect class="th-zone" x="51" y="2" width="47" height="56" rx="2" fill="currentColor" opacity="0" />
        <!-- the modal being dragged -->
        <g class="th-modal-group">
          <rect x="22" y="20" width="34" height="22" rx="2.5" fill="var(--bg)" stroke="currentColor" stroke-width="1.2" />
          <rect x="22" y="20" width="34" height="5"  rx="2.5" fill="currentColor" opacity="0.35" />
        </g>
        <!-- cursor -->
        <path class="th-cursor" d="M0 0 L0 9 L2.5 7 L4.5 10 L6 9 L4 6 L7 6 Z" fill="currentColor" />
      </svg>
    </div>
    <div class="tour-hint-text"><b>Pro tip:</b> drag any window's title bar to a screen edge to snap it. Drag to the top for fullscreen.</div>
    <button class="tour-hint-dismiss" type="button">Got it</button>
  `;
  document.body.appendChild(pop);

  // Prefer placing to the right of the modal; fall back to left, then below.
  pop.style.opacity = '0';
  requestAnimationFrame(() => {
    const pw = pop.offsetWidth || 260;
    const ph = pop.offsetHeight || 200;
    let left = r.right + 14;
    let top  = r.top;
    if (left + pw > window.innerWidth - 8) {
      left = r.left - pw - 14;
      if (left < 8) {
        left = Math.max(8, r.left + (r.width - pw) / 2);
        top  = r.bottom + 14;
        if (top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 14);
      }
    }
    pop.style.left = left + 'px';
    pop.style.top  = top  + 'px';
    pop.style.opacity = '';
    pop.classList.add('tour-hint-in');
  });

  const dismiss = () => {
    pop.classList.add('tour-hint-out');
    setTimeout(() => pop.remove(), 280);
    markTourSeen(HINT_SEEN_KEY);
  };
  pop.querySelector('.tour-hint-dismiss').addEventListener('click', dismiss);
  // Auto-dismiss after 14s so it doesn't linger forever.
  setTimeout(() => { if (pop.isConnected) dismiss(); }, 14000);
}

export function init() {
  if (_initialized) return;
  _initialized = true;
  if (hasSeenTour(HINT_SEEN_KEY)) return;
  observeModals(SHOW_MODALS, _onModalOpened);
}

if (typeof window !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}

export default { init };
