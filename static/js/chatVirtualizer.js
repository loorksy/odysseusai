// chatVirtualizer.js
// Windowing for #chat-history. Keeps on/near-screen messages live; for messages
// far off-screen it detaches their child nodes and pins the pixel height. This
// removes their layout/paint cost (the source of the lag) while keeping scroll
// position stable and preserving real child nodes (so <details> state, code
// highlighting and any per-node listeners survive a round-trip).
//
// Framework-free. Hooks the container, not any append site, so it automatically
// covers user/ai bubbles, agent multi-bubbles, image bubbles and the streaming
// reply without special-casing any of them.

const CONTAINER_ID = 'chat-history';
const LIVE_MARGIN = '2000px';     // keep this much above/below the viewport fully live
const MIN_COLLAPSE_HEIGHT = 40;   // tiny nodes aren't worth collapsing

let io = null;
let mo = null;

function isLive(node) {
  // A node is "live" (must never be detached) if it's the streaming bubble,
  // the thinking spinner, or the tail. .stream-content exists only while a
  // message streams (it's flattened away at finalize), so it's a precise
  // "currently being written" marker that still lets finished bubbles collapse
  // — even mid agent-turn.
  return (
    node === node.parentNode.lastElementChild ||
    node.classList.contains('agent-thinking-dots') ||
    node.querySelector('.stream-content') !== null
  );
}

function collapse(node) {
  try {
    if (node.__vCollapsed) return;
    if (isLive(node)) return;
    const h = node.offsetHeight;
    if (h < MIN_COLLAPSE_HEIGHT) return;

    const kids = [];
    while (node.firstChild) kids.push(node.removeChild(node.firstChild));
    node.__vChildren = kids;
    node.__vCollapsed = true;
    node.style.boxSizing = 'border-box';
    node.style.minHeight = h + 'px';
  } catch (e) { /* never let one node break scrolling */ }
}

function restore(node) {
  try {
    if (!node.__vCollapsed) return;
    const kids = node.__vChildren || [];
    for (const k of kids) node.appendChild(k);
    node.__vChildren = null;
    node.__vCollapsed = false;
    node.style.minHeight = '';
    node.style.boxSizing = '';
  } catch (e) { /* ignore */ }
}

function onIntersect(entries) {
  for (const e of entries) {
    if (e.isIntersecting) restore(e.target);
    else collapse(e.target);
  }
}

function observeChild(node) {
  if (node.nodeType !== 1 || node.__vObserved) return;
  node.__vObserved = true;
  io.observe(node);
}

export function initChatVirtualizer() {
  const box = document.getElementById(CONTAINER_ID);
  if (!box) { console.warn('[virtualizer] #' + CONTAINER_ID + ' not found'); return; }
  if (box.__vInit) return;
  box.__vInit = true;

  io = new IntersectionObserver(onIntersect, {
    root: box,
    rootMargin: LIVE_MARGIN + ' 0px',
  });

  for (const child of Array.from(box.children)) observeChild(child);

  // Direct children only. Streaming mutates deep inside a node, so subtree:false
  // means the stream never trips this observer.
  mo = new MutationObserver((muts) => {
    for (const m of muts) {
      m.addedNodes.forEach(observeChild);
      m.removedNodes.forEach((n) => { if (n.nodeType === 1) io.unobserve(n); });
    }
  });
  mo.observe(box, { childList: true });
}
