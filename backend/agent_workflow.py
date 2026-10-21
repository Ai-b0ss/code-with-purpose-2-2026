"""LangGraph runtime for the evidence-backed assessment steps.

Session persistence and human review live in sessions.py. The graph is intentionally
ephemeral: only the versioned, validated result is persisted, and no contacts enter
the graph or model prompt.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.decision import route_candidate
from backend.evaluator import EvaluationError

WORKFLOW_VERSION = "gim-langgraph-v1"


class AssessmentState(TypedDict, total=False):
    application: Any
    answer_ids: dict[str, str]
    eligibility: dict
    peer_results: list[dict]
    adapter: Any
    knowledge: list[dict]
    result: dict


def _retrieve(state: AssessmentState) -> dict:
    from backend.scoring import retrieve

    application = state["application"]
    try:
        knowledge = retrieve(application.desired_direction,
                             f"{application.desired_direction} {application.motivation}")
    except Exception as exc:
        raise EvaluationError("knowledge_unavailable") from exc
    if not knowledge:
        raise EvaluationError("knowledge_unavailable")
    return {"knowledge": knowledge}


def _assess(state: AssessmentState) -> dict:
    from backend.scoring import assess

    result = assess(state["application"], adapter=state.get("adapter"),
                    answer_ids=state.get("answer_ids"), knowledge=state["knowledge"])
    result["eligibility"] = state["eligibility"]
    result["workflow_version"] = WORKFLOW_VERSION
    return {"result": result}


def _route(state: AssessmentState) -> dict:
    result = state["result"]
    result["decision"] = route_candidate(result, state.get("peer_results", []))
    return {"result": result}


def _build_graph():
    graph = StateGraph(AssessmentState)
    graph.add_node("retrieve_graph_context", _retrieve)
    graph.add_node("assess_with_model", _assess)
    graph.add_node("route_by_policy", _route)
    graph.add_edge(START, "retrieve_graph_context")
    graph.add_edge("retrieve_graph_context", "assess_with_model")
    graph.add_edge("assess_with_model", "route_by_policy")
    graph.add_edge("route_by_policy", END)
    return graph.compile()


ASSESSMENT_GRAPH = _build_graph()


def run_assessment(application: Any, *, answer_ids: dict[str, str] | None = None,
                   eligibility: dict, peer_results: list[dict] | None = None,
                   adapter: Any = None) -> dict:
    if eligibility.get("status") != "eligible":
        raise ValueError("eligibility_gate_must_run_before_assessment")
    # The model only receives the scoring field allowlist, but also keep contacts
    # out of transient LangGraph state and any future tracing integration.
    safe_application = application.model_copy(update={
        "applicant_name": "[withheld]", "email": "", "vk_url": "", "birth_date": "",
    })
    state: AssessmentState = {
        "application": safe_application, "answer_ids": answer_ids or {},
        "eligibility": eligibility, "peer_results": peer_results or [],
    }
    if adapter is not None:
        state["adapter"] = adapter
    return ASSESSMENT_GRAPH.invoke(state)["result"]
