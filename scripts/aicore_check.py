#!/usr/bin/env python
"""
Quick local check that SAP AI Core works with the values in .env.

    python scripts/aicore_check.py
    python scripts/aicore_check.py "Apa itu OEE?"

Prints whether login worked and the model's reply. Never prints the secret or token.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from rag import aicore  # noqa: E402


def main() -> int:
    if not aicore.configured():
        print("AI Core is not configured. Copy .env.example to .env and fill in "
              "AICORE_AUTH_URL, AICORE_CLIENT_ID, AICORE_CLIENT_SECRET and AI_API_URL.")
        return 1
    prompt = " ".join(sys.argv[1:]) or "Say hello in Bahasa Indonesia in one sentence."
    try:
        aicore.get_token()
        print("1/2 login ok (token received)")
        reply = aicore.ask(prompt)
        print(f"2/2 chat ok ({aicore.model_label()}, deployment {aicore.deployment_id()}):\n")
        print(reply)
        return 0
    except aicore.AICoreError as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
