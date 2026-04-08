"""
V3 (Spine 3.8.99) SCSP reading and JSON writing functions.
"""
from __future__ import annotations

import numpy as np

from scsp_common import (
    SpineBinaryReader, SkeletonData, ScspVersion,
    BoneData, SlotData, IKConstraintData, TransformConstraintData,
    PathConstraintData, SkinData, EventData, AnimationData,
    Attachment, VertexAttachment, RegionAttachment, MeshAttachment,
    LinkedMeshAttachment, BoundingBoxAttachment, PathAttachment,
    PointAttachment, ClippingAttachment,
    TimelineData, RotateTimeline, TranslateTimeline, ScaleTimeline,
    ShearTimeline, AttachmentTimeline, ColorTimeline, DeformTimeline,
    EventTimeline, DrawOrderTimeline, IKTimeline, TransformTimeline,
    PathPositionTimeline, PathSpacingTimeline, PathMixTimeline,
    TwoColorTimeline,
    Color, Inherit, BlendMode, PositionMode, SpacingMode, RotateMode,
    CurveType, AttachmentType, TimelineType,
    get_pool_string, read_f32_array, read_u8_array, read_i16_array,
    read_u16_array, read_i32_array, read_u32_array,
    merge_weighted_vertices, f32_color, color_to_string,
)
from typing import Any, Dict, List, Optional, Tuple


# ==============================
# V3 Reading Functions (3.8.99)
# ==============================
def read_skeleton_info_v3(r: SpineBinaryReader, sk: SkeletonData) -> None:
    r.reset_pos(14)
    sk.width = r.read_f32()
    sk.height = r.read_f32()
    r.reset_pos(38)
    sk.fps = r.read_f32()
    r.reset_pos(74)
    hash_off = r.read_u32()
    ver_off = r.read_u32()
    sk.hashString = get_pool_string(hash_off, sk)
    ver_str = get_pool_string(ver_off, sk)
    sk.version = ver_str[:6] if ver_str else None
    r.reset_pos(86)
    img_off = r.read_u32()
    aud_off = r.read_u32()
    sk.imagesPath = get_pool_string(img_off, sk)
    sk.audioPath = get_pool_string(aud_off, sk)
    r.reset_pos(98)

def read_vertices_v3(r: SpineBinaryReader) -> Tuple[List[float], List[int], int]:
    bones_count = r.read_u16()
    bones = read_u16_array(r, bones_count)
    vertices_length = r.read_u16()
    vertices = read_f32_array(r, vertices_length)
    world_vertices_length = r.read_u32()
    _name_offset = r.read_u32()
    is_weighted = bones_count > 0
    vertex_count = world_vertices_length // 2
    if is_weighted:
        return merge_weighted_vertices(vertices, bones), bones, vertex_count
    return vertices, bones, vertex_count

def read_attachment_v3(r: SpineBinaryReader, sk: SkeletonData) -> Attachment:
    attachment: Attachment = Attachment()
    att_name_off = r.read_u32()
    att_type = AttachmentType(r.read_u16())
    att_path_off = r.read_u32()

    if att_type == AttachmentType.Region:
        region = RegionAttachment()
        floats = read_f32_array(r, 13)
        _uv_count = r.read_u16()
        _uvs = read_f32_array(r, _uv_count)
        _vert_count = r.read_u16()
        _verts = read_f32_array(r, _vert_count)
        _region_name_off = r.read_u32()
        clr = read_f32_array(r, 4)
        region.x, region.y, region.rotation = floats[0], floats[1], floats[2]
        region.scaleX, region.scaleY = floats[3], floats[4]
        region.width, region.height = floats[5], floats[6]
        region.color = f32_color(*clr)
        region.name = get_pool_string(att_name_off, sk)
        region.path = get_pool_string(_region_name_off, sk)
        attachment = region

    elif att_type == AttachmentType.Boundingbox:
        bb = BoundingBoxAttachment()
        verts, bones, vc = read_vertices_v3(r)
        bb.vertices, bb.isWeighted, bb.vertexCount = verts, len(bones) > 0, vc
        attachment = bb

    elif att_type in (AttachmentType.Mesh, AttachmentType.Linkedmesh):
        mesh = MeshAttachment()
        linked = LinkedMeshAttachment()
        verts, bones, vc = read_vertices_v3(r)
        floats6 = read_f32_array(r, 6)
        c1 = r.read_u16(); _f1 = read_f32_array(r, c1)
        uv_count = r.read_u16(); uvs = read_f32_array(r, uv_count)
        tri_count = r.read_u16(); tris = read_u16_array(r, tri_count)
        edge_count = r.read_u16(); edges = read_u16_array(r, edge_count)
        path_off = r.read_u32()
        floats10 = read_f32_array(r, 10)
        hull = r.read_u32()
        _flag = r.read_boolean()
        _flag_data = r.read_u32()
        parent_off = r.read_u32()
        _parent_slot = r.read_i16()
        skin_index = 0
        if sk.scspVersion == ScspVersion.V3:
            skin_index = r.read_i16()
        else:
            skin_name_off = r.read_u32()
        deform_flag = r.read_boolean()
        mesh.vertexCount, mesh.isWeighted, mesh.vertices = vc, len(bones) > 0, verts
        mesh.uvs, mesh.triangles, mesh.edges, mesh.hullLength = uvs, tris, edges, hull
        clr = f32_color(floats10[6], floats10[7], floats10[8], floats10[9])
        mesh.path = get_pool_string(path_off, sk)
        mesh.color, mesh.width, mesh.height = clr, floats6[2], floats6[3]
        linked.parentMesh = get_pool_string(parent_off, sk)
        linked.skinIndex, linked.deform = skin_index, deform_flag
        linked.color, linked.width, linked.height = clr, floats6[2], floats6[3]
        attachment = mesh if att_type == AttachmentType.Mesh else linked

    elif att_type == AttachmentType.Path:
        pa = PathAttachment()
        verts, bones, vc = read_vertices_v3(r)
        lc = r.read_u16(); lengths = read_f32_array(r, lc)
        pa.vertices, pa.isWeighted, pa.vertexCount = verts, len(bones) > 0, vc
        pa.lengths, pa.closed, pa.constantSpeed = lengths, r.read_boolean(), r.read_boolean()
        attachment = pa

    elif att_type == AttachmentType.Point:
        pt = PointAttachment()
        pf = read_f32_array(r, 3)
        pt.x, pt.y, pt.rotation = pf[0], pf[1], pf[2]
        attachment = pt

    elif att_type == AttachmentType.Clipping:
        cl = ClippingAttachment()
        verts, bones, vc = read_vertices_v3(r)
        end_slot = r.read_i16()
        cl.vertexCount, cl.isWeighted, cl.vertices = vc, len(bones) > 0, verts
        cl.endSlot = sk.slots[end_slot].name if 0 <= end_slot < len(sk.slots) else None
        attachment = cl

    if attachment.name is None:
        attachment.name = get_pool_string(att_name_off, sk)
    if attachment.path is None:
        attachment.path = get_pool_string(att_path_off, sk)
    attachment.type = att_type
    return attachment

def read_bones_v3(r: SpineBinaryReader, sk: SkeletonData) -> None:
    bones: List[BoneData] = []
    bone_count = r.read_u16()
    for i in range(bone_count):
        bone = BoneData()
        _index = r.read_i16()
        name_off = r.read_u32()
        parent_idx = r.read_i16()
        floats = read_f32_array(r, 8)
        inherit = Inherit(r.read_i16())
        skin_req = r.read_boolean()
        bone.name = get_pool_string(name_off, sk)
        if len(bones) > parent_idx >= 0:
            bone.parent = bones[parent_idx].name
        elif parent_idx == -1 and _index != 0:
            bone.parent = "root"
        bone.length, bone.x, bone.y, bone.rotation = floats[0], floats[1], floats[2], floats[3]
        bone.scaleX, bone.scaleY, bone.shearX, bone.shearY = floats[4], floats[5], floats[6], floats[7]
        bone.inherit, bone.skinRequired = inherit, skin_req
        bones.append(bone)
    sk.bones = bones

def read_iks_v3(r: SpineBinaryReader, sk: SkeletonData) -> None:
    iks: List[IKConstraintData] = []
    ik_count = r.read_u16()
    for _ in range(ik_count):
        ik = IKConstraintData()
        name_off = r.read_u32()
        ik.order = r.read_u32()
        ik.skinRequired = r.read_boolean()
        bend = r.read_i32()
        ik.compress = r.read_boolean()
        ik.mix = r.read_f32()
        ik.softness = r.read_f32()
        ik.stretch = r.read_boolean()
        ik.uniform = r.read_boolean()
        target_idx = r.read_i16()
        bc = r.read_u16()
        bone_idxs = read_i16_array(r, bc)
        ik.name = get_pool_string(name_off, sk)
        ik.bendPositive = bend > 0
        ik.target = sk.bones[target_idx].name if 0 <= target_idx < len(sk.bones) else None
        for bi in bone_idxs:
            if 0 <= bi < len(sk.bones):
                ik.bones.append(sk.bones[bi].name)
        iks.append(ik)
    sk.ikConstraints = iks

def read_slots_v3(r: SpineBinaryReader, sk: SkeletonData) -> None:
    slots: List[SlotData] = []
    slot_count = r.read_u16()
    for _ in range(slot_count):
        slot = SlotData()
        _idx = r.read_u16()
        name_off = r.read_u32()
        bone_idx = r.read_u16()
        cr, cg, cb, ca = read_f32_array(r, 4)
        dr, dg, db, da = read_f32_array(r, 4)
        has_dark = r.read_boolean()
        att_off = r.read_u32()
        blend = r.read_u16()
        slot.name = get_pool_string(name_off, sk)
        slot.bone = sk.bones[bone_idx].name if 0 <= bone_idx < len(sk.bones) else None
        slot.color = f32_color(cr, cg, cb, ca)
        slot.darkColor = f32_color(dr, dg, db, da) if has_dark else None
        slot.attachmentName = get_pool_string(att_off, sk)
        slot.blendMode = BlendMode(blend)
        slots.append(slot)
    sk.slots = slots

def read_transform_constraints_v3(r: SpineBinaryReader, sk: SkeletonData) -> None:
    tcs: List[TransformConstraintData] = []
    tc_count = r.read_u16()
    for _ in range(tc_count):
        tf = TransformConstraintData()
        name_off = r.read_u32()
        tf.order = r.read_u32()
        tf.skinRequired = r.read_boolean()
        tf.rotateMix, tf.translateMix = r.read_f32(), r.read_f32()
        tf.scaleMix, tf.shearMix = r.read_f32(), r.read_f32()
        tf.offsetRotation, tf.offsetX, tf.offsetY = r.read_f32(), r.read_f32(), r.read_f32()
        tf.offsetScaleX, tf.offsetScaleY, tf.offsetShearY = r.read_f32(), r.read_f32(), r.read_f32()
        tf.local, tf.relative = r.read_boolean(), r.read_boolean()
        target_idx = r.read_i16()
        bc = r.read_u16()
        bone_idxs = read_i16_array(r, bc)
        tf.name = get_pool_string(name_off, sk)
        tf.target = sk.bones[target_idx].name if 0 <= target_idx < len(sk.bones) else None
        for bi in bone_idxs:
            if 0 <= bi < len(sk.bones):
                tf.bones.append(sk.bones[bi].name)
        tcs.append(tf)
    sk.transformConstraints = tcs

def read_path_constraints_v3(r: SpineBinaryReader, sk: SkeletonData) -> None:
    pcs: List[PathConstraintData] = []
    pc_count = r.read_u16()
    for _ in range(pc_count):
        p = PathConstraintData()
        name_off = r.read_u32()
        p.order = r.read_u32()
        p.skinRequired = r.read_boolean()
        p.positionMode = PositionMode(r.read_i16())
        p.spacingMode = SpacingMode(r.read_i16())
        p.rotateMode = RotateMode(r.read_i16())
        p.offsetRotation, p.position, p.spacing = r.read_f32(), r.read_f32(), r.read_f32()
        p.rotateMix, p.translateMix = r.read_f32(), r.read_f32()
        target_slot_idx = r.read_i16()
        bc = r.read_u16()
        bone_idxs = read_i16_array(r, bc)
        p.name = get_pool_string(name_off, sk)
        p.targetSlot = sk.slots[target_slot_idx].name if 0 <= target_slot_idx < len(sk.slots) else None
        for bi in bone_idxs:
            if 0 <= bi < len(sk.bones):
                p.bones.append(sk.bones[bi].name)
        pcs.append(p)
    sk.pathConstraints = pcs

def read_skins_v3(r: SpineBinaryReader, sk: SkeletonData) -> None:
    from collections import defaultdict
    skins: List[SkinData] = []
    skin_count = r.read_u16()
    for _ in range(skin_count):
        skin = SkinData()
        skin_name_off = r.read_u32()
        bc = r.read_u16(); bone_idxs = read_u16_array(r, bc)
        pc = r.read_u16(); path_offs = read_u32_array(r, pc)
        sa_count = r.read_u16()
        attachments: Dict[str, Dict[str, Attachment]] = defaultdict(dict)
        for _ in range(sa_count):
            slot_idx = r.read_i16()
            slot_name = sk.slots[slot_idx].name if 0 <= slot_idx < len(sk.slots) else None
            att = read_attachment_v3(r, sk)
            attachments[slot_name][att.name] = att
        skin.name = get_pool_string(skin_name_off, sk)
        skin.bones = [sk.bones[i].name for i in bone_idxs]
        skin.paths = [get_pool_string(o, sk) for o in path_offs]
        skin.attachments = attachments
        skins.append(skin)
    sk.skins = skins

def read_events_v3(r: SpineBinaryReader, sk: SkeletonData) -> None:
    events: List[EventData] = []
    ec = r.read_u16()
    for _ in range(ec):
        ev = EventData()
        ev.name = get_pool_string(r.read_u32(), sk)
        ev.intValue = r.read_i32()
        ev.floatValue = r.read_f32()
        ev.stringValue = get_pool_string(r.read_u32(), sk)
        ev.audioPath = get_pool_string(r.read_u32(), sk)
        ev.volume = r.read_f32()
        ev.balance = r.read_f32()
        events.append(ev)
    sk.events = events

def read_animations_v3(r: SpineBinaryReader, sk: SkeletonData) -> None:
    anim_count = r.read_u16()
    animations: List[AnimationData] = []
    for _ in range(anim_count):
        anim = AnimationData()
        anim.name = get_pool_string(r.read_u32(), sk)
        anim.duration = r.read_f32()
        tl_count = r.read_u16()
        timelines: List[TimelineData] = []
        for _ in range(tl_count):
            tl = TimelineData()
            tl_type = TimelineType(r.read_u16())

            if tl_type.value in (0, 1, 2, 3, 5, 9, 10, 11, 12, 13, 14):
                index = r.read_i16()
                fc = r.read_u16(); frames = read_f32_array(r, fc)
                cc = r.read_u16(); curves = read_f32_array(r, cc)

                if tl_type == TimelineType.Rotate:
                    t = RotateTimeline(); t.bone_index = index
                    for i in range(0, fc, 2):
                        if i + 1 < fc:
                            t.times.append(frames[i]); t.angles.append(frames[i+1])
                    tl = t
                elif tl_type == TimelineType.Translate:
                    t = TranslateTimeline(); t.bone_index = index
                    for i in range(0, fc, 3):
                        if i + 2 < fc:
                            t.times.append(frames[i]); t.xs.append(frames[i+1]); t.ys.append(frames[i+2])
                    tl = t
                elif tl_type == TimelineType.Scale:
                    t = ScaleTimeline(); t.bone_index = index
                    for i in range(0, fc, 3):
                        if i + 2 < fc:
                            t.times.append(frames[i]); t.xs.append(frames[i+1]); t.ys.append(frames[i+2])
                    tl = t
                elif tl_type == TimelineType.Shear:
                    t = ShearTimeline(); t.bone_index = index
                    for i in range(0, fc, 3):
                        if i + 2 < fc:
                            t.times.append(frames[i]); t.xs.append(frames[i+1]); t.ys.append(frames[i+2])
                    tl = t
                elif tl_type == TimelineType.Color:
                    t = ColorTimeline(); t.slot_index = index
                    for i in range(0, fc, 5):
                        if i + 4 < fc:
                            t.times.append(frames[i])
                            t.colors.append(f32_color(frames[i+1], frames[i+2], frames[i+3], frames[i+4]))
                    tl = t
                elif tl_type == TimelineType.IkConstraint:
                    t = IKTimeline(); t.ik_index = index
                    for i in range(0, fc, 6):
                        if i + 5 < fc:
                            t.times.append(frames[i])
                            t.mixs.append(frames[i+1]); t.softness.append(frames[i+2])
                            t.bend_directions.append(int(frames[i+3]))
                            t.compresses.append(frames[i+4] > 0)
                            t.stretches.append(frames[i+5] > 0)
                    tl = t
                elif tl_type == TimelineType.TransformConstraint:
                    t = TransformTimeline(); t.transform_index = index
                    for i in range(0, fc, 5):
                        if i + 4 < fc:
                            t.times.append(frames[i])
                            t.rotateMixs.append(frames[i+1]); t.translateMixs.append(frames[i+2])
                            t.scaleMixs.append(frames[i+3]); t.shearMixs.append(frames[i+4])
                    tl = t
                elif tl_type == TimelineType.PathConstraintPosition:
                    t = PathPositionTimeline(); t.path_index = index
                    for i in range(0, fc, 2):
                        if i + 1 < fc:
                            t.times.append(frames[i]); t.positions.append(frames[i+1])
                    tl = t
                elif tl_type == TimelineType.PathConstraintSpacing:
                    t = PathSpacingTimeline(); t.path_index = index
                    for i in range(0, fc, 2):
                        if i + 1 < fc:
                            t.times.append(frames[i]); t.spacings.append(frames[i+1])
                    tl = t
                elif tl_type == TimelineType.PathConstraintMix:
                    t = PathMixTimeline(); t.path_index = index
                    for i in range(0, fc, 3):
                        if i + 2 < fc:
                            t.times.append(frames[i])
                            t.rotateMixs.append(frames[i+1]); t.translateMixs.append(frames[i+2])
                    tl = t
                elif tl_type == TimelineType.TwoColor:
                    t = TwoColorTimeline(); t.slot_index = index
                    for i in range(0, fc, 9):
                        if i + 8 < fc:
                            t.times.append(frames[i])
                            t.colorLights.append(f32_color(frames[i+1], frames[i+2], frames[i+3], frames[i+4]))
                            t.colorDarks.append(f32_color(frames[i+5], frames[i+6], frames[i+7], frames[i+8]))
                    tl = t

                tl.type = tl_type; tl.frames = frames; tl.curves = curves

            elif tl_type == TimelineType.Attachment:
                index = r.read_i16()
                fc = r.read_u16(); frames = read_f32_array(r, fc)
                ac = r.read_u16(); att_offs = read_u32_array(r, ac)
                t = AttachmentTimeline()
                t.times = frames
                t.names = [get_pool_string(o, sk) for o in att_offs]
                t.type = tl_type; t.slot_index = index; t.frames = frames
                tl = t

            elif tl_type == TimelineType.Deform:
                slot_idx = r.read_i16()
                fc = r.read_u16(); frames = read_f32_array(r, fc)
                cc = r.read_u16(); curves = read_f32_array(r, cc)
                dc = r.read_u16()
                deform_verts: List[List[float]] = []
                for _ in range(dc):
                    vc = r.read_u16()
                    deform_verts.append(read_f32_array(r, vc))
                att_name_off = r.read_u32()
                skin_idx = r.read_i16() if sk.scspVersion == ScspVersion.V3 else 0
                slot_name = sk.slots[slot_idx].name if 0 <= slot_idx < len(sk.slots) else None
                att_name = get_pool_string(att_name_off, sk)
                att = sk.skins[skin_idx].attachments.get(slot_name, {}).get(att_name) if 0 <= skin_idx < len(sk.skins) else None
                is_weighted = False
                setup_verts: List[float] = []
                if isinstance(att, VertexAttachment):
                    is_weighted = att.isWeighted
                    setup_verts = att.vertices
                offsets: List[List[float]] = []
                if not is_weighted and setup_verts:
                    sv = np.array(setup_verts)
                    for dv in deform_verts:
                        dv_arr = np.zeros(len(sv))
                        cnt = min(len(dv), len(sv))
                        dv_arr[:cnt] = dv[:cnt]
                        offsets.append((dv_arr - sv).tolist())
                else:
                    offsets = deform_verts
                t = DeformTimeline()
                t.times = frames; t.vertices = offsets
                t.skin = sk.skins[skin_idx].name if 0 <= skin_idx < len(sk.skins) else None
                t.attachment = att_name
                t.type = tl_type; t.slot_index = slot_idx
                t.frames = frames; t.curves = curves
                tl = t

            elif tl_type == TimelineType.Event:
                fc = r.read_u16(); frames = read_f32_array(r, fc)
                ec = r.read_u16(); ev_offs = read_u32_array(r, ec)
                t = EventTimeline()
                t.times = frames
                t.names = [get_pool_string(o, sk) for o in ev_offs]
                t.type = tl_type; t.frames = frames
                tl = t

            elif tl_type == TimelineType.DrawOrder:
                fc = r.read_u16(); frames = read_f32_array(r, fc)
                oc = r.read_u16()
                orders: List[List[int]] = []
                for _ in range(oc):
                    sc = r.read_u16()
                    orders.append(read_i32_array(r, sc))
                t = DrawOrderTimeline()
                t.times = frames; t.orders = orders; t.frames = frames
                tl = t

            timelines.append(tl)
        anim.timelines = timelines
        animations.append(anim)
    sk.animations = animations

def read_scsp_v3(r: SpineBinaryReader, sk: SkeletonData) -> None:
    read_bones_v3(r, sk)
    read_iks_v3(r, sk)
    read_slots_v3(r, sk)
    read_transform_constraints_v3(r, sk)
    read_path_constraints_v3(r, sk)
    read_skins_v3(r, sk)
    read_events_v3(r, sk)
    read_animations_v3(r, sk)


# ==============================
# V3 JSON Writer helpers
# ==============================
def write_curve_v3(curves: List[float], frame_index: int) -> Dict[str, Any]:
    item: Dict[str, Any] = {}
    ci = frame_index * 19
    ct = int(curves[ci])
    if ct == CurveType.LINEAR:
        return item
    elif ct == CurveType.STEPPED:
        item["curve"] = "stepped"
    elif ct == CurveType.BEZIER:
        item["curve"] = curves[ci + 1]
        if curves[ci + 2] != 0.0:
            item["c2"] = curves[ci + 2]
        if curves[ci + 3] != 1.0:
            item["c3"] = curves[ci + 3]
        if curves[ci + 4] != 1.0:
            item["c4"] = curves[ci + 4]
    return item

def write_timeline_data_v3(tl: TimelineData, sk: SkeletonData) -> List[Dict[str, Any]]:
    arr: List[Dict[str, Any]] = []
    fc = len(tl.times)
    match tl:
        case RotateTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                item["angle"] = t.angles[i]
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case TranslateTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.xs[i] != 0.0: item["x"] = t.xs[i]
                if t.ys[i] != 0.0: item["y"] = t.ys[i]
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case ScaleTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.xs[i] != 1.0: item["x"] = t.xs[i]
                if t.ys[i] != 1.0: item["y"] = t.ys[i]
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case ShearTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.xs[i] != 0.0: item["x"] = t.xs[i]
                if t.ys[i] != 0.0: item["y"] = t.ys[i]
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case AttachmentTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                item["name"] = t.names[i] if i < len(t.names) and t.names[i] else None
                arr.append(item)
        case ColorTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.colors[i]: item["color"] = color_to_string(t.colors[i], True)
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case DeformTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.offsets and i < len(t.offsets): item["offset"] = t.offsets[i]
                if t.vertices and i < len(t.vertices): item["vertices"] = t.vertices[i]
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case EventTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                item["name"] = t.names[i] if i < len(t.names) and t.names[i] else None
                arr.append(item)
        case DrawOrderTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if i < len(t.orders) and t.orders[i]:
                    order = t.orders[i]
                    pairs = [(si, ni) for ni, si in enumerate(order) if ni != si]
                    pairs.sort(key=lambda x: x[0])
                    offsets = [{"slot": sk.slots[si].name, "offset": ni - si} for si, ni in pairs]
                    if offsets: item["offsets"] = offsets
                arr.append(item)
        case IKTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.mixs and i < len(t.mixs) and t.mixs[i] != 1.0: item["mix"] = t.mixs[i]
                if t.softness and i < len(t.softness) and t.softness[i] != 0.0: item["softness"] = t.softness[i]
                if t.bend_directions and i < len(t.bend_directions): item["bendPositive"] = t.bend_directions[i] >= 0
                if t.compresses and i < len(t.compresses) and t.compresses[i]: item["compress"] = True
                if t.stretches and i < len(t.stretches) and t.stretches[i]: item["stretch"] = True
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case TransformTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.rotateMixs and i < len(t.rotateMixs) and t.rotateMixs[i] != 1.0: item["rotateMix"] = t.rotateMixs[i]
                if t.translateMixs and i < len(t.translateMixs) and t.translateMixs[i] != 1.0: item["translateMix"] = t.translateMixs[i]
                if t.scaleMixs and i < len(t.scaleMixs) and t.scaleMixs[i] != 1.0: item["scaleMix"] = t.scaleMixs[i]
                if t.shearMixs and i < len(t.shearMixs) and t.shearMixs[i] != 1.0: item["shearMix"] = t.shearMixs[i]
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case PathPositionTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.positions and i < len(t.positions) and t.positions[i] != 0.0: item["position"] = t.positions[i]
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case PathSpacingTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.spacings and i < len(t.spacings) and t.spacings[i] != 0.0: item["spacing"] = t.spacings[i]
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case PathMixTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.rotateMixs and i < len(t.rotateMixs) and t.rotateMixs[i] != 1.0: item["rotateMix"] = t.rotateMixs[i]
                if t.translateMixs and i < len(t.translateMixs) and t.translateMixs[i] != 1.0: item["translateMix"] = t.translateMixs[i]
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
        case TwoColorTimeline() as t:
            for i in range(fc):
                item: Dict[str, Any] = {}
                if t.times[i] != 0.0: item["time"] = t.times[i]
                if t.colorLights[i]: item["light"] = color_to_string(t.colorLights[i], True)
                if t.colorDarks[i]: item["dark"] = color_to_string(t.colorDarks[i], False)
                if t.curves and i < fc - 1: item.update(write_curve_v3(t.curves, i))
                arr.append(item)
    return arr

def build_animation_json_v3(anim: AnimationData, sk: SkeletonData) -> None:
    for tl in anim.timelines:
        obj = write_timeline_data_v3(tl, sk)
        if not obj:
            continue
        match tl:
            case RotateTimeline() | TranslateTimeline() | ScaleTimeline() | ShearTimeline() as t:
                bn = sk.bones[t.bone_index].name if 0 <= t.bone_index < len(sk.bones) else ""
                type_key = {TimelineType.Rotate: "rotate", TimelineType.Translate: "translate",
                            TimelineType.Scale: "scale", TimelineType.Shear: "shear"}[t.type]
                anim.bones.setdefault(bn, {})[type_key] = obj
            case AttachmentTimeline() | ColorTimeline() | TwoColorTimeline() as t:
                sn = sk.slots[t.slot_index].name if 0 <= t.slot_index < len(sk.slots) else ""
                type_key = {TimelineType.Attachment: "attachment", TimelineType.Color: "color",
                            TimelineType.TwoColor: "twoColor"}[t.type]
                anim.slots.setdefault(sn, {})[type_key] = obj
            case DeformTimeline() as t:
                skin_name = t.skin or "default"
                sn = sk.slots[t.slot_index].name if 0 <= t.slot_index < len(sk.slots) else ""
                anim.deform.setdefault(skin_name, {}).setdefault(sn, {})[t.attachment] = obj
            case EventTimeline():
                anim.events = obj
            case DrawOrderTimeline():
                anim.drawOrder = obj
            case IKTimeline() as t:
                ik_name = sk.ikConstraints[t.ik_index].name if 0 <= t.ik_index < len(sk.ikConstraints) else ""
                anim.ik[ik_name] = obj
            case TransformTimeline() as t:
                tn = sk.transformConstraints[t.transform_index].name if 0 <= t.transform_index < len(sk.transformConstraints) else ""
                anim.transform[tn] = obj
            case PathPositionTimeline() | PathSpacingTimeline() | PathMixTimeline() as t:
                pn = sk.pathConstraints[t.path_index].name if 0 <= t.path_index < len(sk.pathConstraints) else ""
                type_key = {TimelineType.PathConstraintPosition: "position",
                            TimelineType.PathConstraintSpacing: "spacing",
                            TimelineType.PathConstraintMix: "mix"}[t.type]
                anim.path.setdefault(pn, {})[type_key] = obj
