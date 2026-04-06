#!/usr/bin/env python3
"""
Epic Seven SCSP to JSON converter — supports both V2 (2.1.27) and V3 (3.8.99).

Based on E7_Scsp2Json.py (V3) and epic7_scsp2json_v1_0 (V2 reference).
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple, Set

import lz4.block

ENDIAN = "<"

# ==============================
# Enums
# ==============================
class ScspVersion(IntEnum):
    V2 = 1       # 2.1.27
    V3 = 30001   # 3.8.99

class Inherit(IntEnum):
    Normal = 0
    OnlyTranslation = 1
    NoRotationOrReflection = 2
    NoScale = 3
    NoScaleOrReflection = 4

class BlendMode(IntEnum):
    Normal = 0
    Additive = 1
    Multiply = 2
    Screen = 3

class PositionMode(IntEnum):
    Fixed = 0
    Percent = 1

class SpacingMode(IntEnum):
    Length = 0
    Fixed = 1
    Percent = 2
    Proportional = 3

class RotateMode(IntEnum):
    Tangent = 0
    Chain = 1
    ChainScale = 2

class CurveType(IntEnum):
    LINEAR = 0
    STEPPED = 1
    BEZIER = 2

class AttachmentType(IntEnum):
    Region = 0
    Boundingbox = 1
    Mesh = 2
    Linkedmesh = 3
    Path = 4
    Point = 5
    Clipping = 6

class V2AttachmentType(IntEnum):
    Region = 0
    BoundingBox = 1
    Mesh = 2
    SkinnedMesh = 3

class V2TimelineType(IntEnum):
    Scale = 0
    Rotate = 1
    Translate = 2
    Color = 3
    Attachment = 4
    FlipX = 5
    FlipY = 6
    FFD = 7
    IkConstraint = 8

class TimelineType(IntEnum):
    Rotate = 0
    Translate = 1
    Scale = 2
    Shear = 3
    Attachment = 4
    Color = 5
    TwoColor = 14
    Deform = 6
    Event = 7
    DrawOrder = 8
    IkConstraint = 9
    TransformConstraint = 10
    PathConstraintPosition = 11
    PathConstraintSpacing = 12
    PathConstraintMix = 13

# ==============================
# Data Classes
# ==============================
@dataclass
class Color:
    r: int = 0xFF
    g: int = 0xFF
    b: int = 0xFF
    a: int = 0xFF

def color_to_string(color: Color, has_alpha: bool = True) -> str:
    if has_alpha:
        return f"{color.r:02X}{color.g:02X}{color.b:02X}{color.a:02X}"
    return f"{color.r:02X}{color.g:02X}{color.b:02X}"

def f32_color(r: float, g: float, b: float, a: float) -> Color:
    return Color(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
        max(0, min(255, round(a * 255)))
    )

def uint64_to_base64(v: int) -> str:
    return base64.b64encode(struct.pack("<Q", v)).decode("ascii").rstrip("=")

# ---- Attachment data classes ----
@dataclass
class Attachment:
    name: Optional[str] = None
    path: Optional[str] = None
    type: AttachmentType = AttachmentType.Region

@dataclass
class VertexAttachment(Attachment):
    vertexCount: int = 0
    isWeighted: bool = False
    vertices: List[float] = field(default_factory=list)

@dataclass
class RegionAttachment(Attachment):
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scaleX: float = 1.0
    scaleY: float = 1.0
    width: float = 0.0
    height: float = 0.0
    color: Optional[Color] = None

@dataclass
class MeshAttachment(VertexAttachment):
    uvs: List[float] = field(default_factory=list)
    triangles: List[int] = field(default_factory=list)
    hullLength: int = 0
    color: Optional[Color] = None
    edges: List[int] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    path: Optional[str] = None

@dataclass
class LinkedMeshAttachment(VertexAttachment):
    skinIndex: int = 0
    parentMesh: Optional[str] = None
    deform: bool = True
    width: float = 0.0
    height: float = 0.0
    color: Optional[Color] = None

@dataclass
class BoundingBoxAttachment(VertexAttachment):
    color: Optional[Color] = None

@dataclass
class PathAttachment(VertexAttachment):
    color: Optional[Color] = None
    closed: bool = False
    constantSpeed: bool = False
    lengths: List[float] = field(default_factory=list)

@dataclass
class PointAttachment(Attachment):
    rotation: float = 0.0
    x: float = 0.0
    y: float = 0.0
    color: Optional[Color] = None

@dataclass
class ClippingAttachment(VertexAttachment):
    color: Optional[Color] = None
    endSlot: Optional[str] = None

# V2 skinnedmesh: stored as weighted mesh vertices directly
@dataclass
class SkinnedMeshAttachment(Attachment):
    bones: List[int] = field(default_factory=list)
    weights: List[float] = field(default_factory=list)
    triangles: List[int] = field(default_factory=list)
    uvs: List[float] = field(default_factory=list)
    hullLength: int = 0
    color: Optional[Color] = None
    width: float = 0.0
    height: float = 0.0

# ---- Bone/Slot/Constraint/Skin/Event/Animation ----
@dataclass
class BoneData:
    name: Optional[str] = None
    parent: Optional[str] = None
    rotation: float = 0.0
    x: float = 0.0
    y: float = 0.0
    scaleX: float = 1.0
    scaleY: float = 1.0
    shearX: float = 0.0
    shearY: float = 0.0
    length: float = 0.0
    inherit: Inherit = Inherit.Normal
    skinRequired: bool = False
    color: Optional[Color] = None
    # V2 fields
    flipX: bool = False
    flipY: bool = False
    inheritScale: bool = True
    inheritRotation: bool = True

@dataclass
class SlotData:
    name: Optional[str] = None
    bone: Optional[str] = None
    color: Optional[Color] = None
    darkColor: Optional[Color] = None
    attachmentName: Optional[str] = None
    blendMode: BlendMode = BlendMode.Normal

@dataclass
class IKConstraintData:
    name: Optional[str] = None
    order: int = 0
    skinRequired: bool = False
    bones: List[str] = field(default_factory=list)
    target: Optional[str] = None
    mix: float = 1.0
    softness: float = 0.0
    bendPositive: bool = True
    compress: bool = False
    stretch: bool = False
    uniform: bool = False

@dataclass
class TransformConstraintData:
    name: Optional[str] = None
    order: int = 0
    skinRequired: bool = False
    bones: List[str] = field(default_factory=list)
    target: Optional[str] = None
    local: bool = False
    relative: bool = False
    offsetRotation: float = 0.0
    offsetX: float = 0.0
    offsetY: float = 0.0
    offsetScaleX: float = 0.0
    offsetScaleY: float = 0.0
    offsetShearY: float = 0.0
    rotateMix: float = 1.0
    translateMix: float = 1.0
    scaleMix: float = 1.0
    shearMix: float = 1.0

@dataclass
class PathConstraintData:
    name: Optional[str] = None
    order: int = 0
    skinRequired: bool = False
    bones: List[str] = field(default_factory=list)
    targetSlot: Optional[str] = None
    positionMode: PositionMode = PositionMode.Percent
    spacingMode: SpacingMode = SpacingMode.Length
    rotateMode: RotateMode = RotateMode.Tangent
    offsetRotation: float = 0.0
    position: float = 0.0
    spacing: float = 0.0
    rotateMix: float = 1.0
    translateMix: float = 1.0

@dataclass
class SkinData:
    name: Optional[str] = None
    attachments: Dict[str, Dict[str, Attachment]] = field(default_factory=dict)
    bones: List[str] = field(default_factory=list)
    ik: List[str] = field(default_factory=list)
    transform: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)

@dataclass
class EventData:
    name: Optional[str] = None
    intValue: int = 0
    floatValue: float = 0.0
    stringValue: Optional[str] = None
    audioPath: Optional[str] = None
    volume: float = 1.0
    balance: float = 0.0

# ---- Timeline data classes ----
@dataclass
class TimelineData:
    type: TimelineType = TimelineType.Rotate
    frames: List[float] = field(default_factory=list)
    curves: List[float] = field(default_factory=list)
    times: List[float] = field(default_factory=list)

@dataclass
class RotateTimeline(TimelineData):
    bone_index: int = 0
    angles: List[float] = field(default_factory=list)
@dataclass
class TranslateTimeline(TimelineData):
    bone_index: int = 0
    xs: List[float] = field(default_factory=list)
    ys: List[float] = field(default_factory=list)
@dataclass
class ScaleTimeline(TimelineData):
    bone_index: int = 0
    xs: List[float] = field(default_factory=list)
    ys: List[float] = field(default_factory=list)
@dataclass
class ShearTimeline(TimelineData):
    bone_index: int = 0
    xs: List[float] = field(default_factory=list)
    ys: List[float] = field(default_factory=list)
@dataclass
class AttachmentTimeline(TimelineData):
    slot_index: int = 0
    names: List[Optional[str]] = field(default_factory=list)
@dataclass
class ColorTimeline(TimelineData):
    slot_index: int = 0
    colors: List[Color] = field(default_factory=list)
@dataclass
class DeformTimeline(TimelineData):
    skin: Optional[str] = None
    slot_index: int = 0
    attachment: Optional[str] = None
    offsets: List[List[float]] = field(default_factory=list)
    vertices: List[List[float]] = field(default_factory=list)
@dataclass
class EventTimeline(TimelineData):
    names: List[str] = field(default_factory=list)
@dataclass
class DrawOrderTimeline(TimelineData):
    orders: List[List[int]] = field(default_factory=list)
@dataclass
class IKTimeline(TimelineData):
    ik_index: int = 0
    mixs: List[float] = field(default_factory=list)
    softness: List[float] = field(default_factory=list)
    bend_directions: List[int] = field(default_factory=list)
    compresses: List[bool] = field(default_factory=list)
    stretches: List[bool] = field(default_factory=list)
@dataclass
class TransformTimeline(TimelineData):
    transform_index: int = 0
    rotateMixs: List[float] = field(default_factory=list)
    translateMixs: List[float] = field(default_factory=list)
    scaleMixs: List[float] = field(default_factory=list)
    shearMixs: List[float] = field(default_factory=list)
@dataclass
class PathPositionTimeline(TimelineData):
    path_index: int = 0
    positions: List[float] = field(default_factory=list)
@dataclass
class PathSpacingTimeline(TimelineData):
    path_index: int = 0
    spacings: List[float] = field(default_factory=list)
@dataclass
class PathMixTimeline(TimelineData):
    path_index: int = 0
    rotateMixs: List[float] = field(default_factory=list)
    translateMixs: List[float] = field(default_factory=list)
@dataclass
class TwoColorTimeline(TimelineData):
    slot_index: int = 0
    colorLights: List[Color] = field(default_factory=list)
    colorDarks: List[Color] = field(default_factory=list)

# V2 FFD timeline
@dataclass
class FFDTimeline(TimelineData):
    skin_name: Optional[str] = None
    slot_name: Optional[str] = None
    attachment_name: Optional[str] = None
    vertices: List[List[float]] = field(default_factory=list)

@dataclass
class AnimationData:
    name: Optional[str] = None
    duration: float = 0.0
    timelines: List[TimelineData] = field(default_factory=list)
    slots: Dict[str, Dict[str, List[Any]]] = field(default_factory=dict)
    bones: Dict[str, Dict[str, List[Any]]] = field(default_factory=dict)
    ik: Dict[str, List[Any]] = field(default_factory=dict)
    transform: Dict[str, List[Any]] = field(default_factory=dict)
    path: Dict[str, Dict[str, List[Any]]] = field(default_factory=dict)
    deform: Dict[str, Dict[str, Dict[str, List[Any]]]] = field(default_factory=dict)
    ffd: Dict[str, Dict[str, Dict[str, List[Any]]]] = field(default_factory=dict)
    drawOrder: List[Any] = field(default_factory=list)
    events: List[Any] = field(default_factory=list)

@dataclass
class SkeletonData:
    scspVersion: ScspVersion = ScspVersion.V3
    stringPool: bytes = b""
    hash: int = 0
    hashString: Optional[str] = None
    version: Optional[str] = None
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    nonessential: bool = True
    fps: float = 30.0
    imagesPath: Optional[str] = None
    audioPath: Optional[str] = None
    strings: List[str] = field(default_factory=list)
    bones: List[BoneData] = field(default_factory=list)
    slots: List[SlotData] = field(default_factory=list)
    ikConstraints: List[IKConstraintData] = field(default_factory=list)
    transformConstraints: List[TransformConstraintData] = field(default_factory=list)
    pathConstraints: List[PathConstraintData] = field(default_factory=list)
    skins: List[SkinData] = field(default_factory=list)
    events: List[EventData] = field(default_factory=list)
    animations: List[AnimationData] = field(default_factory=list)
    # V2 specific
    v2_bone_count: int = 0
    v2_ik_count: int = 0
    v2_slot_count: int = 0
    v2_skin_count: int = 0
    v2_event_count: int = 0
    v2_anim_count: int = 0
    v2_skin_records: List[Dict] = field(default_factory=list)


# ==============================
# Binary Reader
# ==============================
class SpineBinaryReader:
    def __init__(self, data: bytes, endian: str = ENDIAN):
        self.data = data
        self.pos = 0
        self.endian = endian

    def _read(self, size: int) -> bytes:
        if self.pos + size > len(self.data):
            raise EOFError(f"Unexpected end of data at {self.pos}, need {size} more bytes, have {len(self.data) - self.pos}")
        chunk = self.data[self.pos:self.pos + size]
        self.pos += size
        return chunk

    def reset_data(self, data: bytes, offset: int = 0) -> None:
        self.data = data
        self.pos = offset

    def reset_pos(self, pos: int = 0) -> None:
        self.pos = pos

    def read_byte(self) -> int:
        return self._read(1)[0]

    def read_bytes(self, n: int) -> bytes:
        return self._read(n)

    def read_sbyte(self) -> int:
        return struct.unpack(f"{self.endian}b", self._read(1))[0]

    def read_boolean(self) -> bool:
        return self.read_byte() != 0

    def read_u8(self) -> int:
        return struct.unpack(f"{self.endian}B", self._read(1))[0]

    def read_i16(self) -> int:
        return struct.unpack(f"{self.endian}h", self._read(2))[0]

    def read_u16(self) -> int:
        return struct.unpack(f"{self.endian}H", self._read(2))[0]

    def read_i32(self) -> int:
        return struct.unpack(f"{self.endian}i", self._read(4))[0]

    def read_u32(self) -> int:
        return struct.unpack(f"{self.endian}I", self._read(4))[0]

    def read_f32(self) -> float:
        return struct.unpack(f"{self.endian}f", self._read(4))[0]

    def read_color(self, has_alpha: bool = True) -> Color:
        r = self.read_byte()
        g = self.read_byte()
        b = self.read_byte()
        a = self.read_byte() if has_alpha else 0xFF
        return Color(r, g, b, a)

    def read_varint(self, optimize_positive: bool) -> int:
        result = 0
        shift = 0
        while True:
            b = self.read_byte()
            result |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                break
            shift += 7
        if not optimize_positive:
            result = (result >> 1) ^ -(result & 1)
        return result

    def read_string(self, strings: Optional[List[str]] = None) -> Optional[str]:
        length = self.read_varint(True)
        if length == 0:
            return None
        if length == 1:
            return ""
        length -= 1
        raw = self._read(length)
        s = raw.decode("utf-8", errors="replace")
        if strings is not None:
            strings.append(s)
        return s

    def read_string_ref(self, strings: List[str]) -> Optional[str]:
        index = self.read_varint(True)
        if index == 0:
            return None
        index -= 1
        if index >= len(strings):
            strings.append(self.read_string())
        return strings[index]

    def skip(self, n: int) -> None:
        self.pos += n


# ==============================
# Helpers
# ==============================
def get_pool_string(offset: int, sk: SkeletonData) -> Optional[str]:
    string_pool = sk.stringPool
    if offset == 0xFFFFFFFF:
        return None
    if offset >= len(string_pool):
        return f'<OOB:{offset:#x}>'
    try:
        end = string_pool.index(b'\x00', offset)
    except ValueError:
        end = len(string_pool)
    return string_pool[offset:end].decode('utf-8', errors='replace')

def read_f32_array(r: SpineBinaryReader, n: int) -> List[float]:
    return [r.read_f32() for _ in range(n)]

def read_u8_array(r: SpineBinaryReader, n: int) -> List[int]:
    return [r.read_u8() for _ in range(n)]

def read_i16_array(r: SpineBinaryReader, n: int) -> List[int]:
    return [r.read_i16() for _ in range(n)]

def read_u16_array(r: SpineBinaryReader, n: int) -> List[int]:
    return [r.read_u16() for _ in range(n)]

def read_i32_array(r: SpineBinaryReader, n: int) -> List[int]:
    return [r.read_i32() for _ in range(n)]

def read_u32_array(r: SpineBinaryReader, n: int) -> List[int]:
    return [r.read_u32() for _ in range(n)]

def can_merge_weighted_vertices(vertices: List[float], bones: List[int]) -> bool:
    bpos = 0
    required = 0
    while bpos < len(bones):
        bc = bones[bpos]; bpos += 1
        if bc <= 0 or bpos + bc > len(bones):
            return False
        bpos += bc
        required += bc * 3
    return required == len(vertices)

def merge_weighted_vertices(vertices: List[float], bones: List[int]) -> List[float]:
    if not can_merge_weighted_vertices(vertices, bones):
        raise ValueError("Cannot merge weighted vertices")
    merged: List[float] = []
    bpos = 0; vpos = 0
    while bpos < len(bones):
        bc = bones[bpos]; bpos += 1
        merged.append(float(bc))
        for _ in range(bc):
            bi = bones[bpos]; bpos += 1
            x, y, w = vertices[vpos], vertices[vpos + 1], vertices[vpos + 2]
            vpos += 3
            merged.extend([float(bi), x, y, w])
    return merged


# ==============================
# Decompression & preprocessing
# ==============================
def lz4_decompress(reader: SpineBinaryReader) -> None:
    uncompressed_size = reader.read_u32()
    compressed_size = reader.read_u32()
    compressed_data = reader.read_bytes(compressed_size)
    decompressed = lz4.block.decompress(compressed_data, uncompressed_size=uncompressed_size)
    reader.reset_data(decompressed)

def custom_data_preprocess(reader: SpineBinaryReader, skeleton: SkeletonData) -> None:
    lz4_decompress(reader)
    data_size = reader.read_u32()
    string_pool_size = reader.read_u32()
    data_start_pos = reader.pos

    magic = reader.read_bytes(4)

    if magic == b"scsp":
        version = reader.read_u32()
        reader.reset_pos(data_start_pos)
        spine_data = reader.read_bytes(data_size)
        string_pool = reader.read_bytes(string_pool_size)
        reader.reset_data(spine_data)
        skeleton.scspVersion = ScspVersion.V3 if version > 2 else ScspVersion.V2
    else:
        # V2: no magic header — data section starts directly with spine data
        reader.reset_pos(data_start_pos)
        spine_data = reader.read_bytes(data_size)
        string_pool = reader.read_bytes(string_pool_size)
        reader.reset_data(spine_data)
        skeleton.scspVersion = ScspVersion.V2

    skeleton.stringPool = string_pool


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
                    for dv in deform_verts:
                        fo: List[float] = []
                        cnt = min(len(dv), len(setup_verts))
                        for i in range(cnt):
                            fo.append(dv[i] - setup_verts[i])
                        if len(dv) < len(setup_verts):
                            fo.extend([0.0] * (len(setup_verts) - len(dv)))
                        offsets.append(fo)
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
        slot.name = get_pool_string(name_off, sk)
        slot.bone = sk.bones[bone_idx].name if 0 <= bone_idx < len(sk.bones) else None
        slot.color = f32_color(cr, cg, cb, ca)
        slot.attachmentName = get_pool_string(att_off, sk) if att_off < 0xFFFF else None
        slot.blendMode = BlendMode(blend) if blend <= 3 else BlendMode.Normal
        slots.append(slot)
    sk.slots = slots

def read_iks_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    iks: List[IKConstraintData] = []
    for _ in range(sk.v2_ik_count):
        ik = IKConstraintData()
        name_off = r.read_u32()
        bone_count = r.read_u16()
        bone_idxs = read_u16_array(r, bone_count)
        target_idx = r.read_u16()
        ik.mix = r.read_f32()
        bend = r.read_i32()
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
        skin.name = get_pool_string(skin_name_off, sk)
        attachments: Dict[str, Dict[str, Attachment]] = defaultdict(dict)

        for _ in range(part_count):
            att_name_off = r.read_u32()
            slot_idx = r.read_u32()
            data_type = r.read_u32()
            item_name_off = r.read_u32()
            item_path_off = r.read_u32()

            att_name = get_pool_string(att_name_off, sk)
            slot_name = sk.slots[slot_idx].name if 0 <= slot_idx < len(sk.slots) else None
            item_name = get_pool_string(item_name_off, sk)
            item_path = get_pool_string(item_path_off, sk)

            skin_records.append({
                'skin': skin.name,
                'skin_slot': slot_name,
                'skin_attachment': att_name
            })

            if data_type == V2AttachmentType.Region:
                att = RegionAttachment()
                att.x = r.read_f32()
                att.y = r.read_f32()
                att.scaleX = r.read_f32()
                att.scaleY = r.read_f32()
                att.rotation = r.read_f32()
                r.skip(8)
                att.color = f32_color(r.read_f32(), r.read_f32(), r.read_f32(), r.read_f32())
                r.skip(8)
                att.width = float(r.read_u32())
                att.height = float(r.read_u32())
                r.skip(72)
                att.name = item_name
                att.path = item_path
                att.type = AttachmentType.Region
                attachments[slot_name][att_name] = att

            elif data_type == V2AttachmentType.Mesh:
                att = MeshAttachment()
                vert_count = r.read_u32()
                verts = read_f32_array(r, vert_count)
                hull = r.read_u32()
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
                all_uvs: List[float] = []
                for _ in range(uv_cnt):
                    all_uvs.append(r.read_f32())
                    all_uvs.append(r.read_f32())
                uvs_region = all_uvs[:len(all_uvs) // 2]
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

def read_animations_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    r.skip(4)  # 4 unknown bytes before animations block
    animations: List[AnimationData] = []
    data_end = len(r.data)
    string_pool_start = data_end  # spine_data doesn't include string pool

    for _ in range(sk.v2_anim_count):
        anim = AnimationData()
        anim.name = get_pool_string(r.read_u32(), sk)
        r.skip(4)  # unknown
        elem_count = r.read_u32()
        anim.duration = 0.0

        for _ in range(elem_count):
            tl_type_v2 = r.read_u32()

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
                    name = get_pool_string(name_offs[fi], sk) if name_offs[fi] < 0xFFFF else None
                    entry["name"] = name if name else None
                    entries.append(entry)
                    anim.duration = max(anim.duration, times[fi])
                slot_name = sk.slots[slot_idx].name if 0 <= slot_idx < len(sk.slots) else ""
                anim.slots.setdefault(slot_name, {})["attachment"] = entries

            elif tl_type_v2 == V2TimelineType.FFD:
                frame_count = r.read_u32()
                times = read_f32_array(r, frame_count)
                r.skip(4)  # unknown
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

                # V2 FFD stores absolute vertex positions; convert to offsets
                setup_verts: List[float] = []
                for s in sk.skins:
                    if s.name == ffd_skin:
                        att = s.attachments.get(ffd_slot, {}).get(ffd_att)
                        if isinstance(att, VertexAttachment) and not att.isWeighted:
                            setup_verts = att.vertices
                        break

                entries = []
                for fi in range(frame_count):
                    entry: Dict[str, Any] = {}
                    if fi < len(curves) and curves[fi]:
                        entry.update(curves[fi])
                    entry["time"] = round(times[fi], 4)
                    fv = all_verts[fi]
                    if setup_verts and len(fv) == len(setup_verts):
                        offsets = [fv[i] - setup_verts[i] for i in range(len(fv))]
                    else:
                        offsets = fv
                    all_zero = all(v == 0 for v in offsets)
                    if not all_zero:
                        entry["vertices"] = [round(v, 8) for v in offsets]
                    entries.append(entry)
                anim.ffd.setdefault(ffd_skin, {}).setdefault(ffd_slot, {})[ffd_att] = entries

            elif tl_type_v2 == V2TimelineType.IkConstraint:
                # V2 IK constraint timeline - not commonly seen but handle it
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
                    entry["bendPositive"] = int(bend) >= 0
                    entries.append(entry)
                    anim.duration = max(anim.duration, t)
                curves = _read_v2_curves(r, frame_count)
                for ci, cv in enumerate(curves):
                    if cv:
                        entries[ci].update(cv)
                ik_name = sk.ikConstraints[ik_idx].name if 0 <= ik_idx < len(sk.ikConstraints) else ""
                anim.ik[ik_name] = entries

            elif tl_type_v2 in (V2TimelineType.FlipX, V2TimelineType.FlipY):
                # Skip flip timelines (not standard in Spine JSON export)
                bone_idx = r.read_u32()
                frame_count = r.read_u32()
                for _ in range(frame_count):
                    r.read_f32()  # time
                    r.read_u32()  # flip value
            else:
                raise ValueError(f"Unknown V2 timeline type: {tl_type_v2} at pos {r.pos}")

        animations.append(anim)
    sk.animations = animations

def read_scsp_v2(r: SpineBinaryReader, sk: SkeletonData) -> None:
    read_bones_v2(r, sk)
    read_iks_v2(r, sk)
    read_slots_v2(r, sk)
    read_skins_v2(r, sk)
    read_animations_v2(r, sk)


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
# JSON Writer
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


def _clean_float(v: float, force_float: bool = False) -> Any:
    """Round to int if close enough, otherwise keep float.
    If force_float is True, always return a Python float to ensure
    JSON output has a decimal point (required by some C# JSON parsers).
    """
    if force_float:
        return float(v)
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
        pass  # V2 typically has no events
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
        anims: Dict[str, Any] = {}
        for anim in sk.animations:
            if not is_v2:
                build_animation_json_v3(anim, sk)
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
            if anim.events: a_obj["events"] = anim.events
            anims[anim.name] = a_obj
        j["animations"] = anims

    return j


# ==============================
# V3 special post-processing
# ==============================
def special_process_v3(json_str: str) -> str:
    """No-op post-processor kept for interface compatibility.

    The original implementation incorrectly deleted expression-slot entries
    (e.g. normal5 / mouth) from the "normal" skin when all expression skins
    shared that slot.  The default skin already contains all base parts
    (body + head structure) and must not be modified; expression skins
    (normal, angry, …) overlay on top of it.  No post-processing is needed.
    """
    return json_str


# ==============================
# Converter
# ==============================
def convert_scsp_to_json(input_path: str, output_path: str, compress: bool = True) -> bool:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(input_path, "rb") as f:
        data = f.read()

    try:
        skeleton, success = read_binary_skeleton(data)
    except Exception as e:
        print(f"[ERROR] {input_path}: {e}")
        return False

    if not success:
        print(f"[SKIP] {input_path}: unsupported format")
        return False

    root = write_json_data(skeleton)
    json_str = json.dumps(root, ensure_ascii=False)

    if skeleton.scspVersion == ScspVersion.V3:
        json_str = special_process_v3(json_str)

    if compress:
        json_str = json.dumps(json.loads(json_str), separators=(",", ":"))

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
