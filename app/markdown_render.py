import html
import re
from hashlib import md5
from io import BytesIO
from typing import Dict, Iterable, List, Optional, Tuple

from pdf2image import convert_from_bytes
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import portrait
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle,
                                PageBreak)


DEFAULT_PAGE_HEIGHT_MM = 400.0
PAGE_BREAK_MARKER = '---PAGE---'

TRIPLE = re.compile(r"\*\*\*(.+?)\*\*\*", flags=re.DOTALL)
DOUBLE = re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL)
SINGLE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", flags=re.DOTALL)
CODE_INLINE = re.compile(r"`([^`]+)`")
HDR1 = re.compile(r"^\s*#\s+(.*)$")
HDR2 = re.compile(r"^\s*##\s+(.*)$")
HDR3 = re.compile(r"^\s*###\s+(.*)$")
UL = re.compile(r"^\s*[-*]\s+(.*)$")
OL = re.compile(r"^\s*(\d+)\.\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
FENCE = re.compile(r"^\s*```")
BLANK = re.compile(r"^\s*$")
TABLE_SPLIT = re.compile(r"(?<!\\)\|")
ALIGN_SPEC = re.compile(r"^:?-{3,}:?$")


def inline_md_to_html(text: str, faces: Tuple[str, str, str, str]) -> str:
    regular, bold, italic, bolditalic = faces

    # Allow safe HTML tags for additional formatting before escaping
    # Temporarily replace them with placeholders
    import re
    placeholders = {}
    counter = 0

    # Normalize all <br> variants to <br/> for ReportLab compatibility
    # ReportLab requires self-closing tags and doesn't accept <br> or <br >
    text = re.sub(r'<br\s*/?>', '<br/>', text, flags=re.IGNORECASE)

    # Preserve <u>, <font>, <para>, <br/>, and other safe formatting tags
    # <para> allows alignment: <para align="center">text</para>
    # <br/> allows explicit line breaks (must be self-closing for ReportLab)
    safe_tags_pattern = r'(</?(?:u|font|sub|super|para)[^>]*>|<br/>)'
    for match in re.finditer(safe_tags_pattern, text, re.IGNORECASE):
        placeholder = f"__HTMLTAG_{counter}__"
        placeholders[placeholder] = match.group(1)
        text = text.replace(match.group(1), placeholder, 1)
        counter += 1

    escaped = html.escape(text, quote=False)
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n")

    # Restore HTML tags after escaping
    for placeholder, tag in placeholders.items():
        escaped = escaped.replace(placeholder, tag)

    escaped = CODE_INLINE.sub(r'<font name="Courier">\1</font>', escaped)
    escaped = TRIPLE.sub(fr'<font name="{bolditalic}">\1</font>', escaped)
    escaped = DOUBLE.sub(fr'<font name="{bold}">\1</font>', escaped)
    escaped = SINGLE.sub(fr'<font name="{italic}">\1</font>', escaped)
    return escaped.replace("\n", "<br/>")


def _split_table_row(line: str) -> Optional[List[str]]:
    parts = TABLE_SPLIT.split(line.strip())
    if not parts or '|' not in line:
        return None
    if line.strip().startswith('|'):
        parts = parts[1:]
    if line.strip().endswith('|'):
        parts = parts[:-1]
    if not parts:
        return None
    cells = [part.strip() for part in parts]
    return [cell.replace("\\|", "|").replace("\\\\", "\\") for cell in cells]


def _parse_alignment_row(line: str, expected_cols: int) -> Optional[List[str]]:
    cells = _split_table_row(line)
    if not cells or len(cells) != expected_cols:
        return None
    alignments = []
    for cell in cells:
        spec = cell.replace(' ', '')
        if not ALIGN_SPEC.match(spec):
            return None
        left = spec.startswith(':')
        right = spec.endswith(':')
        if left and right:
            alignments.append('center')
        elif right:
            alignments.append('right')
        else:
            alignments.append('left')
    return alignments


def _try_parse_table(lines: List[str], start: int) -> Optional[Tuple[int, Tuple[List[str], List[str], List[List[str]]]]]:
    header = _split_table_row(lines[start])
    if not header or len(header) < 2:
        return None
    if start + 1 >= len(lines):
        return None
    aligns = _parse_alignment_row(lines[start + 1], len(header))
    if aligns is None:
        return None

    rows: List[List[str]] = []
    idx = start + 2
    while idx < len(lines):
        if BLANK.match(lines[idx]):
            break
        row = _split_table_row(lines[idx])
        if not row or len(row) != len(header):
            break
        rows.append(row)
        idx += 1

    return idx, (header, aligns, rows)


def parse_blocks(md_text: str) -> List[Tuple[str, object]]:
    lines = md_text.replace("\r\n", "\n").replace("\r", "\n").split('\n')
    idx = 0
    blocks: List[Tuple[str, object]] = []

    while idx < len(lines):
        line = lines[idx]
        if FENCE.match(line):
            language_or_text = line.strip()[3:].lstrip()
            code_lines: List[str] = []
            if language_or_text and not re.match(r"^[A-Za-z0-9_+\-\.]+$", language_or_text):
                code_lines.append(language_or_text)
            idx += 1
            while idx < len(lines) and not FENCE.match(lines[idx]):
                code_lines.append(lines[idx])
                idx += 1
            if idx < len(lines):
                idx += 1
            blocks.append(('codeblock', '\n'.join(code_lines)))
            continue

        if line.strip() == PAGE_BREAK_MARKER:
            blocks.append(('pagebreak', None))
            idx += 1
            continue

        if BLANK.match(line):
            idx += 1
            continue

        match = HDR1.match(line)
        if match:
            blocks.append(('h1', match.group(1)))
            idx += 1
            continue
        match = HDR2.match(line)
        if match:
            blocks.append(('h2', match.group(1)))
            idx += 1
            continue
        match = HDR3.match(line)
        if match:
            blocks.append(('h3', match.group(1)))
            idx += 1
            continue

        if QUOTE.match(line):
            quote_lines: List[str] = []
            while idx < len(lines) and QUOTE.match(lines[idx]):
                quote_lines.append(QUOTE.match(lines[idx]).group(1))
                idx += 1
            blocks.append(('quote', '\n'.join(quote_lines)))
            continue

        if UL.match(line):
            items: List[str] = []
            while idx < len(lines) and UL.match(lines[idx]):
                items.append(UL.match(lines[idx]).group(1))
                idx += 1
            blocks.append(('ul', items))
            continue

        if OL.match(line):
            items: List[str] = []
            while idx < len(lines) and OL.match(lines[idx]):
                items.append(OL.match(lines[idx]).group(2))
                idx += 1
            blocks.append(('ol', items))
            continue

        table = _try_parse_table(lines, idx)
        if table is not None:
            idx, table_data = table
            blocks.append(('table', table_data))
            continue

        para_lines = [line]
        idx += 1
        while idx < len(lines) and not any(pattern.match(lines[idx]) for pattern in (BLANK, HDR1, HDR2, HDR3, UL, OL, QUOTE, FENCE)):
            table_probe = _try_parse_table(lines, idx)
            if table_probe is not None:
                break
            para_lines.append(lines[idx])
            idx += 1
        blocks.append(('p', '\n'.join(para_lines)))

    return blocks


def register_font_face(path: Optional[str], label: str) -> str:
    if not path:
        return label
    font_id = md5(path.encode('utf-8')).hexdigest()[:8]
    font_name = f"{label}_{font_id}"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, path))
    return font_name


def resolve_font_faces(style_map: Dict[str, str], preferred_style: str) -> Tuple[str, str, str, str]:
    def find_with_keywords(keywords: Iterable[str], fallback: str) -> str:
        for key, path in style_map.items():
            name = key.lower()
            if "symbols" in name or "emoji" in name:
                continue
            if all(word in name for word in keywords):
                return path
        return style_map.get(fallback, '')

    regular_path = style_map.get(preferred_style, '') or style_map.get('Regular', '') or next((path for key, path in style_map.items() if "regular" in key.lower() and "symbols" not in key.lower() and "emoji" not in key.lower()), '') or next(iter(style_map.values()), '')
    bold_path = find_with_keywords(['bold'], preferred_style) or regular_path
    italic_path = find_with_keywords(['italic'], preferred_style) or find_with_keywords(['oblique'], preferred_style) or regular_path
    bolditalic_path = find_with_keywords(['bold', 'italic'], preferred_style) or find_with_keywords(['bold', 'oblique'], preferred_style) or bold_path or italic_path or regular_path

    if not regular_path:
        return ('Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique', 'Helvetica-BoldOblique')

    regular_name = register_font_face(regular_path, 'MDRegular')
    bold_name = register_font_face(bold_path or regular_path, 'MDBold')
    italic_name = register_font_face(italic_path or regular_path, 'MDItalic')
    bolditalic_name = register_font_face(bolditalic_path or bold_path or italic_path or regular_path, 'MDBoldItalic')
    return (regular_name, bold_name, italic_name, bolditalic_name)


def build_pdf(md_text: str,
              width_px: int,
              dpi: int,
              base_font_pt: float,
              line_spacing: int,
              faces: Tuple[str, str, str, str],
              allow_pagebreaks: bool) -> Tuple[bytes, List[Tuple[int, float, str]]]:
    width_mm = width_px / dpi * 25.4
    page_w = width_mm * mm
    page_h = DEFAULT_PAGE_HEIGHT_MM * mm

    buffer = BytesIO()
    doc = BaseDocTemplate(buffer,
                          pagesize=portrait((page_w, page_h)),
                          leftMargin=0,
                          rightMargin=0,
                          topMargin=0,
                          bottomMargin=0)

    frame = Frame(0, 0, page_w, page_h, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, showBoundary=0)
    doc.addPageTemplates([PageTemplate(id="label", frames=[frame])])

    spacing_factor = max(line_spacing, 1) / 100.0
    lead = max(base_font_pt * spacing_factor, base_font_pt)
    regular, bold, italic, bolditalic = faces

    style_p = ParagraphStyle('P', fontName=regular, fontSize=base_font_pt, leading=lead,
                             textColor=colors.black, alignment=0, spaceAfter=max(2, base_font_pt * 0.15))
    style_h1 = ParagraphStyle('H1', parent=style_p, fontName=bold, fontSize=int(base_font_pt * 1.6), leading=int(lead * 1.4), spaceAfter=lead)
    style_h2 = ParagraphStyle('H2', parent=style_p, fontName=bold, fontSize=int(base_font_pt * 1.3), leading=int(lead * 1.2), spaceAfter=lead * 0.9)
    style_h3 = ParagraphStyle('H3', parent=style_p, fontName=bolditalic, fontSize=int(base_font_pt * 1.1), leading=int(lead * 1.1), spaceAfter=lead * 0.8)
    style_quote = ParagraphStyle('Quote', parent=style_p, leftIndent=0, textColor=colors.gray)
    style_code = ParagraphStyle('Code', parent=style_p, fontName='Courier', leading=int(lead * 0.95))

    table_header_styles = {
        'left': ParagraphStyle('ThLeft', parent=style_p, fontName=bold, alignment=0),
        'center': ParagraphStyle('ThCenter', parent=style_p, fontName=bold, alignment=1),
        'right': ParagraphStyle('ThRight', parent=style_p, fontName=bold, alignment=2)
    }
    table_cell_styles = {
        'left': ParagraphStyle('TdLeft', parent=style_p, alignment=0),
        'center': ParagraphStyle('TdCenter', parent=style_p, alignment=1),
        'right': ParagraphStyle('TdRight', parent=style_p, alignment=2)
    }

    story = []
    table_boundaries: List[Tuple[int, float]] = []
    blocks = parse_blocks(md_text)

    for kind, data in blocks:
        if kind == 'h1':
            story.append(Paragraph(inline_md_to_html(data, faces), style_h1))
        elif kind == 'h2':
            story.append(Paragraph(inline_md_to_html(data, faces), style_h2))
        elif kind == 'h3':
            story.append(Paragraph(inline_md_to_html(data, faces), style_h3))
        elif kind == 'p':
            story.append(Paragraph(inline_md_to_html(data, faces), style_p))
        elif kind == 'ul':
            for item in data:
                story.append(Paragraph(inline_md_to_html(f"• {item}", faces), style_p))
        elif kind == 'ol':
            for idx, item in enumerate(data, 1):
                story.append(Paragraph(inline_md_to_html(f"{idx}. {item}", faces), style_p))
        elif kind == 'quote':
            quote_para = Paragraph(inline_md_to_html(data, faces), style_quote)
            ruler_width = 3
            table = Table([[ '', quote_para ]], colWidths=[ruler_width, page_w - ruler_width])
            table.setStyle(TableStyle([
                ("LINEBEFORE", (0, 0), (0, -1), 3, colors.gray),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(table)
        elif kind == 'codeblock':
            code = html.escape(data).replace('\t', '    ').replace(' ', '&#160;').replace('\n', '<br/>')
            code_para = Paragraph(f'<font name="Courier">{code}</font>', style_code)
            code_table = Table([[code_para]], colWidths=[page_w])
            code_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("BOX", (0, 0), (-1, -1), 0.3, colors.whitesmoke)
            ]))
            story.append(code_table)
        elif kind == 'table':
            headers, aligns, rows = data

            def make_cell(text: str, idx: int, header: bool) -> Paragraph:
                align = aligns[idx] if idx < len(aligns) else 'left'
                style_map = table_header_styles if header else table_cell_styles
                return Paragraph(inline_md_to_html(text, faces), style_map.get(align, style_map['left']))

            table_rows = [[make_cell(text, idx, True) for idx, text in enumerate(headers)]]
            for row in rows:
                table_rows.append([make_cell(text, idx, False) for idx, text in enumerate(row)])

            tbl = TrackingTable(table_rows, tracker=table_boundaries)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.black),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.black),
                ("BOX", (0, 0), (-1, -1), 1.0, colors.black),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),  # Vertically center all cells
            ]))
            story.append(tbl)
        elif kind == 'pagebreak':
            if allow_pagebreaks:
                story.append(PageBreak())
            continue
        story.append(Spacer(1, base_font_pt * 0.3))

    if not story:
        story.append(Paragraph('&nbsp;', style_p))

    doc.build(story)
    return buffer.getvalue(), table_boundaries


def _normalize_pdf_image(page: Image.Image, target_px_w: int) -> Optional[Tuple[Image.Image, int]]:
    image = page.convert('RGB')

    if target_px_w > 0 and image.width != target_px_w:
        if image.width > target_px_w:
            delta = image.width - target_px_w
            left = delta // 2
            right = image.width - (delta - left)
            image = image.crop((left, 0, right, image.height))
        else:
            pad = (target_px_w - image.width) // 2
            canvas = Image.new('RGB', (target_px_w, image.height), (255, 255, 255))
            canvas.paste(image, (pad, 0))
            image = canvas

    grayscale = image.convert('L')
    mask = grayscale.point(lambda p: 0 if p >= 250 else 255, mode='1').convert('L')
    bbox = mask.getbbox()
    if not bbox:
        return None

    top = max(0, bbox[1])
    bottom = max(top + 1, bbox[3])
    if bottom < image.height or top > 0:
        image = image.crop((0, top, image.width, bottom))
    return image, top


def pdf_bytes_to_image(pdf_bytes: bytes, dpi: int, target_px_w: int) -> Tuple[Image.Image, List[int], List[int], List[int]]:
    pages = convert_from_bytes(pdf_bytes, dpi=dpi)
    processed: List[Image.Image] = []
    page_heights: List[int] = []
    page_top_offsets: List[int] = []
    for page in pages:
        normalized = _normalize_pdf_image(page, target_px_w)
        if normalized is not None:
            normalized_image, top_offset = normalized
            processed.append(normalized_image)
            page_heights.append(normalized_image.height)
            page_top_offsets.append(top_offset)

    if not processed:
        return Image.new('RGB', (max(target_px_w, 1), 1), (255, 255, 255)), [], [], []

    total_height = sum(img.height for img in processed)
    output = Image.new('RGB', (processed[0].width, total_height), (255, 255, 255))
    y = 0
    cumulative_breaks: List[int] = []
    for img in processed:
        output.paste(img, (0, y))
        y += img.height
        cumulative_breaks.append(y)

    # Remove the final total height since it is not a break position
    if cumulative_breaks:
        cumulative_breaks.pop()

    page_starts: List[int] = []
    acc = 0
    for height in page_heights:
        page_starts.append(acc)
        acc += height

    return output, cumulative_breaks, page_starts, page_top_offsets


def add_border_areas(img: Image.Image,
                     *,
                     dpi: int,
                     # Enable flags
                     enable_left_area: bool = False,
                     enable_right_area: bool = False,
                     enable_top_area: bool = False,
                     enable_bottom_area: bool = False,
                     enable_left_bar: bool = False,
                     enable_left_text: bool = False,
                     enable_right_bar: bool = False,
                     enable_right_text: bool = False,
                     enable_top_bar: bool = False,
                     enable_top_text: bool = False,
                     enable_bottom_bar: bool = False,
                     enable_bottom_text: bool = False,
                     # Area dimensions
                     left_area_mm: float = 0,
                     right_area_mm: float = 0,
                     top_area_mm: float = 0,
                     bottom_area_mm: float = 0,
                     # Bar settings
                     left_bar_mm: float = 0,
                     right_bar_mm: float = 0,
                     top_bar_mm: float = 0,
                     bottom_bar_mm: float = 0,
                     left_bar_color: str = 'black',
                     right_bar_color: str = 'black',
                     top_bar_color: str = 'black',
                     bottom_bar_color: str = 'black',
                     left_bar_text: str = '',
                     right_bar_text: str = '',
                     top_bar_text: str = '',
                     bottom_bar_text: str = '',
                     # Text settings
                     left_text: str = '',
                     right_text: str = '',
                     top_text: str = '',
                     bottom_text: str = '',
                     # Font settings
                     font_path: Optional[str] = None,
                     page_num: int = 1,
                     total_pages: int = 1,
                     left_bar_text_size_pt: float = 0,
                     right_bar_text_size_pt: float = 0,
                     top_bar_text_size_pt: float = 0,
                     bottom_bar_text_size_pt: float = 0,
                     top_text_size_pt: float = 0,
                     bottom_text_size_pt: float = 0,
                     default_font_size_pt: float = 12,
                     # Dividers
                     top_divider: bool = False,
                     bottom_divider: bool = False,
                     divider_distance_px: int = 1,
                     # Page numbers
                     draw_page_numbers: bool = False,
                     page_number_circle: bool = True,
                     page_number_mm: float = 4) -> Image.Image:
    """
    Add border areas WITHIN image canvas (resizes content, doesn't expand canvas).

    Areas are reserved space within the canvas:
    - left_area_mm: Total space reserved on left
    - right_area_mm: Total space reserved on right
    - top_area_mm: Total space reserved on top
    - bottom_area_mm: Total space reserved on bottom

    Bars are drawn within their respective areas:
    - left_bar_mm/right_bar_mm: Vertical bars aligned to outer edges
    - top_bar_mm/bottom_bar_mm: Horizontal bars aligned to content area
    """
    from datetime import datetime
    from PIL import ImageDraw, ImageFont

    # Convert mm to pixels
    left_area_px = int(left_area_mm * dpi / 25.4) if left_area_mm > 0 else 0
    right_area_px = int(right_area_mm * dpi / 25.4) if right_area_mm > 0 else 0
    top_area_px = int(top_area_mm * dpi / 25.4) if top_area_mm > 0 else 0
    bottom_area_px = int(bottom_area_mm * dpi / 25.4) if bottom_area_mm > 0 else 0

    left_bar_px = int(left_bar_mm * dpi / 25.4) if left_bar_mm > 0 else 0
    right_bar_px = int(right_bar_mm * dpi / 25.4) if right_bar_mm > 0 else 0
    top_bar_px = int(top_bar_mm * dpi / 25.4) if top_bar_mm > 0 else 0
    bottom_bar_px = int(bottom_bar_mm * dpi / 25.4) if bottom_bar_mm > 0 else 0

    if top_area_px == 0:
        top_bar_px = 0
    else:
        top_bar_px = min(top_bar_px, top_area_px)

    if bottom_area_px == 0:
        bottom_bar_px = 0
    else:
        bottom_bar_px = min(bottom_bar_px, bottom_area_px)

    # If no areas, return original
    if left_area_px == 0 and right_area_px == 0 and top_area_px == 0 and bottom_area_px == 0:
        return img

    # The input image is already rendered at the correct content size
    # We need to create a larger canvas and add border areas around it
    content_width = img.width
    content_height = img.height

    # Calculate final canvas size (content + border areas)
    final_width = content_width + left_area_px + right_area_px
    final_height = content_height + top_area_px + bottom_area_px

    # Create result canvas with border areas
    result = Image.new('RGB', (final_width, final_height), 'white')

    # Paste content at position offset by border areas
    content_x = left_area_px
    content_y = top_area_px
    result.paste(img, (content_x, content_y))

    draw = ImageDraw.Draw(result)

    # Process text variables
    def process_vars(text: str) -> str:
        now = datetime.now()
        # Use simple string formatting to avoid encoding issues
        date_str = f"{now.day:02d}.{now.month:02d}.{now.year}"
        time_str = f"{now.hour:02d}:{now.minute:02d}"
        datetime_str = f"{date_str} {time_str}"
        
        return text.replace('{page}', str(page_num)) \
                   .replace('{pages}', str(total_pages)) \
                   .replace('{date}', date_str) \
                   .replace('{time}', time_str) \
                   .replace('{datetime}', datetime_str)

    # LEFT AREA
    if left_area_px > 0 and enable_left_area:
        # Left bar (aligned LEFT in area) - only if bar is enabled
        if left_bar_px > 0 and enable_left_bar:
            left_bar_fill = (255, 0, 0) if left_bar_color == 'red' else (0, 0, 0)
            draw.rectangle([(0, 0), (left_bar_px, final_height)], fill=left_bar_fill)

            if left_bar_text and font_path:
                try:
                    font_size_px = int(left_bar_text_size_pt * dpi / 72) if left_bar_text_size_pt > 0 else int(left_bar_px * 0.7)
                    try:
                        if font_path:
                            font = ImageFont.truetype(font_path, font_size_px)
                        else:
                            font = ImageFont.load_default()
                    except Exception as font_e:
                        font = ImageFont.load_default()

                    # Create text image to rotate - dimensions for vertical bar
                    txt_img = Image.new('RGB', (final_height, left_bar_px), left_bar_fill)
                    txt_draw = ImageDraw.Draw(txt_img)

                    bbox = txt_draw.textbbox((0, 0), left_bar_text, font=font)
                    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

                    # Center text in the horizontal image (will be vertical after rotation)
                    # x = horizontal center (will become vertical center after rotation)
                    # y = vertical center in bar (will become horizontal center after rotation)
                    x = (final_height - w) // 2 - bbox[0]
                    y = (left_bar_px - h) // 2 - bbox[1]
                    txt_draw.text((x, y), left_bar_text, fill=(255, 255, 255), font=font)

                    # Rotate 90° CCW - this makes the text read bottom-to-top
                    txt_img = txt_img.rotate(90, expand=True)
                    result.paste(txt_img, (0, 0))
                except Exception as e:
                    pass

        # Left text (only if text is enabled, not bar)
        elif enable_left_text and left_text and font_path:
            try:
                font_size_px = int(default_font_size_pt * dpi / 72)
                try:
                    if font_path:
                        font = ImageFont.truetype(font_path, font_size_px)
                    else:
                        font = ImageFont.load_default()
                except Exception as font_e:
                    font = ImageFont.load_default()

                # Create text image for vertical text
                txt_img = Image.new('RGB', (final_height, left_area_px), (255, 255, 255))
                txt_draw = ImageDraw.Draw(txt_img)

                bbox = txt_draw.textbbox((0, 0), left_text, font=font)
                w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

                x = (final_height - w) // 2 - bbox[0]
                y = (left_area_px - h) // 2 - bbox[1]
                txt_draw.text((x, y), left_text, fill=(0, 0, 0), font=font)

                txt_img = txt_img.rotate(90, expand=True)
                result.paste(txt_img, (0, 0))
            except Exception as e:
                pass

    # RIGHT AREA
    if right_area_px > 0 and enable_right_area:
        # Right bar (aligned RIGHT in area) - only if bar is enabled
        if right_bar_px > 0 and enable_right_bar:
            right_bar_fill = (255, 0, 0) if right_bar_color == 'red' else (0, 0, 0)
            bar_x = final_width - right_bar_px
            draw.rectangle([(bar_x, 0), (final_width, final_height)], fill=right_bar_fill)

            if right_bar_text and font_path:
                try:
                    font_size_px = int(right_bar_text_size_pt * dpi / 72) if right_bar_text_size_pt > 0 else int(right_bar_px * 0.7)
                    try:
                        if font_path:
                            font = ImageFont.truetype(font_path, font_size_px)
                        else:
                            font = ImageFont.load_default()
                    except Exception as font_e:
                        font = ImageFont.load_default()

                    txt_img = Image.new('RGB', (final_height, right_bar_px), right_bar_fill)
                    txt_draw = ImageDraw.Draw(txt_img)

                    bbox = txt_draw.textbbox((0, 0), right_bar_text, font=font)
                    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

                    # Center horizontally and vertically
                    x = (final_height - w) // 2 - bbox[0]
                    y = (right_bar_px - h) // 2 - bbox[1]
                    txt_draw.text((x, y), right_bar_text, fill=(255, 255, 255), font=font)

                    # Rotate 90° CW
                    txt_img = txt_img.rotate(270, expand=True)
                    result.paste(txt_img, (bar_x, 0))
                except:
                    pass

        # Right text (only if text is enabled, not bar)
        elif enable_right_text and right_text and font_path:
            try:
                font_size_px = int(default_font_size_pt * dpi / 72)
                try:
                    if font_path:
                        font = ImageFont.truetype(font_path, font_size_px)
                    else:
                        font = ImageFont.load_default()
                except Exception as font_e:
                    font = ImageFont.load_default()

                bar_x = final_width - right_area_px
                txt_img = Image.new('RGB', (final_height, right_area_px), (255, 255, 255))
                txt_draw = ImageDraw.Draw(txt_img)

                bbox = txt_draw.textbbox((0, 0), right_text, font=font)
                w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

                x = (final_height - w) // 2 - bbox[0]
                y = (right_area_px - h) // 2 - bbox[1]
                txt_draw.text((x, y), right_text, fill=(0, 0, 0), font=font)

                txt_img = txt_img.rotate(270, expand=True)
                result.paste(txt_img, (bar_x, 0))
            except:
                pass

    # TOP AREA
    if top_area_px > 0 and enable_top_area:
        bar_height = top_bar_px

        # Top bar - only if bar is enabled
        if bar_height > 0 and enable_top_bar:
            top_bar_fill = (255, 0, 0) if top_bar_color == 'red' else (0, 0, 0)
            bar_x1 = content_x
            bar_x2 = content_x + content_width
            draw.rectangle([(bar_x1, 0), (bar_x2, bar_height)], fill=top_bar_fill)

            if top_bar_text and font_path:
                try:
                    font_size_px = int(top_bar_text_size_pt * dpi / 72) if top_bar_text_size_pt > 0 else int(bar_height * 0.6)
                    try:
                        font = ImageFont.truetype(font_path, max(font_size_px, 1))
                    except Exception as font_e:
                        font = ImageFont.load_default()
                    text = process_vars(top_bar_text)
                    bbox = draw.textbbox((0, 0), text, font=font)
                    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                    x = bar_x1 + (content_width - w) // 2 - bbox[0]
                    y = (bar_height - h) // 2 - bbox[1]
                    draw.text((x, y), text, fill=(255, 255, 255), font=font)
                except Exception as e:
                    pass

        # Top text - only if text is enabled (not bar)
        elif enable_top_text:
            # Divider line (optional)
            if top_divider:
                div_y = top_area_px - divider_distance_px
                draw.line([(content_x, div_y), (content_x + content_width, div_y)], fill=(0, 0, 0), width=1)

            if top_text and font_path:
                try:
                    text = process_vars(top_text)
                    # Use custom font size if specified, otherwise use default
                    font_size_px = int(top_text_size_pt * dpi / 72) if top_text_size_pt > 0 else int(default_font_size_pt * dpi / 72)
                    try:
                        if font_path:
                            font = ImageFont.truetype(font_path, font_size_px)
                        else:
                            font = ImageFont.load_default()
                    except Exception as font_e:
                        font = ImageFont.load_default()

                    bbox = draw.textbbox((0, 0), text, font=font)
                    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

                    # Center horizontally on content area, vertically within top area
                    x = content_x + (content_width - w) // 2 - bbox[0]
                    y = (top_area_px - h) // 2 - bbox[1]
                    draw.text((x, y), text, fill=(0, 0, 0), font=font)
                except Exception as e:
                    pass

    # BOTTOM AREA
    if bottom_area_px > 0 and enable_bottom_area:
        bar_height = bottom_bar_px
        bar_y1 = final_height - bottom_area_px
        bar_y2 = bar_y1 + bar_height

        if bar_height > 0 and enable_bottom_bar:
            bottom_bar_fill = (255, 0, 0) if bottom_bar_color == 'red' else (0, 0, 0)
            bar_x1 = content_x
            bar_x2 = content_x + content_width
            draw.rectangle([(bar_x1, bar_y1), (bar_x2, bar_y2)], fill=bottom_bar_fill)

            if bottom_bar_text and font_path:
                try:
                    font_size_px = int(bottom_bar_text_size_pt * dpi / 72) if bottom_bar_text_size_pt > 0 else int(bar_height * 0.6)
                    try:
                        if font_path:
                            font = ImageFont.truetype(font_path, max(font_size_px, 1))
                        else:
                            font = ImageFont.load_default()
                    except Exception as font_e:
                        font = ImageFont.load_default()
                    text = process_vars(bottom_bar_text)
                    bbox = draw.textbbox((0, 0), text, font=font)
                    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                    x = bar_x1 + (content_width - w) // 2 - bbox[0]
                    y = bar_y1 + (bar_height - h) // 2 - bbox[1]
                    draw.text((x, y), text, fill=(255, 255, 255), font=font)
                except Exception:
                    pass

        elif enable_bottom_text:
            # Divider line (optional)
            if bottom_divider:
                div_y = content_y + content_height + divider_distance_px
                draw.line([(content_x, div_y), (content_x + content_width, div_y)], fill=(0, 0, 0), width=1)

            # Draw page numbers if enabled, otherwise draw bottom text
            if draw_page_numbers and total_pages > 0:
                # Draw page number in bottom area
                try:
                    diameter_px = int(page_number_mm * dpi / 25.4)
                    number_text = str(page_num)

                    if font_path:
                        font_size = max(8, min(diameter_px - 2, int(diameter_px * 0.85)))
                        try:
                            font = ImageFont.truetype(font_path, font_size)
                        except Exception as font_e:
                            font = ImageFont.load_default()
                    else:
                        font = ImageFont.load_default()

                    bbox = draw.textbbox((0, 0), number_text, font=font)
                    text_w = bbox[2] - bbox[0]
                    text_h = bbox[3] - bbox[1]

                    # Center on content area, not full width
                    cx = content_x + content_width // 2
                    content_area_height = bottom_area_px
                    cy = final_height - bottom_area_px + content_area_height // 2

                    if page_number_circle:
                        circle_width = diameter_px
                        circle_height = diameter_px
                        outline_width = max(1, diameter_px // 18)
                        ellipse_box = (
                            cx - circle_width // 2,
                            cy - circle_height // 2,
                            cx + circle_width // 2,
                            cy + circle_height // 2
                        )
                        draw.ellipse(ellipse_box, outline='black', width=outline_width)

                    text_x = int(round(cx - text_w / 2 - bbox[0]))
                    text_y = int(round(cy - text_h / 2 - bbox[1]))
                    draw.text((text_x, text_y), number_text, fill='black', font=font)
                except:
                    pass
            elif bottom_text and font_path:
                # Draw bottom text
                try:
                    text = process_vars(bottom_text)
                    # Use custom font size if specified, otherwise use default
                    font_size_px = int(bottom_text_size_pt * dpi / 72) if bottom_text_size_pt > 0 else int(default_font_size_pt * dpi / 72)
                    try:
                        if font_path:
                            font = ImageFont.truetype(font_path, font_size_px)
                        else:
                            font = ImageFont.load_default()
                    except Exception as font_e:
                        font = ImageFont.load_default()

                    bbox = draw.textbbox((0, 0), text, font=font)
                    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

                    # Centered horizontally on content area, vertically centered in bottom area
                    x = content_x + (content_width - w) // 2 - bbox[0]
                    text_area_top = final_height - bottom_area_px
                    text_area_height = bottom_area_px
                    y = text_area_top + (text_area_height - h) // 2 - bbox[1]
                    draw.text((x, y), text, fill=(0, 0, 0), font=font)
                except:
                    pass

    return result


def render_markdown_to_image(markdown_text: str,
                             *,
                             content_width_px: int,
                             dpi: int,
                             base_font_pt: int,
                             line_spacing: int,
                             font_map: Dict[str, str],
                             preferred_style: str,
                             allow_pagebreaks: bool = False) -> Tuple[Image.Image, List[int], List[int]]:
    text = markdown_text or ''
    width_px = max(content_width_px, 10)
    faces = resolve_font_faces(font_map, preferred_style or '')
    pdf_bytes, table_boundaries_pt = build_pdf(text, width_px, dpi, base_font_pt, line_spacing, faces, allow_pagebreaks)
    image, page_breaks, page_starts_px, page_top_offsets_px = pdf_bytes_to_image(pdf_bytes, dpi, width_px)
    scale = dpi / 72.0
    table_boundaries_px: List[int] = []
    boundary_types: Dict[int, str] = {}
    for page_index, boundary_top_pt, boundary_type in table_boundaries_pt:
        if page_index >= len(page_starts_px):
            continue
        boundary_px = int(round(boundary_top_pt * scale))
        top_offset = page_top_offsets_px[page_index] if page_index < len(page_top_offsets_px) else 0
        adjusted_px = boundary_px - top_offset
        global_px = page_starts_px[page_index] + max(adjusted_px, 0)
        table_boundaries_px.append(global_px)
        boundary_types[global_px] = boundary_type
    table_boundaries_px.sort()
    rgb_image = image.convert('RGB')
    return rgb_image, page_breaks, (table_boundaries_px, boundary_types)
class TrackingTable(Table):
    def __init__(self, data, *args, tracker=None, **kwargs):
        super().__init__(data, *args, **kwargs)
        self._slice_tracker = tracker

    def drawOn(self, canvas, x, y, _sW=0):
        if self._slice_tracker is not None and self._rowHeights:
            page_height = canvas._pagesize[1]
            page_index = max(0, canvas.getPageNumber() - 1)

            # Record boundaries after each row (measured from the top edge of each row's bottom border)
            # In PDF: y points to bottom-left of table, increases upward
            # When we add heights going up from y, we're moving toward the top of the table
            # But in image space, we want boundaries in top-to-bottom order

            # Total table height
            total_height = sum(self._rowHeights)

            # Record boundaries starting from the top of the table (in image space)
            # which is y + total_height in PDF space (at the top of the table)
            cumulative = total_height
            for i, height in enumerate(self._rowHeights):
                # Boundary before this row (at top of row)
                # In PDF: this is at y + cumulative (measured from bottom)
                # Convert to "from top of page": page_height - (y + cumulative)
                boundary_from_top = page_height - (y + cumulative)
                # Mark first boundary as table start
                boundary_type = 'table_start' if i == 0 else 'row'
                self._slice_tracker.append((page_index, boundary_from_top, boundary_type))
                cumulative -= height

            # Record boundary after last row (at bottom of table)
            boundary_from_top = page_height - y
            self._slice_tracker.append((page_index, boundary_from_top, 'table_end'))
        return super().drawOn(canvas, x, y, _sW=_sW)
                    text_area_top = final_height - bottom_area_px
                    text_area_height = bottom_area_px
                    y = text_area_top + (text_area_height - h) // 2 - bbox[1]
                    draw.text((x, y), text, fill=(0, 0, 0), font=font)
                except:
                    pass

    return result


def render_markdown_to_image(markdown_text: str,
                             *,
                             content_width_px: int,
                             dpi: int,
                             base_font_pt: int,
                             line_spacing: int,
                             font_map: Dict[str, str],
                             preferred_style: str,
                             allow_pagebreaks: bool = False) -> Tuple[Image.Image, List[int], List[int]]:
    text = markdown_text or ''
    width_px = max(content_width_px, 10)
    faces = resolve_font_faces(font_map, preferred_style or '')
    pdf_bytes, table_boundaries_pt = build_pdf(text, width_px, dpi, base_font_pt, line_spacing, faces, allow_pagebreaks)
    image, page_breaks, page_starts_px, page_top_offsets_px = pdf_bytes_to_image(pdf_bytes, dpi, width_px)
    scale = dpi / 72.0
    table_boundaries_px: List[int] = []
    boundary_types: Dict[int, str] = {}
    for page_index, boundary_top_pt, boundary_type in table_boundaries_pt:
        if page_index >= len(page_starts_px):
            continue
        boundary_px = int(round(boundary_top_pt * scale))
        top_offset = page_top_offsets_px[page_index] if page_index < len(page_top_offsets_px) else 0
        adjusted_px = boundary_px - top_offset
        global_px = page_starts_px[page_index] + max(adjusted_px, 0)
        table_boundaries_px.append(global_px)
        boundary_types[global_px] = boundary_type
    table_boundaries_px.sort()
    rgb_image = image.convert('RGB')
    return rgb_image, page_breaks, (table_boundaries_px, boundary_types)
class TrackingTable(Table):
    def __init__(self, data, *args, tracker=None, **kwargs):
        super().__init__(data, *args, **kwargs)
        self._slice_tracker = tracker

    def drawOn(self, canvas, x, y, _sW=0):
        if self._slice_tracker is not None and self._rowHeights:
            page_height = canvas._pagesize[1]
            page_index = max(0, canvas.getPageNumber() - 1)

            # Record boundaries after each row (measured from the top edge of each row's bottom border)
            # In PDF: y points to bottom-left of table, increases upward
            # When we add heights going up from y, we're moving toward the top of the table
            # But in image space, we want boundaries in top-to-bottom order

            # Total table height
            total_height = sum(self._rowHeights)

            # Record boundaries starting from the top of the table (in image space)
            # which is y + total_height in PDF space (at the top of the table)
            cumulative = total_height
            for i, height in enumerate(self._rowHeights):
                # Boundary before this row (at top of row)
                # In PDF: this is at y + cumulative (measured from bottom)
                # Convert to "from top of page": page_height - (y + cumulative)
                boundary_from_top = page_height - (y + cumulative)
                # Mark first boundary as table start
                boundary_type = 'table_start' if i == 0 else 'row'
                self._slice_tracker.append((page_index, boundary_from_top, boundary_type))
                cumulative -= height

            # Record boundary after last row (at bottom of table)
            boundary_from_top = page_height - y
            self._slice_tracker.append((page_index, boundary_from_top, 'table_end'))
        return super().drawOn(canvas, x, y, _sW=_sW)
