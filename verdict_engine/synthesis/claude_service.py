"""
Claude API service for verdict synthesis and secondary validation.
Two-pass system: primary synthesis → independent validation check.
"""
import json
import os
from typing import List, Optional

import anthropic

from ..models import ResearchPaper

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


SYNTHESIS_SYSTEM = """You are a scientific research synthesis expert for a truth-verification platform.

Your role:
1. Synthesize peer-reviewed research into clear, unbiased verdicts
2. Distinguish between established consensus, emerging research, and contradiction
3. Identify limitations, caveats, and uncertainty
4. NEVER speculate beyond what the evidence shows
5. Output structured JSON only — no prose outside the JSON object

Scoring rules:
- 90-100: Overwhelming consensus across multiple meta-analyses
- 70-89: Strong consensus with minor caveats
- 50-69: Mixed evidence or emerging field
- 30-49: Contradicted by majority of research
- 0-29: False or clearly misleading based on evidence

Confidence (separate from score):
- 90-100: 10+ studies, consistent, meta-analyses exist
- 70-89: 5-10 studies, some variation
- 50-69: <5 studies or conflicting data
- 0-49: Insufficient research
"""

VALIDATION_SYSTEM = """You are an independent scientific auditor.
You review verdicts written by another AI for accuracy, fairness, and logical soundness.
You have NOT seen the original prompt — only the verdict and the research abstracts.
Output ONLY valid JSON.
"""


def synthesize_verdict(claim: str, papers: List[ResearchPaper]) -> dict:
    """Primary Claude pass: synthesize research into a structured verdict."""
    client = _get_client()

    research_text = "\n\n".join([
        f"**{p.title}**\nJournal: {p.journal} ({p.year})\n"
        f"Study type: {p.study_type}\nAuthors: {', '.join(p.authors)}\n"
        f"Abstract: {p.abstract}\nURL: {p.url}"
        for p in papers[:8]
    ])

    user_prompt = f"""
Claim to evaluate: "{claim}"

Peer-reviewed research ({len(papers)} papers found):
{research_text if research_text else "No relevant research found in PubMed."}

Return a JSON object with these exact keys:
{{
  "verdict_score": <int 0-100>,
  "confidence_level": <int 0-100>,
  "verdict_category": "<established_consensus|crystallizing|emerging|speculative|contradicted|unverifiable|evolving>",
  "research_summary": "<2-3 sentences of what research actually shows>",
  "key_findings": ["<finding 1>", "<finding 2>", "<finding 3>"],
  "limitations": "<caveats, gaps, what research doesn't cover>",
  "is_emerging_field": <true|false>,
  "consensus_percentage": <int 0-100, % of studies agreeing on direction>,
  "source_selection_reasoning": "<why these studies were included>",
  "false_balance_detected": <true|false>,
  "false_balance_explanation": "<if applicable, explain asymmetry>",
  "display_strategy": "<clear_consensus|genuine_debate|fringe_vs_mainstream>",
  "publication_bias_risk": "<low|medium|high>",
  "caveat": "<important context or null>"
}}
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=SYNTHESIS_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return _parse_json_response(message.content[0].text, _default_verdict(claim))


def validate_verdict(claim: str, verdict: dict, papers: List[ResearchPaper]) -> dict:
    """Secondary Claude pass: independent validation of the primary verdict."""
    client = _get_client()

    abstract_text = "\n".join([
        f"- {p.title} ({p.year}, {p.journal}): {p.abstract[:200]}..."
        for p in papers[:5]
    ])

    user_prompt = f"""
A previous analysis produced this verdict for the claim: "{claim}"

VERDICT TO AUDIT:
{json.dumps(verdict, indent=2)}

RESEARCH ABSTRACTS AVAILABLE:
{abstract_text}

Audit this verdict for:
1. Does the score accurately reflect the research?
2. Is the consensus_percentage defensible?
3. Are any key findings missing or misrepresented?
4. Is false_balance_detected correct?
5. Is publication_bias_risk appropriately flagged?

Return JSON:
{{
  "audit_passed": <true|false>,
  "score_adjustment": <int, e.g. -5 or +10 or 0>,
  "issues_found": ["<issue 1>", "<issue 2>"],
  "corrected_summary": "<improved summary if needed, or null>",
  "corrected_key_findings": ["<corrected findings if needed>"],
  "audit_reasoning": "<why audit passed/failed>"
}}
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=VALIDATION_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return _parse_json_response(message.content[0].text, {
        "audit_passed": True,
        "score_adjustment": 0,
        "issues_found": [],
        "corrected_summary": None,
        "corrected_key_findings": [],
        "audit_reasoning": "Validation parse failed — original verdict retained",
    })


def _parse_json_response(text: str, default: dict) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except json.JSONDecodeError:
        pass
    return default


def _default_verdict(claim: str) -> dict:
    return {
        "verdict_score": 50,
        "confidence_level": 10,
        "verdict_category": "unverifiable",
        "research_summary": "Unable to synthesize verdict — insufficient data or API error.",
        "key_findings": [],
        "limitations": "Verdict generation failed.",
        "is_emerging_field": False,
        "consensus_percentage": 0,
        "source_selection_reasoning": "",
        "false_balance_detected": False,
        "false_balance_explanation": None,
        "display_strategy": "genuine_debate",
        "publication_bias_risk": "unknown",
        "caveat": None,
    }
