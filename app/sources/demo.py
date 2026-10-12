from datetime import date

def search_demo_jobs(what="generative ai engineer", where="India"):
    today = date.today().isoformat()
    return [
        {"title":"Generative AI Engineer (DEMO)","company":"Example AI Labs (DEMO)","location":where,
         "url":"https://example.com/demo/genai-engineer","description":"DEMO ONLY. Build LLM applications, RAG pipelines, LangChain and LangGraph agents using Python and FastAPI.","source":"demo","posted_at":today},
        {"title":"Agentic AI Developer (DEMO)","company":"Sample Systems (DEMO)","location":"Remote, India",
         "url":"https://example.com/demo/agentic-ai-developer","description":"DEMO ONLY. Multi-agent workflows, tool calling, retrieval augmented generation, prompt engineering, Python.","source":"demo","posted_at":today},
        {"title":"Sales Executive (DEMO)","company":"Sample Sales (DEMO)","location":where,
         "url":"https://example.com/demo/sales-executive","description":"DEMO ONLY. Sales targets and business development.","source":"demo","posted_at":today}
    ]
