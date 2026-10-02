"""Native checks must not reuse or remove another checkout's build cache."""

import importlib.util
from pathlib import Path
import sys

import pytest


CHECKER_PATH = Path(__file__).resolve().parents[1] / "check_native_ui.py"
spec = importlib.util.spec_from_file_location("check_native_ui", CHECKER_PATH)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_defaults_are_checkout_specific_from_another_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(CHECKER_PATH)])
    monkeypatch.setattr(checker, "check_cache_source", lambda *_args: None)
    commands = []
    monkeypatch.setattr(checker.subprocess, "run", lambda command, **_kwargs: commands.append(command))

    assert checker.main() == 0

    repo = CHECKER_PATH.parents[1]
    configured_dirs = [Path(command[command.index("-B") + 1])
                       for command in commands if "-B" in command]
    assert configured_dirs == [repo / ".cache/native-ui/debug", repo / ".cache/native-ui/release"]
    assert all(path.is_absolute() for path in configured_dirs)


def test_foreign_release_cache_is_rejected_before_either_build(monkeypatch, tmp_path, capsys):
    debug = tmp_path / "fresh-debug"
    release = tmp_path / "foreign-release"
    release.mkdir()
    cache = release / "CMakeCache.txt"
    original = "CMAKE_HOME_DIRECTORY:INTERNAL=/another/checkout/tools/native_ui\n"
    cache.write_text(original)
    monkeypatch.setattr(sys, "argv", [str(CHECKER_PATH), "--debug-build-dir", str(debug),
                                     "--release-build-dir", str(release)])
    commands = []
    monkeypatch.setattr(checker.subprocess, "run", lambda command, **_kwargs: commands.append(command))

    with pytest.raises(SystemExit) as error:
        checker.main()

    assert error.value.code == 2
    assert "belongs to" in capsys.readouterr().err
    assert commands == []
    assert cache.read_text() == original
    assert not debug.exists()


def test_matching_cache_accepts_explicit_directories(monkeypatch, tmp_path):
    source = CHECKER_PATH.parents[1] / "tools/native_ui"
    debug, release = tmp_path / "debug", tmp_path / "release"
    for directory in (debug, release):
        directory.mkdir()
        (directory / "CMakeCache.txt").write_text(
            f"CMAKE_HOME_DIRECTORY:INTERNAL={source}\nCJSON_INCLUDE_DIR:PATH=/idf/cJSON\n"
        )
    monkeypatch.delenv("IDF_PATH", raising=False)
    monkeypatch.setattr(sys, "argv", [str(CHECKER_PATH), "--debug-build-dir", str(debug),
                                     "--release-build-dir", str(release)])
    commands = []
    monkeypatch.setattr(checker.subprocess, "run", lambda command, **_kwargs: commands.append(command))

    assert checker.main() == 0
    assert "-DCJSON_INCLUDE_DIR=/idf/cJSON" in commands[0]
    assert "-DCJSON_INCLUDE_DIR=/idf/cJSON" in commands[3]
