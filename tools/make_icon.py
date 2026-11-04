"""Draws the RenameLedger icon: a ledger sheet whose middle row is a rename arrow.

Run from the repo root: py -m pip install pillow; py tools\\make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "renameledger" / "assets"

GREEN, DEEP, PAPER, FOLD, AMBER = "#1E6B57", "#164F41", "#FFFFFF", "#BFDCD2", "#F2A93B"

def draw(size):
    S = 1024
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((48, 48, 976, 976), radius=210, fill=GREEN)
    small = size <= 24
    # sheet with folded corner
    x0, y0, x1, y1, f = (250, 170, 774, 854, 170) if not small else (220, 140, 804, 884, 190)
    d.polygon([(x0, y0), (x1 - f, y0), (x1, y0 + f), (x1, y1), (x0, y1)], fill=PAPER)
    d.polygon([(x1 - f, y0), (x1 - f, y0 + f), (x1, y0 + f)], fill=FOLD)
    lw = 64 if not small else 110
    pad = 80 if not small else 70
    rows = [400, 540, 680] if not small else [420, 690]
    for i, y in enumerate(rows):
        if i == len(rows) // 2 and not small or (small and i == 1):
            # arrow row: amber shaft + head
            ax0, ax1 = x0 + pad - 10, x1 - pad + 20
            d.rounded_rectangle((ax0, y - lw // 2, ax1 - 110, y + lw // 2), radius=lw // 2, fill=AMBER)
            d.polygon([(ax1 - 170, y - 110), (ax1 + 10, y), (ax1 - 170, y + 110)], fill=AMBER)
        else:
            d.rounded_rectangle((x0 + pad, y - lw // 2, x1 - pad - (0 if i else 120), y + lw // 2),
                                radius=lw // 2, fill=DEEP)
    return im.resize((size, size), Image.LANCZOS)

sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
imgs = [draw(s) for s in sizes]
imgs[-1].save(OUT / "renameledger.ico", sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
draw(256).save(OUT / "renameledger.png")
