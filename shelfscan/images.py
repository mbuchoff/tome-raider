"""Image work with Pillow: loading phone photos, the shelf-finding overview, reading crops and the numbered result image."""
import io, math
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()          # iPhone photos
except ImportError:
    pass

OWN_MAX, PAD = 1800, 200        # widest part a crop owns, and the overlap on each side (px), as in the API test
RULER = 44                      # height of the percentage ruler added above each crop (px)


def load(path):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    return im.convert('RGB')


def jpeg(im, quality=88):
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=quality)
    return b.getvalue()


def overview(im, size=1568):
    o = im.copy()
    o.thumbnail((size, size))
    return jpeg(o, 85)


def tiles(im, bands):
    """Overlapping full-resolution crops of each shelf band, with red lines marking the part each crop owns."""
    W, H = im.size
    out = []
    for b in bands:
        if b['bottom_pct'] - b['top_pct'] < 6: continue      # a strip of book tops from the shelf below, not a readable row
        # the shelf finder's bands tend to stop short of the spine feet, where publishers' names are, so pad the bottom more
        y0 = max(0, int(H * (b['top_pct'] - 2) / 100)); y1 = min(H, int(H * (b['bottom_pct'] + 4) / 100))
        n = math.ceil(W / OWN_MAX)
        for i in range(n):
            o0, o1 = round(W * i / n), round(W * (i + 1) / n)
            x0, x1 = max(0, o0 - PAD), min(W, o1 + PAD)
            crop = with_ruler(im.crop((x0, y0, x1, y1)))
            d = ImageDraw.Draw(crop)
            lines = []
            if o0 > 0: d.line([(o0 - x0, RULER), (o0 - x0, RULER + y1 - y0)], fill='red', width=3); lines.append('left')
            if o1 < W: d.line([(o1 - x0, RULER), (o1 - x0, RULER + y1 - y0)], fill='red', width=3); lines.append('right')
            if lines == ['left', 'right']: txt = 'Two thin red vertical lines mark your region: the strip between them.'
            elif lines == ['left']: txt = 'A thin red vertical line marks the left edge of your region, which runs from that line to the right edge of the image.'
            elif lines == ['right']: txt = 'A thin red vertical line marks the right edge of your region, which runs from the left edge of the image to that line.'
            else: txt = 'Your region is the whole image.'
            out.append(dict(shelf=b['shelf'], idx=i, x0=x0, y0=y0, w=x1 - x0, h=y1 - y0, own=(o0, o1), lines=txt, jpeg=jpeg(crop, 90)))
    return out


def with_ruler(crop):
    """Adds a white band above the crop with ticks every 5% of its width, labelled every 10%, to help place spines."""
    w, h = crop.size
    out = Image.new('RGB', (w, h + RULER), 'white')
    out.paste(crop, (0, RULER))
    d = ImageDraw.Draw(out)
    f = _font(20)
    for p in range(0, 101, 5):
        x = min(w - 1, round(w * p / 100))
        d.line([(x, RULER - (14 if p % 10 == 0 else 7)), (x, RULER - 1)], fill='black', width=2)
        if p % 10 == 0 and 0 < p < 100:
            l, t, r, b = d.textbbox((0, 0), str(p), font=f)
            d.text((x - (r - l) / 2, 2 - t), str(p), fill='black', font=f)
    return out


def spine_sheets(im, books, height=700, max_width=1560, max_scale=4.0, max_sheets=3, rows=1):
    """Close-ups of the given spines, enlarged and numbered side by side in up to `rows` rows per sheet, a few sheets at most.
    Returns [(jpeg, [book index...])], the indexes in the order of the numbers on the sheet."""
    tiles = []
    for i, b in enumerate(books):
        pad = (b['x1'] - b['x0']) * 0.15 + 4
        c = im.crop((max(0, round(b['x0'] - pad)), b['y0'], min(im.size[0], round(b['x1'] + pad)), b['y1']))
        s = min(max_scale, height / max(1, c.size[1]))
        tiles.append((i, c.resize((max(1, round(c.size[0] * s)), max(1, round(c.size[1] * s))), Image.LANCZOS)))
    sheets, row = [], []
    for t in tiles:
        if row and sum(x.size[0] + 16 for _, x in row) + t[1].size[0] > max_width:
            sheets.append(row); row = []
        row.append(t)
    if row: sheets.append(row)
    sheets = [sheets[k:k + rows] for k in range(0, len(sheets), rows)][:max_sheets]
    out, f = [], _font(28)
    for group in sheets:
        w = max(sum(c.size[0] + 16 for _, c in row) + 16 for row in group)
        h = sum(max(c.size[1] for _, c in row) + 50 for row in group)
        sheet = Image.new('RGB', (max(w, 120), h), 'white')
        d = ImageDraw.Draw(sheet)
        y, n = 0, 0
        for row in group:
            x = 16
            for i, c in row:
                n += 1
                d.text((x + c.size[0] / 2 - 8, y + 8), str(n), fill='black', font=f)
                sheet.paste(c, (x, y + 46))
                x += c.size[0] + 16
            y += max(c.size[1] for _, c in row) + 50
        out.append((jpeg(sheet, 90), [i for row in group for i, _ in row]))
    return out


def _font(size):
    for name in ('DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        try: return ImageFont.truetype(name, size)
        except OSError: pass
    return ImageFont.load_default(size=size)


def annotate(im, boxes, max_width=1600):
    """boxes: (label, x0, y0, x1, y1, color, line width) in full-resolution pixels, drawn in order. Returns JPEG bytes, scaled to max_width."""
    s = min(1.0, max_width / im.size[0])
    out = im.resize((round(im.size[0] * s), round(im.size[1] * s))) if s < 1 else im.copy()
    d = ImageDraw.Draw(out)
    f = _font(max(14, round(out.size[0] / 70)))
    for label, x0, y0, x1, y1, color, width in boxes:
        x0, y0, x1, y1 = (round(v * s) for v in (x0, y0, x1, y1))
        d.rectangle([x0, y0, x1, y1], outline=color, width=width)
        label = str(label)
        l, t, r, b = d.textbbox((0, 0), label, font=f)
        tx, ty = x0 + 2, (y0 - (b - t) - 8) if y0 - (b - t) - 8 >= 0 else y0 + 2
        d.rectangle([tx - 3, ty - 2, tx + (r - l) + 5, ty + (b - t) + 6], fill=color)
        d.text((tx, ty - t + 2), label, fill=_ink(color), font=f)
    return jpeg(out, 85)


def _ink(color):
    """Black text on a light label colour, white on a dark one."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 'black' if 0.299 * r + 0.587 * g + 0.114 * b > 150 else 'white'
