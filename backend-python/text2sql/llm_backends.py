"""LLM backends.

The primary backend is HuggingFaceBackend, which loads the local model once
and reuses it for all subsequent generations.

The implementation is optimized for the current Text-to-SQL workflow:

    user question
        ↓
    schema/retrieval
        ↓
    prompt
        ↓
    local Qwen model
        ↓
    SQL / answer

Important design decisions:

- Local HuggingFace inference only.
- Qwen3/Qwen2.5 chat template with Qwen3 non-thinking mode is respected.
- CPU uses float32.
- GPU uses float16.
- No `dtype=` argument is passed to older Transformers versions.
- Generation is performed directly through `model.generate()` to reduce
  pipeline overhead.
- `model.eval()` + `torch.inference_mode()` are used for inference.
- KV caching remains enabled for autoregressive generation.
"""

from abc import ABC, abstractmethod


class LLMBackend(ABC):

    @abstractmethod
    def generate(
        self,
        prompt: str,
        max_new_tokens: int = 220,
        temperature: float = 0.1,
    ) -> str:
        ...

    def warm_up(self) -> None:
        """Optional model pre-loading."""
        pass


class HuggingFaceBackend(LLMBackend):
    """Local inference using HuggingFace Transformers.

    The model is loaded exactly once and reused.

    CPU:
        float32

    GPU:
        float16

    4-bit:
        optional GPU-only mode through bitsandbytes.
    """

    def __init__(
        self,
        model_name: str,
        device_map: str = "auto",
        load_in_4bit: bool = False,
        do_sample: bool = False,
        top_p: float = 0.8,
        top_k: int = 20,
        local_files_only: bool = False,
    ):
        self.model_name = model_name
        self.device_map = device_map
        self.load_in_4bit = load_in_4bit
        self.do_sample = bool(do_sample)
        self.top_p = float(top_p)
        self.top_k = int(top_k)
        self.local_files_only = bool(local_files_only)

        # Loaded once and reused.
        self._generator = None

        # EOS token IDs used to stop generation.
        self._eos_token_ids = None

        # Optional LoRA adapter.
        self._adapter_path = None

        # Remember whether the current model is running on GPU.
        self._has_gpu = False

    # ------------------------------------------------------------------
    # MODEL LOADING
    # ------------------------------------------------------------------

    def _load(self):
        """Load the tokenizer and model exactly once."""

        if self._generator is not None:
            return self._generator

        import os
        import torch
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            pipeline,
        )

        self._has_gpu = torch.cuda.is_available()

        # --------------------------------------------------------------
        # CPU optimization
        # --------------------------------------------------------------
        if not self._has_gpu:
            # Avoid pathological oversubscription on laptop CPUs while still
            # using the available cores. These are process-local settings and
            # are intentionally conservative for an interactive web backend.
            try:
                threads = max(1, min(8, (os.cpu_count() or 4)))
                torch.set_num_threads(threads)
                torch.set_num_interop_threads(1)
            except Exception:
                pass

        # --------------------------------------------------------------
        # CPU optimization
        # --------------------------------------------------------------

        if hasattr(torch, "set_float32_matmul_precision"):
            try:
                torch.set_float32_matmul_precision("high")
            except Exception:
                pass

        # --------------------------------------------------------------
        # Tokenizer
        # --------------------------------------------------------------

        tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            local_files_only=self.local_files_only,
        )

        # Some causal LMs don't define a pad token.
        # EOS is safe as a padding token for generation.
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token

        # --------------------------------------------------------------
        # Model configuration
        # --------------------------------------------------------------

        model_kwargs = {
            "low_cpu_mem_usage": True,
        }

        if self.load_in_4bit and not self._has_gpu:
            print(
                "load_in_4bit=True requires a GPU with bitsandbytes. "
                "No GPU detected; using float32 CPU inference."
            )

        # --------------------------------------------------------------
        # GPU
        # --------------------------------------------------------------

        if self._has_gpu:

            if self.load_in_4bit:
                model_kwargs["load_in_4bit"] = True
                model_kwargs["device_map"] = self.device_map

            else:
                # IMPORTANT:
                # Keep torch_dtype here.
                #
                # Older Transformers versions used by this project
                # reject `dtype=` in AutoModelForCausalLM.from_pretrained().
                model_kwargs["torch_dtype"] = torch.float16

                model_kwargs["device_map"] = self.device_map

        # --------------------------------------------------------------
        # CPU
        # --------------------------------------------------------------

        else:
            # IMPORTANT:
            # CPU stays float32 for compatibility and numerical stability.
            model_kwargs["torch_dtype"] = torch.float32

            # Do NOT use device_map="auto" on this CPU-only setup.
            #
            # It can cause Accelerate to offload layers to disk,
            # which makes generation extremely slow.
            #
            # The model is loaded directly into RAM and moved to CPU.
            model_kwargs.pop("device_map", None)

        # --------------------------------------------------------------
        # Load model
        # --------------------------------------------------------------

        model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            local_files_only=self.local_files_only,
            **model_kwargs,
        )

        if not self._has_gpu:
            model = model.to("cpu")

        # --------------------------------------------------------------
        # Inference mode
        # --------------------------------------------------------------

        model.eval()

        # KV cache is important for autoregressive generation.
        try:
            model.config.use_cache = True
        except Exception:
            pass

        try:
            model.generation_config.use_cache = True
        except Exception:
            pass

        # Remove the old max_length default.
        #
        # We explicitly control generation using max_new_tokens.
        try:
            model.generation_config.max_length = None
        except Exception:
            pass

        # --------------------------------------------------------------
        # Create pipeline object only as a compatibility container.
        # Actual generation below uses model.generate() directly.
        # --------------------------------------------------------------

        self._generator = pipeline(
            "text-generation",
            model=model,
            tokenizer=tokenizer,
        )

        self._eos_token_ids = (
            self._resolve_eos_token_ids(
                tokenizer
            )
        )

        return self._generator

    # ------------------------------------------------------------------
    # EOS HANDLING
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_eos_token_ids(tokenizer):
        """Find all relevant end-of-turn tokens.

        Qwen-style chat models may use <|im_end|> in addition to the
        tokenizer's generic EOS token.
        """

        ids = set()

        if tokenizer.eos_token_id is not None:
            ids.add(
                tokenizer.eos_token_id
            )

        for special in (
            "<|im_end|>",
            "<|eot_id|>",
            "<end_of_turn>",
        ):
            try:
                token_id = (
                    tokenizer.convert_tokens_to_ids(
                        special
                    )
                )
            except Exception:
                token_id = None

            if (
                token_id is not None
                and token_id
                != getattr(
                    tokenizer,
                    "unk_token_id",
                    None,
                )
            ):
                ids.add(token_id)

        return (
            sorted(ids)
            if ids
            else None
        )

    # ------------------------------------------------------------------
    # WARM UP
    # ------------------------------------------------------------------

    def warm_up(self) -> None:
        """Load the model before the first real request."""

        self._load()

    # ------------------------------------------------------------------
    # LORA
    # ------------------------------------------------------------------

    def load_adapter(
        self,
        adapter_path: str,
    ) -> None:
        """Load an existing LoRA adapter onto the current model.

        A second adapter must never be stacked accidentally. Re-training starts
        from the base model or a freshly created backend instance.
        """

        from peft import PeftModel

        generator = self._load()

        if hasattr(generator.model, "peft_config"):
            raise RuntimeError(
                "A LoRA/PEFT adapter is already attached to this backend. "
                "Create a fresh base-model backend before loading another adapter."
            )

        generator.model = PeftModel.from_pretrained(
            generator.model,
            adapter_path,
        )
        generator.model.eval()
        self._adapter_path = adapter_path

    # ------------------------------------------------------------------
    # PROMPT FORMATTING
    # ------------------------------------------------------------------

    def _format_prompt(
        self,
        tokenizer,
        prompt: str,
    ) -> str:
        """Apply the model's native chat template."""

        if getattr(
            tokenizer,
            "chat_template",
            None,
        ):
            messages = [
                {
                    "role": "user",
                    "content": prompt,
                }
            ]
            # Qwen3 defaults to thinking mode. SQL generation and semantic
            # labeling should use non-thinking mode for latency and compact
            # outputs. Older Qwen2.5 templates simply reject the argument, so
            # retain a compatibility fallback.
            try:
                return tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
            except TypeError:
                return tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )

        # Non-chat model fallback.
        return prompt

    # ------------------------------------------------------------------
    # GENERATION
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        max_new_tokens: int = 220,
        temperature: float = 0.1,
    ) -> str:
        """Generate a response from the local model.

        Optimized path:

            format prompt
                ↓
            tokenize once
                ↓
            model.generate()
                ↓
            decode only newly generated tokens
        """

        generator = self._load()

        tokenizer = generator.tokenizer
        model = generator.model

        # --------------------------------------------------------------
        # Prompt formatting
        # --------------------------------------------------------------

        formatted_prompt = self._format_prompt(
            tokenizer,
            prompt,
        )

        # --------------------------------------------------------------
        # Tokenization
        # --------------------------------------------------------------

        encoded = tokenizer(
            formatted_prompt,
            return_tensors="pt",
            padding=False,
            truncation=True,
        )

        # Put input tensors on the same device as the model.
        #
        # For this project:
        # CPU -> cpu
        # GPU -> cuda
        try:
            model_device = next(
                model.parameters()
            ).device
        except StopIteration:
            model_device = (
                "cuda"
                if self._has_gpu
                else "cpu"
            )

        encoded = {
            key: value.to(model_device)
            for key, value in encoded.items()
        }

        input_length = encoded[
            "input_ids"
        ].shape[-1]

        # --------------------------------------------------------------
        # Generation settings
        # --------------------------------------------------------------

        # SQL generation is deterministic by default. Semantic inference may
        # opt into sampling through the backend constructor if desired.
        do_sample = self.do_sample

        generation_kwargs = {
            "max_new_tokens": max(
                1,
                int(max_new_tokens),
            ),

            "do_sample": do_sample,

            "use_cache": True,

            "repetition_penalty": 1.10,

            "pad_token_id": (
                tokenizer.pad_token_id
            ),

            "return_dict_in_generate": False,
        }

        if do_sample:
            generation_kwargs["temperature"] = max(0.01, float(temperature))
            generation_kwargs["top_p"] = max(0.01, min(1.0, self.top_p))
            generation_kwargs["top_k"] = max(0, self.top_k)

        # Only pass EOS IDs when we actually found them.
        eos_ids = (
            self._eos_token_ids
            or tokenizer.eos_token_id
        )

        if eos_ids is not None:
            generation_kwargs[
                "eos_token_id"
            ] = eos_ids

        # --------------------------------------------------------------
        # Generate
        # --------------------------------------------------------------

        import torch

        with torch.inference_mode():

            generated = model.generate(
                **encoded,
                **generation_kwargs,
            )

        # --------------------------------------------------------------
        # IMPORTANT:
        # Remove the input prompt from the generated sequence.
        # We only want the newly generated SQL/answer.
        # --------------------------------------------------------------

        generated_tokens = generated[
            0,
            input_length:,
        ]

        text = tokenizer.decode(
            generated_tokens,
            skip_special_tokens=True,
        )

        return text.strip()


# ----------------------------------------------------------------------
# TEST / OFFLINE BACKEND
# ----------------------------------------------------------------------

class EchoBackend(LLMBackend):
    """Deterministic backend for unit tests and offline demos."""

    def __init__(self, responder):
        self.responder = responder

    def generate(
        self,
        prompt: str,
        max_new_tokens: int = 220,
        temperature: float = 0.1,
    ) -> str:
        return self.responder(prompt)