"""The text LLM the Auditor reads transcripts with: any OpenAI-compatible chat endpoint, JSON schema output.

Configured by LLM_PROVIDER (openai | openrouter | gemini | boson), LLM_MODEL and that provider's key, as
in `.env.example`. With LLM_PROVIDER unset, the first provider whose key is set is used, Boson's last.
With no key, `from_env()` returns None and the Auditor runs on rules and the Caller's own records alone.
No retries here: the Auditor's timeout bounds the whole thing.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

PROVIDERS = {   # provider → (base URL, key variable, default model)
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4.1-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openai/gpt-4.1-mini"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
               "gemini-2.5-flash"),
    # Higgs answers text chat with JSON schema output on the same key as the calls. Last, so a dedicated
    # text LLM wins when one is configured.
    "boson": ("https://api.boson.ai/v1", "BOSON_API_KEY", "higgs-realtime"),
}


@dataclass(frozen=True)
class Chat:
    base_url: str
    api_key: str
    model: str
    timeout_s: float = 30.0

    async def json(
        self, system: str, user: str, schema: dict[str, Any], name: str = "result"
    ) -> dict[str, Any]:
        body = {
            "model": self.model, "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": name, "strict": True, "schema": schema}},
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            r = await client.post(f"{self.base_url}/chat/completions", json=body,
                                  headers={"Authorization": f"Bearer {self.api_key}"})
            r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"])


def from_env() -> Chat | None:
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        provider = next((p for p, (_, key, _) in PROVIDERS.items() if os.getenv(key)), "")
    if provider not in PROVIDERS:
        return None
    base, key_var, default_model = PROVIDERS[provider]
    key = os.getenv(key_var)
    if not key:
        return None
    return Chat(base, key, (os.getenv("LLM_MODEL") or "").strip() or default_model)
