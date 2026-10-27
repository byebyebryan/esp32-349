"""Installed host text policy must track the pinned font/Unicode inputs."""

import importlib.util
from pathlib import Path


def test_installed_text_presentation_table_is_current():
    path = Path(__file__).resolve().parents[1] / "generate_text_presentation.py"
    spec = importlib.util.spec_from_file_location("generate_text_presentation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.OUTPUT.read_text() == module.generated_source()
