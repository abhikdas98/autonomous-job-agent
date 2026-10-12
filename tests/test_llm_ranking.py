import json

import pytest

from app.services.llm_ranking import parse_result


def valid_response():
    return json.dumps({
        "fit_score": 85,
        "rationale": "The role explicitly requires Python, RAG, and LangChain.",
        "matching_skills": ["Python", "RAG", "LangChain"],
    })


def test_parse_result_accepts_valid_json():
    score, rationale, skills = parse_result(valid_response())

    assert score == 85
    assert "Python" in rationale
    assert skills == ["Python", "RAG", "LangChain"]


def test_parse_result_accepts_json_markdown_fence():
    response = f"```json\n{valid_response()}\n```"

    score, _, _ = parse_result(response)

    assert score == 85


@pytest.mark.parametrize(
    "response",
    [
        "",
        "not JSON",
        '{"fit_score": 120, "rationale": "Relevant", "matching_skills": []}',
        '{"fit_score": -1, "rationale": "Relevant", "matching_skills": []}',
        '{"fit_score": "85", "rationale": "Relevant", "matching_skills": []}',
        '{"fit_score": true, "rationale": "Relevant", "matching_skills": []}',
        '{"fit_score": 85, "rationale": "", "matching_skills": []}',
        '{"fit_score": 85, "rationale": "Relevant", "matching_skills": "Python"}',
        '{"fit_score": 85, "rationale": "Relevant", "matching_skills": ["Python"], "extra": true}',
    ],
)
def test_parse_result_rejects_invalid_response(response):
    with pytest.raises((ValueError, TypeError, json.JSONDecodeError)):
        parse_result(response)


def test_parse_result_rejects_more_than_five_skills():
    response = json.dumps({
        "fit_score": 85,
        "rationale": "Relevant role.",
        "matching_skills": ["Python", "RAG", "LangChain", "FastAPI", "LangGraph", "SQL"],
    })

    with pytest.raises(ValueError):
        parse_result(response)