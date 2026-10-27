"""Pinned notification-font repertoire and source assignments."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
REPERTOIRES = PROJECT / "main/fonts/repertoires"
CHINESE_TABLE = REPERTOIRES / "tgh-2013-level1.txt"
CHINESE_TABLE_SHA256 = "4caca78057eb62257ed475e0a140b02a58c5907fd054613a93cfdec3a7df6320"

GF_INPUTS = {
    "GF Latin Core": (
        "GF_Latin_Core.nam",
        "26936a4812ff33ee62a2e8156b9976173755909be84138ca4794a33f123b7d18",
        319,
    ),
    "GF Latin Plus": (
        "GF_Latin_Plus.nam",
        "9fc716a8ee03bcf2720ce77eceb4c8e193474eef09e563a1a5d770bb45678d56",
        110,
    ),
}

CLDR_INPUTS = {
    "CLDR 49 en punctuation": (
        "cldr-49-en-punctuation.txt",
        "42934b81b51bb911960c2d73b142104864657ffbfedef4a48668e078b831aeb3",
        32,
    ),
    "CLDR 49 en numbers": (
        "cldr-49-en-numbers.txt",
        "69655826822939ebe0225a38ca1eb997a4f586690d12bbbb89500a365ac2c37c",
        18,
    ),
    "CLDR 49 zh-Hans punctuation": (
        "cldr-49-zh-hans-punctuation.txt",
        "15ca6be7a474ef0b9b04c1aba4267f2519b6b29486e6e145136b1215e57c89ca",
        55,
    ),
    "CLDR 49 zh-Hans numbers": (
        "cldr-49-zh-hans-numbers.txt",
        "1b166d92becb4399068fa6892b71f6f8074375f9d00336f0b8d2704d9f0a2ae9",
        28,
    ),
}

CLDR_SOURCE_HASHES = {
    "main/en.xml": "f8cbf26964d0e349fc96d9e5a56292edcbaab25c8e64177ca4f111773dfb79cf",
    "main/zh_Hans.xml": "5e84f410a0d715051dddcf7fa3ac6bf346e9edbf3b885d6f5e86b807b63bede2",
    "main/zh.xml": "95a7bf986c594389498b7c9243e255d3c466ab27df7efed8be11e9a1fccfa6ec",
    "main/root.xml": "7447cc014ec93a1425a2ab51e3d2b5cc48e6865c95350caa0fca36cbebcc93e6",
    "supplemental/supplementalData.xml": "745150ec1408d50e1a272b65654a5dd645f3e60f5cc2898b576f725b28538d1a",
}

SOURCE_ASSIGNMENTS = REPERTOIRES / "status-text-source-assignments.json"
SOURCE_ASSIGNMENTS_SHA256 = "cef83fb39d3810a5b623a0354663ffbf5bd22ff7484577defdde940eacff2070"

CHINESE_PUNCTUATION = {ord(char) for char in "　、。，！？：；（）【】《》〈〉「」『』"}
BOX_DRAWING = set(range(0x2500, 0x2580))
BLOCK_ELEMENTS = set(range(0x2580, 0x25A0))
BRAILLE_PATTERNS = set(range(0x2800, 0x2900))


def _verify_hash(data: bytes, expected: str, description: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(
            f"pinned {description} changed: expected SHA-256 {expected}, found {actual}"
        )


def _parse_codepoint_lines(data: bytes, description: str) -> set[int]:
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{description} must be ASCII U+ codepoints") from error

    codepoints: list[int] = []
    for line in lines:
        if not re.fullmatch(r"U\+[0-9A-F]{4,6}", line):
            raise ValueError(f"invalid codepoint in {description}: {line!r}")
        codepoint = int(line[2:], 16)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError(f"invalid Unicode scalar in {description}: {line}")
        codepoints.append(codepoint)
    if codepoints != sorted(set(codepoints)):
        raise ValueError(f"{description} must be sorted and contain no duplicates")
    return set(codepoints)


def _read_codepoint_list(filename: str, expected_hash: str, expected_count: int) -> set[int]:
    data = (REPERTOIRES / filename).read_bytes()
    _verify_hash(data, expected_hash, filename)
    codepoints = _parse_codepoint_lines(data, filename)
    if len(codepoints) != expected_count:
        raise ValueError(
            f"{filename} must contain {expected_count} codepoints; found {len(codepoints)}"
        )
    return codepoints


def _read_glyphset(filename: str, expected_hash: str, expected_count: int) -> set[int]:
    data = (REPERTOIRES / filename).read_bytes()
    _verify_hash(data, expected_hash, filename)
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{filename} must be ASCII") from error

    codepoints: list[int] = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"0x([0-9A-Fa-f]{4,6})\s+#\s+.+", line)
        if match is None:
            raise ValueError(f"invalid Google Fonts glyphset entry in {filename}: {line!r}")
        codepoint = int(match.group(1), 16)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError(f"invalid Unicode scalar in {filename}: {line}")
        codepoints.append(codepoint)
    if len(codepoints) != expected_count or len(set(codepoints)) != expected_count:
        raise ValueError(
            f"{filename} must contain {expected_count} distinct entries; "
            f"found {len(codepoints)} entries and {len(set(codepoints))} distinct codepoints"
        )
    return set(codepoints)


def _parse_cldr_exemplar(pattern: str, description: str) -> set[int]:
    """Parse the literal UnicodeSet syntax used by the pinned CLDR exemplars."""
    if not pattern.startswith("[") or not pattern.endswith("]"):
        raise ValueError(f"unsupported CLDR UnicodeSet in {description}: {pattern!r}")

    codepoints: set[int] = set()
    index = 1
    end = len(pattern) - 1
    while index < end:
        char = pattern[index]
        if char.isspace():
            index += 1
            continue
        if char in "{}&^":
            raise ValueError(f"unsupported CLDR UnicodeSet operator in {description}: {char!r}")
        if char == "-":
            raise ValueError(f"unescaped hyphen in CLDR UnicodeSet {description}")
        if char == "\\":
            index += 1
            if index >= end:
                raise ValueError(f"trailing escape in CLDR UnicodeSet {description}")
            escape = pattern[index]
            if escape in ("u", "U"):
                digits = 4 if escape == "u" else 8
                start = index + 1
                value = pattern[start:start + digits]
                if len(value) != digits or not re.fullmatch(r"[0-9A-Fa-f]+", value):
                    raise ValueError(f"invalid Unicode escape in CLDR UnicodeSet {description}")
                codepoint = int(value, 16)
                index = start + digits
            else:
                codepoint = ord(escape)
                index += 1
        else:
            codepoint = ord(char)
            index += 1
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError(f"invalid Unicode scalar in CLDR UnicodeSet {description}")
        codepoints.add(codepoint)
    return codepoints


def _cldr_parent_map(supplemental_path: Path) -> dict[str, str]:
    root = ET.parse(supplemental_path).getroot()
    parents: dict[str, str] = {}
    for parent_locales in root.findall("./parentLocales"):
        if parent_locales.get("component") is not None:
            continue
        for entry in parent_locales.findall("./parentLocale"):
            parent = entry.get("parent")
            if not parent:
                raise ValueError("CLDR parentLocale has no parent")
            for locale in entry.get("locales", "").split():
                if locale in parents and parents[locale] != parent:
                    raise ValueError(f"conflicting CLDR parents for {locale}")
                parents[locale] = parent
    return parents


def _cldr_exemplar(
    common: Path, parents: dict[str, str], locale: str, exemplar_type: str
) -> set[int]:
    current = locale.replace("-", "_")
    visited: set[str] = set()
    while current not in visited:
        visited.add(current)
        path = common / "main" / f"{current}.xml"
        if not path.exists() and current == "root":
            raise ValueError("CLDR root locale is missing")
        if path.exists():
            root = ET.parse(path).getroot()
            exemplar = root.find(
                f"./characters/exemplarCharacters[@type='{exemplar_type}']"
            )
            if exemplar is not None and (exemplar.text or "").strip() != "↑↑↑":
                return _parse_cldr_exemplar(exemplar.text or "", f"{current}/{exemplar_type}")

        if current == "root":
            break
        current = parents.get(
            current,
            current.rsplit("_", 1)[0] if "_" in current else "root",
        )
    raise ValueError(f"no CLDR {exemplar_type} exemplar found for {locale}")


def extract_cldr_repertoires(common: Path, output: Path) -> dict[str, set[int]]:
    """Regenerate pinned en/zh-Hans exemplar lists from a CLDR 49 checkout."""
    for relative, expected_hash in CLDR_SOURCE_HASHES.items():
        path = common / relative
        if not path.is_file():
            raise ValueError(f"pinned CLDR source is missing: {relative}")
        _verify_hash(path.read_bytes(), expected_hash, f"CLDR {relative}")

    parents = _cldr_parent_map(common / "supplemental/supplementalData.xml")
    extracted: dict[str, set[int]] = {}
    pending: dict[str, bytes] = {}
    for name, (filename, expected_hash, expected_count) in CLDR_INPUTS.items():
        language, exemplar_type = name.removeprefix("CLDR 49 ").rsplit(" ", 1)
        locale = "zh-Hans" if language == "zh-Hans" else language
        codepoints = _cldr_exemplar(common, parents, locale, exemplar_type)
        if len(codepoints) != expected_count:
            raise ValueError(
                f"CLDR {locale} {exemplar_type} must contain {expected_count} codepoints; "
                f"found {len(codepoints)}"
            )
        data = "".join(f"U+{codepoint:04X}\n" for codepoint in sorted(codepoints)).encode()
        _verify_hash(data, expected_hash, filename)
        pending[filename] = data
        extracted[name] = codepoints
    output.mkdir(parents=True, exist_ok=True)
    for filename, data in pending.items():
        (output / filename).write_bytes(data)
    return extracted


def simplified_chinese() -> set[int]:
    """Read the first 3,500 kTGH 2013 entries extracted from Unicode 17.0.0."""
    data = CHINESE_TABLE.read_bytes()
    _verify_hash(data, CHINESE_TABLE_SHA256, "Chinese repertoire")
    characters = "".join(data.decode("utf-8").split())
    codepoints = set(map(ord, characters))
    if len(characters) != 3500 or len(codepoints) != 3500:
        raise ValueError("Chinese repertoire must contain exactly 3,500 distinct characters")
    if any(not 0x4E00 <= codepoint <= 0x9FFF for codepoint in codepoints):
        raise ValueError("Chinese repertoire contains a character outside the Han block")
    return codepoints


def profile_groups() -> dict[str, set[int]]:
    """Return required codepoints by pinned repertoire source or Unicode block."""
    groups = {
        name: _read_glyphset(filename, expected_hash, expected_count)
        for name, (filename, expected_hash, expected_count) in GF_INPUTS.items()
    }
    groups.update(
        {
            name: _read_codepoint_list(filename, expected_hash, expected_count)
            for name, (filename, expected_hash, expected_count) in CLDR_INPUTS.items()
        }
    )
    groups.update(
        {
            "TGH 2013 level 1": simplified_chinese(),
            "Box Drawing": set(BOX_DRAWING),
            "Block Elements": set(BLOCK_ELEMENTS),
            "Braille Patterns": set(BRAILLE_PATTERNS),
        }
    )
    return groups


def notification_profile() -> set[int]:
    """Return the pinned notification repertoire, excluding preserved legacy extras."""
    groups = profile_groups()
    return set().union(*groups.values())


def status_text_source_assignments() -> dict[str, set[int]]:
    """Read the pinned source map for codepoints added beyond the legacy fonts."""
    data = SOURCE_ASSIGNMENTS.read_bytes()
    _verify_hash(data, SOURCE_ASSIGNMENTS_SHA256, "status text source assignments")
    try:
        manifest = json.loads(data)
    except json.JSONDecodeError as error:
        raise ValueError("status text source assignments are not valid JSON") from error

    if manifest.get("schema") != 1:
        raise ValueError("unsupported status text source-assignment schema")
    expected = {
        "profile_codepoints": 4365,
        "preserved_current_codepoints": 4972,
        "added_codepoints": 355,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"status text source assignments {key} must be {value}")

    assignments: dict[str, set[int]] = {}
    all_codepoints: list[int] = []
    for source in manifest.get("sources", []):
        name = source.get("name")
        if not isinstance(name, str) or name in assignments:
            raise ValueError("status text source assignment has a missing or duplicate name")
        listed: list[int] = []
        for value in source.get("added_codepoints", []):
            if not isinstance(value, str) or not re.fullmatch(r"U\+[0-9A-F]{4,6}", value):
                raise ValueError(f"invalid codepoint in source assignment for {name}: {value!r}")
            listed.append(int(value[2:], 16))
        if listed != sorted(set(listed)) or len(listed) != source.get("added_count"):
            raise ValueError(f"invalid sorted codepoint list for source assignment {name}")
        assignments[name] = set(listed)
        all_codepoints.extend(listed)

    expected_names = {
        "LVGL Montserrat Medium",
        "DejaVu Sans",
        "Source Han Sans SC",
        "FontAwesome5",
        "Montserrat Medium 500",
        "Noto Sans Medium 500",
    }
    if set(assignments) != expected_names:
        raise ValueError("status text source assignment names differ from the pinned source set")
    if len(all_codepoints) != 355 or len(set(all_codepoints)) != 355:
        raise ValueError("status text source assignments must cover 355 unique additions")
    assignment_hash = hashlib.sha256(
        "".join(f"{codepoint:06X}\n" for codepoint in sorted(all_codepoints)).encode()
    ).hexdigest()
    if assignment_hash != manifest.get("added_codepoints_sha256"):
        raise ValueError("status text source assignment codepoint hash does not match")
    return assignments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--extract-cldr",
        type=Path,
        metavar="COMMON_DIR",
        help="regenerate the pinned CLDR lists from a CLDR 49 common/ directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPERTOIRES,
        help="output directory for --extract-cldr (default: pinned repertoire directory)",
    )
    args = parser.parse_args()
    if args.extract_cldr is None:
        parser.error("--extract-cldr is required")
    try:
        extract_cldr_repertoires(args.extract_cldr, args.output)
    except (OSError, ET.ParseError, ValueError) as error:
        parser.exit(1, f"CLDR extraction failed: {error}\n")
    print("Pinned CLDR 49 en/zh-Hans punctuation and numbers lists: current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
