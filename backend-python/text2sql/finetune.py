"""Dynamic LoRA fine-tuning of the local LLM on the connected database.

Training is optional and operates on dynamically generated (question, SQL)
pairs for the currently connected database.

The resulting LoRA adapter is cached by a schema fingerprint so the same
database does not need to be retrained unnecessarily.
"""

import hashlib
import os
from dataclasses import dataclass
from typing import List, Optional

from .exceptions import Text2SQLError


class FineTuneUnavailableError(Text2SQLError):
    """Raised when fine-tuning cannot run."""


@dataclass
class FineTuneResult:
    adapter_path: str
    num_examples: int
    num_epochs: int
    train_loss: Optional[float] = None
    reused_cached_adapter: bool = False


def _schema_fingerprint(metadata) -> str:
    """Create a stable fingerprint from table/column structure."""

    parts = []

    for table in sorted(
        metadata.tables.values(),
        key=lambda t: t.name,
    ):
        col_sig = ",".join(
            f"{c.name}:{c.data_type}"
            for c in table.columns
        )

        parts.append(
            f"{table.name}[{col_sig}]"
        )

    raw = "|".join(parts)

    return hashlib.sha1(
        raw.encode("utf-8")
    ).hexdigest()[:16]


class SQLFineTuner:
    """Run LoRA fine-tuning on the local HuggingFace model."""

    def __init__(
        self,
        adapter_root: str = "text2sql_adapters",
        lora_r: int = 8,
        lora_alpha: int = 16,
        lora_dropout: float = 0.05,
        num_epochs: int = 3,
        learning_rate: float = 1e-4,
        per_device_batch_size: int = 2,
        max_seq_length: int = 768,
    ):
        self.adapter_root = adapter_root
        self.lora_r = lora_r
        self.lora_alpha = lora_alpha
        self.lora_dropout = lora_dropout
        self.num_epochs = num_epochs
        self.learning_rate = learning_rate
        self.per_device_batch_size = per_device_batch_size
        self.max_seq_length = max_seq_length

    def train(
        self,
        backend,
        metadata,
        training_pairs: List[dict],
        force_retrain: bool = False,
    ) -> FineTuneResult:

        from .llm_backends import HuggingFaceBackend

        # ---------------------------------------------------------
        # Validate backend
        # ---------------------------------------------------------

        if not isinstance(
            backend,
            HuggingFaceBackend,
        ):
            raise FineTuneUnavailableError(
                "Fine-tuning requires a HuggingFaceBackend "
                "(local transformers model)."
            )

        if not training_pairs:
            raise FineTuneUnavailableError(
                "No training examples were generated for this "
                "schema -- nothing to train on."
            )

        # ---------------------------------------------------------
        # Build schema-specific adapter path
        # ---------------------------------------------------------

        fingerprint = _schema_fingerprint(
            metadata
        )

        safe_model_name = (
            backend.model_name.replace(
                "/",
                "__",
            )
        )

        adapter_path = os.path.join(
            self.adapter_root,
            safe_model_name,
            fingerprint,
        )

        # ---------------------------------------------------------
        # Reuse existing adapter
        # ---------------------------------------------------------

        if (
            os.path.isdir(adapter_path)
            and not force_retrain
            and os.listdir(adapter_path)
        ):
            backend.load_adapter(
                adapter_path
            )

            return FineTuneResult(
                adapter_path=adapter_path,
                num_examples=len(training_pairs),
                num_epochs=0,
                reused_cached_adapter=True,
            )

        # ---------------------------------------------------------
        # Optional dependencies
        # ---------------------------------------------------------

        try:
            import torch

            from datasets import Dataset

            from peft import (
                LoraConfig,
                get_peft_model,
            )

            from transformers import (
                Trainer,
                TrainingArguments,
            )

        except ImportError as exc:
            raise FineTuneUnavailableError(
                "Fine-tuning needs 'peft' and 'datasets' "
                f"installed ({exc}). "
                "Run: pip install -q peft datasets"
            ) from exc

        # ---------------------------------------------------------
        # Load existing model
        # ---------------------------------------------------------

        generator = backend._load()

        if hasattr(generator.model, "peft_config"):
            raise FineTuneUnavailableError(
                "The backend already has a LoRA/PEFT adapter attached. "
                "Create a fresh base-model backend before re-training."
            )

        tokenizer = generator.tokenizer
        base_model = generator.model

        # ---------------------------------------------------------
        # Configure LoRA
        # ---------------------------------------------------------

        target_modules = self._guess_target_modules(
            base_model
        )

        if not target_modules:
            raise FineTuneUnavailableError(
                "Could not identify suitable LoRA target modules "
                "for this model."
            )

        lora_config = LoraConfig(
            r=self.lora_r,
            lora_alpha=self.lora_alpha,
            lora_dropout=self.lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=target_modules,
        )

        model = get_peft_model(
            base_model,
            lora_config,
        )

        model.train()

        # ---------------------------------------------------------
        # Prepare training examples
        # ---------------------------------------------------------

        def _format(example):
            prompt = backend._format_prompt(
                tokenizer,
                example["prompt"],
            )

            completion = str(example["completion"]).strip()
            if tokenizer.eos_token:
                completion += tokenizer.eos_token

            # Preserve the completion whenever possible. Truncating the full
            # sequence from the right can otherwise remove all SQL tokens and
            # create a training sample with zero loss-bearing labels.
            prompt_ids = tokenizer(
                prompt,
                add_special_tokens=False,
            )["input_ids"]
            completion_ids = tokenizer(
                completion,
                add_special_tokens=False,
            )["input_ids"]

            if not completion_ids:
                raise FineTuneUnavailableError(
                    "Encountered a training example with an empty completion."
                )

            if len(completion_ids) >= self.max_seq_length:
                raise FineTuneUnavailableError(
                    "A SQL completion is longer than max_seq_length; "
                    "increase max_seq_length or shorten the generated example."
                )

            max_prompt_tokens = (
                self.max_seq_length - len(completion_ids)
            )
            prompt_ids = prompt_ids[:max_prompt_tokens]

            input_ids = prompt_ids + completion_ids
            attention_mask = [1] * len(input_ids)

            labels = [-100] * len(prompt_ids) + completion_ids

            if not any(label != -100 for label in labels):
                raise FineTuneUnavailableError(
                    "Training example contains no loss-bearing completion tokens."
                )

            return {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "labels": labels,
            }


        dataset = Dataset.from_list(
            training_pairs
        ).map(
            _format,
            remove_columns=[
                "prompt",
                "completion",
            ],
        )

        # ---------------------------------------------------------
        # Custom collator
        # ---------------------------------------------------------
        #
        # IMPORTANT:
        # Do NOT use DataCollatorForLanguageModeling here.
        #
        # We already constructed labels with -100 for the prompt.
        # A language-modeling collator could recreate those labels.
        #

        def collate(features):
            input_ids = [
                feature["input_ids"]
                for feature in features
            ]

            attention_masks = [
                feature["attention_mask"]
                for feature in features
            ]

            labels = [
                feature["labels"]
                for feature in features
            ]

            max_len = max(
                len(ids)
                for ids in input_ids
            )

            padded_inputs = []
            padded_masks = []
            padded_labels = []

            pad_token_id = (
                tokenizer.pad_token_id
            )

            if pad_token_id is None:
                pad_token_id = (
                    tokenizer.eos_token_id
                )

            for ids, mask, label in zip(
                input_ids,
                attention_masks,
                labels,
            ):
                padding = (
                    max_len - len(ids)
                )

                padded_inputs.append(
                    ids
                    + [pad_token_id] * padding
                )

                padded_masks.append(
                    mask
                    + [0] * padding
                )

                padded_labels.append(
                    label
                    + [-100] * padding
                )

            return {
                "input_ids": torch.tensor(
                    padded_inputs,
                    dtype=torch.long,
                ),
                "attention_mask": torch.tensor(
                    padded_masks,
                    dtype=torch.long,
                ),
                "labels": torch.tensor(
                    padded_labels,
                    dtype=torch.long,
                ),
            }

        # ---------------------------------------------------------
        # Training
        # ---------------------------------------------------------

        os.makedirs(
            adapter_path,
            exist_ok=True,
        )

        use_cuda = torch.cuda.is_available()

        args = TrainingArguments(
            output_dir=adapter_path,

            num_train_epochs=self.num_epochs,

            per_device_train_batch_size=(
                self.per_device_batch_size
            ),

            gradient_accumulation_steps=4,

            learning_rate=self.learning_rate,

            logging_steps=10,

            save_strategy="no",

            report_to=[],

            # FP16 only when CUDA is actually available.
            fp16=use_cuda,

            # Keep CPU training in normal float32.
            bf16=False,

            remove_unused_columns=False,

            # Avoid unnecessary evaluation overhead because this
            # training path currently has no validation dataset.
            eval_strategy="no",
        )

        trainer = Trainer(
            model=model,
            args=args,
            train_dataset=dataset,
            data_collator=collate,
        )

        train_output = trainer.train()

        # ---------------------------------------------------------
        # Save adapter
        # ---------------------------------------------------------

        model.save_pretrained(
            adapter_path
        )

        tokenizer.save_pretrained(
            adapter_path
        )

        # ---------------------------------------------------------
        # Attach adapter to the live backend
        # ---------------------------------------------------------

        generator.model = model

        backend._adapter_path = (
            adapter_path
        )

        return FineTuneResult(
            adapter_path=adapter_path,
            num_examples=len(training_pairs),
            num_epochs=self.num_epochs,
            train_loss=getattr(
                train_output,
                "training_loss",
                None,
            ),
        )

    @staticmethod
    def _guess_target_modules(
        model,
    ) -> List[str]:
        """Automatically discover compatible LoRA target modules."""

        candidates = {
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        }

        present = set()

        for name, _ in model.named_modules():
            leaf = name.rsplit(
                ".",
                1,
            )[-1]

            if leaf in candidates:
                present.add(leaf)

        if present:
            return sorted(present)

        # Safe fallback for common causal-LM architectures.
        return [
            "q_proj",
            "v_proj",
        ]