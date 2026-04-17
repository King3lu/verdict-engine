"""
Expert consensus aggregation.
Extensible module for incorporating domain expert positions into verdict scoring.
"""
from typing import List


def aggregate_expert_positions(claim: str, expert_sources: List[dict]) -> dict:
    """
    Aggregate expert positions on a claim.

    expert_sources: list of {name, institution, position, confidence, url}
    Returns consensus summary dict.
    """
    if not expert_sources:
        return {
            "consensus_found": False,
            "expert_count": 0,
            "positions": [],
            "summary": "No expert positions available.",
        }

    agree = [e for e in expert_sources if e.get("position") == "agree"]
    disagree = [e for e in expert_sources if e.get("position") == "disagree"]
    neutral = [e for e in expert_sources if e.get("position") == "neutral"]

    total = len(expert_sources)
    agreement_pct = int(len(agree) / total * 100) if total else 0

    return {
        "consensus_found": agreement_pct >= 70,
        "agreement_percentage": agreement_pct,
        "expert_count": total,
        "agreeing": len(agree),
        "disagreeing": len(disagree),
        "neutral": len(neutral),
        "positions": expert_sources,
        "summary": f"{len(agree)}/{total} experts agree with the claim ({agreement_pct}% consensus).",
    }
