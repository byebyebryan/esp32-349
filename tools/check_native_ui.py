#!/usr/bin/env python3
"""Build and run the native LVGL checks in Debug and Release."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess


def cached_cjson(build_dir: Path) -> str | None:
    cache = build_dir / "CMakeCache.txt"
    if not cache.is_file():
        return None
    for line in cache.read_text(encoding="utf-8").splitlines():
        if line.startswith("CJSON_INCLUDE_DIR:PATH="):
            value = line.split("=", 1)[1]
            return value or None
    return None


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    source = repo / "tools/native_ui"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug-build-dir", type=Path,
                        default=Path("/tmp/349-native-ui-build"))
    parser.add_argument("--release-build-dir", type=Path,
                        default=Path("/tmp/349-native-ui-release"))
    parser.add_argument("--cjson-include", type=Path)
    args = parser.parse_args()

    idf_path = os.environ.get("IDF_PATH")
    configured_cjson = args.cjson_include
    if configured_cjson is None and idf_path:
        configured_cjson = Path(idf_path) / "components/json/cJSON"

    for configuration, build_dir in (
        ("Debug", args.debug_build_dir),
        ("Release", args.release_build_dir),
    ):
        build_dir = build_dir.resolve()
        command = ["rtk", "cmake", "-S", str(source), "-B", str(build_dir),
                   f"-DCMAKE_BUILD_TYPE={configuration}"]
        cjson = configured_cjson or cached_cjson(build_dir)
        if cjson is not None:
            command.append(f"-DCJSON_INCLUDE_DIR={cjson}")
        subprocess.run(command, check=True)
        subprocess.run(["rtk", "cmake", "--build", str(build_dir)], check=True)
        subprocess.run(["rtk", "ctest", "--test-dir", str(build_dir),
                        "--output-on-failure"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
