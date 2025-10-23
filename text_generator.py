import time
from typing import Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


class TextGenerator:
    def __init__(
        self,
        model_path: str = r"C:\Users\nicol\Documents\01_Code\models\dolphin-2.6-mistral-7b",
        temperature: float = 0.8,
        top_p: float = 0.95,
        max_new_tokens: int = 512,
        seed: int = 42,
    ):
        self.temperature = temperature
        self.top_p = top_p
        self.max_new_tokens = max_new_tokens
        self.seed = seed

        # Seed CPU (+ all GPUs if present)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        bnb_cfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path,
            device_map="auto",
            quantization_config=bnb_cfg,
            torch_dtype="auto",
        )
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)

    def _generate(self, prompt: str, *, max_new_tokens: Optional[int] = None):
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

        enc = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        out = self.model.generate(
            **enc,
            max_new_tokens=tokens_to_generate,
            do_sample=True,
            temperature=self.temperature,
            top_p=self.top_p,
        )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        _elapsed = time.perf_counter() - t0
        return enc, out

    def generate_text(self, prompt: str, *, max_new_tokens: Optional[int] = None) -> str:
        """Generate text including the prompt.

        Parameters
        ----------
        prompt:
            The text prompt to feed into the model.
        max_new_tokens:
            Optional override for the number of new tokens to produce.
        """
        _enc, out = self._generate(prompt, max_new_tokens=max_new_tokens)
        text = self.tokenizer.decode(out[0], skip_special_tokens=True)
        return text.strip()

    def generate_response(self, prompt: str, *, max_new_tokens: Optional[int] = None) -> str:
        """Generate a response to ``prompt`` without echoing it back."""
        enc, out = self._generate(prompt, max_new_tokens=max_new_tokens)
        prompt_len = enc["input_ids"].shape[-1]
        generated_ids = out[0, prompt_len:]
        if generated_ids.numel() == 0:
            return ""
        response_text = self.tokenizer.decode(generated_ids, skip_special_tokens=True)
        return response_text.strip()
