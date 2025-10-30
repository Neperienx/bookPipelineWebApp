"""Utilities for running local large language models.

This module exposes :class:`TextGenerator`, a small convenience wrapper used
throughout the application to call a Hugging Face Transformers causal language
model.  The original implementation was tightly coupled to a single Windows
filesystem path and assumed 4-bit quantisation support would always be
available.  The updated version below keeps the same interface while adding a
few quality-of-life improvements:

* Graceful fallback when ``bitsandbytes`` (required for 4-bit loading) is not
  installed or a GPU is unavailable.
* Support for overriding generation parameters (temperature, top-p, etc.) at
  call time so prompt configuration can influence inference behaviour.
* Automatic pad token configuration to avoid runtime warnings and align with
  the HF ``generate`` defaults.

The class remains intentionally lightweight—Flask initialises a single instance
and reuses it for all requests, letting the rest of the application stay
agnostic of the underlying model.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

try:  # ``BitsAndBytesConfig`` requires an optional dependency (bitsandbytes).
    from transformers import BitsAndBytesConfig  # type: ignore
except ImportError:  # pragma: no cover - transformers should always provide this
    BitsAndBytesConfig = None  # type: ignore


LOGGER = logging.getLogger(__name__)


class TextGenerator:
    def __init__(
        self,
        model_path: str,
        *,
        temperature: Optional[float] = 0.8,
        top_p: Optional[float] = 0.95,
        max_new_tokens: int = 512,
        seed: int = 42,
        device_map: str | Dict[str, Any] | None = "auto",
        use_4bit: bool = True,
        trust_remote_code: bool = False,
    ):
        self.temperature = temperature
        self.top_p = top_p
        self.max_new_tokens = max_new_tokens
        self.seed = seed
        self.device_map = device_map
        self.use_4bit = use_4bit
        self.trust_remote_code = trust_remote_code

        # Seed CPU (+ all GPUs if present)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        quantization_config = self._build_quantization_config()
        model_kwargs: Dict[str, Any] = {
            "device_map": self.device_map,
            "torch_dtype": "auto",
            "trust_remote_code": trust_remote_code,
        }
        if quantization_config is not None:
            model_kwargs["quantization_config"] = quantization_config

        self.model = AutoModelForCausalLM.from_pretrained(model_path, **model_kwargs)
        self.model.eval()
        self._compute_device_label = self._detect_compute_device()

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            trust_remote_code=trust_remote_code,
        )
        if self.tokenizer.pad_token is None:
            # Many causal models do not ship with a dedicated pad token.  Align
            # generation behaviour by reusing the EOS token for padding.
            self.tokenizer.pad_token = self.tokenizer.eos_token
        if self.tokenizer.padding_side != "left":
            # Left padding avoids shifting tokens when using attention masks.
            self.tokenizer.padding_side = "left"

    def _build_quantization_config(self) -> Optional[BitsAndBytesConfig]:
        """Return a 4-bit quantisation config when supported.

        The application can operate without ``bitsandbytes`` installed; in that
        scenario (or when running on CPU) the model simply loads in standard
        precision.  Logging happens at INFO level so operators can verify
        whether quantisation is active.
        """

        if not self.use_4bit:
            return None

        if BitsAndBytesConfig is None:
            LOGGER.info("transformers BitsAndBytesConfig unavailable; using full precision model loading.")
            return None

        if not torch.cuda.is_available():
            LOGGER.info("CUDA is not available; skipping 4-bit quantisation.")
            return None

        try:  # Ensure optional dependency is present before configuring.
            import bitsandbytes  # type: ignore  # noqa: F401
        except ImportError:
            LOGGER.info("bitsandbytes not installed; using full precision model loading.")
            return None

        LOGGER.info("Loading model with 4-bit quantisation enabled.")
        return BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )

    def _generate(
        self,
        prompt: str,
        *,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        **extra_parameters: Any,
    ):
        """Generate token ids for ``prompt``.

        Parameters
        ----------
        prompt:
            The prompt text used as input for the model.
        max_new_tokens:
            Optional override for the number of new tokens to generate. When
            not provided the generator wide default configured at
            instantiation time is used.
        """
        tokens_to_generate = self.max_new_tokens if max_new_tokens is None else max_new_tokens
        if tokens_to_generate <= 0:
            raise ValueError("max_new_tokens must be a positive integer")

        try:
            tokens_to_generate = int(tokens_to_generate)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError("max_new_tokens must be a positive integer") from exc

        generation_kwargs = self._prepare_generation_kwargs(
            tokens_to_generate,
            temperature=temperature,
            top_p=top_p,
            **extra_parameters,
        )

        enc = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        with torch.no_grad():
            out = self.model.generate(**enc, **generation_kwargs)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        _elapsed = time.perf_counter() - t0
        self._compute_device_label = self._detect_compute_device()
        return enc, out

    def _prepare_generation_kwargs(
        self,
        max_new_tokens: int,
        *,
        temperature: Optional[float],
        top_p: Optional[float],
        **extra_parameters: Any,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "temperature": self.temperature if temperature is None else temperature,
            "top_p": self.top_p if top_p is None else top_p,
            "do_sample": True,
            "pad_token_id": self.tokenizer.pad_token_id,
        }

        # Remove unset sampling parameters so Hugging Face can apply defaults.
        if kwargs["temperature"] is None:
            kwargs.pop("temperature")
        if kwargs["top_p"] is None:
            kwargs.pop("top_p", None)

        for key, value in extra_parameters.items():
            if value is not None:
                kwargs[key] = value

        return kwargs

    def generate_text(
        self,
        prompt: str,
        *,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        **extra_parameters: Any,
    ) -> str:
        """Generate text including the prompt.

        Parameters
        ----------
        prompt:
            The text prompt to feed into the model.
        max_new_tokens:
            Optional override for the number of new tokens to produce.
        """
        _enc, out = self._generate(
            prompt,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            **extra_parameters,
        )
        text = self.tokenizer.decode(out[0], skip_special_tokens=True)
        return text.strip()

    def generate_response(
        self,
        prompt: str,
        *,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        **extra_parameters: Any,
    ) -> str:
        """Generate a response to ``prompt`` without echoing it back."""
        enc, out = self._generate(
            prompt,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            **extra_parameters,
        )
        prompt_len = enc["input_ids"].shape[-1]
        generated_ids = out[0, prompt_len:]
        if generated_ids.numel() == 0:
            return ""
        response_text = self.tokenizer.decode(generated_ids, skip_special_tokens=True)
        return response_text.strip()

    def _detect_compute_device(self) -> str:
        """Return a human readable label describing the active compute device."""

        try:
            parameter = next(self.model.parameters())
        except StopIteration:  # pragma: no cover - defensive fallback
            device = getattr(self.model, "device", torch.device("cpu"))
        else:
            device = parameter.device

        device_str = str(device).lower()
        if any(token in device_str for token in ("cuda", "hip", "mps")):
            return "GPU"
        return "CPU"

    def get_compute_device(self) -> str:
        """Expose the last known compute device label."""

        return self._compute_device_label
