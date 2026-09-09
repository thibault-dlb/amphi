"""Génère amphi/assets/amphi.ico — ICO multi-résolutions (16→256), badge rouge + onde.

Lancé par install.ps1. L'ICO multi-tailles évite le flou dans la barre des tâches.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "amphi" / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)

_RED = "#e5484d"
_WHITE = "#ffffff"
# onde sonore symétrique (pic au centre) ; 5 barres aux petites tailles, 7 sinon
_BARS_SMALL = (0.34, 0.62, 0.88, 0.62, 0.34)
_BARS_LARGE = (0.26, 0.44, 0.66, 0.90, 0.66, 0.44, 0.26)


def render(size: int) -> QImage:
    """Badge rouge arrondi + onde sonore blanche (lisible jusqu'à 16 px)."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    margin = size * (0.055 if size >= 48 else 0.03)
    radius = size * (0.24 if size >= 48 else 0.17)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(_RED))
    p.drawRoundedRect(
        QRectF(margin, margin, size - 2 * margin, size - 2 * margin), radius, radius
    )

    bars = _BARS_SMALL if size <= 32 else _BARS_LARGE
    span = size * (0.60 if size >= 48 else 0.66)
    gap_ratio = 0.9
    bw = span / (len(bars) + (len(bars) - 1) * gap_ratio)
    step = bw * (1 + gap_ratio)
    x = (size - span) / 2
    p.setBrush(QColor(_WHITE))
    for h in bars:
        bh = max(bw, size * h)
        p.drawRoundedRect(QRectF(x, (size - bh) / 2, bw, bh), bw / 2, bw / 2)
        x += step
    p.end()
    return img


def _png(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def build_ico(path: Path) -> None:
    frames = [(sz, _png(render(sz))) for sz in SIZES]
    count = len(frames)
    out = struct.pack("<HHH", 0, 1, count)  # ICONDIR
    offset = 6 + 16 * count
    body = b""
    for sz, data in frames:
        dim = 0 if sz >= 256 else sz  # 0 == 256 dans le format ICO
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        body += data
        offset += len(data)
    path.write_bytes(out + body)


def main() -> int:
    QGuiApplication(sys.argv)
    ASSETS.mkdir(parents=True, exist_ok=True)
    ico = ASSETS / "amphi.ico"
    build_ico(ico)
    render(256).save(str(ASSETS / "amphi.png"), "PNG")
    kb = ico.stat().st_size / 1024
    print(f"écrit {ico}  ({kb:.0f} Ko, {len(SIZES)} tailles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
