// tourAutoplay.js — auto-fires the matching `/tour-<x>` slash command the
// first time the user opens a tool modal. One-shot per modal.
//
// Mobile is excluded — tours position halos by rect math that doesn't fit
// the bottom-sheet layout cleanly.

import { handleSlashCommand } from './slashCommands.js';
import { isTourActive, isElementVisible, hasSeenTour, markTourSeen, observeModals } from './tour-core.js';

// Modal id → slash command to fire (without the leading "/"). Add to this
// map when a new feature picks up a `tour-*` command.
const TOUR_FOR_MODAL = {
  'doclib-modal':           'tour-library',
  'cookbook-modal':         'tour-cookbook',
  'research-overlay':       'tour-research',
  'compare-model-overlay':  'tour-compare',
  'theme-modal':            'tour-theme',
  'settings-modal':         'tour-settings',
  'gallery-modal':          'tour-gallery',
};

const SEEN_KEY = (tour) => `odysseus-tour-autoplay-seen-${tour}`;

let _initialized = false;

async function _maybeFire(modal) {
  const id = modal.id;
  const tour = TOUR_FOR_MODAL[id];
  if (!tour) return;
  if (isTourActive()) {
    try { window.cancelActiveTour?.('modal-opened'); } catch (_) {}
    return;
  }
  if (hasSeenTour(SEEN_KEY(tour))) return;
  markTourSeen(SEEN_KEY(tour));

  // Let the modal's own enter-animation settle before halos try to position
  // off the title bar / first card / etc. ~400ms matches tourHints.
  setTimeout(() => {
    if (isTourActive()) return;
    try {
      handleSlashCommand('/' + tour);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn(`Tour autoplay failed for ${id}:`, e);
    }
  }, 400);
}

export function init() {
  if (_initialized) return;
  _initialized = true;
  observeModals(Object.keys(TOUR_FOR_MODAL), _maybeFire);
}

if (typeof window !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}

export default { init };
