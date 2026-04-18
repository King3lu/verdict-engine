"""
Multi-source research aggregator.
Searches PubMed, Europe PMC, Cochrane, arXiv, and government sources in parallel.
"""
import json
import os
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError

from ..models import ResearchPaper, ResearchBundle
from .government_sources import search_government_sources

TIMEOUT = 15

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

_HEALTH_KEYWORDS = {
    "vaccine", "vaccination", "drug", "medication", "treatment", "therapy",
    "clinical", "trial", "placebo", "randomized", "randomised", "cohort",
    "cancer", "tumor", "carcinoma", "diabetes", "insulin", "hiv", "aids",
    "covid", "sars", "influenza", "infection", "virus", "bacteria", "pathogen",
    "disease", "syndrome", "disorder", "symptom", "diagnosis", "prognosis",
    "surgery", "hospital", "patient", "mortality", "morbidity", "dose",
    "efficacy", "safety", "adverse", "protein", "gene", "dna", "rna",
    "cell", "immune", "antibody", "antigen", "mutation", "genome",
    "diet", "nutrition", "supplement", "vitamin", "mineral", "obesity",
    "depression", "anxiety", "alzheimer", "dementia", "autism", "adhd",
    "blood", "heart", "lung", "liver", "kidney", "brain", "neuron",
    "lenacapavir", "mrna", "monoclonal", "antibiotic", "antiviral",
}

_PHYSICS_KEYWORDS = {
    "quark", "boson", "higgs", "lepton", "fermion", "hadron",
    "gravitational wave", "black hole", "neutron star", "pulsar",
    "dark matter", "dark energy", "cosmological", "cosmology",
    "quantum field", "particle physics", "collider", "accelerator",
    "exoplanet", "galaxy", "nebula", "telescope", "astrophysics",
}

_HEALTH_CATEGORIES = {"health", "nutrition", "medicine", "science"}

# MeSH term mappings for common health concepts
_MESH_MAP: dict[str, str] = {
    "hiv": "HIV Infections[MeSH Terms]",
    "aids": "Acquired Immunodeficiency Syndrome[MeSH Terms]",
    "covid": "COVID-19[MeSH Terms]",
    "sars": "SARS-CoV-2[MeSH Terms]",
    "influenza": "Influenza, Human[MeSH Terms]",
    "vaccine": "Vaccines[MeSH Terms]",
    "vaccination": "Vaccination[MeSH Terms]",
    "cancer": "Neoplasms[MeSH Terms]",
    "tumor": "Neoplasms[MeSH Terms]",
    "diabetes": "Diabetes Mellitus[MeSH Terms]",
    "insulin": "Insulin[MeSH Terms]",
    "depression": "Depression[MeSH Terms]",
    "alzheimer": "Alzheimer Disease[MeSH Terms]",
    "dementia": "Dementia[MeSH Terms]",
    "antibiotic": "Anti-Bacterial Agents[MeSH Terms]",
    "antiviral": "Antiviral Agents[MeSH Terms]",
    "mrna": "RNA, Messenger[MeSH Terms]",
    "obesity": "Obesity[MeSH Terms]",
    "hypertension": "Hypertension[MeSH Terms]",
    "clinical trial": "Clinical Trials as Topic[MeSH Terms]",
    "gene therapy": "Genetic Therapy[MeSH Terms]",
}

# Drug/compound names with PubMed Supplementary Concept entries
_SUPPLEMENTARY_CONCEPTS: set[str] = {
    "lenacapavir", "remdesivir", "molnupiravir", "nirmatrelvir",
    "dolutegravir", "cabotegravir", "bictegravir", "darunavir",
    "semaglutide", "tirzepatide", "ozempic", "wegovy",
    "paxlovid", "metformin", "atorvastatin",
}

# Title/abstract terms that signal off-topic physics/astronomy papers
_OFFSITE_EXCLUSION = (
    'NOT (quark[Title/Abstract] OR boson[Title/Abstract] OR '
    '"dark matter"[Title/Abstract] OR astrophysics[Title/Abstract] OR '
    'cosmology[Title/Abstract] OR "particle physics"[Title/Abstract] OR '
    'neutron[Title/Abstract] OR "gravitational wave"[Title/Abstract])'
)


def _build_pubmed_query(claim: str, category: str | None = None) -> str:
    """Build a PubMed search query with domain filters to prevent cross-domain pollution."""
    claim_lower = claim.lower()
    words = set(claim_lower.split())

    is_health_category = bool(category and category.lower() in _HEALTH_CATEGORIES)
    has_health_keywords = bool(words & _HEALTH_KEYWORDS) or any(
        kw in claim_lower for kw in _HEALTH_KEYWORDS if " " in kw
    )
    has_physics_keywords = any(kw in claim_lower for kw in _PHYSICS_KEYWORDS)

    if not ((is_health_category or has_health_keywords) and not has_physics_keywords):
        return claim

    parts = [f"({claim})"]

    # Anchor to MeSH terms detected in the claim — ensures papers are actually
    # indexed under these medical concepts, not just containing the words
    mesh_anchors = [mesh for kw, mesh in _MESH_MAP.items() if kw in claim_lower]
    for drug in _SUPPLEMENTARY_CONCEPTS:
        if drug in claim_lower:
            mesh_anchors.append(f"{drug}[Supplementary Concept]")
    if mesh_anchors:
        # OR so we don't over-restrict when a claim spans multiple concepts
        parts.append("AND (" + " OR ".join(mesh_anchors) + ")")

    # Restrict to MEDLINE-indexed biomedical journals
    parts.append("AND medline[sb]")

    # Require human or animal subjects — definitively excludes physics/astronomy
    if is_health_category or has_health_keywords:
        parts.append("AND (humans[MeSH Terms] OR animals[MeSH Terms])")

    # Belt-and-suspenders: block off-topic physics terms from appearing in titles
    parts.append(_OFFSITE_EXCLUSION)

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Study type inference
# ---------------------------------------------------------------------------

def _infer_study_type(text: str, extra: str = "") -> str:
    t = (text + " " + extra).lower()
    if "meta-analysis" in t or "systematic review" in t:
        return "meta_analysis"
    if "randomized" in t or "randomised" in t or " rct " in t:
        return "rct"
    if "cohort" in t:
        return "cohort"
    if "case-control" in t or "case control" in t:
        return "case_control"
    if "cross-sectional" in t:
        return "cross_sectional"
    if "preprint" in t or "biorxiv" in t or "medrxiv" in t:
        return "preprint"
    return "observational"


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------

def _ncbi_params(extra: dict) -> str:
    params = {
        "email": os.environ.get("NCBI_EMAIL", "research@verdict-engine.io"),
        "tool": "verdict-engine",
    }
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key
    params.update(extra)
    return urlencode(params)


def search_pubmed(claim: str, max_results: int = 15, category: str | None = None) -> List[ResearchPaper]:
    try:
        pubmed_query = _build_pubmed_query(claim, category)
        print(f"[verdict-engine] PubMed query: {pubmed_query!r}")
        search_url = (
            f"{NCBI_BASE}/esearch.fcgi?"
            + _ncbi_params({
                "db": "pubmed",
                "term": pubmed_query,
                "retmax": max_results,
                "sort": "relevance",
                "retmode": "json",
            })
        )
        with urlopen(search_url, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        pmids = data.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []

        time.sleep(0.4)

        fetch_url = (
            f"{NCBI_BASE}/efetch.fcgi?"
            + _ncbi_params({
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "xml",
                "rettype": "abstract",
            })
        )
        with urlopen(fetch_url, timeout=20) as resp:
            xml_bytes = resp.read()

        return _parse_pubmed_xml(xml_bytes)

    except (URLError, json.JSONDecodeError) as e:
        print(f"[verdict-engine] PubMed error: {e}")
        return []
    except Exception as e:
        print(f"[verdict-engine] PubMed unexpected error: {e}")
        return []


def _parse_pubmed_xml(xml_bytes: bytes) -> List[ResearchPaper]:
    papers = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"[verdict-engine] PubMed XML parse error: {e}")
        return []

    for article_el in root.findall(".//PubmedArticle"):
        try:
            paper = _extract_paper(article_el)
            if paper:
                papers.append(paper)
        except Exception:
            continue

    return papers


def _extract_paper(article_el: ET.Element) -> ResearchPaper | None:
    def text(path: str) -> str:
        el = article_el.find(path)
        return "".join(el.itertext()).strip() if el is not None else ""

    title = text(".//ArticleTitle")
    if not title:
        return None

    abstract_parts = []
    for ab in article_el.findall(".//AbstractText"):
        part = "".join(ab.itertext()).strip()
        if part:
            abstract_parts.append(part)
    abstract = " ".join(abstract_parts)

    journal = text(".//Journal/Title")
    year = text(".//PubDate/Year") or text(".//PubDate/MedlineDate")[:4]
    pmid = text(".//PMID")

    authors = []
    for author in article_el.findall(".//Author"):
        last_el = author.find("LastName")
        if last_el is not None:
            authors.append(last_el.text or "")

    doi = None
    for id_el in article_el.findall(".//ArticleId"):
        if id_el.get("IdType") == "doi":
            doi = id_el.text
            break

    study_type = _infer_study_type(title + " " + abstract)

    return ResearchPaper(
        pmid=pmid,
        title=title,
        abstract=abstract[:600],
        year=year,
        journal=journal,
        authors=[a for a in authors[:3] if a],
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        doi=doi,
        study_type=study_type,
    )


# ---------------------------------------------------------------------------
# Europe PMC
# ---------------------------------------------------------------------------

def _get_json(url: str) -> dict:
    req = Request(url, headers=REQUEST_HEADERS)
    with urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _search_europe_pmc(q: str) -> List[ResearchPaper]:
    url = (
        f"https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={q}&format=json&resultType=core&pageSize=10"
    )
    data = _get_json(url)
    papers = []
    for r in data.get("resultList", {}).get("result", []):
        title = r.get("title", "").strip()
        if not title:
            continue
        abstract = r.get("abstractText", "")[:600]
        papers.append(ResearchPaper(
            pmid=r.get("pmid", ""),
            title=title,
            abstract=abstract,
            year=str(r.get("pubYear", "")),
            journal=r.get("journalTitle", ""),
            authors=[
                a.get("fullName", "")
                for a in r.get("authorList", {}).get("author", [])[:3]
            ],
            url=(
                f"https://europepmc.org/article/"
                f"{r.get('source', 'MED')}/{r.get('id', '')}"
            ),
            doi=r.get("doi"),
            study_type=_infer_study_type(title + " " + abstract),
        ))
    return papers


def _search_cochrane(q: str) -> List[ResearchPaper]:
    cochrane_query = quote_plus(
        f"{q} (JOURNAL:\"Cochrane Database Syst Rev\" OR SRC:MED)"
    )
    url = (
        f"https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={cochrane_query}&format=json&resultType=core&pageSize=5"
    )
    data = _get_json(url)
    papers = []
    for r in data.get("resultList", {}).get("result", []):
        title = r.get("title", "").strip()
        if not title:
            continue
        abstract = r.get("abstractText", "")[:600]
        doi = r.get("doi")
        papers.append(ResearchPaper(
            pmid=r.get("pmid", ""),
            title=title,
            abstract=abstract,
            year=str(r.get("pubYear", "")),
            journal=r.get("journalTitle", "Cochrane Database of Systematic Reviews"),
            authors=[
                a.get("fullName", "")
                for a in r.get("authorList", {}).get("author", [])[:3]
            ],
            url=f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/MED/{r.get('id', '')}",
            doi=doi,
            study_type="meta_analysis",
        ))
    return papers


def _search_arxiv(q: str) -> List[ResearchPaper]:
    url = f"https://export.arxiv.org/api/query?search_query=all:{q}&max_results=5"
    req = Request(url, headers=REQUEST_HEADERS)
    with urlopen(req, timeout=TIMEOUT) as resp:
        xml_bytes = resp.read()

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_bytes)
    papers = []

    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue

        summary_el = entry.find("atom:summary", ns)
        abstract = (summary_el.text or "")[:600].strip() if summary_el is not None else ""

        id_el = entry.find("atom:id", ns)
        arxiv_id = (id_el.text or "").split("/abs/")[-1] if id_el is not None else ""

        year = ""
        published_el = entry.find("atom:published", ns)
        if published_el is not None and published_el.text:
            year = published_el.text[:4]

        authors = []
        for author in entry.findall("atom:author", ns)[:3]:
            name_el = author.find("atom:name", ns)
            if name_el is not None:
                authors.append(name_el.text or "")

        doi = None
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "doi":
                doi = link.get("href", "").replace("https://doi.org/", "") or None
                break

        papers.append(ResearchPaper(
            pmid="",
            title=title,
            abstract=abstract,
            year=year,
            journal="arXiv (preprint)",
            authors=authors,
            url=f"https://arxiv.org/abs/{arxiv_id}",
            doi=doi,
            study_type="preprint",
        ))
    return papers


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _deduplicate(paper_results: Dict[str, List[ResearchPaper]]) -> List[ResearchPaper]:
    """Deduplicate by DOI first, then PMID. PubMed wins ties."""
    seen_dois: set = set()
    seen_pmids: set = set()
    unique: List[ResearchPaper] = []

    for source in ["pubmed", "europe_pmc", "cochrane", "arxiv"]:
        for paper in paper_results.get(source, []):
            if paper.doi and paper.doi in seen_dois:
                continue
            if paper.pmid and paper.pmid in seen_pmids:
                continue
            if paper.doi:
                seen_dois.add(paper.doi)
            if paper.pmid:
                seen_pmids.add(paper.pmid)
            unique.append(paper)

    return unique


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def search_all_sources(claim: str, category: str | None = None) -> ResearchBundle:
    """Search all configured sources in parallel. Source failures never crash the pipeline."""
    q = quote_plus(claim)

    paper_tasks = {
        "pubmed":     lambda: search_pubmed(claim, max_results=15, category=category),
        "europe_pmc": lambda: _search_europe_pmc(q),
        "cochrane":   lambda: _search_cochrane(q),
        "arxiv":      lambda: _search_arxiv(q),
    }
    gov_tasks = search_government_sources(claim, q)

    all_tasks = {**paper_tasks, **{k: v for k, v in gov_tasks.items()}}
    paper_results: Dict[str, List[ResearchPaper]] = {}
    gov_results: Dict[str, list] = {}

    with ThreadPoolExecutor(max_workers=7) as executor:
        futures = {executor.submit(fn): name for name, fn in all_tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                if name in paper_tasks:
                    paper_results[name] = result
                else:
                    gov_results[name] = result
            except Exception as e:
                print(f"[verdict-engine] {name} failed: {e}")
                if name in paper_tasks:
                    paper_results[name] = []
                else:
                    gov_results[name] = []

    all_papers = _deduplicate(paper_results)

    government_positions = []
    for source, positions in gov_results.items():
        for pos in positions:
            government_positions.append({**pos, "source": source})

    return ResearchBundle(
        papers=all_papers,
        government_positions=government_positions,
        source_counts={name: len(papers) for name, papers in paper_results.items()},
        total_count=len(all_papers),
    )
