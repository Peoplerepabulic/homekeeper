"""Make src/ importable for tests and pin a fixture recall for the seed."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import seed  # noqa: E402

seed.DEMO_RECALL_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "demo_recall.json")
