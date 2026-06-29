r"""Group-chat reload regression: peer turns must hydrate as their agent (#4964).

In a group chat, each peer agent's turn is cross-persisted into a participant
session as a ``role:'user'`` message whose content is prefixed ``"[name]: "``
(static/js/group.js). The live group view always renders these on the agent
side with the agent's name, but the reload/hydration path used to derive the
bubble side from ``role`` alone, so on reload every peer turn flipped to the
right as "You" (wrong side + wrong sender).

The fix lives entirely in ``sessions.js`` ``_renderHistoryMessage`` (the history
reload/pager path): during group-session hydration only, it extracts the
``[name]:`` prefix into ``_groupPeerName``, strips it from the body, and derives
an effective ``_renderRole`` so the bubble renders on the agent side, named and
colored, instead of as the local user.

These are whole-file source guards: they pin the invariant so a refactor can't
silently regress the side/sender derivation back to role-only.
"""

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SESSIONS = (_REPO / "static" / "js" / "sessions.js").read_text(encoding="utf-8")


def test_peer_extraction_gated_on_group_session():
    # The peer-prefix extraction must be gated on a group session so a normal
    # chat message that merely starts with "[x]:" is never mis-attributed.
    assert "startsWith('[GRP]')" in _SESSIONS
    assert "_groupPeerName" in _SESSIONS
    m = re.search(r"startsWith\('\[GRP\]'\)\)\s*\{(.*?)\n  \}", _SESSIONS, re.S)
    assert m, "expected a group-session-gated peer-extraction block in sessions.js"
    block = m.group(1)
    assert "match(" in block and r"\[" in block, "peer block must parse a [name]: prefix"
    assert "_groupPeerName =" in block, "peer block must set _groupPeerName"


def test_bubble_side_is_not_role_only():
    # The history bubble side class must consult the group-peer-aware effective
    # role, not bare `msg.role`. A role-only side decision is exactly the #4964 bug.
    assert "_renderRole" in _SESSIONS
    side = re.search(r"wrap\.className = 'msg ' \+ \(([^)]*)\)", _SESSIONS)
    assert side, "could not find history-bubble side assignment in sessions.js"
    expr = side.group(1)
    assert "_renderRole" in expr, (
        "bubble side must derive from _renderRole (group-peer aware), "
        f"got role-only side: {expr!r}"
    )
    assert "msg.role === 'user' ?" not in expr, "side reverted to role-only derivation"


def test_group_peer_named_as_sender():
    # When a message is a group peer, the role label is the peer's name (left,
    # named) rather than "You".
    assert re.search(
        r"else if \(_groupPeerName\)\s*\{\s*roleEl\.textContent = _groupPeerName;",
        _SESSIONS,
    ), "expected the role label to use _groupPeerName for group-peer messages"
