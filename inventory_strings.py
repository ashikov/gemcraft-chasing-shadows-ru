"""Read-only, version-bound SWF inventory. Extracted content stays local."""
from collections import Counter
import hashlib
from html.parser import HTMLParser
import re

from swf_geometry import Reader, decode_matrix, decode_rect, parse_font, parse_text, read_swf, read_tags
from translation_catalog import SHA256, source_hash


def _cstring(reader):
    end = reader.data.find(b'\0', reader.pos)
    if end < 0:
        raise ValueError('Unterminated SWF string')
    value = reader.take(end - reader.pos).decode('utf-8')
    reader.take(1)
    return value


def walk_tags(tags, path=(), sprites=(), depth=0):
    if depth > 128:
        raise ValueError('Excessive DefineSprite nesting')
    for index, tag in enumerate(tags):
        tag_path = path + (index,)
        yield tag, tag_path, sprites
        if tag.code == 39:
            reader = Reader(tag.payload)
            identifier, _ = reader.u16(), reader.u16()
            children = read_tags(reader.take(len(reader.data) - reader.pos))
            yield from walk_tags(children, tag_path, sprites + (identifier,), depth + 1)


def parse_edit_text(payload):
    reader = Reader(payload)
    identifier = reader.u16()
    bounds, size = decode_rect(reader.data[reader.pos:])
    reader.take(size)
    flags = int.from_bytes(reader.take(2), 'big')
    result = {'bounds': bounds, 'flags': flags}
    names = ('has_text', 'word_wrap', 'multiline', 'password', 'read_only',
             'has_color', 'has_max_length', 'has_font', 'has_font_class', 'auto_size',
             'has_layout', 'no_select', 'border', 'was_static', 'html', 'use_outlines')
    result.update({name: bool(flags & (1 << (15 - i))) for i, name in enumerate(names)})
    if result['has_font']:
        result['font_id'] = reader.u16()
    if result['has_font_class']:
        result['font_class'] = _cstring(reader)
    if result['has_font'] or result['has_font_class']:
        result['font_height'] = reader.u16()
    if result['has_color']:
        result['color'] = list(reader.take(4))
    if result['has_max_length']:
        result['max_length'] = reader.u16()
    if result['has_layout']:
        result.update(align=reader.u8(), left_margin=reader.u16(), right_margin=reader.u16(),
                      indent=reader.s16(), leading=reader.s16())
        if result['align'] > 3:
            raise ValueError('Invalid DefineEditText alignment')
    result['variable_name'] = _cstring(reader)
    source = _cstring(reader) if result['has_text'] else ''
    reader.finish()
    return identifier, source, result


def _entry(identifier, category, source, location, context, classification='confirmed'):
    return {'id': identifier, 'category': category, 'source': source,
            'source_sha256': source_hash(source), 'location': location,
            'context': context, 'context_confidence': 'inferred',
            'classification': classification, 'translation': None,
            'mandatory_placeholders': []}


def _static_entry(tag, location, fonts):
    text = parse_text(tag.payload, tag.code)
    runs, font_id, used_fonts = [], None, set()
    for index, record in enumerate(text['records']):
        font_id = record.get('font_id', font_id)
        if record['entries'] and font_id not in fonts:
            raise ValueError(f"Static ID {text['id']}: missing supported font {font_id}")
        chars = []
        for glyph_index, _ in record['entries']:
            if glyph_index >= len(fonts[font_id]):
                raise ValueError(f"Static ID {text['id']}: invalid glyph index")
            chars.append(chr(fonts[font_id][glyph_index]))
            used_fonts.add(font_id)
        run = {key: value for key, value in record.items() if key not in ('entries', 'color')}
        if 'color' in record:
            run['color'] = list(record['color'])
        run.update(record_index=index, source=''.join(chars), font_id=font_id,
                   glyph_indices=[g for g, _ in record['entries']],
                   advances=[a for _, a in record['entries']])
        runs.append(run)
    location.update(character_id=text['id'], font_ids=sorted(used_fonts))
    entry = _entry(f"static.{text['id']}", 'static', ''.join(r['source'] for r in runs), location,
                   'Encoded static display text; placement and runtime visibility need review')
    entry.update(text_records=runs, bounds=text['bounds'], matrix=decode_matrix(text['matrix_raw'])[0])
    return entry


def extract_inventory(raw, expected_sha256):
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(expected_sha256, str) or not SHA256.fullmatch(expected_sha256) or digest != expected_sha256:
        raise ValueError('Input SHA-256 does not match the selected original SWF')
    _, tags = read_swf(raw)
    walked = list(walk_tags(tags))
    fonts = {}
    for tag, _, _ in walked:
        if tag.code in (48, 75):
            font = parse_font(tag.payload)
            if font['id'] in fonts:
                raise ValueError('Duplicate font ID')
            fonts[font['id']] = font['codes']
    entries, placements, abc_coverage, unsupported = [], [], [], []
    character_ids, frames = set(), Counter()
    for tag, path, sprites in walked:
        location = {'tag_code': tag.code, 'tag_path': list(path), 'sprite_path': list(sprites)}
        if tag.code == 1:
            frames[path[:-1]] += 1
        if tag.code in (26, 70):
            # Character-bearing PlaceObject2/3 prefix. Other placement attributes
            # remain in the source; this graph is not a timeline simulation.
            reader = Reader(tag.payload)
            flags = reader.u8()
            flags2 = reader.u8() if tag.code == 70 else 0
            depth = reader.u16()
            if tag.code == 70 and (flags2 & 8 or flags2 & 16 and not flags & 2):
                _cstring(reader)
            if flags & 2:
                placements.append(dict(location, character_id=reader.u16(), depth=depth,
                                       frame=frames[path[:-1]]))
        elif tag.code == 4:
            reader = Reader(tag.payload)
            placements.append(dict(location, character_id=reader.u16(), depth=reader.u16(),
                                   frame=frames[path[:-1]]))
        if tag.code in (11, 33, 37):
            if tag.code == 37:
                identifier, source, formatting = parse_edit_text(tag.payload)
                location.update(character_id=identifier)
                entry = _entry(f'edit.{identifier}', 'html' if formatting['html'] else 'static',
                               source, location, 'Editable text component; dynamic assignments need runtime review',
                               'confirmed' if source else 'unknown')
                entry['format'] = formatting
            else:
                entry = _static_entry(tag, location, fonts)
                identifier = entry['location']['character_id']
            if identifier in character_ids:
                raise ValueError(f'Duplicate text character ID {identifier}')
            character_ids.add(identifier)
            entries.append(entry)
        elif tag.code in (72, 82):
            from abc_inventory import extract_abc
            reader = Reader(tag.payload)
            if tag.code == 82:
                location['abc_flags'] = reader.u32()
                location['abc_name'] = _cstring(reader)
            data = extract_abc(reader.take(len(reader.data) - reader.pos), location)
            entries.extend(data['entries'])
            abc_coverage.append(data['coverage'])
        elif tag.code in (10, 13, 62, 91, 12, 59):
            unsupported.append({'tag_code': tag.code, 'tag_path': list(path)})
    return {'schema_version': 1, 'game_sha256': digest, 'entries': entries,
            'placements': placements, 'coverage': {
                'tag_counts': dict(sorted(Counter(str(t.code) for t, _, _ in walked).items())),
                'abc': abc_coverage, 'unsupported_resources': unsupported,
                'static': 'All DefineText/2 and DefineEditText in recursively parsed tag streams; fonts2/3 only',
                'runtime': 'Not performed; visibility, dynamic assembly and external resources are not proven'}}


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []

    def handle_data(self, data):
        self.chunks.append(data)


def inventory_report(inventory):
    entries = inventory['entries']
    sources = Counter(e['source_sha256'] for e in entries)
    classifications = Counter(e['classification'] for e in entries)
    characters, words, raw_characters, raw_words = Counter(), Counter(), Counter(), Counter()
    for entry in entries:
        if entry['classification'] in ('confirmed', 'probable'):
            text = entry['source']
            raw_characters[entry['classification']] += len(text)
            raw_words[entry['classification']] += len(re.findall(r'\w+', text))
            if entry['category'] == 'html' or entry.get('content_type') == 'html_fragment':
                parser = _VisibleText()
                parser.feed(text)
                parser.close()
                text = ''.join(parser.chunks)
            characters[entry['classification']] += len(text)
            words[entry['classification']] += len(re.findall(r'\w+', text))
    context_count = sum(e['context_confidence'] == 'confirmed' for e in entries)
    return {'schema_version': 1, 'game_sha256': inventory['game_sha256'],
            'total_records': len(entries),
            'classifications': {c: classifications[c] for c in ('confirmed', 'probable', 'technical', 'unknown')},
            'unique_sources': len(sources), 'repeated_records': sum(n - 1 for n in sources.values()),
            'categories': dict(sorted(Counter(e['category'] for e in entries).items())),
            'origins': dict(sorted(Counter('actionscript' if e['location']['tag_code'] in (72, 82)
                                           else 'swf' for e in entries).items())),
            'html_records': sum(e['category'] == 'html' or e.get('content_type') == 'html_fragment' for e in entries),
            'empty_records': sum(not e['source'] for e in entries),
            'characters_to_translate': dict(characters), 'words_to_translate': dict(words),
            'raw_characters': dict(raw_characters), 'raw_word_tokens': dict(raw_words),
            'workload_basis': 'Per occurrence; confirmed/probable only; HTML stripped and entities decoded for estimates; dynamic assembly unresolved',
            'confirmed_context_records': context_count,
            'confirmed_context_fraction': context_count / len(entries) if entries else 0,
            'coverage': inventory['coverage']}
