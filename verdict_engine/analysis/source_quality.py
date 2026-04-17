"""
Source quality constants and helpers — re-exported from synthesis.verdict_scorer.
Import from here for convenience when only doing quality assessment.
"""
from ..synthesis.verdict_scorer import (
    TOP_TIER_JOURNALS,
    HIGH_IMPACT_JOURNALS,
    score_study,
    calculate_verdict_score,
    classify_research_maturity,
)

__all__ = [
    "TOP_TIER_JOURNALS",
    "HIGH_IMPACT_JOURNALS",
    "score_study",
    "calculate_verdict_score",
    "classify_research_maturity",
]
