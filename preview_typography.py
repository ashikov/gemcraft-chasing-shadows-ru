"""Offline glyph previews; never loads or executes a Flash application."""

from pathlib import Path
import math

from PIL import Image, ImageDraw

from swf_geometry import parse_shape


def flatten(contours, scale, origin):
    polygons = []
    for contour in contours:
        points = []
        current = (0, 0)
        for segment in contour:
            if segment[0] in ("M", "L"):
                current = segment[1:]
                points.append(current)
            else:
                cx, cy, x, y = segment[1:]
                sx, sy = current
                for step in range(1, 17):
                    t = step / 16
                    points.append(
                        (
                            (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t * t * x,
                            (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t * t * y,
                        )
                    )
                current = (x, y)
        polygons.append(
            [(x * scale + origin[0], y * scale + origin[1]) for x, y in points]
        )
    return polygons


def glyph_mask(size, polygons):
    """Nonzero winding fill keeps holes and unions overlapping strokes."""
    mask = Image.new("L", size)
    draw = ImageDraw.Draw(mask)
    edges = []
    for points in polygons:
        for a, b in zip(points, points[1:] + points[:1]):
            if a[1] != b[1]:
                edges.append((a, b))
    for row in range(size[1]):
        y = row + 0.5
        crossings = []
        for (x1, y1), (x2, y2) in edges:
            if min(y1, y2) <= y < max(y1, y2):
                crossings.append(
                    (x1 + (y - y1) * (x2 - x1) / (y2 - y1), 1 if y2 > y1 else -1)
                )
        crossings.sort()
        winding, left = 0, 0
        for x, direction in crossings:
            if winding == 0:
                left = x
            winding += direction
            if winding == 0:
                start, end = math.ceil(left - 0.5), math.ceil(x - 0.5) - 1
                if start <= end:
                    draw.line((start, row, end, row), fill=255)
    return mask


def draw_run(image, font, entries, height_px, origin, color=(253, 229, 202), magnify=1):
    factor = 3
    for index, advance in entries:
        contours = parse_shape(font["glyphs"][index])
        scale = height_px / 20480 * factor * magnify
        size = (image.width * factor, image.height * factor)
        polys = flatten(contours, scale, (origin[0] * factor, origin[1] * factor))
        mask = glyph_mask(size, polys).resize(image.size, Image.Resampling.LANCZOS)
        image.paste(color, (0, 0), mask)
        origin = (origin[0] + advance * magnify, origin[1])


def text_entries(font, text, px):
    return [
        (
            font["codes"].index(ord(ch)),
            font["advances"][font["codes"].index(ord(ch))] * px / 20480,
        )
        for ch in text
    ]


def samples(fonts, destination):
    image = Image.new("RGB", (1120, 540), (23, 20, 18))
    draw = ImageDraw.Draw(image)
    for row, font in enumerate(fonts):
        y = row * 175
        label = f"ID {font['id']}  {font['name'].rstrip(bytes([0])).decode()}"
        draw.text((20, y + 12), label, fill=(180, 170, 160))
        text = (
            "Options  Hh Nn Mm Ii Kk  abcdefghijklmnopqrstuvwxyz"
            if font["advances"]
            else "GemCraft Shadows 1.0"
        )
        if font["advances"]:
            entries = text_entries(font, text, 24)
        else:
            # No layout in this subset: use conservative outline width for illustration.
            from swf_geometry import shape_bounds

            entries = [
                (
                    font["codes"].index(ord(ch)),
                    (
                        shape_bounds(
                            parse_shape(font["glyphs"][font["codes"].index(ord(ch))])
                        )[1]
                        + 1000
                    )
                    * 24
                    / 20480,
                )
                for ch in text
                if ord(ch) in font["codes"]
            ]
        draw_run(image, font, entries, 24, (20, y + 70))
        chosen = "Options HNMIKact" if font["advances"] else "GemCraft"
        enlarged = text_entries(font, chosen, 45) if font["advances"] else entries[:8]
        draw_run(image, font, enlarged, 45, (20, y + 145))
    image.save(Path(destination))
