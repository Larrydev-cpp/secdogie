"""SecDogie MCP Guard: spiking prefrontal cortex + world-model trajectory gate for AI agents.

Exposes SecDogie's 1,280-neuron SNN Cortex (``deliberate``) and multi-step
world-model causal closure engine (``WorldTrajectory``) over the Model Context
Protocol (JSON-RPC 2.0 on stdio) with zero external dependencies, so OpenClaw,
Claude Code, Cursor, Windsurf, or any MCP-compatible agent can gate both direct
risky requests and multi-step compositional (Semantic Chaining) tool trajectories
in milliseconds on local CPU—with zero regular expressions or word lists.
"""
from __future__ import annotations

import json
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .cortex import questions as Q
from .deliberate import (
    CLARIFY,
    DISSUADE,
    PROCEED,
    REFUSE,
    SUPPORT,
    TALK,
    Deliberation,
    default_cortex,
    deliberate,
    language,
)
from .mind.trajectory import TrajectoryAssessment, WorldTrajectory, infer_effects

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "secdogie-mcp"
SERVER_VERSION = "1.2.0"

# Effects that always require explicit human approval even if the cortex leans toward proceeding.
IRREVERSIBLE_EFFECTS = frozenset({"removes", "erases", "closes", "pays", "lends", "tells", "runs"})
APPROVAL_TTL_SECONDS = 300.0


@dataclass
class ApprovalTicket:
    ticket_id: str
    code: str  # 6-digit human verification code (matches Studio / Phone WebRTC flow)
    action: str
    tool_name: str
    verdict: str
    category: str
    created_at: float
    expires_at: float
    status: str = "pending"  # pending | approved | denied | expired
    resolved_by: str = ""

    def is_expired(self, now: float | None = None) -> bool:
        t = time.time() if now is None else now
        return t > self.expires_at

    def view(self, now: float | None = None) -> dict[str, Any]:
        status = "expired" if (self.status == "pending" and self.is_expired(now)) else self.status
        return {
            "ticket_id": self.ticket_id,
            "code": self.code,
            "action": self.action,
            "tool_name": self.tool_name,
            "verdict": self.verdict,
            "category": self.category,
            "status": status,
            "expires_in": max(0, int(self.expires_at - (time.time() if now is None else now))),
            "resolved_by": self.resolved_by,
        }


@dataclass
class ApprovalStore:
    """In-memory ticket store for 6-digit human-in-the-loop approvals (Studio / Phone P2P bridge)."""

    tickets: dict[str, ApprovalTicket] = field(default_factory=dict)
    ttl: float = APPROVAL_TTL_SECONDS

    def create(
        self,
        action: str,
        *,
        tool_name: str = "",
        verdict: str = DISSUADE,
        category: str = "thinking",
        now: float | None = None,
    ) -> ApprovalTicket:
        t = time.time() if now is None else now
        ticket_id = secrets.token_hex(8)
        code = f"{secrets.randbelow(1_000_000):06d}"
        ticket = ApprovalTicket(
            ticket_id=ticket_id,
            code=code,
            action=action,
            tool_name=tool_name,
            verdict=verdict,
            category=category,
            created_at=t,
            expires_at=t + self.ttl,
        )
        self.tickets[ticket_id] = ticket
        return ticket

    def resolve(
        self,
        ticket_id: str,
        *,
        approve: bool,
        code: str = "",
        actor: str = "person",
        now: float | None = None,
    ) -> ApprovalTicket | None:
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            return None
        if ticket.status != "pending":
            return ticket
        if ticket.is_expired(now):
            ticket.status = "expired"
            return ticket
        if approve and code.strip() != ticket.code:
            return ticket
        ticket.status = "approved" if approve else "denied"
        ticket.resolved_by = actor
        return ticket

    def get(self, ticket_id: str) -> ApprovalTicket | None:
        return self.tickets.get(ticket_id)


def _format_gate_result(
    action: str,
    d: Deliberation,
    *,
    latency_ms: float,
    tool_name: str = "",
    irreversible: bool = False,
    store: ApprovalStore | None = None,
    now: float | None = None,
    trajectory_assessment: TrajectoryAssessment | None = None,
) -> dict[str, Any]:
    """Serialize a ``Deliberation`` + optional ``TrajectoryAssessment`` into a fail-closed MCP guard decision."""
    effect = d.effect or (d.answers.get("effect", ("",))[0] if d.answers else "")
    verdict = d.verdict
    category = d.category
    final = d.final
    source = d.source

    # Layer 2: World-model causal trajectory override (only ever tightens, never loosens).
    if trajectory_assessment is not None and trajectory_assessment.blocked:
        if trajectory_assessment.verdict == REFUSE and verdict != SUPPORT:
            verdict = REFUSE
            category = "trajectory"
            final = True
            source = "world-trajectory"
        elif trajectory_assessment.verdict == DISSUADE and verdict not in (REFUSE, SUPPORT):
            verdict = DISSUADE
            category = "trajectory"
            source = "world-trajectory"

    hard_block = verdict in (REFUSE, SUPPORT)
    high_risk_effect = effect in IRREVERSIBLE_EFFECTS
    needs_approval = (not hard_block) and (
        verdict in (DISSUADE, CLARIFY) or irreversible or high_risk_effect
    )
    allow = (verdict == PROCEED) and (not needs_approval) and (not hard_block)

    ticket_view = None
    if needs_approval and store is not None:
        ticket = store.create(
            action,
            tool_name=tool_name,
            verdict=verdict,
            category=category,
            now=now,
        )
        ticket_view = ticket.view(now=now)

    u = d.understanding or {}
    answers_out: dict[str, dict[str, Any]] = {}
    lang = language(action)
    for q in Q.QUESTIONS:
        if q.id in d.answers:
            ans, sure = d.answers[q.id][0], float(d.answers[q.id][1])
            answers_out[q.id] = {
                "answer": ans,
                "confidence": round(sure, 4),
                "meaning": q.meaning(ans, lang),
            }

    return {
        "allow": allow,
        "verdict": verdict,
        "category": category,
        "final": final,
        "requires_human_approval": needs_approval,
        "tool_name": tool_name,
        "effect": effect,
        "sure": round(d.sure, 4),
        "novelty": round(float(u.get("novelty", 0.0)), 4),
        "strange": list(u.get("strange", ())),
        "generation": u.get("generation", ""),
        "latency_ms": round(latency_ms, 3),
        "says": d.says,
        "source": source,
        "question": (
            {
                "qid": d.question.qid,
                "text": d.question.text,
                "options": [{"id": a, "label": lab} for a, lab in d.question.options],
            }
            if d.question is not None
            else None
        ),
        "approval": ticket_view,
        "answers": answers_out,
        "trajectory": trajectory_assessment.to_dict() if trajectory_assessment is not None else None,
    }


def evaluate_action(
    action: str,
    *,
    cortex=None,
    tool_name: str = "",
    irreversible: bool = False,
    store: ApprovalStore | None = None,
    now: float | None = None,
    effects: tuple[str, ...] | list[str] = (),
    trajectory: WorldTrajectory | None = None,
    commit_trajectory: bool = True,
) -> dict[str, Any]:
    """Run SecDogie's spiking cortex + world-model trajectory gate over a candidate agent action."""
    text = (action or "").strip()
    if not text:
        return {
            "allow": False,
            "verdict": TALK,
            "category": "empty",
            "final": False,
            "requires_human_approval": False,
            "tool_name": tool_name,
            "effect": "",
            "sure": 0.0,
            "novelty": 0.0,
            "strange": [],
            "generation": "",
            "latency_ms": 0.0,
            "says": "",
            "source": "",
            "question": None,
            "approval": None,
            "answers": {},
            "trajectory": None,
        }
    c = cortex if cortex is not None else default_cortex()
    t0 = time.perf_counter()
    d = deliberate(text, cortex=c, now=now)

    traj_assessment: TrajectoryAssessment | None = None
    proposed_effects = infer_effects(effects=effects, tool_name=tool_name, irreversible=irreversible)
    if trajectory is not None and proposed_effects:
        traj_assessment = trajectory.simulate(proposed_effects, tool=tool_name, label=text)

    latency_ms = (time.perf_counter() - t0) * 1000.0
    result = _format_gate_result(
        text,
        d,
        latency_ms=latency_ms,
        tool_name=tool_name,
        irreversible=irreversible,
        store=store,
        now=now,
        trajectory_assessment=traj_assessment,
    )
    if (
        trajectory is not None
        and commit_trajectory
        and proposed_effects
        and (traj_assessment is None or not traj_assessment.blocked)
        and result["verdict"] not in (REFUSE, SUPPORT)
    ):
        trajectory.commit(proposed_effects, tool=tool_name, label=text, verdict=result["verdict"])
    return result


def guard_tool_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    intent: str = "",
    irreversible: bool = False,
    cortex=None,
    approver: Callable[[dict[str, Any]], bool] | None = None,
    store: ApprovalStore | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Wrap any external MCP/OpenClaw tool invocation with SecDogie's spiking gate.

    Safety boundary:
    - ``refuse`` and ``support`` verdicts ALWAYS fail closed and can NEVER be bypassed
      by an ``approver`` callback.
    - ``dissuade``, ``clarify``, or irreversible steps require an explicit ``True`` from
      ``approver(decision)`` (representing a human approval on Studio or a paired phone).
    """
    args = arguments or {}
    summary = intent.strip() if intent and intent.strip() else f"{tool_name} {json.dumps(args, ensure_ascii=False, sort_keys=True)}"
    decision = evaluate_action(
        summary,
        cortex=cortex,
        tool_name=tool_name,
        irreversible=irreversible,
        store=store,
        now=now,
    )
    if decision["verdict"] in (REFUSE, SUPPORT):
        decision["allow"] = False
        decision["approved_by_human"] = False
        return decision
    if decision["requires_human_approval"]:
        approved = bool(approver(decision)) if approver is not None else False
        decision["approved_by_human"] = approved
        decision["allow"] = approved
        return decision
    decision["approved_by_human"] = False
    return decision


MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "secdogie_gate_check",
        "description": (
            "Evaluate a user request or planned agent tool call through SecDogie's local "
            "spiking prefrontal cortex + multi-step world-model trajectory engine (<5ms on CPU). "
            "Blocks both direct harms and multi-step compositional (Semantic Chaining) attacks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Natural-language description of the request or planned tool action.",
                },
                "tool_name": {
                    "type": "string",
                    "description": "Optional name of the downstream tool (e.g. shell_exec, read_secret, http_post).",
                },
                "irreversible": {
                    "type": "boolean",
                    "description": "Set true if the tool action cannot be undone; enforces explicit authorization.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session ID for cross-turn world-model trajectory tracking.",
                },
                "effects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional structured system-state effect atoms (e.g. secret_in_context, network_egress).",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "secdogie_session_trajectory",
        "description": (
            "Inspect or reset the accumulated world-model state atoms for a session trajectory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session identifier (default: 'default')."},
                "reset": {"type": "boolean", "description": "If true, clear accumulated state for this session."},
            },
        },
    },
    {
        "name": "secdogie_resolve_approval",
        "description": (
            "Approve or deny a pending local operator authorization ticket issued by secdogie_gate_check."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "Pending authorization ticket ID."},
                "approve": {"type": "boolean", "description": "True to approve, False to deny."},
                "code": {"type": "string", "description": "Verification code (required when approve=true)."},
            },
            "required": ["ticket_id", "approve"],
        },
    },
    {
        "name": "secdogie_cortex_status",
        "description": (
            "Inspect the active local SecDogie spiking cortex: generation number, neuron & synapse "
            "counts, digest, familiarity mode, independence ledger, and active trajectory sessions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


class MCPGuardServer:
    """Zero-dependency JSON-RPC 2.0 MCP server wrapping SecDogie's spiking cortex and world-model trajectory."""

    def __init__(self, *, cortex=None, store: ApprovalStore | None = None, clock: Callable[[], float] = time.time):
        self._cortex = cortex
        self.store = store if store is not None else ApprovalStore()
        self.clock = clock
        self.sessions: dict[str, WorldTrajectory] = {}

    def trajectory(self, session_id: str = "default") -> WorldTrajectory:
        sid = (session_id or "default").strip() or "default"
        if sid not in self.sessions:
            self.sessions[sid] = WorldTrajectory(session_id=sid)
        return self.sessions[sid]

    def cortex(self):
        if self._cortex is None:
            self._cortex = default_cortex()
        return self._cortex

    def _cortex_status(self) -> dict[str, Any]:
        c = self.cortex()
        fam_mode = getattr(getattr(c, "familiar", None), "mode", "bloom")
        net = getattr(c, "net", None)
        ledger_view = c.ledger.view() if hasattr(c, "ledger") and hasattr(c.ledger, "view") else {}
        return {
            "generation": getattr(c, "number", 0),
            "digest": c.digest()[:16] if hasattr(c, "digest") else "",
            "neurons": getattr(net, "n", 0) if net is not None else 0,
            "synapses": getattr(net, "synapses", 0) if net is not None else 0,
            "familiar_mode": fam_mode,
            "scores": dict(getattr(c, "scores", {})),
            "ledger": ledger_view,
            "pending_approvals": sum(1 for t in self.store.tickets.values() if t.status == "pending"),
            "active_sessions": len(self.sessions),
        }

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "secdogie_gate_check":
            action = str(arguments.get("action", ""))
            tool_name = str(arguments.get("tool_name", ""))
            irreversible = bool(arguments.get("irreversible", False))
            session_id = str(arguments.get("session_id", "")).strip()
            raw_effects = arguments.get("effects") or []
            effects = [str(e) for e in raw_effects] if isinstance(raw_effects, list) else []
            traj = self.trajectory(session_id) if (session_id or effects) else None
            payload = evaluate_action(
                action,
                cortex=self.cortex(),
                tool_name=tool_name,
                irreversible=irreversible,
                store=self.store,
                now=self.clock(),
                effects=effects,
                trajectory=traj,
            )
            return {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                "structuredContent": payload,
                "isError": False,
            }
        if name == "secdogie_session_trajectory":
            session_id = str(arguments.get("session_id", "default"))
            traj = self.trajectory(session_id)
            if bool(arguments.get("reset", False)):
                traj.reset()
            payload = traj.snapshot()
            return {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                "structuredContent": payload,
                "isError": False,
            }
        if name == "secdogie_resolve_approval":
            ticket_id = str(arguments.get("ticket_id", ""))
            approve = bool(arguments.get("approve", False))
            code = str(arguments.get("code", ""))
            ticket = self.store.resolve(ticket_id, approve=approve, code=code, now=self.clock())
            if ticket is None:
                err = {"error": "unknown_ticket", "ticket_id": ticket_id}
                return {
                    "content": [{"type": "text", "text": json.dumps(err, ensure_ascii=False)}],
                    "structuredContent": err,
                    "isError": True,
                }
            payload = ticket.view(now=self.clock())
            return {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                "structuredContent": payload,
                "isError": False,
            }
        if name == "secdogie_cortex_status":
            payload = self._cortex_status()
            return {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                "structuredContent": payload,
                "isError": False,
            }
        raise KeyError(name)

    def handle_message(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Process one JSON-RPC 2.0 request or notification. Returns None for notifications."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return {
                "jsonrpc": "2.0",
                "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"},
            }
        method = msg["method"]
        req_id = msg.get("id")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        # Notifications carry no id and expect no reply.
        if req_id is None and method.startswith("notifications/"):
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}
        if method == "tools/call":
            tool_name = str(params.get("name", ""))
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Tool arguments must be an object"},
                }
            try:
                result = self._call_tool(tool_name, arguments)
            except KeyError:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
                }
            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def serve_stdio(self, stdin=None, stdout=None) -> None:
        """Run line-delimited JSON-RPC 2.0 MCP loop on stdio until EOF."""
        inp = sys.stdin if stdin is None else stdin
        out = sys.stdout if stdout is None else stdout
        for raw in inp:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                out.write(json.dumps(resp, ensure_ascii=False) + "\n")
                out.flush()
                continue
            resp = self.handle_message(msg)
            if resp is not None:
                out.write(json.dumps(resp, ensure_ascii=False) + "\n")
                out.flush()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--check" in args:
        idx = args.index("--check")
        prompt = " ".join(args[idx + 1 :]).strip()
        if not prompt:
            sys.stderr.write("Usage: secdogie-mcp --check <action sentence>\n")
            return 2
        res = evaluate_action(prompt)
        sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
        return 0 if res["allow"] else 3
    if "--help" in args or "-h" in args:
        sys.stdout.write(
            "secdogie-mcp — Local 1,280-neuron spiking safety gate for OpenClaw, Claude Code, Cursor & MCP agents.\n"
            "  secdogie-mcp                  Start MCP JSON-RPC 2.0 server on stdio\n"
            "  secdogie-mcp --check <text>   Evaluate one action immediately (exit 0=allow, 3=blocked/approval)\n"
        )
        return 0
    MCPGuardServer().serve_stdio()
    return 0


__all__ = [
    "APPROVAL_TTL_SECONDS",
    "IRREVERSIBLE_EFFECTS",
    "MCP_PROTOCOL_VERSION",
    "MCP_TOOLS",
    "SERVER_NAME",
    "SERVER_VERSION",
    "ApprovalStore",
    "ApprovalTicket",
    "MCPGuardServer",
    "evaluate_action",
    "guard_tool_call",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
