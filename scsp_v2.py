"""
V2 (Spine 2.1.27) SCSP reading functions.
"""
from __future__ import annotations

import logging
import math
import struct

import numpy as np

from scsp_common import (
    SpineBinaryReader, SkeletonData, AnimationData,
    BoneData, SlotData, IKConstraintData, SkinData, EventData,
    Attachment, VertexAttachment, RegionAttachment, MeshAttachment,
    BoundingBoxAttachment, SkinnedMeshAttachment,
    BlendMode, CurveType, AttachmentType, V2AttachmentType, V2TimelineType,
    get_pool_string, read_f32_array, read_u8_array,
    read_u16_array, read_u32_array,
    f32_color, color_to_string,
)
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple, Set


# ==============================
# V2 Reading Functions (2.1.27)
# ==============================
def read_skeleton_info_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    """Two V2 sub-layouts exist:

    WITH magic ("scsp" at spine_data[0:4], version u32 at [4:8]):
      [8]   _unknown (88)
      [12]  width        f32
      [16]  height       f32
      [20]  extra_floats (4 × f32, 16 bytes)
      [36]  bone_count … anim_count (6 × u32, 24 bytes)
      [60]  (40 bytes reserved)
      [100] hash_off     u32
      [104] ver_off      u32
      [108] bone data begins

    WITHOUT magic (data starts directly):
      [0]   bone_count … anim_count (6 × u32, 24 bytes)
      [24]  (40 bytes reserved)
      [64]  width        f32
      [68]  height       f32
      [72]  hash_off     u32
      [76]  ver_off      u32
      [80]  bone data begins
    """
    has_magic = r.data[:4] == b"scsp"
    sk.v2_has_magic = has_magic

    if has_magic:
        r.reset_pos(8)
        _unknown = r.read_u32()
        sk.width = r.read_f32()
        sk.height = r.read_f32()
        r.skip(16)  # 4 extra floats
        sk.v2_bone_count = r.read_u32()
        sk.v2_ik_count = r.read_u32()
        sk.v2_slot_count = r.read_u32()
        sk.v2_skin_count = r.read_u32()
        sk.v2_event_count = r.read_u32()
        sk.v2_anim_count = r.read_u32()
        r.skip(40)
        hash_off = r.read_u32()
        ver_off = r.read_u32()
    else:
        r.reset_pos(0)
        sk.v2_bone_count = r.read_u32()
        sk.v2_ik_count = r.read_u32()
        sk.v2_slot_count = r.read_u32()
        sk.v2_skin_count = r.read_u32()
        sk.v2_event_count = r.read_u32()
        sk.v2_anim_count = r.read_u32()
        r.skip(40)
        sk.width = r.read_f32()
        sk.height = r.read_f32()
        hash_off = r.read_u32()
        ver_off = r.read_u32()

    sk._v2_used_pool_offsets.update((hash_off, ver_off))
    sk.hashString = get_pool_string(hash_off, sk)
    ver_str = get_pool_string(ver_off, sk)
    if ver_str and ".scsp" in ver_str:
        sk.version = ver_str.replace(".scsp", "")
    else:
        sk.version = ver_str

def read_bones_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    bones: List[BoneData] = []
    for _ in range(sk.v2_bone_count):
        bone = BoneData()
        bone.length = r.read_f32()
        bone.x = r.read_f32()
        bone.y = r.read_f32()
        bone.rotation = r.read_f32()
        bone.scaleX = r.read_f32()
        bone.scaleY = r.read_f32()
        bone.flipX = r.read_u32() > 0
        bone.flipY = r.read_u32() > 0
        bone.inheritScale = r.read_u32() > 0
        bone.inheritRotation = r.read_u32() > 0
        name_off = r.read_u32()
        parent_idx = r.read_u16()
        sk._v2_used_pool_offsets.add(name_off)
        bone.name = get_pool_string(name_off, sk)
        if parent_idx < len(bones):
            bone.parent = bones[parent_idx].name
        bones.append(bone)
    sk.bones = bones

def read_slots_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    slots: List[SlotData] = []
    for _ in range(sk.v2_slot_count):
        slot = SlotData()
        name_off = r.read_u32()
        bone_idx = r.read_u16()
        att_off = r.read_u32()
        cr, cg, cb, ca = r.read_f32(), r.read_f32(), r.read_f32(), r.read_f32()
        blend = r.read_u32()
        sk._v2_used_pool_offsets.add(name_off)
        if att_off != 0xFFFFFFFF:
            sk._v2_used_pool_offsets.add(att_off)
        slot.name = get_pool_string(name_off, sk)
        slot.bone = sk.bones[bone_idx].name if 0 <= bone_idx < len(sk.bones) else None
        slot.color = f32_color(cr, cg, cb, ca)
        slot.attachmentName = get_pool_string(att_off, sk) if att_off != 0xFFFFFFFF else None
        slot.blendMode = BlendMode(blend) if blend <= 3 else BlendMode.Normal
        slots.append(slot)
    sk.slots = slots

def read_iks_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    iks: List[IKConstraintData] = []
    for _ in range(sk.v2_ik_count):
        ik = IKConstraintData()
        name_off = r.read_u32()
        bone_count = r.read_u32() if not sk.v2_has_magic else r.read_u16()
        bone_idxs = read_u16_array(r, bone_count)
        target_idx = r.read_u16()
        ik.mix = r.read_f32()
        bend = r.read_i32()
        sk._v2_used_pool_offsets.add(name_off)
        ik.name = get_pool_string(name_off, sk)
        ik.bendPositive = bend > 0
        ik.target = sk.bones[target_idx].name if 0 <= target_idx < len(sk.bones) else None
        for bi in bone_idxs:
            if 0 <= bi < len(sk.bones):
                ik.bones.append(sk.bones[bi].name)
        iks.append(ik)
    sk.ikConstraints = iks

def read_skins_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    skins: List[SkinData] = []
    skin_records: List[Dict] = []

    for _ in range(sk.v2_skin_count):
        skin = SkinData()
        skin_name_off = r.read_u32()
        part_count = r.read_u16()
        sk._v2_used_pool_offsets.add(skin_name_off)
        skin.name = get_pool_string(skin_name_off, sk)
        attachments: Dict[str, Dict[str, Attachment]] = defaultdict(dict)

        for _ in range(part_count):
            att_name_off = r.read_u32()
            slot_idx = r.read_u32()
            data_type = r.read_u32()

            sk._v2_used_pool_offsets.add(att_name_off)
            att_name = get_pool_string(att_name_off, sk)
            slot_name = sk.slots[slot_idx].name if 0 <= slot_idx < len(sk.slots) else None

            skin_records.append({
                'skin': skin.name,
                'skin_slot': slot_name,
                'skin_attachment': att_name
            })

            if data_type == V2AttachmentType.BoundingBox:
                item_name_off = r.read_u32()
                vert_count = r.read_u32()
                verts = read_f32_array(r, vert_count)
                att = BoundingBoxAttachment()
                att.vertices = verts
                att.vertexCount = vert_count // 2
                sk._v2_used_pool_offsets.add(item_name_off)
                att.name = get_pool_string(item_name_off, sk)
                att.type = AttachmentType.Boundingbox
                attachments[slot_name][att_name] = att
                continue

            item_name_off = r.read_u32()
            item_path_off = r.read_u32()
            sk._v2_used_pool_offsets.update((item_name_off, item_path_off))
            item_name = get_pool_string(item_name_off, sk)
            item_path = get_pool_string(item_path_off, sk)

            if data_type == V2AttachmentType.Region:
                att = RegionAttachment()
                att.x = r.read_f32()
                att.y = r.read_f32()
                att.scaleX = r.read_f32()
                att.scaleY = r.read_f32()
                att.rotation = r.read_f32()
                orig_width = r.read_f32()
                orig_height = r.read_f32()
                att.color = f32_color(r.read_f32(), r.read_f32(), r.read_f32(), r.read_f32())
                r.skip(8)
                _atlas_width = r.read_u32()
                _atlas_height = r.read_u32()
                r.skip(72)
                att.width = orig_width
                att.height = orig_height
                att.name = item_name
                att.path = item_path
                att.type = AttachmentType.Region
                attachments[slot_name][att_name] = att

            elif data_type == V2AttachmentType.Mesh:
                att = MeshAttachment()
                vert_count = r.read_u32()
                verts = read_f32_array(r, vert_count)
                hull = r.read_u32()
                # Mesh stores atlas UV first, then region UV (reversed vs SkinnedMesh)
                _uvs_atlas = read_f32_array(r, vert_count)
                uvs_region = read_f32_array(r, vert_count)
                tri_count = r.read_u32()
                tris = read_u32_array(r, tri_count)
                att.color = f32_color(r.read_f32(), r.read_f32(), r.read_f32(), r.read_f32())
                r.skip(48)
                att.width = r.read_f32()
                att.height = r.read_f32()
                att.vertices = verts
                att.vertexCount = vert_count // 2
                att.hullLength = hull
                att.uvs = uvs_region
                att.triangles = tris
                att.name = item_name
                att.path = item_path
                att.type = AttachmentType.Mesh
                attachments[slot_name][att_name] = att

            elif data_type == V2AttachmentType.SkinnedMesh:
                att = SkinnedMeshAttachment()
                bone_cnt = r.read_u32()
                mbones = read_u32_array(r, bone_cnt)
                weight_cnt = r.read_u32()
                weights = read_f32_array(r, weight_cnt)
                tri_cnt = r.read_u32()
                tris = read_u32_array(r, tri_cnt)
                uv_cnt = r.read_u32()
                # SkinnedMesh stores region UV first, then atlas UV (reversed vs Mesh)
                uvs_region = read_f32_array(r, uv_cnt)
                _uvs_atlas = read_f32_array(r, uv_cnt)
                hull = r.read_u32()
                att.color = f32_color(r.read_f32(), r.read_f32(), r.read_f32(), r.read_f32())
                r.skip(48)
                att.width = r.read_f32()
                att.height = r.read_f32()
                att.bones = mbones
                att.weights = weights
                att.triangles = tris
                att.uvs = uvs_region
                att.hullLength = hull
                att.name = item_name
                att.path = item_path
                att.type = AttachmentType.Mesh
                attachments[slot_name][att_name] = att
            else:
                raise ValueError(f"Unknown V2 attachment type: {data_type}")

        skin.attachments = attachments
        skins.append(skin)

    sk.skins = skins
    sk.v2_skin_records = skin_records

def _normalize_rotation_angles(entries: List[Dict]) -> None:
    """Normalize rotation angles so consecutive-frame differences stay within [-180, 180].

    Spine runtimes wrap internally, but many viewers do naive linear
    interpolation, causing visual glitches when raw angles jump by >180°.
    """
    for i in range(1, len(entries)):
        prev = entries[i - 1].get('angle', 0)
        cur = entries[i].get('angle', 0)
        diff = cur - prev
        while diff > 180:
            diff -= 360
        while diff < -180:
            diff += 360
        entries[i]['angle'] = round(prev + diff, 4)


# ==============================
# 180° rotation offset fix
# ==============================
_TOGGLE_THRESHOLD = 130
_OFFSET_THRESHOLD = 120
_COST_RATIO = 0.5
_MAX_SEGMENTS = 16
_rotation_fix_enabled = True


def set_rotation_fix_enabled(enabled: bool) -> None:
    global _rotation_fix_enabled
    _rotation_fix_enabled = enabled


def _wrap_180(a: float) -> float:
    a = a % 360
    if a > 180:
        a -= 360
    return a


def _flip_angle(a: float) -> float:
    return a - 180 if a > 0 else a + 180


def _fix_rotation_timeline(entries: List[Dict]) -> bool:
    """Fix 180-degree offset artifacts in a single rotation timeline.

    Some SCSP V2 rotation keyframes are stored with a ~180° offset from their
    intended values.  This function detects and corrects such artifacts by:

      1. Detecting "toggle points" (consecutive frames differing by >130°).
      2. If no toggles exist but ALL frames are near ±180° (>150°), applying
         a uniform 180° flip (handles constant-offset timelines).
      3. For timelines with toggles: splitting into segments, identifying
         offset-candidate segments (mean |wrap(angle)| > 120°), and picking
         the flip combination that minimises total squared frame-to-frame
         wrapped differences.
      4. Safety: requiring >=50% cost reduction and max corrected diff < 130°.

    Operates on *entries* in place.  Returns True if any correction was made.
    """
    if not _rotation_fix_enabled:
        return False
    if len(entries) < 2:
        return False

    angles = [e.get('angle', 0) for e in entries]

    toggles: List[int] = []
    for i in range(1, len(angles)):
        wd = _wrap_180(angles[i] - angles[i - 1])
        if abs(wd) > _TOGGLE_THRESHOLD:
            toggles.append(i)

    if not toggles:
        UNIFORM_THRESHOLD = 150
        if all(abs(_wrap_180(a)) > UNIFORM_THRESHOLD for a in angles):
            for i, e in enumerate(entries):
                e['angle'] = round(_flip_angle(angles[i]), 4)
            return True
        return False

    bounds = [0] + toggles + [len(angles)]
    segments = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    n_seg = len(segments)

    if n_seg > _MAX_SEGMENTS:
        return False

    is_candidate = []
    for start, end in segments:
        mean_abs_wrap = sum(abs(_wrap_180(angles[j]))
                           for j in range(start, end)) / (end - start)
        is_candidate.append(mean_abs_wrap > _OFFSET_THRESHOLD)

    if not any(is_candidate):
        return False

    def _cost(cand: List[float]) -> float:
        return sum(_wrap_180(cand[i] - cand[i - 1]) ** 2
                   for i in range(1, len(cand)))

    base_cost = _cost(angles)
    best_cost = base_cost
    best_mask = 0

    for mask in range(1, 1 << n_seg):
        if any((mask >> si) & 1 and not is_candidate[si]
               for si in range(n_seg)):
            continue
        cand = list(angles)
        for si, (start, end) in enumerate(segments):
            if (mask >> si) & 1:
                for j in range(start, end):
                    cand[j] = _flip_angle(cand[j])
        cost = _cost(cand)
        if cost < best_cost:
            best_cost = cost
            best_mask = mask

    if best_mask == 0:
        return False

    if base_cost > 0 and best_cost / base_cost > _COST_RATIO:
        return False

    corrected = list(angles)
    for si, (start, end) in enumerate(segments):
        if (best_mask >> si) & 1:
            for j in range(start, end):
                corrected[j] = round(_flip_angle(corrected[j]), 4)

    max_wd = max(abs(_wrap_180(corrected[i] - corrected[i - 1]))
                 for i in range(1, len(corrected)))
    if max_wd > _TOGGLE_THRESHOLD:
        return False

    for i, e in enumerate(entries):
        e['angle'] = corrected[i]
    return True


def _read_v2_curves(r: SpineBinaryReader, frame_count: int) -> List[Dict]:
    """Read V2-style curve data. Returns list of curve dicts for each frame transition."""
    curves: List[Dict] = []
    marker = r.read_u16()
    if marker >= 0xFFFE:
        return []
    for ci in range(frame_count - 1):
        cv = r.read_u8()
        entry: Dict[str, Any] = {}
        if cv == CurveType.STEPPED:
            entry["curve"] = "stepped"
        elif cv == CurveType.BEZIER:
            r.skip(4)
            c1, c2, c3, c4 = r.read_f32(), r.read_f32(), r.read_f32(), r.read_f32()
            entry["curve"] = [round(c1, 4), round(c2, 4), round(c3, 4), round(c4, 4)]
        curves.append(entry)
    return curves


def read_events_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    r.skip(4)  # default skin index
    events: List[EventData] = []
    for _ in range(sk.v2_event_count):
        ev = EventData()
        name_off = r.read_u32()
        ev.intValue = r.read_i32()
        ev.floatValue = r.read_f32()
        str_off = r.read_u32()
        sk._v2_used_pool_offsets.update((name_off, str_off))
        ev.name = get_pool_string(name_off, sk)
        ev.stringValue = get_pool_string(str_off, sk)
        events.append(ev)
    sk.events = events


def _parse_v2_timeline_entry(r: SpineBinaryReader, sk: SkeletonData,
                             anim: AnimationData, tl_type_v2: int) -> bool:
    """Parse a single V2 timeline entry. Returns True if parsed, False to break."""
    if tl_type_v2 == V2TimelineType.Scale:
        bone_idx = r.read_u32()
        raw_count = r.read_u32()
        frame_count = raw_count // 3
        entries = []
        for fi in range(frame_count):
            t, x, y = r.read_f32(), r.read_f32(), r.read_f32()
            entry: Dict[str, Any] = {"time": round(t, 4)}
            entry["x"] = round(x, 4)
            entry["y"] = round(y, 4)
            entries.append(entry)
            anim.duration = max(anim.duration, t)
        curves = _read_v2_curves(r, frame_count)
        for ci, cv in enumerate(curves):
            if cv:
                entries[ci].update(cv)
        bone_name = sk.bones[bone_idx].name if 0 <= bone_idx < len(sk.bones) else ""
        anim.bones.setdefault(bone_name, {})["scale"] = entries

    elif tl_type_v2 == V2TimelineType.Rotate:
        bone_idx = r.read_u32()
        raw_count = r.read_u32()
        frame_count = raw_count // 2
        entries = []
        for fi in range(frame_count):
            t, angle = r.read_f32(), r.read_f32()
            entry: Dict[str, Any] = {"time": round(t, 4), "angle": round(angle, 4)}
            entries.append(entry)
            anim.duration = max(anim.duration, t)
        curves = _read_v2_curves(r, frame_count)
        for ci, cv in enumerate(curves):
            if cv:
                entries[ci].update(cv)
        _fix_rotation_timeline(entries)
        _normalize_rotation_angles(entries)
        bone_name = sk.bones[bone_idx].name if 0 <= bone_idx < len(sk.bones) else ""
        anim.bones.setdefault(bone_name, {})["rotate"] = entries

    elif tl_type_v2 == V2TimelineType.Translate:
        bone_idx = r.read_u32()
        raw_count = r.read_u32()
        frame_count = raw_count // 3
        entries = []
        for fi in range(frame_count):
            t, x, y = r.read_f32(), r.read_f32(), r.read_f32()
            entry: Dict[str, Any] = {"time": round(t, 4)}
            if round(x, 4) != 0: entry["x"] = round(x, 4)
            if round(y, 4) != 0: entry["y"] = round(y, 4)
            entries.append(entry)
            anim.duration = max(anim.duration, t)
        curves = _read_v2_curves(r, frame_count)
        for ci, cv in enumerate(curves):
            if cv:
                entries[ci].update(cv)
        bone_name = sk.bones[bone_idx].name if 0 <= bone_idx < len(sk.bones) else ""
        anim.bones.setdefault(bone_name, {})["translate"] = entries

    elif tl_type_v2 == V2TimelineType.Color:
        slot_idx = r.read_u32()
        raw_count = r.read_u32()
        frame_count = raw_count // 5
        entries = []
        for fi in range(frame_count):
            t = r.read_f32()
            cr, cg, cb, ca = r.read_f32(), r.read_f32(), r.read_f32(), r.read_f32()
            color = f32_color(cr, cg, cb, ca)
            entry: Dict[str, Any] = {"time": round(t, 4)}
            entry["color"] = color_to_string(color, True)
            entries.append(entry)
            anim.duration = max(anim.duration, t)
        curves = _read_v2_curves(r, frame_count)
        for ci, cv in enumerate(curves):
            if cv:
                entries[ci].update(cv)
        slot_name = sk.slots[slot_idx].name if 0 <= slot_idx < len(sk.slots) else ""
        anim.slots.setdefault(slot_name, {})["color"] = entries

    elif tl_type_v2 == V2TimelineType.Attachment:
        slot_idx = r.read_u32()
        frame_count = r.read_u32()
        times = read_f32_array(r, frame_count)
        name_offs = read_u32_array(r, frame_count)
        entries = []
        for fi in range(frame_count):
            entry: Dict[str, Any] = {"time": round(times[fi], 4)}
            name = get_pool_string(name_offs[fi], sk) if name_offs[fi] != 0xFFFFFFFF else None
            entry["name"] = name
            entries.append(entry)
            anim.duration = max(anim.duration, times[fi])
        slot_name = sk.slots[slot_idx].name if 0 <= slot_idx < len(sk.slots) else ""
        anim.slots.setdefault(slot_name, {})["attachment"] = entries

    elif tl_type_v2 == V2TimelineType.FFD:
        frame_count = r.read_u32()
        times = read_f32_array(r, frame_count)
        r.skip(4)
        verts_per_frame = r.read_u32()
        all_verts: List[List[float]] = []
        for fi in range(frame_count):
            frame_verts = read_f32_array(r, verts_per_frame)
            all_verts.append(frame_verts)
            anim.duration = max(anim.duration, times[fi])
        curves = _read_v2_curves(r, frame_count)
        skin_record_id = r.read_u32()
        record = sk.v2_skin_records[skin_record_id] if skin_record_id < len(sk.v2_skin_records) else {}
        ffd_skin = record.get('skin', 'default')
        ffd_slot = record.get('skin_slot', '')
        ffd_att = record.get('skin_attachment', '')

        setup_verts: List[float] = []
        for s_obj in sk.skins:
            if s_obj.name == ffd_skin:
                att = s_obj.attachments.get(ffd_slot, {}).get(ffd_att)
                if isinstance(att, VertexAttachment) and not att.isWeighted:
                    setup_verts = att.vertices
                break

        entries = []
        sv_arr = np.array(setup_verts) if setup_verts else None
        for fi in range(frame_count):
            entry: Dict[str, Any] = {}
            if fi < len(curves) and curves[fi]:
                entry.update(curves[fi])
            entry["time"] = round(times[fi], 4)
            fv = all_verts[fi]
            if sv_arr is not None and len(fv) == len(sv_arr):
                off_arr = np.array(fv) - sv_arr
                if np.any(off_arr != 0):
                    entry["vertices"] = np.round(off_arr, 8).tolist()
            else:
                if any(v != 0 for v in fv):
                    entry["vertices"] = [round(v, 8) for v in fv]
            entries.append(entry)
        anim.ffd.setdefault(ffd_skin, {}).setdefault(ffd_slot, {})[ffd_att] = entries

    elif tl_type_v2 == V2TimelineType.IkConstraint:
        ik_idx = r.read_u32()
        raw_count = r.read_u32()
        frame_count = raw_count // 3
        entries = []
        for fi in range(frame_count):
            t = r.read_f32()
            mix = r.read_f32()
            bend = r.read_f32()
            entry: Dict[str, Any] = {"time": round(t, 4)}
            if round(mix, 4) != 1: entry["mix"] = round(mix, 4)
            entry["bendPositive"] = (not math.isnan(bend)) and int(bend) >= 0
            entries.append(entry)
            anim.duration = max(anim.duration, t)
        curves = _read_v2_curves(r, frame_count)
        for ci, cv in enumerate(curves):
            if cv:
                entries[ci].update(cv)
        ik_name = sk.ikConstraints[ik_idx].name if 0 <= ik_idx < len(sk.ikConstraints) else ""
        anim.ik[ik_name] = entries

    elif tl_type_v2 in (V2TimelineType.FlipX, V2TimelineType.FlipY):
        bone_idx = r.read_u32()
        frame_count = r.read_u32()

        # Sentinel FlipY: bone_idx == slot_count encodes draw-order timing.
        # The N time values mark when each precomputed draw-order array should
        # be applied; the "values" field does not exist — the next bytes are
        # the [0x01][array] trailing block.
        if tl_type_v2 == V2TimelineType.FlipY and bone_idx == len(sk.slots):
            times = read_f32_array(r, frame_count)
            anim._v2_sentinel_times = [round(t, 4) for t in times]
            return False

        saved_pos = r.pos
        times = read_f32_array(r, frame_count)
        if frame_count > 1 and all(t < 1e-6 for t in times):
            r.reset_pos(saved_pos - 12)
            return False
        values = [r.read_byte() for _ in range(frame_count)]
        is_flip_x = (tl_type_v2 == V2TimelineType.FlipX)
        flip_key = "flipX" if is_flip_x else "flipY"
        val_key = "x" if is_flip_x else "y"
        entries = []
        for fi in range(frame_count):
            entry: Dict[str, Any] = {"time": round(times[fi], 4)}
            entry[val_key] = bool(values[fi])
            entries.append(entry)
            anim.duration = max(anim.duration, times[fi])
        bone_name = sk.bones[bone_idx].name if 0 <= bone_idx < len(sk.bones) else ""
        anim.bones.setdefault(bone_name, {})[flip_key] = entries

    elif tl_type_v2 == V2TimelineType.DrawOrder:
        slot_idx = r.read_u32()
        offset = r.read_u32()
        count = r.read_u32()
        frame_count = count // 2
        frames = []
        for _ in range(frame_count):
            t = r.read_f32()
            activation = r.read_f32()
            frames.append((round(t, 4), activation != 0.0))
            anim.duration = max(anim.duration, t)
        if slot_idx < len(sk.slots):
            anim._v2_draworder_raw.append({
                'slot_idx': slot_idx,
                'offset': offset,
                'frames': frames,
            })

    elif tl_type_v2 == V2TimelineType.Event:
        _effect_bone_idx = r.read_u32()  # bone index for effect spawn position (engine-only)
        _reserved = r.read_u32()  # always 0
        raw_count = r.read_u32()
        frame_count = raw_count // 2
        for fi in range(frame_count):
            t = r.read_f32()
            event_idx = int(r.read_f32())
            entry: Dict[str, Any] = {"time": round(t, 4)}
            if 0 <= event_idx < len(sk.events):
                entry["name"] = sk.events[event_idx].name
                ev = sk.events[event_idx]
                if ev.intValue != 0:
                    entry["int"] = ev.intValue
                if ev.floatValue != 0.0:
                    entry["float"] = ev.floatValue
                if ev.stringValue:
                    entry["string"] = ev.stringValue
            anim.events.append(entry)
            anim.duration = max(anim.duration, t)

    else:
        return False

    return True


def _extract_draworder_arrays(
    data: bytes,
    boundary: int,
    slot_count: int,
    parser_end: int,
    anim_start: int = 0,
    sentinel_n: int = 0,
) -> List[List[int]]:
    """Extract precomputed draw-order permutation arrays from an animation.

    When *sentinel_n* > 0 (from the sentinel FlipY entry), the parser has
    already stopped right before the trailing block.  We use a forward scan
    from *parser_end* with a small probe window to locate the first ``0x01``
    separator, then read exactly *sentinel_n* units.

    Otherwise falls back to the legacy remaining-based and backward-scan
    strategies.
    """
    if slot_count <= 0:
        return []

    array_size = slot_count * 4
    unit_size = array_size + 1
    target = list(range(slot_count))

    def _read_block_at(block_start: int, n: int) -> Optional[List[List[int]]]:
        """Read *n* units of ``[0x01][array]`` starting at *block_start*."""
        if block_start < anim_start:
            return None
        arrays: List[List[int]] = []
        for i in range(n):
            sep_pos = block_start + i * unit_size
            if sep_pos >= len(data) or data[sep_pos] != 0x01:
                return None
            arr_pos = sep_pos + 1
            if arr_pos + array_size > len(data):
                return None
            try:
                arr = [
                    struct.unpack_from("<I", data, arr_pos + j * 4)[0]
                    for j in range(slot_count)
                ]
            except struct.error:
                return None
            if not (all(0 <= v < slot_count for v in arr) and sorted(arr) == target):
                return None
            arrays.append(arr)
        return arrays

    # Strategy 0: sentinel FlipY told us exactly N arrays; forward-probe from
    # parser_end (which should be right at or very near the block start).
    if sentinel_n > 0:
        for probe in range(8):
            pos = parser_end + probe
            if pos < len(data) and data[pos] == 0x01:
                result = _read_block_at(pos, sentinel_n)
                if result is not None:
                    return result

    remaining = boundary - parser_end
    if remaining > 0 and remaining % array_size == 0:
        n_expected = remaining // array_size
        if n_expected > 0:
            for gap in range(4):
                block_start = boundary - gap - n_expected * unit_size
                result = _read_block_at(block_start, n_expected)
                if result is not None:
                    return result

    # Fallback: backward scan with multiple offsets
    for gap in range(4):
        arrays = _backward_scan_block(data, boundary - gap, slot_count, anim_start)
        if arrays:
            return arrays

    return []


def _backward_scan_block(
    data: bytes, end: int, slot_count: int, anim_start: int
) -> List[List[int]]:
    """Scan backward from *end* for contiguous ``[0x01][array]`` units."""
    array_size = slot_count * 4
    target = list(range(slot_count))
    arrays: List[List[int]] = []

    pos = end - array_size
    while pos > anim_start:
        try:
            arr = [
                struct.unpack_from("<I", data, pos + i * 4)[0]
                for i in range(slot_count)
            ]
        except struct.error:
            break
        if not (all(0 <= v < slot_count for v in arr) and sorted(arr) == target):
            break
        if pos - 1 < anim_start or data[pos - 1] != 0x01:
            break
        arrays.append(arr)
        pos = pos - 1 - array_size

    arrays.reverse()
    return arrays


def _reverse_spine_offsets(
    draw_order: List[int], slot_count: int
) -> Optional[List[Tuple[int, int]]]:
    """Reverse-engineer Spine-style (slot_index, offset) pairs from a
    precomputed draw-order permutation array.

    Returns a sorted list of ``(slot_index, offset)`` tuples, or ``None`` on
    failure.
    """
    position_of = {slot_idx: pos for pos, slot_idx in enumerate(draw_order)}
    offset_slots: set = set()

    for _ in range(slot_count):
        unchanged = [s for s in range(slot_count) if s not in offset_slots]
        occupied = {position_of[s]: s for s in offset_slots}
        result = [-1] * slot_count
        for p, s in occupied.items():
            result[p] = s
        ui = 0
        for p in range(slot_count):
            if result[p] == -1:
                if ui < len(unchanged):
                    result[p] = unchanged[ui]
                    ui += 1
        if result == list(draw_order):
            break
        new_found = False
        for p in range(slot_count):
            if result[p] != draw_order[p]:
                s = draw_order[p]
                if s not in offset_slots:
                    offset_slots.add(s)
                    new_found = True
        if not new_found:
            break

    offsets = [(s, position_of[s] - s) for s in sorted(offset_slots)]

    # Verify by forward simulation
    check = [-1] * slot_count
    for s, o in offsets:
        idx = s + o
        if not (0 <= idx < slot_count):
            return None
        check[idx] = s
    unchanged = [s for s in range(slot_count) if s not in offset_slots]
    ui = 0
    for i in range(slot_count):
        if check[i] == -1:
            check[i] = unchanged[ui]
            ui += 1
    if check != list(draw_order):
        return None

    return offsets


def _merge_v2_draworder(anim: AnimationData, sk: SkeletonData) -> None:
    """Build the drawOrder timeline for a V2 animation.

    When sentinel timing is available, each trailing array maps directly to
    its corresponding sentinel time (1:1).  Otherwise falls back to the
    activation-state heuristic or per-slot type-9 offsets.
    """
    slot_count = len(sk.slots)
    trailing_arrays: List[List[int]] = anim._v2_trailing_arrays
    sentinel_times: List[float] = anim._v2_sentinel_times

    # Primary path: sentinel times give a 1:1 mapping to trailing arrays.
    if sentinel_times and trailing_arrays:
        offset_cache: Dict[int, List[Dict[str, Any]]] = {}
        all_ok = True
        for arr in trailing_arrays:
            arr_id = id(arr)
            if arr_id not in offset_cache:
                spine_offsets = _reverse_spine_offsets(arr, slot_count)
                if spine_offsets is None:
                    all_ok = False
                    break
                offset_cache[arr_id] = [
                    {"slot": sk.slots[s].name, "offset": o}
                    for s, o in spine_offsets
                    if 0 <= s < slot_count
                ]

        if all_ok:
            n = min(len(sentinel_times), len(trailing_arrays))
            for i in range(n):
                t = sentinel_times[i]
                offset_list = offset_cache[id(trailing_arrays[i])]
                keyframe: Dict[str, Any] = {"time": t}
                if offset_list:
                    keyframe["offsets"] = list(offset_list)
                anim.drawOrder.append(keyframe)
            return

    raw = anim._v2_draworder_raw
    if not raw:
        return

    all_times: set = set()
    for entry in raw:
        for t, _ in entry['frames']:
            all_times.add(t)
    sorted_times = sorted(all_times)

    if trailing_arrays:
        slot_frames: Dict[int, List[Tuple[float, bool]]] = {}
        for entry in raw:
            slot_frames[entry['slot_idx']] = entry['frames']

        def _activation_state_at(t: float) -> frozenset:
            active: set = set()
            for slot_idx, frames in slot_frames.items():
                a = False
                for ft, fa in frames:
                    if ft <= t:
                        a = fa
                    else:
                        break
                if a:
                    active.add(slot_idx)
            return frozenset(active)

        unique_arrays: List[List[int]] = []
        for arr in trailing_arrays:
            if arr not in unique_arrays:
                unique_arrays.append(arr)

        state_to_array: Dict[frozenset, List[int]] = {}
        array_cursor = 0

        for t in sorted_times:
            state = _activation_state_at(t)
            if state not in state_to_array:
                if array_cursor < len(unique_arrays):
                    state_to_array[state] = unique_arrays[array_cursor]
                    array_cursor += 1
                elif len(unique_arrays) == 1:
                    state_to_array[state] = unique_arrays[0]
                else:
                    state_to_array[state] = unique_arrays[-1]

        all_ok = True
        offset_cache: Dict[int, List[Dict[str, Any]]] = {}
        for arr in unique_arrays:
            if id(arr) not in offset_cache:
                spine_offsets = _reverse_spine_offsets(arr, slot_count)
                if spine_offsets is None:
                    all_ok = False
                    break
                offset_cache[id(arr)] = [
                    {"slot": sk.slots[s].name, "offset": o}
                    for s, o in spine_offsets
                    if 0 <= s < slot_count
                ]

        if all_ok:
            for t in sorted_times:
                state = _activation_state_at(t)
                arr = state_to_array[state]
                offset_list = offset_cache[id(arr)]
                keyframe: Dict[str, Any] = {"time": t}
                if offset_list:
                    keyframe["offsets"] = list(offset_list)
                anim.drawOrder.append(keyframe)
            return

    # Fallback: per-slot type-9 logic (used when N=0 or offset conversion fails)
    slot_state: Dict[int, Dict] = {}
    for entry in raw:
        slot_state[entry['slot_idx']] = {
            'offset': entry['offset'],
            'frames': entry['frames'],
        }

    for t in sorted_times:
        offsets = []
        for slot_idx in sorted(slot_state.keys()):
            info = slot_state[slot_idx]
            active = False
            for ft, fa in info['frames']:
                if ft <= t:
                    active = fa
                else:
                    break
            if active and info['offset'] != 0:
                clamped = info['offset']
                target = slot_idx + clamped
                if target >= slot_count:
                    clamped = slot_count - 1 - slot_idx
                elif target < 0:
                    clamped = -slot_idx
                if clamped != 0:
                    slot_name = sk.slots[slot_idx].name
                    offsets.append({"slot": slot_name, "offset": clamped})
        keyframe: Dict[str, Any] = {"time": t}
        if offsets:
            keyframe["offsets"] = offsets
        anim.drawOrder.append(keyframe)


def _collect_anim_name_offsets(sk: SkeletonData) -> Set[int]:
    """Collect string pool offsets for animation names by excluding known names.

    Primary filter: string-text exclusion (any pool entry whose text
    matches a bone/slot/skin/attachment/event name is excluded).

    Collision fix: a structural entity name (bone/slot/IK/skin/event)
    may share its text with an animation (e.g. both a bone and an
    animation called ``down``).  We detect this when a name appears at
    exactly two pool offsets — one consumed by the entity parser, one
    not — and add the unused offset back as an animation candidate.
    """
    known_names: Set[str] = set()
    structural_names: Set[str] = set()

    for b in sk.bones:
        known_names.add(b.name)
        structural_names.add(b.name)
    for sl in sk.slots:
        known_names.add(sl.name)
        structural_names.add(sl.name)
    for ik in sk.ikConstraints:
        known_names.add(ik.name)
        structural_names.add(ik.name)
    for skin in sk.skins:
        known_names.add(skin.name)
        structural_names.add(skin.name)
        for slot_name in skin.attachments:
            known_names.add(slot_name)
            for att_name, att_obj in skin.attachments[slot_name].items():
                known_names.add(att_name)
                if hasattr(att_obj, 'name') and att_obj.name:
                    known_names.add(att_obj.name)
                if hasattr(att_obj, 'path') and att_obj.path:
                    known_names.add(att_obj.path)
    for ev in sk.events:
        known_names.add(ev.name)
        structural_names.add(ev.name)
        if ev.stringValue:
            known_names.add(ev.stringValue)
    if sk.hashString:
        known_names.add(sk.hashString)
    if sk.version:
        known_names.add(sk.version)
        known_names.add(sk.version + ".scsp")

    pool = sk.stringPool
    used_offsets = sk._v2_used_pool_offsets

    name_to_offsets: Dict[str, List[int]] = defaultdict(list)
    anim_offsets: Set[int] = set()
    i = 0
    while i < len(pool):
        end = pool.find(b'\x00', i)
        if end == -1:
            end = len(pool)
        name = pool[i:end].decode('utf-8', errors='replace')
        if name and len(name) >= 2:
            if name not in known_names:
                anim_offsets.add(i)
            else:
                name_to_offsets[name].append(i)
        i = end + 1

    for name in structural_names:
        offsets = name_to_offsets.get(name)
        if not offsets or len(offsets) != 2:
            continue
        entity = [o for o in offsets if o in used_offsets]
        candidate = [o for o in offsets if o not in used_offsets]
        if len(entity) == 1 and len(candidate) == 1:
            anim_offsets.add(candidate[0])

    return anim_offsets


def _prescan_v2_anim_headers(r: SpineBinaryReader, sk: SkeletonData,
                              anim_start: int,
                              anim_name_offsets: Set[int]) -> List[int]:
    """Pre-scan data to find all animation header positions."""
    bone_count = len(sk.bones) if sk.bones else sk.v2_bone_count
    slot_count = len(sk.slots) if sk.slots else sk.v2_slot_count
    data = r.data
    headers: List[int] = []
    seen_names: Set[int] = set()

    for pos in range(anim_start, len(data) - 20):
        name_off = struct.unpack_from("<I", data, pos)[0]
        if name_off not in anim_name_offsets or name_off in seen_names:
            continue
        ec = struct.unpack_from("<I", data, pos + 8)[0]
        if ec < 1 or ec > 5000:
            continue
        ft = struct.unpack_from("<I", data, pos + 12)[0]
        if ft > 10:
            continue
        fi = struct.unpack_from("<I", data, pos + 16)[0]
        if ft in (0, 1, 2, 5, 6) and fi >= bone_count:
            continue
        if ft in (3, 4) and fi >= slot_count:
            continue
        seen_names.add(name_off)
        headers.append(pos)

    headers.sort()
    return headers


def read_animations_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    animations: List[AnimationData] = []
    anim_start = r.pos

    anim_name_offsets = _collect_anim_name_offsets(sk)
    header_positions = _prescan_v2_anim_headers(r, sk, anim_start, anim_name_offsets)

    if len(header_positions) != sk.v2_anim_count:
        logging.warning(
            f"Pre-scan found {len(header_positions)} animation headers, "
            f"expected {sk.v2_anim_count}"
        )

    slot_count = len(sk.slots)

    for ai in range(len(header_positions)):
        r.reset_pos(header_positions[ai])
        anim = AnimationData()
        try:
            anim.name = get_pool_string(r.read_u32(), sk)
            anim.duration = r.read_f32()
            elem_count = r.read_u32()
        except (EOFError, struct.error):
            break

        boundary = header_positions[ai + 1] if ai + 1 < len(header_positions) else len(r.data)

        for _ in range(elem_count):
            if r.pos >= boundary:
                break
            try:
                tl_type_v2 = r.read_u32()
            except (EOFError, struct.error):
                break

            try:
                _parse_ok = _parse_v2_timeline_entry(r, sk, anim, tl_type_v2)
            except (EOFError, struct.error, ValueError, OverflowError):
                break
            if not _parse_ok:
                break

        # Extract draw-order arrays AFTER parsing timelines.
        # The sentinel FlipY (bone_idx==slot_count) tells us exactly how many
        # arrays to expect (N) and the reader is positioned right at the block.
        if slot_count > 0:
            sentinel_n = len(anim._v2_sentinel_times)
            trailing_arrays = _extract_draworder_arrays(
                r.data, boundary, slot_count,
                parser_end=r.pos, anim_start=header_positions[ai],
                sentinel_n=sentinel_n,
            )
            if trailing_arrays:
                anim._v2_trailing_arrays = trailing_arrays

        _merge_v2_draworder(anim, sk)
        animations.append(anim)
    sk.animations = animations

def read_scsp_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    read_bones_v2(r, sk)
    read_iks_v2(r, sk)
    read_slots_v2(r, sk)
    read_skins_v2(r, sk)
    read_events_v2(r, sk)
    read_animations_v2(r, sk)
