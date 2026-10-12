import httpx
from app.config import settings

def search_adzuna_jobs(what, where="India", limit=20):
    if not settings.adzuna_app_id or not settings.adzuna_app_key:
        raise RuntimeError("Configure ADZUNA_APP_ID and ADZUNA_APP_KEY first.")
    url = f"https://api.adzuna.com/v1/api/jobs/{settings.job_country}/search/1"
    params = {"app_id":settings.adzuna_app_id,"app_key":settings.adzuna_app_key,
              "results_per_page":min(max(limit,1),50),"what":what,"where":where,"content-type":"application/json"}
    with httpx.Client(timeout=20) as client:
        r = client.get(url,params=params); r.raise_for_status(); data = r.json()
    result = []
    for x in data.get("results",[]):
        result.append({"title":x.get("title") or "Untitled","company":(x.get("company") or {}).get("display_name","Unknown"),
                       "location":(x.get("location") or {}).get("display_name",where),"url":x.get("redirect_url",""),
                       "description":x.get("description",""),"source":"adzuna","posted_at":x.get("created","")})
    return [j for j in result if j["url"]]
