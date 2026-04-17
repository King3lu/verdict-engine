"""
Bias detector: analyzes how sources with different political leans frame a claim
vs. what the research actually shows. Uses outlet lean labels from AllSides/Ad Fontes —
never Claude's own political judgment.
"""
import json
import os
from typing import List, Optional

import anthropic

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def analyze_source_bias(
    claim: str,
    research_summary: str,
    sources: List[dict],
) -> dict:
    """
    Analyze bias in how sources frame a claim vs. what research shows.

    sources: list of {outlet, lean, framing} dicts
    """
    client = _get_client()

    sources_text = "\n".join([
        f"- {s.get('outlet', 'Unknown')} (lean: {s.get('lean', 'unknown')}): {s.get('framing', '')}"
        for s in sources[:8]
    ])

    user_prompt = f"""
Claim: "{claim}"
Research consensus: {research_summary}

Sources and their reported political leans (from AllSides/Ad Fontes — not your judgment):
{sources_text}

Analyze how each lean group frames the claim vs. what research shows.
Focus on: selective emphasis, omitted caveats, loaded language, misrepresentation of studies.
Political lean DOES NOT determine truthfulness — evidence does.

Return JSON:
{{
  "lean_balance_score": <int 0-100, 100=perfectly balanced>,
  "framing_bias_detected": <true|false>,
  "cherry_picked_studies": <true|false>,
  "bias_vs_accuracy": "<clear analysis: left emphasizes X, right emphasizes Y, evidence shows Z>",
  "political_influence_on_score": <true|false>,
  "loaded_language_examples": ["<example 1>", "<example 2>"],
  "left_framing_summary": "<how left-leaning sources frame it>",
  "right_framing_summary": "<how right-leaning sources frame it>",
  "neutral_framing": "<how evidence-based sources frame it>"
}}
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system="You analyze media bias. Be specific. Output only JSON.",
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = message.content[0].text.strip()
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

    return {
        "lean_balance_score": 50,
        "framing_bias_detected": False,
        "cherry_picked_studies": False,
        "bias_vs_accuracy": "Bias analysis unavailable.",
        "political_influence_on_score": False,
        "loaded_language_examples": [],
        "left_framing_summary": "",
        "right_framing_summary": "",
        "neutral_framing": "",
    }
