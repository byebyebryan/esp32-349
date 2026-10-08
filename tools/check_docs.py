#!/usr/bin/env python3
"""Check local Markdown links/anchors and presentation media provenance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def prose(text: str) -> str:
    return re.sub(r"(?ms)^```[^\n]*\n.*?^```[ \t]*$", "", text)


def anchors(path: Path) -> set[str]:
    text = prose(path.read_text())
    result = set(re.findall(r'\bid=["\']([^"\']+)["\']', text))
    counts: dict[str, int] = {}
    for heading in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$", text):
        heading = re.sub(r"<[^>]+>", "", heading)
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(f"{slug}-{count}" if count else slug)
    return result


def main() -> int:
    listed = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, text=True,
    ).split("\0")
    docs = sorted({ROOT / name for name in listed if name.endswith(".md")})
    errors: list[str] = []
    links = 0
    anchor_cache: dict[Path, set[str]] = {}
    for path in docs:
        if not path.is_file():
            errors.append(f"missing document: {path.relative_to(ROOT)}")
            continue
        text = prose(path.read_text())
        targets = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text)
        targets += re.findall(r'(?m)^\[[^\]]+\]:\s*(\S+)', text)
        targets += re.findall(r'<(?:img|a)\b[^>]*\b(?:src|href)=["\']([^"\']+)["\']', text)
        for target in targets:
            target = target.split()[0].strip("<>")
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            links += 1
            destination = (path.parent / unquote(url.path)).resolve() if url.path else path.resolve()
            label = f"{path.relative_to(ROOT)}: {target}"
            if not destination.exists():
                errors.append(f"broken link: {label}")
            elif url.fragment and destination.suffix in {".md", ".html"}:
                if destination not in anchor_cache:
                    anchor_cache[destination] = anchors(destination)
                if unquote(url.fragment) not in anchor_cache[destination]:
                    errors.append(f"missing anchor: {label}")

    manifests = sorted(ROOT.glob("projects/*/docs/media/manifest.json"))
    assets = 0
    for manifest_path in manifests:
        media = manifest_path.parent
        label = str(manifest_path.relative_to(ROOT))
        try:
            manifest = json.loads(manifest_path.read_text())
            for group, base in [("source_sha256", ROOT), ("files", media)]:
                for name, record in manifest[group].items():
                    expected = record["sha256"] if isinstance(record, dict) else record
                    path = base / name
                    if not path.is_file():
                        errors.append(f"{label}: missing presentation input/output: {name}")
                        continue
                    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                        errors.append(f"{label}: presentation hash changed: {name}; regenerate media")
                    if isinstance(record, dict) and path.stat().st_size != record["bytes"]:
                        errors.append(f"{label}: presentation size changed: {name}")
            media_suffixes = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".svg", ".mp4", ".webm"}
            asset_names = {p.name for p in media.iterdir() if p.suffix.lower() in media_suffixes}
            if asset_names != set(manifest["files"]):
                errors.append(f"{label}: presentation assets and manifest file list differ")
            assets += len(manifest["files"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{label}: invalid presentation manifest: {exc}")

    for error in errors:
        print(error)
    if errors:
        return 1
    print(f"Documentation: {len(docs)} files, {links} local links/anchors; "
          f"presentation source and {assets} asset hashes verified "
          f"across {len(manifests)} project manifests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
