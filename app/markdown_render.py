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
    escaped = html.escape(text, quote=False)
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n")
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
            if all(word in name for word in keywords):
                return path
        return style_map.get(fallback, '')

    regular_path = style_map.get(preferred_style, '') or style_map.get('Regular', '') or next(iter(style_map.values()), '')
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
              allow_pagebreaks: bool) -> bytes:
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

            tbl = Table(table_rows)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.black),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.black),
                ("BOX", (0, 0), (-1, -1), 1.0, colors.black),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
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
    return buffer.getvalue()


def _normalize_pdf_image(page: Image.Image, target_px_w: int) -> Optional[Image.Image]:
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

    bottom = max(bbox[3], 20)
    if bottom < image.height:
        image = image.crop((0, 0, image.width, bottom))
    return image


def pdf_bytes_to_image(pdf_bytes: bytes, dpi: int, target_px_w: int) -> Tuple[Image.Image, List[int]]:
    pages = convert_from_bytes(pdf_bytes, dpi=dpi)
    processed: List[Image.Image] = []
    for page in pages:
        normalized = _normalize_pdf_image(page, target_px_w)
        if normalized is not None:
            processed.append(normalized)

    if not processed:
        return Image.new('RGB', (max(target_px_w, 1), 1), (255, 255, 255)), []

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

    return output, cumulative_breaks


def render_markdown_to_image(markdown_text: str,
                             *,
                             content_width_px: int,
                             dpi: int,
                             base_font_pt: int,
                             line_spacing: int,
                             font_map: Dict[str, str],
                             preferred_style: str,
                             allow_pagebreaks: bool = False) -> Tuple[Image.Image, List[int]]:
    text = markdown_text or ''
    width_px = max(content_width_px, 10)
    faces = resolve_font_faces(font_map, preferred_style or '')
    pdf_bytes = build_pdf(text, width_px, dpi, base_font_pt, line_spacing, faces, allow_pagebreaks)
    image, page_breaks = pdf_bytes_to_image(pdf_bytes, dpi, width_px)
    rgb_image = image.convert('RGB')
    return rgb_image, page_breaks
