"""
Transparent source quality scoring rubric.
Every study gets scored on 6 dimensions — fully auditable.
"""
from ..models import QualityScore

TOP_TIER_JOURNALS = {
    "nature", "science", "the lancet", "lancet", "new england journal of medicine",
    "nejm", "jama", "bmj", "cell", "pnas", "annals of internal medicine",
    "the journal of the american medical association",
}

HIGH_IMPACT_JOURNALS = {
    "nature medicine", "nature communications", "plos medicine", "plos one",
    "journal of clinical oncology", "circulation", "diabetes care",
    "american journal of public health", "epidemiology",
}


def score_study(
    study_type: str,
    sample_size: int = 0,
    journal: str = "",
    funding_disclosed: bool = True,
    replicated: bool = False,
    replication_count: int = 0,
    pre_registered: bool = False,
    blinded: bool = False,
    controls_confounders: bool = True,
) -> QualityScore:
    design_map = {
        "meta_analysis": 120,
        "rct": 100,
        "cohort": 70,
        "case_control": 60,
        "cross_sectional": 50,
        "observational": 45,
        "case_series": 30,
        "case_report": 10,
        "preprint": 20,
        "opinion": 5,
        "unknown": 40,
    }
    design_score = design_map.get(study_type, 40)
    if blinded:
        design_score = min(design_score + 15, 120)
    design_score = min(design_score, 120)

    if sample_size >= 10_000:
        ss_score = 100
    elif sample_size >= 1_000:
        ss_score = 80
    elif sample_size >= 100:
        ss_score = 60
    elif sample_size > 0:
        ss_score = 40
    else:
        ss_score = 50

    method_score = 50
    if controls_confounders:
        method_score += 20
    if blinded:
        method_score += 15
    if pre_registered:
        method_score += 15
    method_score = min(method_score, 100)

    journal_lower = journal.lower()
    if any(j in journal_lower for j in TOP_TIER_JOURNALS):
        jt_score = 100
    elif any(j in journal_lower for j in HIGH_IMPACT_JOURNALS):
        jt_score = 85
    elif journal_lower:
        jt_score = 65
    else:
        jt_score = 40

    if replication_count >= 3:
        rep_score = 100
    elif replication_count == 2:
        rep_score = 80
    elif replication_count == 1 or replicated:
        rep_score = 60
    else:
        rep_score = 50

    ft_score = 100 if funding_disclosed else 20

    total = int(
        design_score * 0.30 +
        ss_score * 0.20 +
        method_score * 0.20 +
        jt_score * 0.15 +
        rep_score * 0.10 +
        ft_score * 0.05
    )
    total = min(100, total)

    return QualityScore(
        study_design_score=min(design_score, 100),
        sample_size_score=ss_score,
        methodology_score=method_score,
        journal_tier_score=jt_score,
        replication_score=rep_score,
        funding_transparency_score=ft_score,
        total_quality_score=total,
        breakdown={
            "study_design": min(design_score, 100),
            "sample_size": ss_score,
            "methodology": method_score,
            "journal_tier": jt_score,
            "replication": rep_score,
            "funding_transparency": ft_score,
            "weights": "design×0.30, sample×0.20, method×0.20, journal×0.15, replication×0.10, funding×0.05",
        },
    )


def calculate_verdict_score(quality_scores: list[int], consensus_pct: int, recency_avg: int) -> int:
    """
    VERDICT_SCORE = (study_quality_avg × 0.40)
                  + (consensus_strength × 0.35)
                  + (recency × 0.15)
                  + (sample_size_component × 0.10)
    """
    if not quality_scores:
        return 50

    quality_avg = sum(quality_scores) / len(quality_scores)

    if consensus_pct >= 90:
        consensus_score = 100
    elif consensus_pct >= 70:
        consensus_score = 80
    elif consensus_pct >= 50:
        consensus_score = 50
    else:
        consensus_score = 30

    total = int(
        quality_avg * 0.40 +
        consensus_score * 0.35 +
        recency_avg * 0.15 +
        min(quality_avg, 100) * 0.10
    )
    return min(100, max(0, total))


def classify_research_maturity(study_count: int, consensus_pct: int, years_of_data: int) -> str:
    """
    established    ≥50 studies, ≥90% consensus, ≥5 years
    crystallizing  ≥20 studies, ≥70% consensus, ≥2 years
    emerging       ≥5 studies,  mixed results,  <2 years
    speculative    <5 studies
    """
    if study_count >= 50 and consensus_pct >= 90 and years_of_data >= 5:
        return "established"
    if study_count >= 20 and consensus_pct >= 70 and years_of_data >= 2:
        return "crystallizing"
    if study_count >= 5:
        return "emerging"
    return "speculative"
