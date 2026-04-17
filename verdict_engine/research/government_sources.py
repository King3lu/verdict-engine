"""
Government source search: WHO, CDC, NIH.
Returns structured position summaries, not research papers.
"""
import json
from typing import Callable, Dict, List
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import requests

TIMEOUT = 15
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def search_government_sources(claim: str, q: str) -> Dict[str, Callable]:
    """Return task dict for government sources, keyed by source name."""
    return {
        "who": lambda: search_who(q),
        "cdc": lambda: search_cdc(q),
        "nih": lambda: search_nih(claim),
    }


def search_who(q: str) -> List[dict]:
    search_term = quote_plus(q.replace("+", " "))
    url = (
        f"https://www.who.int/api/news/searchindexes"
        f"?$search={search_term}&$top=5"
    )
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    positions = []
    for item in (data.get("value") or [])[:5]:
        positions.append({
            "title":   item.get("Title", ""),
            "url":     item.get("Url", ""),
            "date":    item.get("PublicationDate", ""),
            "summary": item.get("Summary", "")[:300],
        })
    return positions


def search_cdc(q: str) -> List[dict]:
    url = f"https://tools.cdc.gov/api/v2/resources/media?q={q}&max=5"
    req = Request(url, headers=REQUEST_HEADERS)
    with urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    positions = []
    for item in data.get("results", [])[:5]:
        positions.append({
            "title":   item.get("name", ""),
            "url":     item.get("targetUrl", item.get("sourceUrl", "")),
            "date":    item.get("datePublished", ""),
            "summary": item.get("description", "")[:300],
        })
    return positions


def search_nih(claim: str) -> List[dict]:
    url = "https://api.reporter.nih.gov/v2/projects/search"
    payload = json.dumps({
        "criteria": {
            "text_search": {
                "operator": "and",
                "search_field": "all",
                "terms": claim,
            }
        },
        "limit": 5,
        "offset": 0,
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    positions = []
    for item in data.get("results", [])[:5]:
        positions.append({
            "title":   item.get("project_title", ""),
            "url":     f"https://reporter.nih.gov/project-details/{item.get('appl_id', '')}",
            "date":    str(item.get("fiscal_year", "")),
            "summary": (item.get("abstract_text") or "")[:300],
        })
    return positions
