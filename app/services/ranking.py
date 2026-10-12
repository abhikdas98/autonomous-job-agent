import re

TARGET_TERMS = {
    "agentic ai":16, "agentic":12, "generative ai":14, "genai":14, "llm":10,
    "langgraph":10, "langchain":8, "rag":8, "ai engineer":12, "gen ai":10,
    "llm engineer":12, "ai agents":10, "python":5, "fastapi":4,
    "vector database":4, "prompt engineering":4,
}
NEGATIVE_TERMS = {"sales executive":18, "business development":15, "data entry":20}

def rank_job(title, company, location, description):
    text = f"{title} {description}".lower()
    score, reasons = 0, []
    for term, points in TARGET_TERMS.items():
        if term in text:
            score += points
            reasons.append(f"+{points}: {term}")
    for term, penalty in NEGATIVE_TERMS.items():
        if term in title.lower():
            score -= penalty
            reasons.append(f"-{penalty}: {term}")
    if re.search(r"\b(senior|lead|staff|principal)\b", title.lower()):
        reasons.append("Seniority keyword detected; validate experience requirements")
    if location and any(x in location.lower() for x in ("india","remote","hyderabad","bengaluru","bangalore","gurugram","gurgaon","pune","noida")):
        score += 4
        reasons.append("+4: preferred geography/remote keyword")
    return round(max(0, min(100, score)), 2), reasons
