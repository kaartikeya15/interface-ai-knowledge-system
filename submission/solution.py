"""Entry point for the scorer: build() returns the knowledge system."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kb.system import KnowledgeBase  # noqa: E402


def build():
    return KnowledgeBase()
