#!/usr/bin/env python3
"""
Fix Spine atlas files for SpineViewer V2.1 compatibility.

SpineViewer's V2.1 atlas parser does not understand the 'pma' field
(premultiplied alpha, added in Spine 3.x), causing Enum.Parse to fail
on the value 'true'/'false'. This script strips the pma line.

Usage: python fix_atlas.py <input.atlas> [output.atlas]
"""

import logging
import sys
import re
import os


def fix_atlas(input_path: str, output_path: str) -> None:
    with open(input_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    out_lines = [l for l in lines if not re.match(r'^pma\s*:\s*', l.strip())]

    with open(output_path, 'w', encoding='utf-8') as f:
        f.writelines(out_lines)

    removed = len(lines) - len(out_lines)
    logging.debug(f"[OK] {input_path} -> {output_path} (removed {removed} pma line(s))")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fix_atlas.py <input.atlas> [output.atlas]")
        sys.exit(1)

    inp = sys.argv[1]
    outp = sys.argv[2] if len(sys.argv) > 2 else inp
    fix_atlas(inp, outp)
