# api_handler.py
from __future__ import annotations
from typing import Any, List, Optional, Tuple

try:
    import openai  # type: ignore
except ImportError:
    openai = None  # type: ignore


class OpenAIUnifiedGenerator:
    """
    Unified wrapper that auto-selects between Responses, Chat Completions,
    and legacy Completions APIs depending on the model name.

    - GPT-5 / o3 / o4 / 4.1(x) → Responses API
    - GPT-4 / 4o / 3.5 (chatty models) → Chat Completions API
    - Very old text-* models → Legacy Completions API

    Compatible with OpenAI Python SDK >= 1.0.
    """

    def __init__(self, model_name: str, api_key: str, default_max_tokens: int = 512) -> None:
        if openai is None:
            raise RuntimeError("Install the 'openai' package to use the API backend.")
        self.model_name = (model_name or "").strip()
        self.api_key = (api_key or "").strip()
        self.default_max_tokens = int(default_max_tokens or 512)

        client_cls = getattr(openai, "OpenAI", None)
        if client_cls is None:
            raise RuntimeError("OpenAI client not available. Update the 'openai' package.")
        self._client = client_cls(api_key=self.api_key)

    # ---------------- heuristics ----------------
    def _uses_responses_api(self) -> bool:
        """
        Newer families (gpt-5, o3, o4, 4.1 variants, some reasoning models) use Responses.
        """
        name = self.model_name.lower()
        return name.startswith((
            "gpt-5", "o3", "o4", "gpt-4.1", "gpt-4.1-mini", "gpt-4o-reasoning"
        ))

    def _uses_chat_completions(self) -> bool:
        """
        Chat-based models like gpt-4, gpt-4o, gpt-4o-mini, gpt-3.5-turbo.
        """
        if self._uses_responses_api():
            return False
        name = self.model_name.lower()
        legacy_prefixes = ("text-", "code-", "ada", "babbage", "curie", "davinci")
        return not name.startswith(legacy_prefixes)

    # ---------------- public API ----------------
    def generate_response(
        self,
        prompt: str,
        *,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string.")
        max_tokens = int(max_new_tokens if max_new_tokens is not None else self.default_max_tokens)
        if max_tokens <= 0:
            raise ValueError("max_new_tokens must be positive.")

        if self._uses_responses_api():
            return self._call_responses(prompt, max_tokens, temperature, top_p)
        if self._uses_chat_completions():
            return self._call_chat(prompt, max_tokens, temperature, top_p)
        return self._call_legacy(prompt, max_tokens, temperature, top_p)

    def get_compute_device(self) -> str:
        return "OpenAI API"

    def signature(self) -> Tuple[str, str]:
        # Never return raw secrets
        redacted = (self.api_key[:4] + "…" + self.api_key[-4:]) if self.api_key else ""
        return (self.model_name, redacted)

    # ---------------- internal callers ----------------
    def _call_responses(
        self,
        prompt: str,
        max_tokens: int,
        temperature: Optional[float],
        top_p: Optional[float],
    ) -> str:
        """
        Use the Responses API for GPT-5 / o3 / o4 / 4.1… families.
        Avoid sending unsupported 'text.format' or 'verbosity' fields.
        """
        def clean_kwargs(d: dict) -> dict:
            return {k: v for k, v in d.items() if v is not None}

        payload = clean_kwargs({
            "model": self.model_name,
            "input": prompt,
            "max_output_tokens": max_tokens,
            "temperature": float(temperature) if temperature is not None else None,
            "top_p": float(top_p) if top_p is not None else None,
            # keep the model from invoking tools:
            "tool_choice": "none",
            # hint for minimal hidden reasoning (supported for reasoning models):
            "reasoning": {"effort": "low"},
            # DO NOT include "text": {...} unless you need structured outputs.
        })

        resp = self._client.responses.create(**payload)
        text = (getattr(resp, "output_text", None) or self._deep_collect_text(resp)).strip()
        if text:
            return text

        # Retry once with a slightly larger budget (helps when we hit the ceiling)
        status = getattr(resp, "status", None)
        reason = getattr(getattr(resp, "incomplete_details", None), "reason", None)
        hit_token_ceiling = (status == "incomplete" and reason == "max_output_tokens")

        if hit_token_ceiling or not text:
            retry_payload = dict(payload)
            retry_payload["max_output_tokens"] = min(2048, max(256, int(max_tokens * 1.5)))
            resp2 = self._client.responses.create(**retry_payload)
            text2 = (getattr(resp2, "output_text", None) or self._deep_collect_text(resp2)).strip()
            if text2:
                return text2
            snippet = self._shorten_debug(str(resp2))
            raise RuntimeError(f"Model returned no text after retry. Raw response (truncated): {snippet}")

        snippet = self._shorten_debug(str(resp))
        raise RuntimeError(f"Model returned no text content. Raw response (truncated): {snippet}")

    def _call_chat(
        self,
        prompt: str,
        max_tokens: int,
        temperature: Optional[float],
        top_p: Optional[float],
    ) -> str:
        kwargs = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "n": 1,
        }
        if temperature is not None:
            kwargs["temperature"] = float(temperature)
        if top_p is not None:
            kwargs["top_p"] = float(top_p)

        resp = self._client.chat.completions.create(**kwargs)
        text = self._extract_text_from_chat(resp).strip()
        if text:
            return text
        snippet = self._shorten_debug(str(resp))
        raise RuntimeError(f"Chat completion returned no text. Raw response (truncated): {snippet}")

    def _call_legacy(
        self,
        prompt: str,
        max_tokens: int,
        temperature: Optional[float],
        top_p: Optional[float],
    ) -> str:
        kwargs = {
            "model": self.model_name,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "n": 1,
        }
        if temperature is not None:
            kwargs["temperature"] = float(temperature)
        if top_p is not None:
            kwargs["top_p"] = float(top_p)

        resp = self._client.completions.create(**kwargs)
        text = self._extract_text_from_legacy_completions(resp).strip()
        if text:
            return text
        snippet = self._shorten_debug(str(resp))
        raise RuntimeError(f"Legacy completion returned no text. Raw response (truncated): {snippet}")

    # ---------------- extractors ----------------
    def _extract_text_from_chat(self, resp: Any) -> str:
        choices = getattr(resp, "choices", []) or []
        if not choices:
            return ""
        first = choices[0]
        msg = getattr(first, "message", None)
        if isinstance(msg, dict):
            content = msg.get("content")
        else:
            content = getattr(msg, "content", None)
        if isinstance(content, list):
            parts: List[str] = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(str(p.get("text") or ""))
            return "\n".join([p for p in parts if p])
        return str(content or getattr(first, "text", "") or "")

    def _extract_text_from_legacy_completions(self, resp: Any) -> str:
        choices = getattr(resp, "choices", []) or []
        if not choices:
            return ""
        first = choices[0]
        return str(getattr(first, "text", "") or "")

    # ---- deep collector for Responses API (handles many shapes) ----
    def _deep_collect_text(self, obj: Any) -> str:
        bucket: List[str] = []

        def walk(node: Any) -> None:
            if node is None:
                return
            if hasattr(node, "__dict__"):
                walk(vars(node))
                return
            if isinstance(node, dict):
                # common text carriers
                for key in ("output_text", "text", "value", "string", "content"):
                    v = node.get(key)
                    if isinstance(v, (str, int, float)):
                        s = str(v).strip()
                        if s:
                            bucket.append(s)
                # nested content arrays / blocks
                for key in ("output", "message", "messages", "data", "parts", "items"):
                    if key in node:
                        walk(node[key])
                # still walk everything else
                for v in node.values():
                    walk(v)
                return
            if isinstance(node, (list, tuple)):
                for item in node:
                    walk(item)

        walk(obj)
        # filter metadata-y crumbs
        candidates = []
        for t in bucket:
            if t.startswith("{") or t.startswith("["):
                continue
            if t.lower() in {"none", "null"}:
                continue
            if len(t) < 3:
                continue
            candidates.append(t)

        # de-dup while preserving order
        seen = set()
        ordered: List[str] = []
        for t in candidates:
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        return "\n".join(ordered).strip()

    @staticmethod
    def _shorten_debug(s: str, limit: int = 1200) -> str:
        s = s.replace("\n", " ")
        return (s[:limit] + "…") if len(s) > limit else s
