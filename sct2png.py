#!/usr/bin/env python3
"""
Epic Seven SCT to PNG converter.

Supports both SCT1 (old format) and SCT2 (new format with ASTC/ETC2 textures).
Based on EpicSevenAssetRipper-2.0's sct.py hook.
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

import lz4.block
import PIL.Image
from texture2ddecoder import decode_astc, decode_etc2a8


def _read_uint8(data: bytes, offset: int) -> tuple[int, int]:
    return data[offset], offset + 1

def _read_uint16(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from("<H", data, offset)[0], offset + 2

def _read_uint32(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from("<I", data, offset)[0], offset + 4


def decode_sct(data: bytes) -> PIL.Image.Image:
    """Decode raw SCT bytes into a PIL Image (RGBA)."""
    if len(data) < 4:
        raise ValueError("Data too short to be an SCT file")

    sign = data[0:3]
    is_sct2 = data[3:4] == b"\x32"

    if not is_sct2:
        off = 4
        byte_format, off = _read_uint8(data, off)
        width, off = _read_uint16(data, off)
        height, off = _read_uint16(data, off)
        uncompressed_size, off = _read_uint32(data, off)
        compressed_size, off = _read_uint32(data, off)
        compressed = data[off:off + compressed_size]
        pixel_data = lz4.block.decompress(compressed, uncompressed_size=uncompressed_size)

        match byte_format:
            case 2:
                return PIL.Image.frombytes("RGBA", (width, height), pixel_data, "raw", "RGBA")
            case 4:
                img = PIL.Image.frombytes("RGB", (width, height), pixel_data, "raw", "BGR;16", 0, 1)
                alpha = PIL.Image.frombytes("L", (width, height), pixel_data[-width * height:])
                img.putalpha(alpha)
                return img
            case 102:
                return PIL.Image.frombytes("L", (width, height), pixel_data)
            case _:
                raise ValueError(f"Unknown SCT1 pixel format: {byte_format}")
    else:
        off = 4
        data_len, off = _read_uint32(data, off)
        _, off = _read_uint32(data, off)
        data_offset, off = _read_uint32(data, off)
        block_size, off = _read_uint32(data, off)
        byte_format, off = _read_uint32(data, off)
        width, off = _read_uint16(data, off)
        height, off = _read_uint16(data, off)
        width2, off = _read_uint16(data, off)
        height2, off = _read_uint16(data, off)

        off = data_offset
        uncompressed_size, off = _read_uint32(data, off)
        compressed_size, off = _read_uint32(data, off)

        if compressed_size == data_len - 80:
            compressed = data[off:off + compressed_size]
            pixel_data = lz4.block.decompress(compressed, uncompressed_size=uncompressed_size)
        else:
            off = data_offset
            pixel_data = data[off:off + compressed_size]

        match byte_format:
            case 19:
                image_data = decode_etc2a8(pixel_data, width, height)
            case 40:
                image_data = decode_astc(pixel_data, width, height, 4, 4)
            case 44:
                image_data = decode_astc(pixel_data, width, height, 6, 6)
            case 47:
                image_data = decode_astc(pixel_data, width, height, 8, 8)
            case _:
                raise ValueError(f"Unknown SCT2 texture format: {byte_format}")

        return PIL.Image.frombytes("RGBA", (width, height), image_data, "raw", "BGRA")


def convert_sct_to_png(input_path: str, output_path: str) -> bool:
    """Convert a .sct file to .png. Returns True on success."""
    with open(input_path, "rb") as f:
        data = f.read()

    image = decode_sct(data)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")
    print(f"[OK] {input_path} -> {output_path}")
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python sct2png.py <input.sct> [output.png]")
        sys.exit(1)
    inp = sys.argv[1]
    outp = sys.argv[2] if len(sys.argv) > 2 else str(Path(inp).with_suffix(".png"))
    convert_sct_to_png(inp, outp)
