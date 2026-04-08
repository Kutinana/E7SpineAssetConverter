#!/usr/bin/env python3
"""
Epic Seven SCSP to JSON converter — supports both V2 (2.1.27) and V3 (3.8.99).

Based on E7_Scsp2Json.py (V3) and epic7_scsp2json_v1_0 (V2 reference).

This is the main entry point.  Parsing logic lives in:
  - scsp_common.py  (shared types, reader, helpers)
  - scsp_v2.py      (V2 / Spine 2.1.27 parsing)
  - scsp_v3.py      (V3 / Spine 3.8.99 parsing + JSON timeline writer)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Re-export everything from sub-modules for backward compatibility
# (other scripts do `from scsp2json import *`)
from scsp_common import *  # noqa: F401,F403
from scsp_v2 import *      # noqa: F401,F403
from scsp_v3 import *      # noqa: F401,F403

# Explicit imports used locally
from scsp_common import (
    SpineBinaryReader, SkeletonData, ScspVersion, AnimationData,
    Attachment, VertexAttachment, RegionAttachment, MeshAttachment,
    LinkedMeshAttachment, BoundingBoxAttachment, PathAttachment,
    PointAttachment, ClippingAttachment, SkinnedMeshAttachment,
    Inherit, BlendMode, PositionMode, SpacingMode, RotateMode,
    AttachmentType,
    Color, color_to_string, f32_color,
    custom_data_preprocess,
)
from scsp_v2 import read_skeleton_info_v2, read_scsp_v2
from scsp_v3 import read_skeleton_info_v3, read_scsp_v3, build_animation_json_v3


# ==============================
# Main read function
# ==============================
def read_skeleton_info(r: SpineBinaryReader, sk: SkeletonData) -> None:
    custom_data_preprocess(r, sk)
    if sk.scspVersion == ScspVersion.V2:
        read_skeleton_info_v2(r, sk)
    else:
        read_skeleton_info_v3(r, sk)

def read_binary_skeleton(data: bytes) -> Tuple[SkeletonData, bool]:
    r = SpineBinaryReader(data)
    sk = SkeletonData()
    read_skeleton_info(r, sk)
    if sk.scspVersion == ScspVersion.V2:
        read_scsp_v2(r, sk)
    else:
        read_scsp_v3(r, sk)
    return sk, True


# ==============================
# Post-processing: ensure loop-safe animations
# ==============================
def _ensure_setup_keyframes(sk: SkeletonData) -> None:
    """Prepend t=0 setup-pose keyframes for timelines that start after t=0.

    The Spine runtime does NOT reset properties to setup pose on animation
    loop.  If a timeline's first keyframe is at t>0, the end-of-animation
    value leaks into the next loop iteration (e.g. alpha=0 persists,
    making slots invisible).  The SCSP source data relies on the game
    engine resetting state each cycle, so we patch in explicit t=0
    keyframes for compatibility with standard Spine viewers.
    """
    slot_by_name = {s.name: s for s in sk.slots}
    bone_by_name = {b.name: b for b in sk.bones}

    for anim in sk.animations:
        _patch_anim_setup_keyframes(anim, slot_by_name, bone_by_name)


def _patch_anim_setup_keyframes(
    anim: AnimationData,
    slot_by_name: Dict[str, Any],
    bone_by_name: Dict[str, Any],
) -> None:
    """Patch a single animation's timelines with t=0 setup-pose keyframes."""

    def _needs_t0(frames: Any) -> bool:
        return (isinstance(frames, list) and frames
                and isinstance(frames[0], dict)
                and frames[0].get("time", 0) > 0)

    for slot_name, timelines in anim.slots.items():
        slot = slot_by_name.get(slot_name)
        if slot is None:
            continue

        if "color" in timelines and _needs_t0(timelines["color"]):
            setup_color = color_to_string(slot.color, True) if slot.color else "FFFFFFFF"
            timelines["color"].insert(0, {"time": 0, "color": setup_color})

        if "attachment" in timelines and _needs_t0(timelines["attachment"]):
            timelines["attachment"].insert(0, {"time": 0, "name": slot.attachmentName})

    for bone_name, timelines in anim.bones.items():
        bone = bone_by_name.get(bone_name)
        if bone is None:
            continue

        if "rotate" in timelines and _needs_t0(timelines["rotate"]):
            timelines["rotate"].insert(0, {"time": 0, "angle": 0})

        if "translate" in timelines and _needs_t0(timelines["translate"]):
            timelines["translate"].insert(0, {"time": 0, "x": 0, "y": 0})

        if "scale" in timelines and _needs_t0(timelines["scale"]):
            timelines["scale"].insert(0, {"time": 0, "x": 1, "y": 1})

        if "shear" in timelines and _needs_t0(timelines["shear"]):
            timelines["shear"].insert(0, {"time": 0, "x": 0, "y": 0})

        if "flipX" in timelines and _needs_t0(timelines["flipX"]):
            timelines["flipX"].insert(0, {"time": 0, "x": bone.flipX})

        if "flipY" in timelines and _needs_t0(timelines["flipY"]):
            timelines["flipY"].insert(0, {"time": 0, "y": bone.flipY})


# ==============================
# JSON Writer
# ==============================
def _clean_float(v: float) -> Any:
    """Round to int if close enough, otherwise keep float."""
    if round(v) == v:
        return round(v)
    return v

def write_json_data(sk: SkeletonData) -> Dict[str, Any]:
    is_v2 = sk.scspVersion == ScspVersion.V2

    j: Dict[str, Any] = {"skeleton": {}, "bones": [], "slots": []}

    # skeleton
    skeleton_obj: Dict[str, Any] = {}
    if sk.hashString:
        skeleton_obj["hash"] = sk.hashString
    if sk.version:
        skeleton_obj["spine"] = sk.version
    if is_v2:
        skeleton_obj["width"] = round(sk.width, 2)
        skeleton_obj["height"] = round(sk.height, 2)
    else:
        skeleton_obj["x"] = sk.x
        skeleton_obj["y"] = sk.y
        skeleton_obj["width"] = sk.width
        skeleton_obj["height"] = sk.height
        if sk.imagesPath is not None:
            skeleton_obj["images"] = sk.imagesPath
        if sk.audioPath is not None:
            skeleton_obj["audio"] = sk.audioPath
        if sk.fps != 30.0:
            skeleton_obj["fps"] = sk.fps
    j["skeleton"] = skeleton_obj

    # bones
    for b in sk.bones:
        obj: Dict[str, Any] = {"name": b.name}
        if b.parent is not None:
            obj["parent"] = b.parent
        if is_v2:
            if b.length != 0.0: obj["length"] = round(b.length, 2)
            if b.x != 0.0: obj["x"] = round(b.x, 2)
            if b.y != 0.0: obj["y"] = round(b.y, 2)
            if b.rotation != 0.0: obj["rotation"] = round(b.rotation, 2)
            if b.scaleX != 1.0: obj["scaleX"] = round(b.scaleX, 5)
            if b.scaleY != 1.0: obj["scaleY"] = round(b.scaleY, 5)
            if b.flipX: obj["flipX"] = True
            if b.flipY: obj["flipY"] = True
            if not b.inheritScale: obj["inheritScale"] = False
            if not b.inheritRotation: obj["inheritRotation"] = False
        else:
            if b.length != 0.0: obj["length"] = b.length
            if b.x != 0.0: obj["x"] = b.x
            if b.y != 0.0: obj["y"] = b.y
            if b.rotation != 0.0: obj["rotation"] = b.rotation
            if b.scaleX != 1.0: obj["scaleX"] = b.scaleX
            if b.scaleY != 1.0: obj["scaleY"] = b.scaleY
            if b.shearX != 0.0: obj["shearX"] = b.shearX
            if b.shearY != 0.0: obj["shearY"] = b.shearY
            if b.inherit != Inherit.Normal:
                obj["transform"] = {Inherit.Normal: "normal", Inherit.OnlyTranslation: "onlyTranslation",
                                    Inherit.NoRotationOrReflection: "noRotationOrReflection",
                                    Inherit.NoScale: "noScale", Inherit.NoScaleOrReflection: "noScaleOrReflection"}[b.inherit]
            if b.skinRequired: obj["skin"] = True
        j["bones"].append(obj)

    # slots
    for s in sk.slots:
        obj: Dict[str, Any] = {}
        if s.name: obj["name"] = s.name
        if s.bone: obj["bone"] = s.bone
        if s.color:
            cs = color_to_string(s.color, True)
            if not is_v2 or cs != "FFFFFFFF":
                obj["color"] = cs
        if not is_v2 and s.darkColor:
            obj["dark"] = color_to_string(s.darkColor, False)
        if s.attachmentName is not None:
            obj["attachment"] = s.attachmentName
        if s.blendMode != BlendMode.Normal:
            if is_v2:
                if s.blendMode == BlendMode.Additive:
                    obj["additive"] = True
            else:
                obj["blend"] = {BlendMode.Additive: "additive", BlendMode.Multiply: "multiply",
                                BlendMode.Screen: "screen"}[s.blendMode]
        j["slots"].append(obj)

    # ik (both versions)
    if sk.ikConstraints or not is_v2:
        j["ik"] = []
        for ik in sk.ikConstraints:
            obj: Dict[str, Any] = {}
            if ik.name: obj["name"] = ik.name
            if ik.order != 0: obj["order"] = ik.order
            if ik.skinRequired: obj["skin"] = True
            if ik.bones: obj["bones"] = ik.bones
            if ik.target: obj["target"] = ik.target
            if ik.mix != 1.0: obj["mix"] = ik.mix
            if not is_v2 and ik.softness != 0.0: obj["softness"] = ik.softness
            if not ik.bendPositive: obj["bendPositive"] = False
            if not is_v2:
                if ik.compress: obj["compress"] = True
                if ik.stretch: obj["stretch"] = True
                if ik.uniform: obj["uniform"] = True
            j["ik"].append(obj)

    # transform (V3 only)
    if not is_v2:
        j["transform"] = []
        for tf in sk.transformConstraints:
            obj: Dict[str, Any] = {}
            if tf.name: obj["name"] = tf.name
            if tf.order != 0: obj["order"] = tf.order
            if tf.skinRequired: obj["skin"] = True
            if tf.bones: obj["bones"] = tf.bones
            if tf.target: obj["target"] = tf.target
            if tf.rotateMix != 1.0: obj["rotateMix"] = tf.rotateMix
            if tf.translateMix != 1.0: obj["translateMix"] = tf.translateMix
            if tf.scaleMix != 1.0: obj["scaleMix"] = tf.scaleMix
            if tf.shearMix != 1.0: obj["shearMix"] = tf.shearMix
            if tf.offsetRotation != 0.0: obj["rotation"] = tf.offsetRotation
            if tf.offsetX != 0.0: obj["x"] = tf.offsetX
            if tf.offsetY != 0.0: obj["y"] = tf.offsetY
            if tf.offsetScaleX != 0.0: obj["scaleX"] = tf.offsetScaleX
            if tf.offsetScaleY != 0.0: obj["scaleY"] = tf.offsetScaleY
            if tf.offsetShearY != 0.0: obj["shearY"] = tf.offsetShearY
            if tf.relative: obj["relative"] = True
            if tf.local: obj["local"] = True
            j["transform"].append(obj)

    # path (V3 only)
    if not is_v2:
        j["path"] = []
        for p in sk.pathConstraints:
            obj: Dict[str, Any] = {}
            if p.name: obj["name"] = p.name
            if p.order != 0: obj["order"] = p.order
            if p.skinRequired: obj["skin"] = True
            if p.bones: obj["bones"] = p.bones
            if p.targetSlot: obj["target"] = p.targetSlot
            if p.positionMode != PositionMode.Percent:
                obj["positionMode"] = "fixed"
            if p.spacingMode != SpacingMode.Length:
                obj["spacingMode"] = {SpacingMode.Fixed: "fixed", SpacingMode.Percent: "percent",
                                      SpacingMode.Proportional: "proportional"}.get(p.spacingMode, "length")
            if p.rotateMode != RotateMode.Tangent:
                obj["rotateMode"] = {RotateMode.Chain: "chain", RotateMode.ChainScale: "chainScale"}.get(p.rotateMode, "tangent")
            if p.offsetRotation != 0.0: obj["rotation"] = p.offsetRotation
            if p.position != 0.0: obj["position"] = p.position
            if p.spacing != 0.0: obj["spacing"] = p.spacing
            if p.rotateMix != 1.0: obj["rotateMix"] = p.rotateMix
            if p.translateMix != 1.0: obj["translateMix"] = p.translateMix
            j["path"].append(obj)

    # skins
    if is_v2:
        skins_dict: Dict[str, Any] = {}
        for skin in sk.skins:
            skin_obj: Dict[str, Any] = {}
            for slot_name, slot_map in skin.attachments.items():
                slot_obj: Dict[str, Any] = {}
                for att_name, att in slot_map.items():
                    a_obj: Dict[str, Any] = {}
                    if isinstance(att, RegionAttachment):
                        a_obj["name"] = att.name
                        a_obj["path"] = att.path
                        if att.x != 0: a_obj["x"] = _clean_float(round(att.x, 5))
                        if att.y != 0: a_obj["y"] = _clean_float(round(att.y, 5))
                        a_obj["scaleX"] = _clean_float(round(att.scaleX, 5))
                        a_obj["scaleY"] = _clean_float(round(att.scaleY, 5))
                        if att.rotation != 0: a_obj["rotation"] = _clean_float(round(att.rotation, 4))
                    elif isinstance(att, SkinnedMeshAttachment):
                        a_obj["type"] = "skinnedmesh"
                        a_obj["name"] = att.name
                        a_obj["path"] = att.path
                        interleaved: List = []
                        bi, wi = 0, 0
                        while bi < len(att.bones):
                            bc = att.bones[bi]
                            interleaved.append(bc)
                            bi += 1
                            for _ in range(bc):
                                interleaved.append(att.bones[bi])
                                interleaved.append(_clean_float(round(att.weights[wi], 5)))
                                interleaved.append(_clean_float(round(att.weights[wi + 1], 5)))
                                interleaved.append(_clean_float(round(att.weights[wi + 2], 5)))
                                bi += 1
                                wi += 3
                        a_obj["uvs"] = [_clean_float(round(u, 8)) for u in att.uvs]
                        a_obj["vertices"] = interleaved
                        a_obj["triangles"] = att.triangles
                        a_obj["hull"] = att.hullLength
                    elif isinstance(att, MeshAttachment):
                        a_obj["type"] = "mesh"
                        a_obj["name"] = att.name
                        a_obj["path"] = att.path
                        if att.vertices: a_obj["vertices"] = [_clean_float(round(v, 5)) for v in att.vertices]
                        a_obj["hull"] = att.hullLength
                        if att.uvs: a_obj["uvs"] = [_clean_float(round(u, 8)) for u in att.uvs]
                        if att.triangles: a_obj["triangles"] = att.triangles
                    elif isinstance(att, BoundingBoxAttachment):
                        a_obj["type"] = "boundingbox"
                        if att.vertices: a_obj["vertexCount"] = att.vertexCount; a_obj["vertices"] = att.vertices

                    if hasattr(att, 'color') and att.color:
                        a_obj["color"] = color_to_string(att.color, True)
                    if hasattr(att, 'width'):
                        a_obj["width"] = _clean_float(att.width)
                    if hasattr(att, 'height'):
                        a_obj["height"] = _clean_float(att.height)
                    slot_obj[att_name] = a_obj
                skin_obj[slot_name] = slot_obj
            skins_dict[skin.name] = skin_obj
        j["skins"] = skins_dict
    else:
        # V3 skins format (list)
        skin_list = []
        for skin in sk.skins:
            s_obj: Dict[str, Any] = {"name": skin.name}
            if skin.bones: s_obj["bones"] = skin.bones
            if skin.ik: s_obj["ik"] = skin.ik
            if skin.transform: s_obj["transform"] = skin.transform
            if skin.paths: s_obj["path"] = skin.paths
            for slot_name, slot_map in skin.attachments.items():
                for att_name, att in slot_map.items():
                    a_obj: Dict[str, Any] = {}
                    if att.name != att_name: a_obj["name"] = att.name
                    if att.type not in (AttachmentType.Mesh, AttachmentType.Linkedmesh):
                        if att.path and att.path != att_name: a_obj["path"] = att.path
                    if att.type != AttachmentType.Region:
                        a_obj["type"] = {AttachmentType.Boundingbox: "boundingbox", AttachmentType.Mesh: "mesh",
                                         AttachmentType.Linkedmesh: "linkedmesh", AttachmentType.Path: "path",
                                         AttachmentType.Point: "point", AttachmentType.Clipping: "clipping"}.get(att.type, "region")
                    match att:
                        case RegionAttachment() as r:
                            if r.x != 0.0: a_obj["x"] = r.x
                            if r.y != 0.0: a_obj["y"] = r.y
                            if r.rotation != 0.0: a_obj["rotation"] = r.rotation
                            if r.scaleX != 1.0: a_obj["scaleX"] = r.scaleX
                            if r.scaleY != 1.0: a_obj["scaleY"] = r.scaleY
                            a_obj["width"] = r.width; a_obj["height"] = r.height
                            if r.color: a_obj["color"] = color_to_string(r.color, True)
                        case BoundingBoxAttachment() as bb:
                            if bb.color: a_obj["color"] = color_to_string(bb.color, True)
                            if bb.vertices: a_obj["vertexCount"] = bb.vertexCount; a_obj["vertices"] = bb.vertices
                        case MeshAttachment() as m:
                            a_obj["width"] = m.width; a_obj["height"] = m.height
                            ep = m.path or att.path
                            if ep and ep != att_name: a_obj["path"] = ep
                            if m.color: a_obj["color"] = color_to_string(m.color, True)
                            if m.hullLength: a_obj["hull"] = m.hullLength
                            if m.triangles: a_obj["triangles"] = m.triangles
                            if m.edges: a_obj["edges"] = m.edges
                            if m.uvs: a_obj["uvs"] = m.uvs
                            if m.vertices: a_obj["vertexCount"] = m.vertexCount; a_obj["vertices"] = m.vertices
                        case LinkedMeshAttachment() as lm:
                            a_obj["width"] = lm.width; a_obj["height"] = lm.height
                            if lm.color: a_obj["color"] = color_to_string(lm.color, True)
                            a_obj["parent"] = lm.parentMesh
                            if not lm.deform: a_obj["deform"] = False
                            a_obj["skin"] = sk.skins[lm.skinIndex].name if 0 <= lm.skinIndex < len(sk.skins) else None
                        case PathAttachment() as pa:
                            if pa.closed: a_obj["closed"] = True
                            if not pa.constantSpeed: a_obj["constantSpeed"] = pa.constantSpeed
                            if pa.color: a_obj["color"] = color_to_string(pa.color, True)
                            if pa.vertices: a_obj["vertexCount"] = pa.vertexCount; a_obj["vertices"] = pa.vertices
                            if pa.lengths: a_obj["lengths"] = pa.lengths
                        case PointAttachment() as pt:
                            if pt.x != 0.0: a_obj["x"] = pt.x
                            if pt.y != 0.0: a_obj["y"] = pt.y
                            if pt.rotation != 0.0: a_obj["rotation"] = pt.rotation
                            if pt.color: a_obj["color"] = color_to_string(pt.color, True)
                        case ClippingAttachment() as cl:
                            if cl.endSlot: a_obj["end"] = cl.endSlot
                            if cl.color: a_obj["color"] = color_to_string(cl.color, True)
                            if cl.vertices: a_obj["vertexCount"] = cl.vertexCount; a_obj["vertices"] = cl.vertices
                    s_obj.setdefault("attachments", {}).setdefault(slot_name, {})[att_name] = a_obj
            skin_list.append(s_obj)
        j["skins"] = skin_list

    # events
    if is_v2:
        if sk.events:
            ev_obj_v2: Dict[str, Any] = {}
            for e in sk.events:
                item_v2: Dict[str, Any] = {}
                if e.intValue != 0: item_v2["int"] = e.intValue
                if e.floatValue != 0.0: item_v2["float"] = e.floatValue
                if e.stringValue is not None and e.stringValue != "": item_v2["string"] = e.stringValue
                ev_obj_v2[e.name] = item_v2
            j["events"] = ev_obj_v2
    else:
        ev_obj: Dict[str, Any] = {}
        for e in sk.events:
            item: Dict[str, Any] = {}
            if e.intValue != 0: item["int"] = e.intValue
            if e.floatValue != 0.0: item["float"] = e.floatValue
            if e.stringValue is not None: item["string"] = e.stringValue
            if e.audioPath:
                item["audio"] = e.audioPath
                if e.volume != 1.0: item["volume"] = e.volume
                if e.balance != 0.0: item["balance"] = e.balance
            ev_obj[e.name] = item
        j["events"] = ev_obj

    # animations
    if sk.animations:
        slot_by_name = {s.name: s for s in sk.slots}
        bone_by_name = {b.name: b for b in sk.bones}
        anims: Dict[str, Any] = {}
        for anim in sk.animations:
            if not is_v2:
                build_animation_json_v3(anim, sk)
            _patch_anim_setup_keyframes(anim, slot_by_name, bone_by_name)
            a_obj: Dict[str, Any] = {}
            if anim.slots: a_obj["slots"] = anim.slots
            if anim.bones: a_obj["bones"] = anim.bones
            if anim.ik: a_obj["ik"] = anim.ik
            if not is_v2:
                if anim.transform: a_obj["transform"] = anim.transform
                if anim.path: a_obj["path"] = anim.path
                if anim.deform: a_obj["deform"] = anim.deform
            if anim.ffd: a_obj["ffd"] = anim.ffd
            if anim.drawOrder: a_obj["drawOrder"] = anim.drawOrder
            if anim.events:
                a_obj["events"] = sorted(anim.events, key=lambda e: e.get("time", 0.0))
            anims[anim.name] = a_obj
        j["animations"] = anims

    return j


# ==============================
# Converter
# ==============================
def convert_scsp_to_json(input_path: str, output_path: str, compress: bool = True) -> bool:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(input_path, "rb") as f:
        data = f.read()

    try:
        skeleton, _ok = read_binary_skeleton(data)
    except Exception as e:
        print(f"[ERROR] {input_path}: {e}")
        return False

    root = write_json_data(skeleton)
    sep = (",", ":") if compress else (", ", ": ")
    json_str = json.dumps(root, ensure_ascii=False, separators=sep)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(json_str)

    ver_label = "V2 (2.1.27)" if skeleton.scspVersion == ScspVersion.V2 else "V3 (3.8.99)"
    print(f"[OK] {input_path} → {output_path} ({ver_label})")
    return True


def batch_convert(input_dir: str, output_dir: str, compress: bool = True):
    inp = Path(input_dir)
    out = Path(output_dir)
    total = success = 0
    for f in inp.rglob("*.scsp"):
        total += 1
        rel = f.relative_to(inp)
        of = out / rel.with_suffix(".json")
        try:
            if convert_scsp_to_json(str(f), str(of), compress):
                success += 1
        except Exception as e:
            print(f"[ERROR] {f}: {e}")
    print(f"\nDone: {success}/{total} files converted.")


# ==============================
# Entry
# ==============================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scsp2json.py <input.scsp|input_dir> [output.json|output_dir]")
        sys.exit(1)

    inp = sys.argv[1]
    if os.path.isdir(inp):
        outp = sys.argv[2] if len(sys.argv) > 2 else inp + "_json"
        batch_convert(inp, outp)
    else:
        outp = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(inp)[0] + ".json"
        convert_scsp_to_json(inp, outp)
