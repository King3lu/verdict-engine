# verdict-engine

> Open-source peer-reviewed fact-checking engine. Give it a claim, get back a research-backed verdict scored 0–100.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

---

## Install

```bash
pip install verdict-engine
```

---

## Quick Start

```python
import os
from verdict_engine import run_verdict_pipeline

os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."

result = run_verdict_pipeline("Vitamin D reduces COVID-19 severity")
print(f"{result.verdict_score}/100 — {result.verdict_category}")
print(result.research_summary)
```

That's it. It searches PubMed, Europe PMC, Cochrane, arXiv, WHO, CDC, and NIH in parallel, scores each paper on a transparent 6-dimension rubric, runs two independent Claude passes (synthesis + audit), and returns a structured verdict with sources.

---

## Architecture

```
verdict_engine/
│
├── pipeline.py              # Orchestrates the full pipeline
│
├── models.py                # Core data structures
│   └── VerdictResult, ResearchPaper, ResearchBundle, QualityScore
│
├── research/
│   ├── multi_source.py      # Parallel search: PubMed, Europe PMC, Cochrane, arXiv
│   ├── government_sources.py  # WHO, CDC, NIH
│   └── expert_consensus.py    # Expert aggregation (extensible)
│
├── synthesis/
│   ├── claude_service.py    # Claude Sonnet: primary synthesis
│   │                          Claude Haiku: independent audit/validation
│   └── verdict_scorer.py    # Transparent 6-dimension quality rubric
│
└── analysis/
    ├── bias_detector.py     # Media framing bias (AllSides/Ad Fontes lean labels)
    ├── source_quality.py    # Quality constants + helpers
    └── content_analyzer.py  # Claude Vision (images) + Gemini Files API (video)
```

### Pipeline Flow

```
Claim text
    │
    ▼
[1] Parallel research search (7 sources, ThreadPoolExecutor)
    ├── PubMed (NCBI)
    ├── Europe PMC
    ├── Cochrane Reviews
    ├── arXiv (preprints)
    ├── WHO
    ├── CDC
    └── NIH Reporter
    │
    ▼
[2] Deduplicate by DOI → PMID
    │
    ▼
[3] Quality score each paper (6 dimensions, transparent weights)
    ├── Study design ×0.30  (meta-analysis > RCT > cohort > observational)
    ├── Sample size  ×0.20
    ├── Methodology  ×0.20  (blinded, pre-registered, controls)
    ├── Journal tier ×0.15  (Nature/Lancet/NEJM = top tier)
    ├── Replication  ×0.10
    └── Funding transparency ×0.05
    │
    ▼
[4] Claude Sonnet — primary synthesis → structured JSON verdict
    │
    ▼
[5] Claude Haiku — independent audit → score adjustment + corrections
    │
    ▼
[6] Blend: 60% algorithm score + 40% Claude score → final verdict
    │
    ▼
VerdictResult (score, category, summary, sources, bias analysis)
```

---

## Verdict Score

| Score | Meaning |
|---|---|
| 90–100 | Overwhelming consensus across meta-analyses |
| 70–89 | Strong consensus with minor caveats |
| 50–69 | Mixed evidence or emerging field |
| 30–49 | Contradicted by majority of research |
| 0–29 | False or clearly misleading |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key (synthesis + validation) |
| `GEMINI_API_KEY` | Optional | Google Gemini for video content analysis |
| `NCBI_API_KEY` | Optional | Increases PubMed rate limit |
| `NCBI_EMAIL` | Optional | Email for NCBI API requests |

---

## Module Reference

### Research

```python
from verdict_engine.research.multi_source import search_all_sources

bundle = search_all_sources("ivermectin COVID treatment")
# bundle.papers          → List[ResearchPaper]
# bundle.government_positions → List[dict]  (WHO/CDC/NIH)
# bundle.source_counts   → {"pubmed": 12, "europe_pmc": 8, ...}
# bundle.total_count     → int
```

### Quality Scoring

```python
from verdict_engine.synthesis.verdict_scorer import score_study

score = score_study(
    study_type="meta_analysis",   # meta_analysis|rct|cohort|observational|preprint
    journal="The Lancet",
    sample_size=50_000,
    blinded=True,
    pre_registered=True,
    funding_disclosed=True,
)
print(score.total_quality_score)  # 0–100
print(score.breakdown)            # per-dimension scores + weights
```

### Bias Detection

```python
from verdict_engine.analysis.bias_detector import analyze_source_bias

bias = analyze_source_bias(
    claim="Masks prevent COVID transmission",
    research_summary="...",
    sources=[{"outlet": "NYT", "lean": "left-center", "framing": "..."}],
)
# bias["bias_vs_accuracy"]           → analysis string
# bias["framing_bias_detected"]      → bool
# bias["political_influence_on_score"] → bool
```

### Content Analysis (images + video)

```python
from verdict_engine.analysis.content_analyzer import analyze_content

# Extract claim from screenshot or photo
result = analyze_content("screenshot.png", "image")
print(result["claim_text"])   # extracted verifiable claim
print(result["confidence"])   # 0–100

# Extract claims from TikTok/YouTube/MP4
result = analyze_content("clip.mp4", "video")
print(result["claims"])       # list of verifiable claims
print(result["raw_transcript"])
```

---

## VerdictResult Fields

```python
@dataclass
class VerdictResult:
    claim_text: str
    verdict_score: int              # 0–100 blended score
    confidence_level: int           # 0–100 data confidence
    verdict_category: str           # established_consensus | crystallizing | emerging
                                    # speculative | contradicted | unverifiable
    research_maturity: str          # established | crystallizing | emerging | speculative
    research_summary: str           # 2–3 sentence synthesis
    key_findings: List[str]
    limitations: str
    is_emerging_field: bool
    caveat: Optional[str]
    false_balance_detected: bool
    false_balance_explanation: Optional[str]
    display_strategy: str           # clear_consensus | genuine_debate | fringe_vs_mainstream
    publication_bias_risk: str      # low | medium | high
    political_lean_aggregate: dict
    bias_analysis: str
    political_influence_detected: bool
    score_calculation_breakdown: dict   # full audit trail
    studies_included_count: int
    source_selection_reasoning: str
    sources: List[dict]             # pmid, title, journal, year, url, study_type
```

---

## Contributing

Contributions are welcome. Here's where the highest-value work is:

**Research sources** — add new peer-reviewed databases in `verdict_engine/research/multi_source.py`. Each source is one function returning `List[ResearchPaper]`. The parallel executor picks it up automatically.

**Quality scoring** — improve the rubric in `verdict_engine/synthesis/verdict_scorer.py`. The 6-dimension weights are clearly documented and easy to adjust.

**Expert consensus** — `verdict_engine/research/expert_consensus.py` is intentionally minimal. There's a clear extension point for integrating domain expert databases.

**Bias detection** — expand outlet lean coverage in `verdict_engine/analysis/bias_detector.py`.

### Setup

```bash
git clone https://github.com/King3lu/verdict-engine.git
cd verdict-engine
pip install -e .

export ANTHROPIC_API_KEY=sk-ant-...
python -c "from verdict_engine import run_verdict_pipeline; print(run_verdict_pipeline('Coffee is good for you').verdict_score)"
```

### Guidelines

- Keep `verdict_engine/` free of database dependencies — the package must return pure Python objects
- Every new research source should fail gracefully (never crash the pipeline)
- Scoring changes should be justified with references to methodology literature
- Open an issue before large refactors

---

## Reference Implementation

**[Tru 3lu](https://github.com/King3lu/tru3lu)** is the platform built on top of verdict-engine. It adds:
- FastAPI backend with verdict persistence (SQLAlchemy)
- Community feed, social posts, likes, comments, follows
- Expert crowdsourcing + outreach
- Article discovery via Gemini
- Dispute system with audit trails
- Outlet bias registry (AllSides + Ad Fontes)

verdict-engine handles the science. Tru 3lu handles the platform.

---

## License

Apache 2.0 — free to use, audit, fork, and build on.
