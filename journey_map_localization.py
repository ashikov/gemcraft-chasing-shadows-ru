"""Optional, guarded raster localization of the Journey Notes opening map.

Pillow is imported only by the local renderer. Inspection and replacement use
stdlib only; no game process or network service is invoked.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import struct

from swf_geometry import Tag, read_swf, read_tags


DEFAULT_RECIPE = Path(__file__).resolve().parent / 'translations/plans/pr34-journey-map.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()



def validate_map_recipe(recipe):
    """Validate the bounded authored plan without importing Pillow or game data."""
    if (recipe.get('schema_version') != 1 or recipe.get('character_id') != 678
            or recipe.get('tag_code') != 6 or recipe.get('tag_path') != [750]
            or recipe.get('size') != [1440, 868]):
        raise ValueError('Unsupported Journey map recipe scope')
    hashes = [recipe.get(key) for key in ('game_sha256', 'source_payload_sha256',
              'source_jpeg_sha256', 'source_pixels_sha256', 'font_sha256')]
    hashes.extend(recipe.get('result', {}).get(key)
                  for key in ('jpeg_sha256', 'rendered_pixels_sha256'))
    if any(not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None
           for value in hashes):
        raise ValueError('Journey map recipe has an invalid SHA-256')
    if (recipe.get('font_weight') != 400 or recipe.get('supersampling') != 3
            or recipe.get('label_canvas') != [400, 120]
            or recipe.get('jpeg') != {'subsampling': 2, 'quantization': 'source',
                                     'progressive': False, 'optimize': False}):
        raise ValueError('Unsupported Journey map font or encoding parameters')
    if (set(recipe.get('renderer', {})) != {'pillow', 'jpeg', 'libjpeg_turbo', 'freetype'}
            or any(not isinstance(value, str) for value in recipe['renderer'].values())):
        raise ValueError('Journey map renderer versions are missing')
    if (recipe.get('mask') != {'black_below': 150, 'brown_chroma_at_least': 5,
                              'dilation_filter_size': 3}
            or recipe.get('colors') != {'black': [0, 0, 0, 255],
                                       'brown': [133, 105, 101, 255]}):
        raise ValueError('Unsupported Journey map mask or ink parameters')
    labels = recipe.get('labels', [])
    expected = {'tomb', 'frozen', 'labyrinth', 'scythe', 'spiritforge',
                'tower', 'waiting', 'possessed', 'wilderness', 'inhabited'}
    if len(labels) != 10 or {label.get('id') for label in labels} != expected:
        raise ValueError('Journey map must contain exactly ten known label regions')
    for label in labels:
        lines = label.get('lines')
        if (not isinstance(lines, list) or not lines
                or any(not isinstance(line, str) or not line
                       or any(ord(char) < 32 for char in line) for line in lines)):
            raise ValueError('Journey map labels must use nonempty explicit text lines')
        if re.fullmatch('[0-9a-f]{64}', label.get('source_sha256', '')) is None:
            raise ValueError('Journey map label has an invalid source SHA-256')
        for key in ('source_bounds', 'allowed_output_bounds'):
            rect = label.get(key)
            if (not isinstance(rect, list) or len(rect) != 4
                    or any(type(value) is not int for value in rect)
                    or not (0 <= rect[0] < rect[2] <= 1440 and 0 <= rect[1] < rect[3] <= 868)):
                raise ValueError('Journey map label rectangle is invalid')
        if (label.get('ink') not in ('black', 'brown')
                or type(label.get('font_size')) is not int or not 16 <= label['font_size'] <= 40
                or type(label.get('angle_degrees')) is not int or not -20 <= label['angle_degrees'] <= 20):
            raise ValueError('Journey map label typography is invalid')
    if sum(len(label['lines']) for label in labels) != 12:
        raise ValueError('Journey map must preserve twelve explicit text lines')
    return True


def jpeg_metadata(data):
    """Require one self-contained baseline JPEG, without decoding its entropy."""
    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise ValueError('Expected an exact SOI..EOI JPEG stream')
    offset, markers, frame, saw_scan = 2, set(), None, False
    while offset < len(data):
        if data[offset] != 255:
            raise ValueError('Invalid JPEG marker boundary')
        while offset < len(data) and data[offset] == 255:
            offset += 1
        if offset >= len(data):
            raise ValueError('Truncated JPEG marker')
        marker = data[offset]
        offset += 1
        if marker in (0, 0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            raise ValueError('Unexpected standalone JPEG marker')
        if offset + 2 > len(data):
            raise ValueError('Truncated JPEG marker length')
        length = int.from_bytes(data[offset:offset + 2], 'big')
        if length < 2 or offset + length > len(data):
            raise ValueError('Invalid JPEG segment length')
        payload = data[offset + 2:offset + length]
        offset += length
        markers.add(marker)
        if 0xc0 <= marker <= 0xcf and marker not in (0xc4, 0xc8, 0xcc):
            if marker != 0xc0 or frame is not None or len(payload) < 6:
                raise ValueError('Only one baseline JPEG frame is supported')
            precision, height, width, components = struct.unpack('>BHHB', payload[:6])
            if (precision != 8 or components != 3 or not width or not height
                    or len(payload) != 6 + 3 * components):
                raise ValueError('Unsupported JPEG frame')
            frame = {'width': width, 'height': height, 'precision': precision,
                     'components': components, 'frame_marker': marker}
        if marker == 0xda:
            if frame is None or not {0xdb, 0xc4}.issubset(markers):
                raise ValueError('JPEG scan requires its own frame and quantization/Huffman tables')
            # Baseline output has one scan. Stuffed FF bytes and restart markers
            # are entropy, not a second image or unaccounted trailing segment.
            while offset < len(data):
                if data[offset] != 255:
                    offset += 1
                    continue
                while offset < len(data) and data[offset] == 255:
                    offset += 1
                if offset >= len(data):
                    raise ValueError('Truncated JPEG entropy marker')
                entropy_marker = data[offset]
                offset += 1
                if entropy_marker == 0 or 0xd0 <= entropy_marker <= 0xd7:
                    continue
                if entropy_marker == 0xd9 and offset == len(data):
                    saw_scan = True
                    break
                raise ValueError('Unexpected marker or trailing bytes after JPEG scan')
            break
    if not saw_scan or frame is None:
        raise ValueError('JPEG has no complete baseline scan')
    return {**frame, 'self_contained': True}


def restore_masked_rows(pixels, mask, width, height):
    """Fill only mask pixels by interpolation between unmasked row neighbors."""
    if len(pixels) != width * height * 3 or len(mask) != width * height:
        raise ValueError('RGB pixels or mask dimensions differ')
    result = bytearray(pixels)
    for y in range(height):
        x = 0
        while x < width:
            if not mask[y * width + x]:
                x += 1
                continue
            start = x
            while x < width and mask[y * width + x]:
                x += 1
            end = x
            if start == 0 or end == width:
                raise ValueError('Restoration mask must have both row neighbors')
            left = (y * width + start - 1) * 3
            right = (y * width + end) * 3
            for column in range(start, end):
                ratio = (column - start + 1) / (end - start + 1)
                at = (y * width + column) * 3
                for channel in range(3):
                    result[at + channel] = round(pixels[left + channel]
                        + (pixels[right + channel] - pixels[left + channel]) * ratio)
    return bytes(result)


def _bitmap_index(tags, recipe):
    matches = [index for index, tag in enumerate(tags)
               if tag.code in (6, 21, 35, 90)
               and int.from_bytes(tag.payload[:2], 'little') == recipe['character_id']]
    if len(matches) != 1:
        raise ValueError('Journey map bitmap must have exactly one definition')
    index = matches[0]
    tag = tags[index]
    if 'tag_path' in recipe and recipe['tag_path'] != [index]:
        raise ValueError('Journey map source tag path differs')
    if tag.code != recipe['tag_code'] or digest(tag.payload) != recipe['source_payload_sha256']:
        raise ValueError('Journey map source bitmap guard differs')
    tables = [digest(tag.payload) for tag in tags if tag.code == 8]
    if tables != [row['payload_sha256'] for row in recipe['jpeg_tables']]:
        raise ValueError('Journey map JPEGTables guard differs')
    for guard in recipe['resource_guards']:
        selected = [tag for tag in tags if tag.code == guard['tag_code']
                    and int.from_bytes(tag.payload[:2], 'little') == guard['character_id']]
        if len(selected) != 1:
            raise ValueError('Journey map referenced resource is missing or duplicated')
        payload = selected[0].payload
        if 'child_index' in guard:
            children = read_tags(payload[4:])
            child = children[guard['child_index']]
            if child.code != guard['child_tag_code']:
                raise ValueError('Journey map placement code differs')
            payload = child.payload
        if digest(payload) != guard['payload_sha256']:
            raise ValueError('Journey map referenced resource guard differs')
    return index


def extract_map_jpeg(tags, recipe):
    """Extract the exact full JPEG; empty JPEGTables contributes no bytes."""
    index = _bitmap_index(tags, recipe)
    jpeg = tags[index].payload[2:]
    meta = jpeg_metadata(jpeg)
    if [meta['width'], meta['height']] != recipe['size']:
        raise ValueError('Journey map source size differs')
    return jpeg


def patch_map_tags(tags, jpeg, recipe):
    """Keep tag 6, character identity, tables and every other raw tag unchanged."""
    index = _bitmap_index(tags, recipe)
    meta = jpeg_metadata(jpeg)
    if [meta['width'], meta['height']] != recipe['size']:
        raise ValueError('Journey map output size differs')
    if digest(jpeg) != recipe['result']['jpeg_sha256']:
        raise ValueError('Journey map JPEG differs from the pinned rendering recipe')
    changed = list(tags)
    changed[index] = Tag(recipe['tag_code'], recipe['character_id'].to_bytes(2, 'little') + jpeg)
    return changed, {'changed_character_ids': [recipe['character_id']],
                     'tag_code': recipe['tag_code'], 'jpeg_sha256': digest(jpeg),
                     'dimensions': recipe['size'], 'other_tags_preserved': True,
                     'jpeg_tables_preserved': True}


def _renderer(recipe):
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont, features
    except ImportError as error:
        raise ValueError('Journey map rendering requires the optional pinned Pillow environment') from error
    actual = {'pillow': Image.__version__, 'jpeg': features.version('jpg'),
              'libjpeg_turbo': features.version('libjpeg_turbo'),
              'freetype': features.version('freetype2')}
    if actual != recipe['renderer']:
        raise ValueError(f'Journey map renderer versions differ: {actual}')
    return Image, ImageDraw, ImageFilter, ImageFont


def _render_map(source_jpeg, font_bytes, recipe):
    """Return local pixels and mask evidence before any lossy JPEG encoding."""
    Image, ImageDraw, ImageFilter, ImageFont = _renderer(recipe)
    if digest(source_jpeg) != recipe['source_jpeg_sha256']:
        raise ValueError('Journey map source JPEG SHA differs')
    if digest(font_bytes) != recipe['font_sha256']:
        raise ValueError('Journey map Alegreya font SHA differs')
    with Image.open(io.BytesIO(source_jpeg)) as loaded:
        loaded.load()
        if loaded.format != 'JPEG' or list(loaded.size) != recipe['size']:
            raise ValueError('Journey map decoded JPEG format or size differs')
        quantization = loaded.quantization
        original = loaded.convert('RGB')
    original_pixels = original.tobytes()
    if digest(original_pixels) != recipe['source_pixels_sha256']:
        raise ValueError('Journey map decoded pixels differ from pinned decoder')
    mask = Image.new('L', original.size)
    source, selected = original.load(), mask.load()
    for label in recipe['labels']:
        left, top, right, bottom = label['source_bounds']
        for y in range(top, bottom):
            for x in range(left, right):
                red, green, blue = source[x, y]
                black = label['ink'] == 'black' and max(red, green, blue) < recipe['mask']['black_below']
                brown = label['ink'] == 'brown' and min(red - green, red - blue) >= recipe['mask']['brown_chroma_at_least']
                if black or brown:
                    selected[x, y] = 255
    mask = mask.filter(ImageFilter.MaxFilter(recipe['mask']['dilation_filter_size']))
    mask_pixels = mask.tobytes()
    restored_pixels = restore_masked_rows(original_pixels, mask_pixels, *original.size)
    restored = Image.frombytes('RGB', original.size, restored_pixels)
    result = restored.copy()
    changed_mask = mask.copy()
    layouts = []
    for label in recipe['labels']:
        factor = recipe['supersampling']
        font = ImageFont.truetype(io.BytesIO(font_bytes), label['font_size'] * factor)
        font.set_variation_by_axes([recipe['font_weight']])
        canvas_width, canvas_height = recipe['label_canvas']
        layer = Image.new('RGBA', (canvas_width * factor, canvas_height * factor))
        draw = ImageDraw.Draw(layer)
        text = '\n'.join(label['lines'])
        draw.multiline_text((canvas_width * factor // 2, canvas_height * factor // 2), text,
                            font=font, fill=tuple(recipe['colors'][label['ink']]),
                            anchor='mm', align='center', spacing=0)
        layer = layer.resize((canvas_width, canvas_height), Image.Resampling.LANCZOS)
        layer = layer.rotate(label['angle_degrees'], resample=Image.Resampling.BICUBIC, expand=True)
        origin = (round(label['center'][0] - layer.width / 2),
                  round(label['center'][1] - layer.height / 2))
        alpha = layer.getchannel('A')
        bounds = alpha.getbbox()
        if bounds is None:
            raise ValueError('Journey map label rendered empty')
        bounds = [bounds[0] + origin[0], bounds[1] + origin[1],
                  bounds[2] + origin[0], bounds[3] + origin[1]]
        allowed = label['allowed_output_bounds']
        if not (allowed[0] <= bounds[0] < bounds[2] <= allowed[2]
                and allowed[1] <= bounds[1] < bounds[3] <= allowed[3]):
            raise ValueError(f'Journey map label exceeds its reviewed bounds: {label["id"]}')
        for landmark in recipe['protected_landmarks']:
            if (bounds[0] < landmark[2] and landmark[0] < bounds[2]
                    and bounds[1] < landmark[3] and landmark[1] < bounds[3]):
                raise ValueError(f'Journey map label overlaps a landmark: {label["id"]}')
        result.paste(layer, origin, layer)
        changed_mask.paste(255, (origin[0], origin[1], origin[0] + layer.width,
                                 origin[1] + layer.height), alpha.point(lambda value: 255 if value else 0))
        layouts.append({'id': label['id'], 'bounds': bounds, 'lines': len(label['lines']),
                        'scaled_bounds': {str(scale): [value * scale / 2 for value in bounds]
                                          for scale in (1, 0.75, 0.5)}})
    output_pixels = result.tobytes()
    union = changed_mask.tobytes()
    preserved = all(original_pixels[at * 3:at * 3 + 3] == output_pixels[at * 3:at * 3 + 3]
                    for at, changed in enumerate(union) if not changed)
    if not preserved:
        raise ValueError('Journey map rendering altered pixels outside the union mask')
    for rectangle in recipe['protected_landmarks']:
        if original.crop(rectangle).tobytes() != result.crop(rectangle).tobytes():
            raise ValueError('Journey map restoration or lettering altered a protected landmark')
    route_pixels = 0
    for at in range(original.width * original.height):
        red, green, blue = original_pixels[at * 3:at * 3 + 3]
        colored_route = (blue - red > 25 or green - red > 20
                         or (red - green > 35 and green - blue > 20)
                         or (red - green > 20 and blue - green > 15))
        if colored_route:
            route_pixels += 1
            if original_pixels[at * 3:at * 3 + 3] != output_pixels[at * 3:at * 3 + 3]:
                raise ValueError('Journey map rendering altered a colored route core pixel')
    report = {'renderer': recipe['renderer'], 'source_jpeg_sha256': digest(source_jpeg),
              'source_pixels_sha256': digest(original_pixels),
              'restoration_mask_sha256': digest(mask_pixels),
              'restoration_mask_pixels': sum(bool(value) for value in mask_pixels),
              'restored_pixels_sha256': digest(restored_pixels),
              'rendered_pixels_sha256': digest(output_pixels),
              'preencode_outside_edit_mask_equal': preserved,
              'preencode_route_core_pixels_preserved': route_pixels,
              'labels': layouts, 'label_regions': len(layouts),
              'text_lines': sum(row['lines'] for row in layouts),
              'jpeg_is_lossy': True, 'runtime': 'NOT TESTED'}
    return result, mask, changed_mask, quantization, report


def _encode_jpeg(image, quantization, recipe):
    output = io.BytesIO()
    image.save(output, 'JPEG', qtables=quantization,
               subsampling=recipe['jpeg']['subsampling'], optimize=False,
               progressive=False)
    jpeg = output.getvalue()
    jpeg_metadata(jpeg)
    return jpeg


def generate_map_jpeg(source_jpeg, font_bytes, recipe):
    """Deterministically render all Russian labels, then verify the pinned bytes."""
    validate_map_recipe(recipe)
    rendered, _, _, quantization, report = _render_map(source_jpeg, font_bytes, recipe)
    jpeg = _encode_jpeg(rendered, quantization, recipe)
    if digest(jpeg) != recipe['result']['jpeg_sha256']:
        raise ValueError('Journey map generated JPEG differs from pinned result')
    if report['rendered_pixels_sha256'] != recipe['result']['rendered_pixels_sha256']:
        raise ValueError('Journey map generated pixels differ from pinned result')
    report['jpeg_sha256'] = digest(jpeg)
    return jpeg, report


def localize_map_tags(tags, font_bytes, recipe):
    validate_map_recipe(recipe)
    source_jpeg = extract_map_jpeg(tags, recipe)
    jpeg, report = generate_map_jpeg(source_jpeg, font_bytes, recipe)
    changed, patch_report = patch_map_tags(tags, jpeg, recipe)
    return changed, {**report, **patch_report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--alegreya-font', type=Path, required=True)
    parser.add_argument('--recipe', type=Path, default=DEFAULT_RECIPE)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    recipe = json.loads(args.recipe.read_text())['journey_map']
    source = args.source.read_bytes()
    if digest(source) != recipe['game_sha256']:
        parser.error('Original SWF SHA differs')
    if args.output_dir.exists() or args.output_dir.is_symlink():
        parser.error('Use a fresh local output directory')
    resolved = args.output_dir.resolve()
    project = Path(__file__).resolve().parent
    if not any(resolved.is_relative_to(project / root) and resolved != project / root
               for root in ('local', 'output', 'artifacts')):
        parser.error('Graphic artifacts must remain in ignored local/, output/ or artifacts/')
    _, tags = read_swf(source)
    jpeg, report = generate_map_jpeg(extract_map_jpeg(tags, recipe),
                                     args.alegreya_font.read_bytes(), recipe)
    patch_map_tags(tags, jpeg, recipe)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'journey-map-ru.jpg').write_bytes(jpeg)
    (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'jpeg_sha256': digest(jpeg), 'labels': report['label_regions'],
                      'runtime': report['runtime']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    main()
