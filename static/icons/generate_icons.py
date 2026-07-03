#!/usr/bin/env python3
"""Generate PWA icons for Gemini Tudástár."""
import struct
import zlib
from pathlib import Path


def create_png(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """Create a solid-color PNG using raw bytes (no external dependencies)."""
    def make_chunk(chunk_type: bytes, data: bytes) -> bytes:
        chunk = chunk_type + data
        return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)

    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"

    # IHDR chunk
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = make_chunk(b"IHDR", ihdr_data)

    # IDAT chunk — raw pixel data with filter byte 0 per row
    raw_rows = b""
    for y in range(height):
        raw_rows += b"\x00" + bytes([r, g, b]) * width
    compressed = zlib.compress(raw_rows)
    idat = make_chunk(b"IDAT", compressed)

    # IEND chunk
    iend = make_chunk(b"IEND", b"")

    return sig + ihdr + idat + iend


def create_gradient_png(width: int, height: int) -> bytes:
    """Create a PNG approximating the icon gradient using raw bytes."""
    def make_chunk(chunk_type, data):
        chunk = chunk_type + data
        return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = make_chunk(b"IHDR", ihdr_data)

    # Background color (#0f1117 = 15, 17, 23)
    bg_r, bg_g, bg_b = 15, 17, 23
    # Accent colors
    ac1_r, ac1_g, ac1_b = 108, 142, 255  # #6c8eff
    ac2_r, ac2_g, ac2_b = 167, 139, 250  # #a78bfa

    cx, cy = width // 2, height // 2
    raw_rows = b""
    for y in range(height):
        raw_rows += b"\x00"  # filter byte
        for x in range(width):
            # Distance from center, normalized 0..1
            dx = (x - cx) / cx
            dy = (y - cy) / cy
            dist = (dx * dx + dy * dy) ** 0.5
            # Crystal shape: draw a star/diamond
            star = abs(dx) + abs(dy)  # diamond shape

            if star < 0.7:
                # Inside crystal — blend accent colors
                t = (dx + 1) / 2  # horizontal gradient
                pr = int(ac1_r + (ac2_r - ac1_r) * t)
                pg = int(ac1_g + (ac2_g - ac1_g) * t)
                pb = int(ac1_b + (ac2_b - ac1_b) * t)
                # Add some brightness toward center
                brightness = max(0, 1 - star * 1.5)
                pr = min(255, int(pr + brightness * 80))
                pg = min(255, int(pg + brightness * 80))
                pb = min(255, int(pb + brightness * 80))
            elif star < 0.75:
                # Border glow
                t = (star - 0.7) / 0.05
                pr = int(bg_r + (ac2_r - bg_r) * (1 - t))
                pg = int(bg_g + (ac2_g - bg_g) * (1 - t))
                pb = int(bg_b + (ac2_b - bg_b) * (1 - t))
            else:
                pr, pg, pb = bg_r, bg_g, bg_b

            # Rounded corners
            corner_x = min(x, width - 1 - x) / (width * 0.12)
            corner_y = min(y, height - 1 - y) / (height * 0.12)
            corner = min(corner_x, corner_y, 1.0)
            if corner < 1.0:
                alpha = corner
                pr = int(pr * alpha)
                pg = int(pg * alpha)
                pb = int(pb * alpha)

            raw_rows += bytes([pr, pg, pb])

    compressed = zlib.compress(raw_rows)
    idat = make_chunk(b"IDAT", compressed)
    iend = make_chunk(b"IEND", b"")

    return sig + ihdr + idat + iend


if __name__ == "__main__":
    icons_dir = Path(__file__).parent

    # Try generating gradient PNG icons
    try:
        for size, name in [(192, "icon-192.png"), (512, "icon-512.png")]:
            data = create_gradient_png(size, size)
            path = icons_dir / name
            path.write_bytes(data)
            print(f"  [OK] {name} ({size}x{size})")
    except Exception as e:
        print(f"  [WARN] PNG gradient generation failed: {e}")
        print("  Falling back to solid-color icons...")
        for size, name in [(192, "icon-192.png"), (512, "icon-512.png")]:
            data = create_png(size, size, 15, 17, 23)
            path = icons_dir / name
            path.write_bytes(data)
            print(f"  [OK] {name} ({size}x{size}) - solid color fallback")
