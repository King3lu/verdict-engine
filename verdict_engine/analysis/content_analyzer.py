"""
Hybrid content analyzer — routes images to Claude Vision, videos to Gemini Files API.
Falls back to first-frame extraction if video analysis fails.
"""
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import anthropic
from google import genai
from google.genai import types as genai_types

IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

IMAGE_TYPES = set(IMAGE_MIME_TYPES.keys())
VIDEO_TYPES = set(VIDEO_MIME_TYPES.keys())

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 500 * 1024 * 1024

FILE_TYPE_MAP = {
    "image": "image", "screenshot": "image", "photo": "image", "document": "image",
    "video": "video", "tiktok": "video", "youtube": "video",
}

IMAGE_OCR_PROMPT = """Examine this image and extract any factual claims.

Return JSON only:
{
  "claim_text": "the single most verifiable factual claim as a clean sentence",
  "raw_text": "all text visible in the image",
  "confidence": <0-100>,
  "extracted_from": "screenshot|photo|document",
  "no_claim_found": false
}

Rules:
- claim_text must be a specific, verifiable assertion — not opinion
- If multiple claims exist, pick the most fact-checkable one
- If no verifiable claim exists, set no_claim_found: true and claim_text: null"""

VIDEO_EXTRACT_PROMPT = """Watch this video and extract all factual claims made visually or verbally.

Return JSON only:
{
  "claims": [
    {
      "claim_text": "the verifiable factual assertion",
      "timestamp_seconds": <when it appears, or null>,
      "source": "visual_text|speech|caption"
    }
  ],
  "confidence": <0-100, overall confidence in extraction>,
  "extracted_from": "tiktok|youtube|other",
  "raw_transcript": "full transcription of any spoken/displayed text"
}

Rules:
- Only extract specific, verifiable factual claims
- Ignore opinions, promotions, and entertainment content
- If no verifiable claims exist, return claims: []"""

_claude: Optional[anthropic.Anthropic] = None
_gemini: Optional[genai.Client] = None


def _get_claude() -> anthropic.Anthropic:
    global _claude
    if _claude is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        _claude = anthropic.Anthropic(api_key=api_key)
    return _claude


def _get_gemini() -> genai.Client:
    global _gemini
    if _gemini is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not configured")
        _gemini = genai.Client(api_key=api_key)
    return _gemini


def _parse_json(text: str, default: dict) -> dict:
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


def _validate_file(file_path: str, allowed_extensions: set, max_bytes: int) -> tuple[Path, str]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    ext = path.suffix.lower()
    if ext not in allowed_extensions:
        raise ValueError(f"Unsupported file type '{ext}'. Allowed: {allowed_extensions}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"File too large ({size / 1024 / 1024:.1f} MB). Max: {max_bytes / 1024 / 1024:.0f} MB")
    return path, ext


def _extract_first_frame(video_path: str) -> Optional[str]:
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-f", "image2", tmp.name],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and Path(tmp.name).stat().st_size > 0:
            return tmp.name
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def analyze_image(file_path: str) -> dict:
    """Use Claude Vision to extract a factual claim from an image. Supports PNG, JPG, WebP. Max 20 MB."""
    try:
        path, ext = _validate_file(file_path, IMAGE_TYPES, MAX_IMAGE_BYTES)
        mime_type = IMAGE_MIME_TYPES[ext]
        b64 = base64.standard_b64encode(path.read_bytes()).decode("utf-8")

        message = _get_claude().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64}},
                    {"type": "text", "text": IMAGE_OCR_PROMPT},
                ],
            }],
        )

        result = _parse_json(message.content[0].text, {
            "claim_text": None, "raw_text": "", "confidence": 0,
            "extracted_from": "photo", "no_claim_found": True,
        })

        return {
            "claim_text": result.get("claim_text"),
            "raw_text": result.get("raw_text", ""),
            "confidence": result.get("confidence", 0),
            "source_type": "image",
            "extracted_from": result.get("extracted_from", "photo"),
            "no_claim_found": result.get("no_claim_found", not result.get("claim_text")),
        }

    except Exception as exc:
        return {
            "claim_text": None, "confidence": 0, "source_type": "image",
            "extracted_from": "unknown", "no_claim_found": True, "error_message": str(exc),
        }


def analyze_video(file_path: str) -> dict:
    """Use Gemini Files API to extract factual claims from a video. Max 500 MB / 10 min."""
    import time

    try:
        path, ext = _validate_file(file_path, VIDEO_TYPES, MAX_VIDEO_BYTES)
        mime_type = VIDEO_MIME_TYPES[ext]
        client = _get_gemini()

        uploaded = client.files.upload(
            path=str(path),
            config=genai_types.UploadFileConfig(mime_type=mime_type),
        )

        for _ in range(30):
            file_info = client.files.get(name=uploaded.name)
            if file_info.state.name == "ACTIVE":
                break
            if file_info.state.name == "FAILED":
                raise RuntimeError("Gemini file processing failed")
            time.sleep(2)
        else:
            raise RuntimeError("Gemini file processing timed out after 60s")

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[uploaded, VIDEO_EXTRACT_PROMPT],
        )

        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

        result = _parse_json(response.text, {
            "claims": [], "confidence": 0, "extracted_from": "other", "raw_transcript": "",
        })

        claims = result.get("claims", [])
        return {
            "claims": [c.get("claim_text") for c in claims if c.get("claim_text")],
            "timestamps": [c.get("timestamp_seconds") for c in claims],
            "confidence": result.get("confidence", 0),
            "source_type": "video",
            "extracted_from": result.get("extracted_from", "other"),
            "raw_transcript": result.get("raw_transcript", ""),
        }

    except Exception as exc:
        return {
            "claims": [], "timestamps": [], "confidence": 0,
            "source_type": "video", "extracted_from": "unknown", "error_message": str(exc),
        }


def analyze_content(file_path: str, file_type: str) -> dict:
    """Route to the correct analyzer. Falls back to first-frame image analysis if video fails."""
    media_kind = FILE_TYPE_MAP.get(file_type.lower(), "image")

    if media_kind == "video":
        result = analyze_video(file_path)

        if result.get("error_message") or not result.get("claims"):
            frame_path = _extract_first_frame(file_path)
            if frame_path:
                try:
                    image_result = analyze_image(frame_path)
                    image_result["fallback_from"] = "video"
                    image_result["video_error"] = result.get("error_message")
                    claim = image_result.get("claim_text")
                    image_result["claims"] = [claim] if claim else []
                    image_result["timestamps"] = [None] if claim else []
                    return image_result
                finally:
                    try:
                        os.unlink(frame_path)
                    except OSError:
                        pass

        return result

    return analyze_image(file_path)
