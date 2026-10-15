#!/usr/bin/env python
"""
Quick local check that Gemini works with the values in .env.

    python scripts/gemini_check.py
    python scripts/gemini_check.py "Apa itu OEE?"

Prints the model, the HTTP result and Google's error reason (e.g. API_KEY_INVALID,
model NOT_FOUND). Never prints the API key.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import httpx  # noqa: E402

from rag import config  # noqa: E402
from rag.llm import GEMINI_URL, gemini_error, gemini_generation_config, gemini_text  # noqa: E402


def main() -> int:
    key = config.gemini_key()
    if not key:
        print("GEMINI_API_KEY is not set in .env.")
        return 1
    model = config.gemini_model()
    prompt = " ".join(sys.argv[1:]) or "Say hello in Bahasa Indonesia in one sentence."
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
               "generationConfig": gemini_generation_config(256, model)}
    try:
        r = httpx.post(GEMINI_URL.format(model=model), json=payload, timeout=60,
                       headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        r.raise_for_status()
    except Exception as exc:
        print(f"FAILED ({model}): {gemini_error(exc)}")
        return 1
    text, finish = gemini_text(r.json())
    if not text:
        print(f"FAILED ({model}): empty reply, finishReason={finish}")
        return 1
    print(f"ok ({model}, finishReason={finish}):\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
