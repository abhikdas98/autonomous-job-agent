import httpx

from app.config import settings


def search_adzuna_jobs(what, where="India", limit=20):
    if not settings.adzuna_app_id or not settings.adzuna_app_key:
        raise RuntimeError("Configure ADZUNA_APP_ID and ADZUNA_APP_KEY first.")

    url = f"https://api.adzuna.com/v1/api/jobs/{settings.job_country}/search/1"
    params = {
        "app_id": settings.adzuna_app_id,
        "app_key": settings.adzuna_app_key,
        "results_per_page": min(max(limit, 1), 50),
        "what": what,
        "where": where,
        "content-type": "application/json",
    }
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(url, params=params)
            if response.is_error:
                # Do not raise HTTPStatusError: its string representation includes
                # the query URL, which contains the Adzuna app_key.
                raise RuntimeError(
                    f"Adzuna returned HTTP {response.status_code}; query credentials were redacted."
                )
            data = response.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Adzuna request failed ({type(exc).__name__}); request URL redacted."
        ) from None
    except ValueError:
        raise RuntimeError("Adzuna returned invalid JSON; query credentials were redacted.") from None

    result = []
    for item in data.get("results", []):
        result.append({
            "title": item.get("title") or "Untitled",
            "company": (item.get("company") or {}).get("display_name", "Unknown"),
            "location": (item.get("location") or {}).get("display_name", where),
            "url": item.get("redirect_url", ""),
            "description": item.get("description", ""),
            "source": "adzuna",
            "posted_at": item.get("created", ""),
        })
    return [job for job in result if job["url"]]
