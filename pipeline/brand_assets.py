"""Build the site's icons and link-preview image from the logo's own typeface.

Writes into web/public/:
  favicon.svg           the tab icon: a chalk "A" (Unbounded Bold, traced to a path so it needs no
                        web font) over the red / yellow / green bar, on the green chalkboard
  favicon-32.png, apple-touch-icon.png (180), icon-192.png, icon-512.png   the same icon as PNGs
  og.png                1200x630 preview shown when a link is shared in chats and social apps

Needs the fonts in data/raw/fonts/ (Unbounded[wght].ttf and AtkinsonHyperlegible-Regular.ttf from
github.com/google/fonts, both SIL Open Font License) plus fonttools and Pillow.
Usage: python pipeline/brand_assets.py
"""
import io
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "data" / "raw" / "fonts"
OUT = ROOT / "web" / "public"

BOARD = "#26352e"  # the green chalkboard
CHALK = "#f1f0e6"
MUTED = "#a9b8ad"
BAR = ["#c4776b", "#c9b062", "#6f9f7b"]  # the logo bar's red, yellow, green on dark boards


def bold_unbounded():
    """Unbounded at weight 700 as a static font, cached next to the variable one."""
    path = FONTS / "Unbounded-700.ttf"
    if not path.exists():
        instantiateVariableFont(TTFont(FONTS / "Unbounded.ttf"), {"wght": 700}).save(path)
    return path


def a_path(font_path):
    """The "A" glyph as SVG path data plus its bounds, in font units (y points up)."""
    font = TTFont(font_path)
    glyphs = font.getGlyphSet()
    name = font.getBestCmap()[ord("A")]
    pen, bounds = SVGPathPen(glyphs), BoundsPen(glyphs)
    glyphs[name].draw(pen)
    glyphs[name].draw(bounds)
    return pen.getCommands(), bounds.bounds


def favicon_svg(font_path):
    d, (x0, y0, x1, y1) = a_path(font_path)
    # Fit the letter's cap height to 13.5 of the 32 units, centered, sitting on y = 21.5.
    scale = 13.5 / (y1 - y0)
    tx = 16 - (x0 + x1) / 2 * scale
    ty = 21.5 + y0 * scale
    bars = "".join(
        f'<rect x="{7 + i * 6.3:.1f}" y="24.6" width="5.4" height="2.4" rx="1.2" fill="{c}"/>' for i, c in enumerate(BAR)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        f'<rect width="32" height="32" rx="7" fill="{BOARD}"/>'
        f'<path d="{d}" fill="{CHALK}" transform="translate({tx:.3f} {ty:.3f}) scale({scale:.5f} {-scale:.5f})"/>'
        f"{bars}</svg>\n"
    )


def favicon_png(font_path, size):
    # Drawn at 4x and scaled down so the small sizes stay smooth.
    k = 4
    s = size * k
    u = s / 32  # one unit of the 32-unit icon grid
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, s - 1, s - 1], radius=7 * u, fill=BOARD)
    font = ImageFont.truetype(str(font_path), size=int(19 * u))
    box = draw.textbbox((0, 0), "A", font=font)
    height = box[3] - box[1]
    font = ImageFont.truetype(str(font_path), size=int(19 * u * 13.5 * u / height))
    box = draw.textbbox((0, 0), "A", font=font)
    draw.text((16 * u - (box[0] + box[2]) / 2, 21.5 * u - box[3]), "A", font=font, fill=CHALK)
    for i, c in enumerate(BAR):
        x = (7 + i * 6.3) * u
        draw.rounded_rectangle([x, 24.6 * u, x + 5.4 * u, 27 * u], radius=1.2 * u, fill=c)
    return img.resize((size, size), Image.LANCZOS)


def og_image(font_path):
    """The link preview: the full wordmark (spaced caps, bar, ripple) and a one-line pitch."""
    k = 2
    W, H = 1200 * k, 630 * k
    img = Image.new("RGB", (W, H), BOARD)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    word, size = "ALLUDO", 112 * k
    font = ImageFont.truetype(str(font_path), size=size)
    spacing = 0.26 * size
    widths = [draw.textlength(ch, font=font) for ch in word]
    total = sum(widths) + spacing * (len(word) - 1)
    x, baseline = (W - total) / 2, 300 * k
    ascent = font.getmetrics()[0]
    o_box = None
    for ch, w in zip(word, widths):
        draw.text((x, baseline - ascent), ch, font=font, fill=CHALK)
        if ch == "O":
            o_box = draw.textbbox((x, baseline - ascent), ch, font=font)
        x += w + spacing
    # Ripple arcs over the final O, like the site's logo.
    cx, cy = (o_box[0] + o_box[2]) / 2, (o_box[1] + o_box[3]) / 2
    r = (o_box[3] - o_box[1]) / 2
    for factor, width, alpha in [(1.65, 0.052, 205), (2.35, 0.042, 107)]:
        rr = r * factor
        draw.arc([cx - rr, cy - rr, cx + rr, cy + rr], start=205, end=335, fill=CHALK[:7] + f"{alpha:02x}", width=round(size * width))
    # The red / yellow / green bar under the word.
    left, right = (W - total) / 2, (W - total) / 2 + total
    gap, top, bar_h = 16 * k, baseline + 34 * k, 13 * k
    seg = (right - left - 2 * gap) / 3
    for i, c in enumerate(BAR):
        x0 = left + i * (seg + gap)
        draw.rounded_rectangle([x0, top, x0 + seg, top + bar_h], radius=bar_h / 2, fill=c)
    body = ImageFont.truetype(str(FONTS / "AtkinsonHyperlegible-Regular.ttf"), size=40 * k)
    small = ImageFont.truetype(str(FONTS / "AtkinsonHyperlegible-Regular.ttf"), size=30 * k)
    for text, f, y, color in [
        ("Find the secret word. Every guess tells you how close you are.", body, 455 * k, CHALK),
        ("A new word every day  ·  alludo.pages.dev", small, 525 * k, MUTED),
    ]:
        draw.text(((W - draw.textlength(text, font=f)) / 2, y), text, font=f, fill=color)
    img.paste(layer, (0, 0), layer)
    return img.resize((1200, 630), Image.LANCZOS)


def main():
    font_path = bold_unbounded()
    (OUT / "favicon.svg").write_text(favicon_svg(font_path), encoding="utf-8")
    for name, size in [("favicon-32.png", 32), ("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)]:
        favicon_png(font_path, size).save(OUT / name, optimize=True)
    buf = io.BytesIO()
    og_image(font_path).save(buf, "PNG", optimize=True)
    (OUT / "og.png").write_bytes(buf.getvalue())
    print("wrote favicon.svg, 4 PNG icons and og.png to", OUT)


if __name__ == "__main__":
    main()
