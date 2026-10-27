#!/usr/bin/env python3
"""Write native lookup inputs from the independent, pinned text profile."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from status_font_repertoire import notification_profile


output = Path(sys.argv[1])
points = sorted(notification_profile())
rows = ["    " + ", ".join(f"0x{cp:X}" for cp in points[start:start + 12]) + ","
        for start in range(0, len(points), 12)]
output.write_text(
    "/* Generated from pinned profile inputs, independent of generated fonts. */\n"
    "static const uint32_t notification_profile_codepoints[] = {\n"
    + "\n".join(rows) + "\n};\n"
)
