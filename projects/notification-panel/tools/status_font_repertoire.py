"""Pinned notification-font repertoire and source assignments."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
REPERTOIRES = PROJECT / "main/fonts/repertoires"
CHINESE_TABLE = REPERTOIRES / "tgh-2013-level1.txt"
CHINESE_TABLE_SHA256 = "4caca78057eb62257ed475e0a140b02a58c5907fd054613a93cfdec3a7df6320"
CHINESE_TABLE_LEVEL_1_2 = REPERTOIRES / "tgh-2013-level1-plus2.txt"
CHINESE_TABLE_LEVEL_1_2_SHA256 = "825ecf0dbac8c66505fcee88e426b527a085d8936324ff4a9f9542b2f69b9a58"
UNIHAN_ARCHIVE_SHA256 = "f7a48b2b545acfaa77b2d607ae28747404ce02baefee16396c5d2d7a8ef34b5e"
UNIHAN_TGH_MEMBER = "Unihan_OtherMappings.txt"
CHINESE_LEVEL_1_COUNT = 3500
CHINESE_LEVEL_2_COUNT = 3000
CHINESE_PROFILE_COUNT = CHINESE_LEVEL_1_COUNT + CHINESE_LEVEL_2_COUNT

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
SOURCE_ASSIGNMENTS_SHA256 = "df595309b56631efb677f85e05c8258b8513f3d76ef4f0d367898adb2b8e4c6a"

CHINESE_PUNCTUATION = {ord(char) for char in "　、。，！？：；（）【】《》〈〉「」『』"}
BOX_DRAWING = set(range(0x2500, 0x2580))
BLOCK_ELEMENTS = set(range(0x2580, 0x25A0))
BRAILLE_PATTERNS = set(range(0x2800, 0x2900))
CJK_IDEOGRAPH_RANGES = (
    (0x3400, 0x4DBF),    # Extension A
    (0x4E00, 0x9FFF),    # Unified Ideographs
    (0x20000, 0x2A6DF),  # Extension B
    (0x2A700, 0x2B73F),  # Extension C
    (0x2B740, 0x2B81F),  # Extension D
    (0x2B820, 0x2CEAF),  # Extension E
    (0x2CEB0, 0x2EBEF),  # Extension F
    (0x2EBF0, 0x2EE5F),  # Extension I
    (0x30000, 0x3134F),  # Extension G
    (0x31350, 0x323AF),  # Extension H
)


def _is_cjk_ideograph(codepoint: int) -> bool:
    return any(start <= codepoint <= end for start, end in CJK_IDEOGRAPH_RANGES)


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


def _read_chinese_table(
    path: Path, expected_hash: str, expected_count: int, description: str
) -> list[int]:
    data = path.read_bytes()
    _verify_hash(data, expected_hash, description)
    characters = "".join(data.decode("utf-8").split())
    codepoints = list(map(ord, characters))
    if len(codepoints) != expected_count or len(set(codepoints)) != expected_count:
        raise ValueError(
            f"{description} must contain exactly {expected_count} distinct characters"
        )
    if any(not _is_cjk_ideograph(codepoint) for codepoint in codepoints):
        raise ValueError(f"{description} contains a character outside CJK Unified Ideographs")
    return codepoints


def simplified_chinese() -> set[int]:
    """Read the preserved first 3,500 kTGH 2013 entries from Unicode 17.0.0."""
    return set(
        _read_chinese_table(
            CHINESE_TABLE, CHINESE_TABLE_SHA256, CHINESE_LEVEL_1_COUNT,
            "TGH 2013 level 1 repertoire",
        )
    )


def simplified_chinese_level_1_2() -> set[int]:
    """Read Unicode 17.0.0 kTGH 2013 table indexes 1 through 6,500."""
    points = _read_chinese_table(
        CHINESE_TABLE_LEVEL_1_2,
        CHINESE_TABLE_LEVEL_1_2_SHA256,
        CHINESE_PROFILE_COUNT,
        "TGH 2013 levels 1+2 repertoire",
    )
    if points[:CHINESE_LEVEL_1_COUNT] != _read_chinese_table(
        CHINESE_TABLE, CHINESE_TABLE_SHA256, CHINESE_LEVEL_1_COUNT,
        "TGH 2013 level 1 repertoire",
    ):
        raise ValueError("TGH 2013 levels 1+2 do not preserve level 1 table order")
    return set(points)


def tgh_2013_level_2() -> set[int]:
    """Return the 3,000 kTGH 2013 entries after level 1."""
    all_levels = simplified_chinese_level_1_2()
    return all_levels - simplified_chinese()


def _format_tgh_table(codepoints: list[int]) -> bytes:
    characters = "".join(map(chr, codepoints))
    return (
        "\n".join(
            characters[start:start + 100]
            for start in range(0, len(characters), 100)
        )
        + "\n"
    ).encode("utf-8")


def extract_unihan_tgh_levels_1_2(archive: Path, output: Path) -> list[int]:
    """Extract and verify kTGH 2013 indexes 1-6,500 from pinned Unicode 17.0.0."""
    archive_data = archive.read_bytes()
    _verify_hash(archive_data, UNIHAN_ARCHIVE_SHA256, "Unicode 17.0.0 Unihan archive")
    try:
        with zipfile.ZipFile(BytesIO(archive_data)) as source:
            if source.namelist().count(UNIHAN_TGH_MEMBER) != 1:
                raise ValueError(f"Unihan archive must contain one {UNIHAN_TGH_MEMBER}")
            data = source.read(UNIHAN_TGH_MEMBER)
    except zipfile.BadZipFile as error:
        raise ValueError("pinned Unihan archive is not a valid ZIP file") from error

    try:
        rows = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("Unihan kTGH table must be UTF-8") from error

    by_index: dict[int, int] = {}
    codepoint_indexes: dict[int, int] = {}
    for line in rows:
        fields = line.split("\t")
        if line.startswith("#") or len(fields) < 2 or fields[1] != "kTGH":
            continue
        if len(fields) != 3:
            raise ValueError(f"invalid kTGH row in {UNIHAN_TGH_MEMBER}: {line!r}")
        codepoint_match = re.fullmatch(r"U\+([0-9A-Fa-f]{4,6})", fields[0])
        index_match = re.fullmatch(r"2013:([0-9]+)", fields[2])
        if codepoint_match is None or index_match is None:
            raise ValueError(f"invalid kTGH 2013 entry: {line!r}")
        codepoint = int(codepoint_match.group(1), 16)
        table_index = int(index_match.group(1))
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError(f"invalid Unicode scalar in kTGH entry: {line!r}")
        if not 1 <= table_index <= CHINESE_PROFILE_COUNT:
            continue
        if not _is_cjk_ideograph(codepoint):
            raise ValueError(
                f"kTGH 2013 index {table_index} is outside CJK Unified Ideographs"
            )
        if table_index in by_index:
            raise ValueError(f"duplicate kTGH 2013 table index: {table_index}")
        if codepoint in codepoint_indexes:
            raise ValueError(f"duplicate kTGH 2013 codepoint: U+{codepoint:04X}")
        by_index[table_index] = codepoint
        codepoint_indexes[codepoint] = table_index

    expected_indexes = set(range(1, CHINESE_PROFILE_COUNT + 1))
    if set(by_index) != expected_indexes:
        missing = sorted(expected_indexes - set(by_index))
        unexpected = sorted(set(by_index) - expected_indexes)
        raise ValueError(
            "kTGH 2013 table indexes 1-6,500 are incomplete: "
            f"{len(missing)} missing, {len(unexpected)} unexpected"
        )
    points = [by_index[index] for index in range(1, CHINESE_PROFILE_COUNT + 1)]
    baseline = _read_chinese_table(
        CHINESE_TABLE, CHINESE_TABLE_SHA256, CHINESE_LEVEL_1_COUNT,
        "TGH 2013 level 1 repertoire",
    )
    if points[:CHINESE_LEVEL_1_COUNT] != baseline:
        raise ValueError("pinned Unihan kTGH ordering differs from the preserved level 1 list")

    output_data = _format_tgh_table(points)
    _verify_hash(output_data, CHINESE_TABLE_LEVEL_1_2_SHA256, "TGH 2013 levels 1+2 list")
    output.mkdir(parents=True, exist_ok=True)
    (output / CHINESE_TABLE_LEVEL_1_2.name).write_bytes(output_data)
    return points


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
            "TGH 2013 level 2": tgh_2013_level_2(),
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
        "profile_codepoints": 7365,
        "preserved_current_codepoints": 4972,
        "added_codepoints": 3355,
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
    if len(all_codepoints) != 3355 or len(set(all_codepoints)) != 3355:
        raise ValueError("status text source assignments must cover 3,355 unique additions")
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
        "--extract-unihan",
        type=Path,
        metavar="UNIHAN_ZIP",
        help="regenerate the pinned TGH 2013 levels 1+2 list from Unicode 17.0.0 Unihan.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPERTOIRES,
        help="output directory for source extraction (default: pinned repertoire directory)",
    )
    args = parser.parse_args()
    if (args.extract_cldr is None) == (args.extract_unihan is None):
        parser.error("select exactly one of --extract-cldr or --extract-unihan")
    try:
        if args.extract_cldr is not None:
            extract_cldr_repertoires(args.extract_cldr, args.output)
            print("Pinned CLDR 49 en/zh-Hans punctuation and numbers lists: current")
        else:
            points = extract_unihan_tgh_levels_1_2(args.extract_unihan, args.output)
            print(
                f"Pinned Unicode 17.0.0 kTGH 2013 indexes 1-6,500: "
                f"{len(points)} ordered unique Han characters"
            )
    except (OSError, ET.ParseError, ValueError) as error:
        label = "CLDR" if args.extract_cldr is not None else "Unihan"
        parser.exit(1, f"{label} extraction failed: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
