from backend.decision import route_candidate


def _result(score, grades):
    return {"score": score, "rubric_version": "pilot-v1",
            "eligibility": {"status": "eligible"},
            "criteria": [{"id": key, "grade": value} for key, value in grades.items()]}


def test_only_basic_gate_blocks_eligibility():
    result = _result(None, {})
    result["eligibility"] = {"status": "ineligible", "reason": "under 18"}
    routed = route_candidate(result)
    assert routed["status"] == "does_not_meet_basic_requirement"
    assert routed["may_proceed"] is False
    result["eligibility"] = {"status": "no_consent", "reason": "consent required"}
    assert route_candidate(result)["may_proceed"] is False


def test_low_average_strong_criterion_triggers_questions_without_claiming_comparison():
    result = _result(1.75, {"motivation": 3, "skills": 1})
    routed = route_candidate(result)
    assert routed["status"] == "additional_questions"
    assert routed["comparison"]["available"] is False
    assert routed["may_proceed"] is None


def test_comparable_peers_control_exceptional_strength():
    result = _result(1.75, {"motivation": 3, "skills": 1})
    peers = [_result(2.0, {"motivation": 2, "skills": 2}) for _ in range(5)]
    routed = route_candidate(result, peers)
    assert routed["comparison"]["above_peer_median"] == ["motivation"]
    assert routed["status"] == "additional_questions"
    peers = [_result(2.0, {"motivation": 3, "skills": 2}) for _ in range(5)]
    assert route_candidate(result, peers)["status"] == "not_prioritized_for_interview"


def test_comparison_ignores_old_rubric_and_high_score_goes_to_coordinator():
    result = _result(1.75, {"motivation": 3})
    peers = [{**_result(2.0, {"motivation": 1}), "rubric_version": "old"} for _ in range(10)]
    assert route_candidate(result, peers)["comparison"]["peer_count"] == 0
    assert route_candidate(_result(2.25, {"motivation": 2}), peers)["status"] == "coordinator_review"
