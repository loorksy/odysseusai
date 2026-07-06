"""Behavioral coverage for the chat-history virtualizer (static/js/chatVirtualizer.js).

The virtualizer windows #chat-history: messages scrolled far off-screen have
their child nodes detached (and pixel height pinned) to drop layout/paint cost,
and re-attached when scrolled back. The one invariant that must never regress is
that a *live* node — the streaming bubble (carries `.stream-content`), the
`agent-thinking-dots` spinner, or the tail — is never detached, or in-flight
output would be lost.

chatVirtualizer.js touches browser globals (document, IntersectionObserver,
MutationObserver) but imports nothing, so we load it under Node with stubbed
globals and a tiny fake DOM, then fire synthetic intersection events and assert
on the observable DOM mutations — behavior-first, per tests/TESTING_STANDARD.md.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "static/js/chatVirtualizer.js"
_HAS_NODE = shutil.which("node") is not None

# Node harness: builds a minimal DOM, loads the real module, drives the
# IntersectionObserver callback, and prints the resulting DOM state as JSON.
# The module source path is passed as argv[2]; the module is imported via a
# data: URL (safe because it has no relative imports).
_HARNESS = r"""
import fs from 'node:fs';

const SOURCE = fs.readFileSync(process.argv[2], 'utf8');

// --- capture IntersectionObserver wiring ---
let ioCallback = null;
let ioRoot = null;
const observed = new Set();
globalThis.IntersectionObserver = class {
  constructor(cb, opts) { ioCallback = cb; ioRoot = opts && opts.root; }
  observe(node) { observed.add(node); }
  unobserve(node) { observed.delete(node); }
};
globalThis.MutationObserver = class { observe() {} };

// --- minimal fake DOM ---
function makeNode({ cls = [], offsetHeight = 200, stream = false } = {}) {
  return {
    nodeType: 1,
    classList: { contains: (c) => cls.includes(c) },
    style: {},
    offsetHeight,
    _kids: [{ nodeType: 1 }, { nodeType: 1 }],   // two real children to stash
    get firstChild() { return this._kids[0] || null; },
    removeChild(c) { const i = this._kids.indexOf(c); if (i >= 0) this._kids.splice(i, 1); return c; },
    appendChild(c) { this._kids.push(c); return c; },
    querySelector(sel) { return (sel === '.stream-content' && stream) ? { nodeType: 1 } : null; },
  };
}

const children = [];
const container = {
  nodeType: 1,
  children,
  get lastElementChild() { return children[children.length - 1] || null; },
};
const add = (n) => { n.parentNode = container; children.push(n); };

globalThis.document = {
  getElementById: (id) => (id === 'chat-history' ? container : null),
};

const m0     = makeNode({ offsetHeight: 200 });               // finished bubble
const stream = makeNode({ offsetHeight: 200, stream: true }); // actively streaming
const think  = makeNode({ cls: ['agent-thinking-dots'] });    // thinking spinner
const m1     = makeNode({ offsetHeight: 200 });               // finished bubble
const tail   = makeNode({ offsetHeight: 200 });               // last child
[m0, stream, think, m1, tail].forEach(add);

const mod = await import('data:text/javascript,' + encodeURIComponent(SOURCE));
mod.initChatVirtualizer();

const out = {};
out.observed_all = [m0, stream, think, m1, tail].every((n) => observed.has(n));
out.root_is_container = ioRoot === container;

// 1) scroll everything off-screen
ioCallback([m0, stream, think, m1, tail].map((target) => ({ target, isIntersecting: false })));

out.m0_collapsed = m0.__vCollapsed === true;
out.m0_min_height = m0.style.minHeight;
out.m0_box_sizing = m0.style.boxSizing;
out.m0_kids_after_collapse = m0._kids.length;
out.m1_collapsed = m1.__vCollapsed === true;
out.stream_collapsed = stream.__vCollapsed === true;
out.stream_kids = stream._kids.length;
out.think_collapsed = think.__vCollapsed === true;
out.tail_collapsed = tail.__vCollapsed === true;

// 2) scroll m0 back into view
ioCallback([{ target: m0, isIntersecting: true }]);
out.m0_restored = m0.__vCollapsed === false;
out.m0_min_height_after_restore = m0.style.minHeight;
out.m0_kids_after_restore = m0._kids.length;

process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def vres():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")
    assert _SRC.exists(), f"missing module under test: {_SRC}"
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=True) as fh:
        fh.write(_HARNESS)
        fh.flush()
        proc = subprocess.run(
            ["node", fh.name, str(_SRC)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert proc.returncode == 0, f"node harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_existing_messages_are_observed(vres):
    assert vres["observed_all"], "init must observe every existing #chat-history child"
    assert vres["root_is_container"], "IntersectionObserver root must be #chat-history"


def test_offscreen_finished_message_is_detached_and_height_pinned(vres):
    assert vres["m0_collapsed"], "a finished off-screen message must collapse"
    assert vres["m0_kids_after_collapse"] == 0, "its child nodes must be detached"
    assert vres["m0_min_height"] == "200px", "collapsed height must be pinned to measured px"
    assert vres["m0_box_sizing"] == "border-box", "border-box keeps pinned height exact"
    assert vres["m1_collapsed"], "a second finished off-screen message must also collapse"


def test_scrolling_back_restores_children_and_clears_height(vres):
    assert vres["m0_restored"], "scrolling a collapsed message back must un-collapse it"
    assert vres["m0_kids_after_restore"] == 2, "the exact child nodes must be re-attached"
    assert vres["m0_min_height_after_restore"] == "", "pinned height must be cleared on restore"


def test_streaming_message_is_never_detached(vres):
    # The regression guard: a node carrying .stream-content is mid-write.
    assert not vres["stream_collapsed"], "a streaming (.stream-content) message must never collapse"
    assert vres["stream_kids"] == 2, "a streaming message's children must stay attached"


def test_thinking_spinner_is_never_detached(vres):
    assert not vres["think_collapsed"], "the agent-thinking-dots spinner must never collapse"


def test_tail_message_is_never_detached(vres):
    assert not vres["tail_collapsed"], "the last child (live/streaming target) must never collapse"
