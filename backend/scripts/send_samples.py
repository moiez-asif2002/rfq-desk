"""POST sample RFQ emails to the inbound webhook, the way a mail provider or n8n would.

    uv run python -m scripts.send_samples                 # all samples
    uv run python -m scripts.send_samples 04_prompt_injection
"""

import os
import sys
from pathlib import Path

import httpx

from app.config import get_settings

WEBHOOK_DIR = Path(__file__).resolve().parents[2] / "samples" / "webhook"
API_URL = os.environ.get("API_URL", "http://localhost:8000")


def main(names: list[str]) -> None:
    files = sorted(WEBHOOK_DIR.glob("*.json"))
    if names:
        files = [f for f in files if f.stem in names]
    secret = get_settings().inbound_webhook_secret
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        for f in files:
            resp = client.post("/api/webhooks/inbound-email", content=f.read_bytes(),
                               headers={"Content-Type": "application/json", "X-Webhook-Secret": secret})
            print(f"{f.stem:<36} {resp.status_code} {resp.text}")


if __name__ == "__main__":
    main(sys.argv[1:])
