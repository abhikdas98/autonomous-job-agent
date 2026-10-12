import hashlib, re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TRACKING_KEYS = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","ref","source","trk","gh_src"}

def canonical_url(url):
    try:
        p = urlsplit(url.strip())
        query = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
                 if k.lower() not in TRACKING_KEYS and not k.lower().startswith("utm_")]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))
    except Exception:
        return url.strip().lower()

def fingerprint(title, company, location, url):
    canonical = canonical_url(url)
    identity = canonical if canonical else "|".join(
        re.sub(r"\s+", " ", x).strip().lower() for x in (title, company, location))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
