import re

TARGET_TERMS = {
    "agentic ai":16, "agentic":12, "generative ai":14, "genai":14, "llm":10,
    "langgraph":10, "langchain":8, "rag":8, "ai engineer":12, "gen ai":10,
    "llm engineer":12, "ai agents":10, "python":5, "fastapi":4,
    "vector database":4, "prompt engineering":4,
}
NEGATIVE_TERMS = {"sales executive":18, "business development":15, "data entry":20}

def rank_job(title, company, location, description, preferences=None):
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

    # User-defined preferences are an optional, small adjustment to the
    # deterministic baseline. Defaults preserve the existing ranking behavior.
    if preferences and preferences.get("source") == "saved":
        title_text = title.casefold()
        location_text = (location or "").casefold()
        for role in preferences.get("preferred_roles", []):
            term = str(role).strip().casefold()
            if term and term in text:
                score += 5
                reasons.append("+5: saved preferred role matched")
        for skill in preferences.get("preferred_skills", []):
            term = str(skill).strip().casefold()
            if term and term in text:
                score += 2
                reasons.append("+2: saved preferred skill matched")
        for term in preferences.get("excluded_terms", []):
            term = str(term).strip().casefold()
            if term and term in title_text:
                score -= 10
                reasons.append("-10: saved excluded title term matched")
        for place in preferences.get("preferred_locations", []):
            term = str(place).strip().casefold()
            if term and term in location_text:
                score += 3
                reasons.append("+3: saved preferred location matched")
                break
    return round(max(0, min(100, score)), 2), reasons
