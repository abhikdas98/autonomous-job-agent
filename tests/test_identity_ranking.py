from app.services.identity import fingerprint
from app.services.ranking import rank_job

def test_tracking_params_do_not_change_identity():
    a = fingerprint("AI Engineer","X","India","https://jobs.example/a?utm_source=x&id=4")
    b = fingerprint("AI Engineer","X","India","https://jobs.example/a?id=4")
    assert a == b

def test_ai_role_scores_higher_than_sales():
    ai, _ = rank_job("Generative AI Engineer","X","India","Python LangGraph RAG")
    sales, _ = rank_job("Sales Executive","Y","India","sales business development")
    assert ai > sales
