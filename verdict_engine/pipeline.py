"""
Verdict pipeline orchestrator.
Runs the full 6-step fact-verification pipeline and returns a VerdictResult.
No database dependency — pure Python in, pure Python out.
"""
import logging
from datetime import datetime
from typing import List, Optional

from .models import ResearchPaper, VerdictResult
from .research.multi_source import search_all_sources
from .synthesis.claude_service import synthesize_verdict, validate_verdict
from .synthesis.verdict_scorer import score_study, calculate_verdict_score, classify_research_maturity
from .analysis.bias_detector import analyze_source_bias

log = logging.getLogger("verdict_engine.pipeline")


def _score_papers(papers: List[ResearchPaper]) -> tuple[List[int], int, int]:
    """
    Returns (quality_scores, avg_recency_score, placeholder_consensus_pct).
    Recency decay: ≤2yr=100, 2-5yr=80, 5-10yr=60, >10yr=40.
    """
    quality_scores = []
    recency_scores = []
    current_year = datetime.utcnow().year

    for p in papers:
        qs = score_study(study_type=p.study_type, journal=p.journal, funding_disclosed=True)
        quality_scores.append(qs.total_quality_score)

        try:
            age = current_year - int(p.year)
        except (ValueError, TypeError):
            age = 5

        if age <= 2:
            recency_scores.append(100)
        elif age <= 5:
            recency_scores.append(80)
        elif age <= 10:
            recency_scores.append(60)
        else:
            recency_scores.append(40)

    avg_recency = int(sum(recency_scores) / len(recency_scores)) if recency_scores else 50
    return quality_scores, avg_recency, 75


def run_verdict_pipeline(
    claim_text: str,
    article_bias_sources: Optional[List[dict]] = None,
    category: Optional[str] = None,
) -> VerdictResult:
    """
    Full 6-step verdict pipeline. Returns VerdictResult with no database side effects.

    article_bias_sources: optional list of {outlet, lean, framing} dicts from article discovery.
    category: claim category hint (e.g. "health") used to improve research source filtering.
    """
    # 1. Research — always a fresh search, no caching
    log.info("[pipeline] step 1/6 research  claim=%r category=%r", claim_text, category)
    bundle = search_all_sources(claim_text, category=category)
    papers = bundle.papers
    log.info("[pipeline] step 1/6 done  papers=%d source_counts=%s",
             len(papers), bundle.source_counts)

    # 2. Score paper quality
    quality_scores, avg_recency, _ = _score_papers(papers)

    # 3. Primary synthesis
    log.info("[pipeline] step 3/6 synthesis  papers_to_claude=%d", min(len(papers), 8))
    synthesis = synthesize_verdict(claim_text, papers)

    # 4. Independent validation
    log.info("[pipeline] step 4/6 validation  raw_score=%s", synthesis.get("verdict_score"))
    audit = validate_verdict(claim_text, synthesis, papers)

    raw_score = synthesis.get("verdict_score", 50)
    adjustment = audit.get("score_adjustment", 0)
    final_score = max(0, min(100, raw_score + adjustment))

    research_summary = audit.get("corrected_summary") or synthesis.get("research_summary", "")
    key_findings = audit.get("corrected_key_findings") or synthesis.get("key_findings", [])
    if not key_findings:
        key_findings = synthesis.get("key_findings", [])

    # 5. Bias analysis (optional — only when article sources provided)
    bias_data: dict = {}
    if article_bias_sources:
        bias_data = analyze_source_bias(claim_text, research_summary, article_bias_sources)

    # 6. Compute blended score (60% algorithm, 40% Claude)
    consensus_pct = synthesis.get("consensus_percentage", 75)
    algo_score = calculate_verdict_score(quality_scores, consensus_pct, avg_recency)
    blended_score = int(algo_score * 0.6 + final_score * 0.4)

    research_maturity = classify_research_maturity(
        study_count=len(papers),
        consensus_pct=consensus_pct,
        years_of_data=5,
    )

    score_breakdown = {
        "algorithm_score": algo_score,
        "claude_score": final_score,
        "blended_score": blended_score,
        "audit_adjustment": adjustment,
        "audit_passed": audit.get("audit_passed", True),
        "quality_scores": quality_scores,
        "avg_recency": avg_recency,
        "consensus_pct": consensus_pct,
        "studies_count": len(papers),
    }

    return VerdictResult(
        claim_text=claim_text,
        verdict_score=blended_score,
        confidence_level=synthesis.get("confidence_level", 50),
        verdict_category=synthesis.get("verdict_category", "unverifiable"),
        research_maturity=research_maturity,
        research_summary=research_summary,
        key_findings=key_findings,
        limitations=synthesis.get("limitations", ""),
        is_emerging_field=synthesis.get("is_emerging_field", False),
        caveat=synthesis.get("caveat"),
        false_balance_detected=synthesis.get("false_balance_detected", False),
        false_balance_explanation=synthesis.get("false_balance_explanation"),
        display_strategy=synthesis.get("display_strategy", "genuine_debate"),
        publication_bias_risk=synthesis.get("publication_bias_risk", "unknown"),
        political_lean_aggregate=bias_data.get("lean_balance_score", {}),
        bias_analysis=bias_data.get("bias_vs_accuracy", ""),
        political_influence_detected=bias_data.get("political_influence_on_score", False),
        score_calculation_breakdown=score_breakdown,
        studies_included_count=len(papers),
        source_selection_reasoning=synthesis.get("source_selection_reasoning", ""),
        sources=[
            {
                "pmid": p.pmid,
                "title": p.title,
                "journal": p.journal,
                "year": p.year,
                "authors": p.authors,
                "url": p.url,
                "study_type": p.study_type,
                "abstract": p.abstract[:300],
            }
            for p in papers
        ],
    )
