"""Optional LLM layer: picks which retrieved passage answers a question. It never writes answer text.

The system quotes the chosen passage verbatim, so every number in an answer still comes from the site. With no key,
an error, a timeout or a rate limit, `pick` returns None and the rules-only path answers instead.

Configuration (environment variables):
  GROQ_API_KEY        -> turns the layer on with Groq (default model below; override with KB_LLM_MODEL)
  KB_LLM_PROVIDER     -> "anthropic" (with ANTHROPIC_API_KEY) or "openai_compatible" (with KB_LLM_BASE_URL and
                         KB_LLM_API_KEY). Only Groq switches on by itself: an Anthropic key is often present for
                         other reasons (the kit's scorer uses one for its judge), and that path is untested here.
  KB_LLM_OFF=1        -> disable the layer even when a key is present
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

TIMEOUT_S = float(os.getenv("KB_LLM_TIMEOUT", "1.8"))   # the speed score wants answers within 2 s; slower calls fall back
DEFAULTS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "qwen/qwen3.8-27b"),
    "anthropic": ("https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY", "claude-haiku-5-5"),
    "openai_compatible": (os.getenv("KB_LLM_BASE_URL", ""), "KB_LLM_API_KEY", ""),
}

SYSTEM = ("You match a credit union member's question to the passage from the credit union's own website that answers "
          "it. A passage that is only on the same topic does not count. For a question asking whether the credit union "
          "offers or does something, a passage showing that it provides that product or service does answer it. Unless the "
          "question mentions a business, it is about personal accounts, so a passage about business products does not "
          "answer it. Reply with JSON only.")


def _provider() -> Optional[str]:
    if os.getenv("KB_LLM_OFF"):
        return None
    forced = os.getenv("KB_LLM_PROVIDER")
    if forced in DEFAULTS:
        return forced if os.getenv(DEFAULTS[forced][1]) else None
    return "groq" if os.getenv(DEFAULTS["groq"][1]) else None


class Selector:
    def __init__(self):
        self.provider = _provider()
        self.cache: dict = {}
        self.calls = self.failures = 0
        if self.provider:
            url, key_var, model = DEFAULTS[self.provider]
            self.url, self.key, self.model = url, os.getenv(key_var), os.getenv("KB_LLM_MODEL", model)

    @property
    def enabled(self) -> bool:
        return bool(self.provider)

    def pick(self, question: str, candidates: list[str]) -> Optional[int]:
        """Index of the candidate that answers the question, -1 for none, None if the layer could not decide."""
        if not self.enabled or not candidates:
            return None
        cache_key = (question.strip().lower(), tuple(candidates))
        if cache_key in self.cache:   # asking twice gives the same answer
            return self.cache[cache_key]
        listing = "\n".join(f"[{i + 1}] {c}" for i, c in enumerate(candidates))
        prompt = (f"Question: {question}\n\nPassages:\n{listing}\n\n"
                  'Which passage directly answers the question? Reply {"pick": <number>}, or {"pick": 0} if none does.')
        self.calls += 1
        try:
            reply = self._call(prompt)
            m = re.search(r'"pick"\s*:\s*(\d+)', reply)
            if not m:
                raise ValueError(f"unparseable reply: {reply[:80]}")
            n = int(m.group(1))
            choice = n - 1 if 1 <= n <= len(candidates) else -1
        except Exception:   # HTTP errors, timeouts, dropped connections, malformed replies
            self.failures += 1
            choice = None   # rate limit, outage or bad reply: the rules decide, and keep deciding for this question
        # Cached either way, so asking again in this run gives the same answer even if the limit has since reset.
        self.cache[cache_key] = choice
        return choice

    def _call(self, prompt: str) -> str:
        if self.provider == "anthropic":
            body = {"model": self.model, "max_tokens": 20, "temperature": 0, "system": SYSTEM,
                    "messages": [{"role": "user", "content": prompt}]}
            headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            data = self._post(body, headers)
            return "".join(part.get("text", "") for part in data["content"])
        body = {"model": self.model, "max_tokens": 20, "temperature": 0,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
        if "gpt-oss" in self.model:   # reasons before replying: needs room to think, and the reasoning kept out of the reply
            body.update(max_tokens=600, reasoning_effort="low", include_reasoning=False)
        elif "qwen" in self.model and self.provider == "groq":
            body["reasoning_effort"] = "none"   # answer directly; a thinking pass would eat the 20-token budget
        headers = {"Authorization": f"Bearer {self.key}", "content-type": "application/json", "User-Agent": "kb-selector"}
        return self._post(body, headers)["choices"][0]["message"]["content"]

    def _post(self, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read())
