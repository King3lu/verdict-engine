from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class ResearchPaper:
    pmid: str
    title: str
    abstract: str
    year: str
    journal: str
    authors: List[str]
    url: str
    doi: Optional[str] = None
    citation_count: int = 0
    study_type: str = "unknown"


@dataclass
class ResearchBundle:
    papers: List[ResearchPaper]
    government_positions: List[dict]
    source_counts: Dict[str, int]
    total_count: int


@dataclass
class QualityScore:
    study_design_score: int
    sample_size_score: int
    methodology_score: int
    journal_tier_score: int
    replication_score: int
    funding_transparency_score: int
    total_quality_score: int
    breakdown: dict


@dataclass
class VerdictResult:
    claim_text: str
    verdict_score: int
    confidence_level: int
    verdict_category: str
    research_maturity: str
    research_summary: str
    key_findings: List[str]
    limitations: str
    is_emerging_field: bool
    caveat: Optional[str]
    false_balance_detected: bool
    false_balance_explanation: Optional[str]
    display_strategy: str
    publication_bias_risk: str
    political_lean_aggregate: dict
    bias_analysis: str
    political_influence_detected: bool
    score_calculation_breakdown: dict
    studies_included_count: int
    source_selection_reasoning: str
    sources: List[dict]
    verdict_id: Optional[str] = None
    generated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
