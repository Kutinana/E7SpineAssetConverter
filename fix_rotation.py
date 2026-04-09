#!/usr/bin/env python3
"""
CLI wrapper: convert SCSP→JSON with verbose 180° rotation fix diagnostics.

The fix itself is built into scsp_v2._fix_rotation_timeline and runs
automatically during every conversion.  This script adds verbose logging
so you can see exactly which timelines were corrected.

Usage:
  python fix_rotation.py <input.scsp|dir> [output.json|dir]
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import scsp_v2

# ---------------------------------------------------------------------------
# Diagnostic wrapper — monkey-patches scsp_v2 to collect per-timeline stats
# ---------------------------------------------------------------------------
_fix_stats: Dict[str, int] = {"checked": 0, "fixed": 0}
_fix_log: List[Dict[str, Any]] = []
_patch_applied = False


def _install_diagnostics() -> None:
    global _patch_applied
    if _patch_applied:
        return

    import struct

    _ctx: Dict[str, Any] = {"anim": "", "bi": -1, "sk": None}
    _orig_parse = scsp_v2._parse_v2_timeline_entry
    _orig_fix = scsp_v2._fix_rotation_timeline

    def _ctx_parse(r, sk, anim, tl_type_v2):
        _ctx["anim"] = anim.name
        _ctx["sk"] = sk
        if tl_type_v2 == 1:  # V2TimelineType.Rotate
            _ctx["bi"] = struct.unpack_from('<I', r.data, r.pos)[0]
        return _orig_parse(r, sk, anim, tl_type_v2)

    def _diag_fix(entries):
        _fix_stats["checked"] += 1
        sk = _ctx.get("sk")
        bi = _ctx.get("bi", -1)
        anim = _ctx.get("anim", "?")
        bone = sk.bones[bi].name if sk and 0 <= bi < len(sk.bones) else "?"

        result = _orig_fix(entries)
        if result:
            _fix_stats["fixed"] += 1
            _fix_log.append({"anim": anim, "bone": bone, "frames": len(entries)})
            angles_preview = [round(e.get('angle', 0), 1) for e in entries[:6]]
            print(f"  [fix_rotation] {anim}/{bone}: "
                  f"corrected {len(entries)} frames: {angles_preview}...")
        return result

    scsp_v2._parse_v2_timeline_entry = _ctx_parse
    scsp_v2._fix_rotation_timeline = _diag_fix
    _patch_applied = True


def main():
    if len(sys.argv) < 2:
        print("Usage: python fix_rotation.py <input.scsp|dir> [output.json|dir]")
        print()
        print("Converts SCSP to JSON with verbose rotation-fix diagnostics.")
        print("The 180° offset fix is built into scsp2json and always active;")
        print("this script simply adds detailed logging.")
        sys.exit(1)

    inp = sys.argv[1]
    outp = (sys.argv[2] if len(sys.argv) > 2
            else os.path.splitext(inp)[0] + ".json")

    _install_diagnostics()

    from scsp2json import convert_scsp_to_json
    ok = convert_scsp_to_json(inp, outp)

    print(f"\n[fix_rotation] {_fix_stats['fixed']}/{_fix_stats['checked']} "
          f"rotation timelines corrected")
    if _fix_log:
        by_anim: Dict[str, int] = {}
        for entry in _fix_log:
            by_anim[entry["anim"]] = by_anim.get(entry["anim"], 0) + 1
        print("[fix_rotation] by animation:")
        for anim_name, count in sorted(by_anim.items()):
            print(f"  {anim_name}: {count} timelines")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
