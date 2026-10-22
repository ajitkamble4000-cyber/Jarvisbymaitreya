"""Generate the PWA icons for the JARVIS web app with the Python standard library only.

The art is placeholder: a square in the dark theme accent colour (#22d3ee) with a centred letter J
drawn from rectangles in the on-accent colour (#04171d). Replace the output with brand artwork
before a public release.

Usage (from the repository root):

    python3 scripts/gen-icons.py                 # writes apps/web/public/icons/
    python3 scripts/gen-icons.py --out some/dir  # writes to another directory

Outputs:

    apple-touch-icon.png  180 x 180
    icon-192.png          192 x 192
    icon-512.png          512 x 512
"""

from __future__ import annotations

import argparse
import logging
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("jarvis.web")

ACCENT_RGB = (0x22, 0xD3, 0xEE)  # dark theme --accent
GLYPH_RGB = (0x04, 0x17, 0x1D)  # dark theme --on-accent

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "apps" / "web" / "public" / "icons"

ICONS: tuple[tuple[str, int], ...] = (
    ("apple-touch-icon.png", 180),
    ("icon-192.png", 192),
    ("icon-512.png", 512),
)


@dataclass(frozen=True)
class Box:
    """A rectangle in unit coordinates (0.0 to 1.0), origin at the top left."""

    x0: float
    y0: float
    x1: float
    y1: float


# A J made of four rectangles: the top serif, the stem, the hook and its upturned end.
GLYPH: tuple[Box, ...] = (
    Box(0.46, 0.22, 0.68, 0.31),
    Box(0.54, 0.22, 0.68, 0.70),
    Box(0.32, 0.64, 0.68, 0.78),
    Box(0.32, 0.56, 0.44, 0.78),
)


def _chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def encode_png(width: int, height: int, rows: list[bytes]) -> bytes:
    """Encodes 8-bit RGB rows (width * 3 bytes each) as a PNG file."""
    if len(rows) != height or any(len(row) != width * 3 for row in rows):
        raise ValueError("rows must match width and height")
    raw = b"".join(b"\x00" + row for row in rows)  # filter type 0 (none) on every scanline
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit, colour type 2 (RGB)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")


def render_rows(size: int) -> list[bytes]:
    """Builds the pixel rows of the icon. Each glyph rectangle is snapped to whole pixels."""
    pixel_boxes = [
        (round(box.x0 * size), round(box.y0 * size), round(box.x1 * size), round(box.y1 * size)) for box in GLYPH
    ]
    background = bytes(ACCENT_RGB) * size
    rows: list[bytes] = []
    for y in range(size):
        row = bytearray(background)
        for x0, y0, x1, y1 in pixel_boxes:
            if y0 <= y < y1:
                row[x0 * 3 : x1 * 3] = bytes(GLYPH_RGB) * (x1 - x0)
        rows.append(bytes(row))
    return rows


def write_icons(out_dir: Path) -> list[tuple[Path, int]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[Path, int]] = []
    for name, size in ICONS:
        data = encode_png(size, size, render_rows(size))
        path = out_dir / name
        path.write_bytes(data)
        written.append((path, len(data)))
        logger.info("Wrote %s (%dx%d, %d bytes)", path, size, size, len(data))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate placeholder PWA icons for the JARVIS web app.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory (default: apps/web/public/icons)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    write_icons(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
