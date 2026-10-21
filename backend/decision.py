"""Pilot routing after scoring. Admission remains a coordinator decision."""
from __future__ import annotations

from statistics import median

POLICY_VERSION = "gim-routing-pilot-v1"
LOW_SCORE_BOUNDARY = 2.0
MIN_COMPARABLE_PEERS = 5


def route_candidate(result: dict, peer_results: list[dict] | None = None) -> dict:
    """Route a scored application, comparing only the same rubric/criterion."""
    peer_results = peer_results or []
    eligibility = result.get("eligibility", {})
    if eligibility.get("status") in {"ineligible", "no_consent"}:
        return {"policy_version": POLICY_VERSION, "status": "does_not_meet_basic_requirement",
                "may_proceed": False, "verdict": "нет", "needs_additional_questions": False,
                "reason": eligibility.get("reason", "Не выполнено обязательное условие."),
                "comparison": {"peer_count": 0, "available": False}}
    if eligibility.get("status") != "eligible" or result.get("score") is None:
        return {"policy_version": POLICY_VERSION, "status": "needs_review",
                "may_proceed": None, "verdict": "сомнительно", "needs_additional_questions": True,
                "reason": "Нужно проверить возраст или уточнить критерии без оценки. Балл пока не рассчитан.",
                "comparison": {"peer_count": 0, "available": False}}

    score = result["score"]
    if score >= LOW_SCORE_BOUNDARY:
        return {"policy_version": POLICY_VERSION, "status": "coordinator_review",
                "may_proceed": True, "verdict": "да", "needs_additional_questions": False,
                "reason": "Пилотный балл достаточен для рассмотрения координатором; приглашение и приём решает музей.",
                "comparison": {"peer_count": 0, "available": False}}

    criteria = {item["id"]: item["grade"] for item in result.get("criteria", [])}
    same_rubric = [peer for peer in peer_results
                   if peer.get("rubric_version") == result.get("rubric_version")
                   and peer.get("score") is not None]
    strong = [cid for cid, grade in criteria.items() if grade == 3]
    comparison = {"peer_count": len(same_rubric),
                  "available": len(same_rubric) >= MIN_COMPARABLE_PEERS,
                  "strong_criteria": strong, "above_peer_median": []}
    if comparison["available"]:
        for cid in strong:
            grades = [item["grade"] for peer in same_rubric for item in peer.get("criteria", [])
                      if item.get("id") == cid and item.get("grade") in (1, 2, 3)]
            if len(grades) >= MIN_COMPARABLE_PEERS and 3 > median(grades):
                comparison["above_peer_median"].append(cid)
    needs_questions = bool(comparison["above_peer_median"] if comparison["available"] else strong)
    if needs_questions:
        reason = ("При слабом среднем балле один или несколько критериев выше медианы сопоставимых анкет. "
                  "Дополнительные вопросы помогут проверить эту сильную сторону."
                  if comparison["available"] else
                  "Средний балл слабый, но есть сильный критерий. Сопоставимых оценок пока мало; нужна проверка уточняющими вопросами.")
        status = "additional_questions"
    else:
        reason = "Пилотный балл слабый; данных о сильном критерии для дополнительного опроса нет. Решение остаётся у координатора."
        status = "not_prioritized_for_interview"
    return {"policy_version": POLICY_VERSION, "status": status,
            "may_proceed": None if needs_questions else False,
            "verdict": "сомнительно" if needs_questions else "нет",
            "needs_additional_questions": needs_questions,
            "reason": reason, "comparison": comparison}
