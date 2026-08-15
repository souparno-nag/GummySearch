"""Evaluation harness entry point (Constitution IX).

Once the labeled datasets and the AI services they measure exist, this script scores
theme tagging, sentiment, and retrieval against evals/datasets/, reports the model's
results alongside the non-LLM baseline (backend/app/ai/baseline.py) side by side, and
tunes the retrieval refusal threshold (R16) against the labelled unanswerable
questions.

This is a scaffold (T010). The datasets (T079, T080, T100) and the actual scoring
logic (T081) land later, once the AI services under evaluation exist — running this
before then reports that plainly rather than fabricating a score.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

REQUIRED_DATASETS = ("themes.jsonl", "sentiment.jsonl", "retrieval.jsonl")

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    missing = [name for name in REQUIRED_DATASETS if not (DATASETS_DIR / name).exists()]
    if missing:
        logger.info(
            "No evaluation run yet - missing dataset(s): %s. Label them (T079, T080, "
            "T100) before the AI services that score against them exist (T081+).",
            ", ".join(missing),
        )
        return 0

    logger.error("Datasets are present but the scoring harness (T081) is not implemented yet.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
