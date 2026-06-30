"""PolicyEngine — Declarative allow/deny rule evaluation (C83).

Evaluates named policies against a context dict. Each policy is a
list of rules evaluated top-to-bottom; first match wins.

Rule structure:
  {
    "effect":     "allow" | "deny",
    "subjects":   ["user:*", "role:admin"],   # glob patterns
    "actions":    ["read", "write:*"],
    "resources":  ["files/*", "db:users"],
    "conditions": {                           # optional
        "ip_in": ["10.0.0.0/8"],
        "time_before": "18:00",
        "attribute": {"key": "dept", "value": "engineering"}
    }
  }

Features:
  - Named policy sets (register_policy / load_policy)
  - First-match-wins evaluation with explicit deny override
  - Glob matching on subjects, actions, resources
  - Built-in condition evaluators: ip_in, time_before, time_after, attribute
  - Pluggable custom condition evaluators
  - explain() returns full match trace for debugging

Public API:
  pe = PolicyEngine()
  pe.register_policy(name, rules)
  pe.add_condition_evaluator(name, fn)
  result, reason = pe.evaluate(policy_name, context)
  trace  = pe.explain(policy_name, context)
"""
from __future__ import annotations
import fnmatch, ipaddress, logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PolicyEngine:
    """Declarative first-match-wins policy evaluator."""

    def __init__(self):
        self._policies:   Dict[str, List[Dict]] = {}
        self._conditions: Dict[str, Callable]   = {
            "ip_in":        self._cond_ip_in,
            "time_before":  self._cond_time_before,
            "time_after":   self._cond_time_after,
            "attribute":    self._cond_attribute,
        }

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_policy(self, name: str, rules: List[Dict]) -> None:
        self._policies[name] = rules
        logger.debug(f"PolicyEngine: registered policy '{name}' ({len(rules)} rules)")

    def add_condition_evaluator(
        self, name: str, fn: Callable[[Any, Dict], bool]
    ) -> None:
        """Register a custom condition: fn(condition_value, context) -> bool."""
        self._conditions[name] = fn

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self, policy_name: str, context: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Return (allowed, reason). Default deny if no rule matches."""
        rules = self._policies.get(policy_name, [])
        for i, rule in enumerate(rules):
            if self._rule_matches(rule, context):
                effect = rule.get("effect", "deny")
                reason = f"rule[{i}] {effect}: {rule}"
                return (effect == "allow"), reason
        return False, "default deny: no matching rule"

    def explain(
        self, policy_name: str, context: Dict[str, Any]
    ) -> List[Dict]:
        """Return full evaluation trace: one entry per rule with match result."""
        rules = self._policies.get(policy_name, [])
        trace = []
        for i, rule in enumerate(rules):
            matched = self._rule_matches(rule, context)
            trace.append({"index": i, "matched": matched, "rule": rule})
            if matched:
                break
        return trace

    # ------------------------------------------------------------------
    # Internal matching
    # ------------------------------------------------------------------

    def _rule_matches(self, rule: Dict, ctx: Dict) -> bool:
        subject  = ctx.get("subject",  "")
        action   = ctx.get("action",   "")
        resource = ctx.get("resource", "")

        if not self._matches_any(subject,  rule.get("subjects",  ["*"])):
            return False
        if not self._matches_any(action,   rule.get("actions",   ["*"])):
            return False
        if not self._matches_any(resource, rule.get("resources", ["*"])):
            return False

        for cond_name, cond_value in rule.get("conditions", {}).items():
            evaluator = self._conditions.get(cond_name)
            if evaluator and not evaluator(cond_value, ctx):
                return False
        return True

    @staticmethod
    def _matches_any(value: str, patterns: List[str]) -> bool:
        return any(fnmatch.fnmatch(value, p) for p in patterns)

    # ------------------------------------------------------------------
    # Built-in condition evaluators
    # ------------------------------------------------------------------

    @staticmethod
    def _cond_ip_in(allowed_cidrs: List[str], ctx: Dict) -> bool:
        ip_str = ctx.get("ip")
        if not ip_str:
            return False
        try:
            ip = ipaddress.ip_address(ip_str)
            return any(
                ip in ipaddress.ip_network(cidr, strict=False)
                for cidr in allowed_cidrs
            )
        except ValueError:
            return False

    @staticmethod
    def _cond_time_before(t_str: str, ctx: Dict) -> bool:
        now = datetime.now().strftime("%H:%M")
        return now < t_str

    @staticmethod
    def _cond_time_after(t_str: str, ctx: Dict) -> bool:
        now = datetime.now().strftime("%H:%M")
        return now > t_str

    @staticmethod
    def _cond_attribute(spec: Dict, ctx: Dict) -> bool:
        key   = spec.get("key", "")
        value = spec.get("value")
        return ctx.get("attributes", {}).get(key) == value
