"""PDF lookbook export (Day 22 / V40).

Turns a deck — named scenes, each holding ordered photos — into a PDF the
user can email to a client. All layout lives here; the /api/decks/<id>/export.pdf
endpoint in app.py only queries the DB and hands the rows over.

Two hard rules that come straight from the product decision and must not be
"optimised" away later:

1. FIXED PAGE SIZE, LETTERBOXED. Every page is landscape US Letter, always.
   An image is scaled to FIT its area with its aspect ratio preserved and is
   centred; whatever space is left over stays page background. We never crop
   to fill and never resize the page to match a photo. This is a
   cinematographer's tool — silently re-framing his shots would make the
   export worse than useless. The dark page colour is what makes the leftover
   space read as intentional margin instead of as bars.

2. THUMBNAILS ONLY. `images.thumbnail_blob` holds raw JPEG bytes (~800px wide,
   quality 85) and that is what goes in the PDF. We deliberately do NOT round
   trip to Google Drive for full-res originals — Ryan wants to judge whether
   800px is sharp enough before paying that cost.

A single unreadable photo must never take the whole export down: anything that
fails to decode is skipped and reported through `skipped`, and a section left
with no usable photos emits no scene title card at all (no stranded headers).
"""

import io
import re
import logging
from datetime import datetime

from PIL import Image, ImageOps
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdf_canvas

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page + palette
# ---------------------------------------------------------------------------

PAGE_SIZE = landscape(letter)          # 11in x 8.5in -> (792, 612) points
PAGE_W, PAGE_H = PAGE_SIZE

# Matches the app's dark cinematic UI (see DESIGN.md).
COLOR_BG = HexColor('#141416')          # near-black page fill
COLOR_GOLD = HexColor('#D9A441')        # warm gold accent
COLOR_TEXT = HexColor('#E2E2E6')        # off-white body text
COLOR_MUTED = HexColor('#9C988D')       # muted grey secondary text
COLOR_HAIRLINE = HexColor('#3A3A3F')    # just enough edge to separate a dark
                                        # photo from the dark page

# reportlab's built-in Helvetica family only. The app's Manrope is not in the
# repo as a font file and shipping one is out of scope.
FONT = 'Helvetica'
FONT_BOLD = 'Helvetica-Bold'

MARGIN = 40                             # page margin, points
FOOTER_H = 22                           # reserved strip for the page number

LAYOUTS = ('full', 'grid')

GRID_COLS = 3
GRID_ROWS = 2

UNSORTED_LABEL = 'Unsorted'


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name, fallback='Deck'):
    """Filesystem-safe version of a deck name.

    Strips path separators and control characters (a deck literally named
    "a/b" must not be able to steer the download anywhere), collapses
    whitespace, and trims leading dots so nothing comes out hidden or empty.
    """
    text = (name or '').strip()
    # Control characters, including newlines and the NUL byte.
    text = ''.join(ch for ch in text if ord(ch) >= 32 and ch != '\x7f')
    text = re.sub(r'[/\\:*?"<>|]+', '-', text)
    text = re.sub(r'\s+', ' ', text).strip(' .-')
    return text or fallback


def pdf_download_name(deck_name):
    """`My Lookbook` -> `My Lookbook - Lookbook.pdf`."""
    return f'{sanitize_filename(deck_name)} - Lookbook.pdf'


def _wrap(text, font, size, max_width, max_lines=None):
    """Greedy word wrap using real glyph widths. Returns a list of lines.

    The last line gets an ellipsis if `max_lines` cut it short — better to
    show a note is truncated than to silently drop half a director's note.
    """
    if not text:
        return []
    lines = []
    for paragraph in str(text).splitlines():
        words = paragraph.split()
        if not words:
            continue
        current = words[0]
        for word in words[1:]:
            trial = f'{current} {word}'
            if pdfmetrics.stringWidth(trial, font, size) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and pdfmetrics.stringWidth(last + '...', font, size) > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + '...'
    return lines


def _fit(iw, ih, box_w, box_h):
    """Largest (w, h) that fits in the box with the aspect ratio intact."""
    if iw <= 0 or ih <= 0 or box_w <= 0 or box_h <= 0:
        return 0.0, 0.0
    scale = min(box_w / float(iw), box_h / float(ih))
    return iw * scale, ih * scale


def _prepare(entry):
    """Decode one deck_image row into something drawable, or None.

    `entry` is a dict with at least `thumbnail_blob`; `storyboard_note` and
    `filename` are optional. Returns None (after logging) on anything that
    doesn't decode — a corrupt thumbnail is a skipped photo, never a 500.

    HAND REPORTLAB JPEG BYTES, NEVER A PIL IMAGE OBJECT. This is a file-size
    rule, not a style preference, and it is worth ~9x. Given a PIL object
    reportlab has no compressed stream to reuse, so it embeds the raw pixels
    Flate-compressed (lossless, and hopeless on photographic content); given
    JPEG bytes it copies them straight into the PDF as DCTDecode. Measured on
    a real 20-frame deck: 13.7MB via PIL vs 1.5MB via bytes. The first number
    does not survive an email attachment limit, and emailing this file IS the
    feature.

    So the original blob is reused verbatim whenever that is lossless-safe,
    and only re-encoded when we actually had to change the pixels (a
    pre-V36 row that still needs EXIF rotation, or a non-JPEG/exotic mode).
    """
    blob = entry.get('thumbnail_blob')
    if not blob:
        return None
    try:
        img = Image.open(io.BytesIO(blob))
        source_format = img.format          # only valid before any transform
        # Read the orientation tag rather than comparing sizes before/after:
        # orientations 2 and 4 are MIRRORS, which rewrite the pixels without
        # changing the dimensions, so a size check would call them a no-op and
        # reuse a blob whose pixels we'd actually just corrected.
        try:
            exif = img.getexif()
            orientation = exif.get(0x0112) if exif else None
        except Exception:  # noqa: BLE001 - unreadable EXIF just means "assume none"
            orientation = None
        # Thumbnails are written EXIF-corrected (V36), but a re-orient here is
        # free insurance for any older row that predates that fix.
        img = ImageOps.exif_transpose(img)
        rotated = orientation not in (None, 0, 1)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.load()
        width, height = img.size
        if width <= 0 or height <= 0:
            return None

        if source_format == 'JPEG' and not rotated and img.mode in ('RGB', 'L'):
            payload = blob                  # untouched pixels — copy as-is
        else:
            out = io.BytesIO()
            img.convert('RGB').save(out, format='JPEG', quality=88, optimize=True)
            payload = out.getvalue()
        reader = ImageReader(io.BytesIO(payload))
    except Exception as exc:  # noqa: BLE001 - any decode failure is a skip
        logger.warning(
            'PDF export: skipping deck_image %s (%s) — thumbnail would not decode: %s',
            entry.get('deck_image_id'), entry.get('filename'), exc
        )
        return None

    note = (entry.get('storyboard_note') or '').strip()
    return {
        'reader': reader,
        'w': width,
        'h': height,
        'note': note,
    }


# ---------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------

def _paint_background(c):
    c.setFillColor(COLOR_BG)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)


def _page_number(c, number):
    c.setFillColor(COLOR_MUTED)
    c.setFont(FONT, 8)
    c.drawCentredString(PAGE_W / 2.0, MARGIN - 12, str(number))


def _draw_image(c, prepared, box_x, box_y, box_w, box_h):
    """Letterbox `prepared` inside the box and return the drawn rect."""
    draw_w, draw_h = _fit(prepared['w'], prepared['h'], box_w, box_h)
    if draw_w <= 0 or draw_h <= 0:
        return None
    x = box_x + (box_w - draw_w) / 2.0
    y = box_y + (box_h - draw_h) / 2.0
    c.drawImage(prepared['reader'], x, y, width=draw_w, height=draw_h, mask=None)
    # Hairline so a very dark frame still reads as a distinct object on a
    # near-black page.
    c.setStrokeColor(COLOR_HAIRLINE)
    c.setLineWidth(0.5)
    c.rect(x, y, draw_w, draw_h, stroke=1, fill=0)
    return (x, y, draw_w, draw_h)


def _title_page(c, deck_name, scene_count, photo_count):
    _paint_background(c)

    max_w = PAGE_W - 2 * MARGIN - 60
    size = 40
    lines = _wrap(deck_name or 'Untitled Deck', FONT_BOLD, size, max_w)
    while len(lines) > 3 and size > 20:
        size -= 4
        lines = _wrap(deck_name or 'Untitled Deck', FONT_BOLD, size, max_w)
    lines = lines[:3]

    block_h = len(lines) * (size + 8)
    y = PAGE_H / 2.0 + block_h / 2.0
    c.setFillColor(COLOR_TEXT)
    c.setFont(FONT_BOLD, size)
    for line in lines:
        c.drawCentredString(PAGE_W / 2.0, y, line)
        y -= size + 8

    rule_y = y + size * 0.35
    c.setStrokeColor(COLOR_GOLD)
    c.setLineWidth(2)
    c.line(PAGE_W / 2.0 - 90, rule_y, PAGE_W / 2.0 + 90, rule_y)

    c.setFillColor(COLOR_MUTED)
    c.setFont(FONT, 10.5)
    stamp = datetime.now().strftime('%B %d, %Y')
    scenes_txt = f'{scene_count} scene' + ('' if scene_count == 1 else 's')
    photos_txt = f'{photo_count} photo' + ('' if photo_count == 1 else 's')
    c.drawCentredString(PAGE_W / 2.0, rule_y - 26, stamp)
    c.drawCentredString(PAGE_W / 2.0, rule_y - 42, f'{scenes_txt}  ·  {photos_txt}')
    c.showPage()


def _scene_card(c, name, photo_count):
    """Full-page scene divider — only ever drawn for a section that has photos."""
    _paint_background(c)

    max_w = PAGE_W - 2 * MARGIN - 60
    size = 30
    lines = _wrap(name, FONT_BOLD, size, max_w)
    while len(lines) > 3 and size > 16:
        size -= 3
        lines = _wrap(name, FONT_BOLD, size, max_w)
    lines = lines[:3]

    block_h = len(lines) * (size + 6)
    y = PAGE_H / 2.0 + block_h / 2.0
    c.setFillColor(COLOR_GOLD)
    c.setFont(FONT_BOLD, size)
    for line in lines:
        c.drawCentredString(PAGE_W / 2.0, y, line)
        y -= size + 6

    c.setFillColor(COLOR_MUTED)
    c.setFont(FONT, 9.5)
    label = f'{photo_count} photo' + ('' if photo_count == 1 else 's')
    c.drawCentredString(PAGE_W / 2.0, y - 6, label)
    c.showPage()


def _grid_header(c, name, continued):
    """Returns the y of the bottom of the header block."""
    title = f'{name} (cont.)' if continued else name
    c.setFillColor(COLOR_GOLD)
    c.setFont(FONT_BOLD, 15)
    baseline = PAGE_H - MARGIN - 12
    c.drawString(MARGIN, baseline, title)
    c.setStrokeColor(COLOR_GOLD)
    c.setLineWidth(0.75)
    c.line(MARGIN, baseline - 9, PAGE_W - MARGIN, baseline - 9)
    return baseline - 9


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------

def _render_full(c, sections, page_no):
    """One image per page, preceded by a scene title card."""
    for section in sections:
        _scene_card(c, section['label'], len(section['photos']))
        for prepared in section['photos']:
            _paint_background(c)

            area_x = MARGIN
            area_w = PAGE_W - 2 * MARGIN
            area_bottom = MARGIN + FOOTER_H
            area_top = PAGE_H - MARGIN
            area_h = area_top - area_bottom

            note_lines = _wrap(prepared['note'], FONT, 9, area_w * 0.8, max_lines=3)
            # No note means NO caption band at all — the photo gets the whole
            # page rather than sitting above an empty gap.
            caption_h = (len(note_lines) * 12 + 12) if note_lines else 0

            _draw_image(c, prepared, area_x, area_bottom + caption_h, area_w, area_h - caption_h)

            if note_lines:
                c.setFillColor(COLOR_MUTED)
                c.setFont(FONT, 9)
                y = area_bottom + caption_h - 14
                for line in note_lines:
                    c.drawCentredString(PAGE_W / 2.0, y, line)
                    y -= 12

            _page_number(c, page_no)
            page_no += 1
            c.showPage()
    return page_no


def _render_grid(c, sections, page_no):
    """Contact sheet: GRID_COLS x GRID_ROWS letterboxed cells per page."""
    per_page = GRID_COLS * GRID_ROWS
    gutter = 18
    caption_h = 22

    for section in sections:
        photos = section['photos']
        for chunk_index in range(0, len(photos), per_page):
            chunk = photos[chunk_index:chunk_index + per_page]
            _paint_background(c)
            header_bottom = _grid_header(c, section['label'], continued=chunk_index > 0)

            grid_top = header_bottom - 16
            grid_bottom = MARGIN + FOOTER_H
            grid_w = PAGE_W - 2 * MARGIN
            grid_h = grid_top - grid_bottom
            cell_w = (grid_w - gutter * (GRID_COLS - 1)) / GRID_COLS
            cell_h = (grid_h - gutter * (GRID_ROWS - 1)) / GRID_ROWS

            for slot, prepared in enumerate(chunk):
                col = slot % GRID_COLS
                row = slot // GRID_COLS
                cell_x = MARGIN + col * (cell_w + gutter)
                cell_y = grid_top - (row + 1) * cell_h - row * gutter

                note_lines = _wrap(prepared['note'], FONT, 7.5, cell_w, max_lines=2)
                band = caption_h if note_lines else 0
                _draw_image(c, prepared, cell_x, cell_y + band, cell_w, cell_h - band)

                if note_lines:
                    c.setFillColor(COLOR_MUTED)
                    c.setFont(FONT, 7.5)
                    y = cell_y + band - 10
                    for line in note_lines:
                        c.drawCentredString(cell_x + cell_w / 2.0, y, line)
                        y -= 9

            _page_number(c, page_no)
            page_no += 1
            c.showPage()
    return page_no


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_deck_pdf(deck, sections, layout='full'):
    """Render a deck to a PDF.

    deck     — dict with at least {'name': str}
    sections — ordered list of {'name': str|None, 'images': [row-ish dicts]}
               where each image dict carries `thumbnail_blob` (raw JPEG bytes)
               and optionally `storyboard_note`, `filename`, `deck_image_id`.
               `name` of None renders as "Unsorted".
    layout   — 'full' (one photo per page) or 'grid' (contact sheet).

    Returns an io.BytesIO positioned at 0.
    """
    if layout not in LAYOUTS:
        raise ValueError(f'unknown layout: {layout!r}')

    prepared_sections = []
    skipped = 0
    for section in sections or []:
        photos = []
        for entry in section.get('images') or []:
            item = _prepare(entry)
            if item is None:
                skipped += 1
                continue
            photos.append(item)
        # An empty section emits nothing at all — no stranded title card.
        if photos:
            prepared_sections.append({
                'label': (section.get('name') or UNSORTED_LABEL).strip() or UNSORTED_LABEL,
                'photos': photos,
            })

    photo_count = sum(len(s['photos']) for s in prepared_sections)
    if skipped:
        logger.warning('PDF export: skipped %s unreadable photo(s) for deck %s',
                       skipped, deck.get('id'))

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=PAGE_SIZE)
    deck_name = (deck.get('name') or 'Untitled Deck').strip() or 'Untitled Deck'
    c.setTitle(f'{deck_name} - Lookbook')
    c.setAuthor('Frame Atlas')
    c.setSubject('Lookbook export')

    _title_page(c, deck_name, len(prepared_sections), photo_count)

    if layout == 'grid':
        _render_grid(c, prepared_sections, 1)
    else:
        _render_full(c, prepared_sections, 1)

    c.save()
    buf.seek(0)
    return buf
