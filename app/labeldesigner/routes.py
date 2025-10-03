import os

import base64

from flask import current_app, render_template, request, make_response, jsonify

from brother_ql.devicedependent import label_type_specs, label_sizes, two_color_support
from brother_ql.devicedependent import ENDLESS_LABEL, DIE_CUT_LABEL, ROUND_DIE_CUT_LABEL

from . import bp
from app.utils import convert_image_to_bw, convert_image_to_grayscale, convert_image_to_red_and_black, pdffile_to_image, imgfile_to_image, image_to_png_bytes
from app import FONTS
from app.markdown_render import render_markdown_to_image

from .label import SimpleLabel, LabelContent, LabelOrientation, LabelType
from .printer import PrinterQueue
from PIL import Image, ImageDraw, ImageFont
from typing import List, Optional

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_LANCZOS = Image.LANCZOS

LINE_SPACINGS = (100, 150, 200, 250, 300)

# Don't change as brother_ql is using this DPI value
DEFAULT_DPI = 300

MARKDOWN_DEFAULT_SLICE_WINDOW_MM = 6.0
MARKDOWN_DEFAULT_MIN_BLANK_RUN = 4
MARKDOWN_DEFAULT_FOOTER_MM = 4.0
MARKDOWN_DEFAULT_PAGE_NUMBER_MM = 4.0
MARKDOWN_DEFAULT_SLICE_MM = 90.0


def get_label_spec(label_size):
    try:
        return label_type_specs[label_size]
    except KeyError as exc:
        raise LookupError("Unknown label_size") from exc


def get_label_dimensions(label_size):
    spec = get_label_spec(label_size)
    dims = spec['dots_printable']
    return dims[0], dims[1]


def mm_to_pixels(mm_value, dpi):
    try:
        mm_float = float(mm_value)
    except (TypeError, ValueError):
        mm_float = 0.0
    return int(round(mm_float / 25.4 * dpi))


def slice_markdown_pages(image, slice_mm, footer_mm, dpi, forced_breaks_px: Optional[List[int]] = None):
    footer_px = mm_to_pixels(footer_mm, dpi)
    window_px = mm_to_pixels(MARKDOWN_DEFAULT_SLICE_WINDOW_MM, dpi)

    def _slice_fragment(fragment: Image.Image) -> List[Image.Image]:
        if fragment.height <= 0:
            return []

        if slice_mm <= 0:
            if footer_px > 0:
                canvas = Image.new('RGB', (fragment.width, fragment.height + footer_px), 'white')
                canvas.paste(fragment, (0, 0))
                return [canvas]
            return [fragment]

        effective_footer_px = footer_px if footer_px > 0 else 1
        return slice_exact_pages(
            fragment,
            slice_mm,
            dpi,
            footer_px=effective_footer_px,
            smart=True,
            window_px=window_px,
            min_blank_run=MARKDOWN_DEFAULT_MIN_BLANK_RUN,
            row_blank=None,
            row_heavy=None
        )

    if not forced_breaks_px:
        pages = _slice_fragment(image)
        return pages if pages else [image]

    pages: List[Image.Image] = []
    start = 0
    height = image.height
    for raw_break in sorted(set(forced_breaks_px)):
        break_y = int(raw_break)
        if break_y <= start or break_y >= height:
            continue
        fragment = image.crop((0, start, image.width, break_y))
        pages.extend(_slice_fragment(fragment))
        start = break_y

    if start < height:
        fragment = image.crop((0, start, image.width, height))
        pages.extend(_slice_fragment(fragment))

    return pages if pages else [image]


def draw_page_number_footer(image, index, total, footer_mm, diameter_mm, dpi,
                            draw_circle, include_total, font_path):
    if total <= 0:
        return image

    diameter_px = max(1, mm_to_pixels(diameter_mm, dpi))
    footer_px = max(1, mm_to_pixels(footer_mm, dpi))

    draw = ImageDraw.Draw(image)
    number_text = f"{index}/{total}" if include_total and total > 1 else str(index)

    try:
        if font_path:
            font_size = max(8, min(diameter_px - 2, int(diameter_px * 0.85)))
            font = ImageFont.truetype(font_path, font_size)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), number_text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    cx = image.width // 2
    circle_height = diameter_px
    baseline = image.height - footer_px // 2
    cy = max(circle_height // 2 + text_h // 2 + 1, baseline)
    cy = min(image.height - circle_height // 2 - 1, cy)

    if draw_circle:
        if include_total and total > 1:
            circle_width = max(diameter_px, text_w + max(6, diameter_px // 2))
        else:
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

    return image


def build_row_blank_map(image: Image.Image, white_threshold: int = 250, max_ink_frac: float = 0.01, downsample_x: int = 4) -> List[bool]:
    gray = image.convert('L')
    width, height = gray.size
    if downsample_x > 1:
        ds_width = max(1, width // downsample_x)
        gray = gray.resize((ds_width, height), resample=Image.BOX)
    else:
        ds_width = width

    data = gray.tobytes()
    stride = ds_width
    allowance = max(1, int(stride * max_ink_frac))

    row_blank: List[bool] = []
    offset = 0
    for _ in range(height):
        ink = 0
        for x in range(stride):
            if data[offset + x] < white_threshold:
                ink += 1
        row_blank.append(ink <= allowance)
        offset += stride

    return row_blank


def compute_row_stats(image: Image.Image, white_threshold: int = 250, max_ink_frac: float = 0.01, downsample_x: int = 4) -> tuple[List[bool], List[bool]]:
    gray = image.convert('L')
    width, height = gray.size
    if downsample_x > 1:
        ds_width = max(1, width // downsample_x)
        gray = gray.resize((ds_width, height), resample=Image.BOX)
    else:
        ds_width = width

    data = gray.tobytes()
    stride = ds_width
    allowance = max(1, int(stride * max_ink_frac))
    heavy_threshold = max(stride - allowance, int(stride * 0.9))

    row_blank: List[bool] = []
    row_heavy: List[bool] = []
    offset = 0
    for _ in range(height):
        ink = 0
        for x in range(stride):
            if data[offset + x] < white_threshold:
                ink += 1
        row_blank.append(ink <= allowance)
        row_heavy.append(ink >= heavy_threshold)
        offset += stride

    return row_blank, row_heavy


def find_safe_cut_y_rows(row_blank: List[bool], approx_y: int, window_px: int, min_blank_run: int) -> int:
    h = len(row_blank)
    if h == 0:
        return approx_y
    approx_y = max(0, min(approx_y, h - 1))
    window_px = max(0, window_px)
    min_blank_run = max(1, min_blank_run)

    top = max(0, approx_y - window_px)
    y = approx_y
    while y >= top:
        y0 = y - (min_blank_run // 2)
        y1 = y0 + min_blank_run
        if y0 >= 0 and y1 <= h and all(row_blank[k] for k in range(y0, y1)):
            return y1
        y -= 1

    bot = min(h, approx_y + window_px)
    y = approx_y
    while y < bot:
        y0 = y - (min_blank_run // 2)
        y1 = y0 + min_blank_run
        if y0 >= 0 and y1 <= h and all(row_blank[k] for k in range(y0, y1)):
            return y0
        y += 1

    return approx_y


def find_table_separator_row(row_heavy: List[bool], approx_y: int, window_px: int) -> Optional[int]:
    h = len(row_heavy)
    if h == 0:
        return None
    approx_y = max(0, min(approx_y, h - 1))
    window_px = max(0, window_px)

    top = max(0, approx_y - window_px)
    bot = min(h, approx_y + window_px)

    for direction in (1, -1):
        y = approx_y
        while top <= y < bot:
            if row_heavy[y]:
                return min(h, y + 1)
            y += direction

    return None


def slice_exact_pages(image: Image.Image, mm_height: float, dpi: int, footer_px: int = 0,
                      smart: bool = True, window_px: int = 0, min_blank_run: int = 4,
                      row_blank: Optional[List[bool]] = None,
                      row_heavy: Optional[List[bool]] = None) -> List[Image.Image]:
    if mm_height <= 0:
        return [image]
    page_px = int(round(mm_height / 25.4 * dpi))
    if page_px <= 0:
        return [image]

    content_px = max(page_px - footer_px, 1)
    pages: List[Image.Image] = []

    effective_row_blank: Optional[List[bool]] = None
    effective_row_heavy: Optional[List[bool]] = None
    if smart:
        if row_blank is not None and row_heavy is not None:
            effective_row_blank = row_blank
            effective_row_heavy = row_heavy
        else:
            effective_row_blank, effective_row_heavy = compute_row_stats(image, white_threshold=250, max_ink_frac=0.01, downsample_x=4)

    if effective_row_blank is not None:
        try:
            last_ink = max(i for i, b in enumerate(effective_row_blank) if not b)
        except ValueError:
            last_ink = -1
    else:
        last_ink = image.height - 1
    effective_total = max(0, last_ink + 1)

    total = effective_total
    y = 0
    window_px = max(0, min(window_px, max(1, content_px - 1)))

    while True:
        if y >= total:
            if not pages and total == 0:
                pages.append(Image.new('RGB', (image.width, page_px), (255, 255, 255)))
            break

        target_cut = min(y + content_px, total)
        remaining = total - y

        if remaining <= content_px:
            cut_y = total
        else:
            if smart and effective_row_blank is not None and window_px > 0:
                cut_y = find_safe_cut_y_rows(effective_row_blank, target_cut, window_px, max(1, min_blank_run))
                if effective_row_heavy is not None and cut_y == target_cut:
                    table_cut = find_table_separator_row(effective_row_heavy, target_cut, window_px)
                    if table_cut is not None and table_cut > y:
                        cut_y = min(table_cut, total)
            else:
                cut_y = target_cut

        min_payload_px = max(int(content_px * 0.25), max(8, min_blank_run))
        if cut_y - y < min_payload_px and remaining > min_payload_px:
            cut_y = min(y + min_payload_px, total)

        if cut_y <= y:
            if y >= total:
                break
            cut_y = min(target_cut, y + max(1, min_blank_run))

        page = Image.new('RGB', (image.width, page_px), (255, 255, 255))
        if cut_y > y:
            page.paste(image.crop((0, y, image.width, cut_y)), (0, 0))
        pages.append(page)

        y = cut_y

    return pages

LABEL_SIZES = [(
    name,
    label_type_specs[name]['name'],
    (label_type_specs[name]['kind'] in (
        ROUND_DIE_CUT_LABEL,)),
    label_type_specs[name]['dots_printable'][0]
) for name in label_sizes]


@bp.route('/')
def index():
    RED_SUPPORT = current_app.config['PRINTER_MODEL'] in two_color_support
    return render_template('labeldesigner.html',
                           font_family_names=FONTS.fontlist(),
                           label_sizes=LABEL_SIZES,
                           red_support=RED_SUPPORT,
                           default_label_size=current_app.config['LABEL_DEFAULT_SIZE'],
                           default_font_size=current_app.config['LABEL_DEFAULT_FONT_SIZE'],
                           default_orientation=current_app.config['LABEL_DEFAULT_ORIENTATION'],
                           default_qr_size=current_app.config['LABEL_DEFAULT_QR_SIZE'],
                           default_image_mode=current_app.config['IMAGE_DEFAULT_MODE'],
                           default_bw_threshold=current_app.config['IMAGE_DEFAULT_BW_THRESHOLD'],
                           default_font_family=current_app.config['LABEL_DEFAULT_FONT_FAMILY'],
                           line_spacings=LINE_SPACINGS,
                           default_line_spacing=current_app.config['LABEL_DEFAULT_LINE_SPACING'],
                           default_dpi=DEFAULT_DPI,
                           default_margin_top=current_app.config['LABEL_DEFAULT_MARGIN_TOP'],
                           default_margin_bottom=current_app.config['LABEL_DEFAULT_MARGIN_BOTTOM'],
                           default_margin_left=current_app.config['LABEL_DEFAULT_MARGIN_LEFT'],
                           default_margin_right=current_app.config['LABEL_DEFAULT_MARGIN_RIGHT']
                           )


@bp.route('/api/font/styles', methods=['POST', 'GET'])
def get_font_styles():
    font = request.values.get(
        'font', current_app.config['LABEL_DEFAULT_FONT_FAMILY'])
    return FONTS.fonts[font]


@bp.route('/api/preview', methods=['POST', 'GET'])
def get_preview_from_image():
    label = create_label_from_request(request)
    labels = getattr(label, '_markdown_labels', None)
    label_list = labels if labels else [label]
    images = [lbl.generate() for lbl in label_list]

    return_format = request.values.get('return_format', 'png')

    if return_format == 'base64':
        import base64
        pages = [base64.b64encode(image_to_png_bytes(img)).decode('ascii') for img in images]
        if len(pages) == 1:
            response = make_response(pages[0])
            response.headers.set('Content-type', 'text/plain')
            return response
        return jsonify({'pages': pages})
    else:
        response = make_response(image_to_png_bytes(images[0]))
        response.headers.set('Content-type', 'image/png')
        return response


@bp.route('/api/markdown/preview', methods=['POST'])
def markdown_preview_api():
    payload = request.get_json(force=True, silent=True)
    if payload is None:
        return jsonify({'error': 'Invalid or missing JSON payload'}), 400

    try:
        context = build_label_context_from_json(payload)
        label = create_label_from_context(context)
        labels = getattr(label, '_markdown_labels', None)
        label_list = labels if labels else [label]
        images = [lbl.generate() for lbl in label_list]
        pages = [base64.b64encode(image_to_png_bytes(img)).decode('ascii') for img in images]
        return jsonify({'pages': pages})
    except Exception as exc:
        current_app.logger.error('Markdown preview failed: %s', exc)
        return jsonify({'error': str(exc)}), 400


@bp.route('/api/print', methods=['POST', 'GET'])
def print_text():
    """
    API to print a label

    returns: JSON

    Ideas for additional URL parameters:
    - alignment
    """

    return_dict = {'success': False}

    try:
        printer = create_printer_from_request(request)
        label = create_label_from_request(request)
        print_count = int(request.values.get('print_count', 1))
        cut_once = int(request.values.get('cut_once', 0)) == 1
    except Exception as e:
        return_dict['message'] = str(e)
        current_app.logger.error('Exception happened: %s', e)
        return return_dict

    markdown_sequence = getattr(label, '_markdown_labels', None)
    if markdown_sequence:
        printer.add_label_sequence(markdown_sequence, print_count, cut_once)
    else:
        printer.add_label_to_queue(label, print_count, cut_once)

    try:
        printer.process_queue()
    except Exception as e:
        return_dict['message'] = str(e)
        current_app.logger.error('Exception happened: %s', e)
        return return_dict

    return_dict['success'] = True
    return return_dict


@bp.route('/api/markdown/print', methods=['POST'])
def markdown_print_api():
    payload = request.get_json(force=True, silent=True)
    if payload is None:
        return jsonify({'success': False, 'error': 'Invalid or missing JSON payload'}), 400

    try:
        context = build_label_context_from_json(payload)
        label = create_label_from_context(context)
        print_count = int(payload.get('print_count', 1))
        cut_once = bool(payload.get('cut_once', False))

        printer = create_printer_queue(context['label_size'])
        markdown_sequence = getattr(label, '_markdown_labels', None)
        if markdown_sequence:
            printer.add_label_sequence(markdown_sequence, print_count, cut_once)
        else:
            printer.add_label_to_queue(label, print_count, cut_once)

        printer.process_queue()
        return jsonify({'success': True})
    except Exception as exc:
        current_app.logger.error('Markdown print failed: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 400


def create_printer_from_request(request):
    d = request.values
    context = {
        'label_size': d.get('label_size', '62')
    }

    return create_printer_queue(context['label_size'])


def create_printer_queue(label_size):
    return PrinterQueue(
        model=current_app.config['PRINTER_MODEL'],
        device_specifier=current_app.config['PRINTER_PRINTER'],
        label_size=label_size
    )


def build_label_context_from_request(request):
    d = request.values
    label_size = d.get('label_size', current_app.config['LABEL_DEFAULT_SIZE'])
    print_type = str(d.get('print_type', 'text')).lower()
    orientation = str(d.get('orientation', current_app.config['LABEL_DEFAULT_ORIENTATION'])).lower()

    def to_float(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def to_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    spec = get_label_spec(label_size)
    width, height = spec['dots_printable']
    context = {
        'label_size': label_size,
        'print_type': print_type,
        'label_orientation': orientation,
        'kind': spec['kind'],
        'margin_top_raw': to_float(d.get('margin_top', None), current_app.config['LABEL_DEFAULT_MARGIN_TOP']),
        'margin_bottom_raw': to_float(d.get('margin_bottom', None), current_app.config['LABEL_DEFAULT_MARGIN_BOTTOM']),
        'margin_left_raw': to_float(d.get('margin_left', None), current_app.config['LABEL_DEFAULT_MARGIN_LEFT']),
        'margin_right_raw': to_float(d.get('margin_right', None), current_app.config['LABEL_DEFAULT_MARGIN_RIGHT']),
        'text': d.get('text', None),
        'align': d.get('align', 'center'),
        'qrcode_size': to_int(d.get('qrcode_size', None), 10),
        'qrcode_correction': d.get('qrcode_correction', 'L'),
        'image_mode': d.get('image_mode', "grayscale"),
        'image_bw_threshold': to_int(d.get('image_bw_threshold', None), 70),
        'font_size': to_int(d.get('font_size', None), current_app.config['LABEL_DEFAULT_FONT_SIZE']),
        'line_spacing': to_int(d.get('line_spacing', None), current_app.config['LABEL_DEFAULT_LINE_SPACING']),
        'font_family': d.get('font_family', current_app.config['LABEL_DEFAULT_FONT_FAMILY']),
        'font_style': d.get('font_style', current_app.config['LABEL_DEFAULT_FONT_STYLE']),
        'print_color': d.get('print_color', 'black'),
        'markdown_paged': int(d.get('markdown_paged', 0)) == 1,
        'markdown_slice_mm': to_float(d.get('markdown_slice_mm', None), 0),
        'markdown_footer_mm': to_float(d.get('markdown_footer_mm', None), MARKDOWN_DEFAULT_FOOTER_MM),
        'markdown_page_numbers': int(d.get('markdown_page_numbers', 1)) == 1,
        'markdown_page_circle': int(d.get('markdown_page_circle', 1)) == 1,
        'markdown_page_number_mm': to_float(d.get('markdown_page_number_mm', None), MARKDOWN_DEFAULT_PAGE_NUMBER_MM),
        'markdown_page_count': int(d.get('markdown_page_count', 1)) == 1,
        'head_width_px': width
    }

    if print_type == 'markdown' and orientation == 'rotated':
        context['markdown_paged'] = True
        slice_mm = context['markdown_slice_mm']
        if slice_mm <= 0:
            context['markdown_slice_mm'] = MARKDOWN_DEFAULT_SLICE_MM
    elif print_type == 'markdown':
        context['markdown_page_numbers'] = False
        context['markdown_page_count'] = False
        context['markdown_page_circle'] = False

    return context


def build_label_context_from_json(data):
    cfg = current_app.config
    label_size = str(data.get('label_size', cfg['LABEL_DEFAULT_SIZE']))
    orientation = str(data.get('orientation', cfg['LABEL_DEFAULT_ORIENTATION'])).lower()
    text = data.get('markdown', data.get('text', '')) or ''

    spec = get_label_spec(label_size)
    width, height = spec['dots_printable']

    def margin_value(name):
        if f'{name}_mm' in data:
            return float(data[f'{name}_mm']) * 10.0
        margins = data.get('margins') or {}
        if isinstance(margins, dict) and f'{name}_mm' in margins:
            return float(margins[f'{name}_mm']) * 10.0
        if f'{name}' in data:
            return float(data[f'{name}'])
        return float(cfg[f'LABEL_DEFAULT_MARGIN_{name.upper()}'])

    context = {
        'label_size': label_size,
        'print_type': 'markdown',
        'label_orientation': orientation,
        'kind': spec['kind'],
        'margin_top_raw': margin_value('top'),
        'margin_bottom_raw': margin_value('bottom'),
        'margin_left_raw': margin_value('left'),
        'margin_right_raw': margin_value('right'),
        'text': text,
        'align': data.get('align', 'center'),
        'qrcode_size': int(data.get('qrcode_size', 10)),
        'qrcode_correction': data.get('qrcode_correction', 'L'),
        'image_mode': data.get('image_mode', 'grayscale'),
        'image_bw_threshold': int(data.get('image_bw_threshold', 70)),
        'font_size': int(data.get('font_size', cfg['LABEL_DEFAULT_FONT_SIZE'])),
        'line_spacing': int(data.get('line_spacing', cfg['LABEL_DEFAULT_LINE_SPACING'])),
        'font_family': data.get('font_family', cfg['LABEL_DEFAULT_FONT_FAMILY']),
        'font_style': data.get('font_style', cfg['LABEL_DEFAULT_FONT_STYLE']),
        'print_color': data.get('print_color', 'black'),
        'markdown_paged': bool(data.get('paged', data.get('slice_mm', 0) > 0)),
        'markdown_slice_mm': float(data.get('slice_mm', 0)),
        'markdown_footer_mm': float(data.get('footer_mm', MARKDOWN_DEFAULT_FOOTER_MM)),
        'markdown_page_numbers': bool(data.get('page_numbers', True)),
        'markdown_page_circle': bool(data.get('page_circle', True)),
        'markdown_page_number_mm': float(data.get('page_number_mm', MARKDOWN_DEFAULT_PAGE_NUMBER_MM)),
        'markdown_page_count': bool(data.get('page_count', True)),
        'head_width_px': width
    }

    if orientation == 'rotated':
        context['markdown_paged'] = True
        if context['markdown_slice_mm'] <= 0:
            context['markdown_slice_mm'] = MARKDOWN_DEFAULT_SLICE_MM
    else:
        context['markdown_page_numbers'] = False
        context['markdown_page_count'] = False
        context['markdown_page_circle'] = False

    return context


def create_label_from_context(context, image_file=None):
    def get_font_info(font_family_name, font_style_name):
        try:
            if font_family_name is None or font_style_name is None:
                font_family_name = current_app.config['LABEL_DEFAULT_FONT_FAMILY']
                font_style_name = current_app.config['LABEL_DEFAULT_FONT_STYLE']
            font_path = FONTS.fonts[font_family_name][font_style_name]
        except KeyError:
            raise LookupError("Couldn't find the font & style")
        return font_path, font_family_name, font_style_name

    def get_uploaded_image(image):
        try:
            name, ext = os.path.splitext(image.filename)
            if ext.lower() in ('.png', '.jpg', '.jpeg'):
                image = imgfile_to_image(image)
                if context['image_mode'] == 'grayscale':
                    return convert_image_to_grayscale(image)
                if context['image_mode'] == 'red_and_black':
                    return convert_image_to_red_and_black(image)
                if context['image_mode'] == 'colored':
                    return image
                return convert_image_to_bw(image, context['image_bw_threshold'])
            if ext.lower() == '.pdf':
                image = pdffile_to_image(image, DEFAULT_DPI)
                if context['image_mode'] == 'grayscale':
                    return convert_image_to_grayscale(image)
                return convert_image_to_bw(image, context['image_bw_threshold'])
            return None
        except AttributeError:
            return None

    def apply_image_mode(image):
        if image is None:
            return None
        if context['image_mode'] == 'grayscale':
            return convert_image_to_grayscale(image)
        if context['image_mode'] == 'red_and_black':
            return convert_image_to_red_and_black(image)
        if context['image_mode'] == 'colored':
            return image
        return convert_image_to_bw(image, context['image_bw_threshold'])

    def scale_image_to_box(image, max_width, max_height):
        if image is None:
            return None
        scale = 1.0
        if max_width > 0 and image.width > max_width:
            scale = min(scale, max_width / image.width)
        if max_height > 0 and image.height > max_height:
            scale = min(scale, max_height / image.height)
        if scale < 1.0:
            new_size = (
                max(1, int(image.width * scale)),
                max(1, int(image.height * scale))
            )
            return image.resize(new_size, resample=RESAMPLE_LANCZOS)
        return image

    def points_to_pixels(pt_value):
        try:
            pt = float(pt_value)
        except (TypeError, ValueError):
            pt = float(current_app.config['LABEL_DEFAULT_FONT_SIZE'])
        return int(round(pt * DEFAULT_DPI / 72.0))

    def margin_in_pixels(raw_value, default_config_key):
        if raw_value is None:
            raw_value = current_app.config[default_config_key]
        try:
            mm = float(raw_value) / 10.0
        except (TypeError, ValueError):
            fallback = current_app.config[default_config_key]
            try:
                mm = float(fallback) / 10.0
            except (TypeError, ValueError):
                mm = 0.0
        return int(round(mm * DEFAULT_DPI / 25.4))

    print_type = str(context.get('print_type', 'text')).lower()
    if print_type == 'text':
        label_content = LabelContent.TEXT_ONLY
    elif print_type == 'qrcode':
        label_content = LabelContent.QRCODE_ONLY
    elif print_type == 'qrcode_text':
        label_content = LabelContent.TEXT_QRCODE
    elif print_type == 'markdown':
        label_content = LabelContent.MARKDOWN_IMAGE
    else:
        image_mode = str(context.get('image_mode', 'bw')).lower()
        if image_mode == 'grayscale':
            label_content = LabelContent.IMAGE_GRAYSCALE
        elif image_mode == 'red_black':
            label_content = LabelContent.IMAGE_RED_BLACK
        elif image_mode == 'colored':
            label_content = LabelContent.IMAGE_COLORED
        else:
            label_content = LabelContent.IMAGE_BW

    orientation_value = str(context.get('label_orientation', 'standard')).lower()
    label_orientation = LabelOrientation.ROTATED if orientation_value == 'rotated' else LabelOrientation.STANDARD

    kind = context.get('kind', ENDLESS_LABEL)
    is_endless = kind == ENDLESS_LABEL
    if is_endless:
        label_type = LabelType.ENDLESS_LABEL
    elif kind == DIE_CUT_LABEL:
        label_type = LabelType.DIE_CUT_LABEL
    else:
        label_type = LabelType.ROUND_DIE_CUT_LABEL

    standard_width_px, standard_height_px = get_label_dimensions(context['label_size'])
    if standard_height_px > standard_width_px:
        standard_width_px, standard_height_px = standard_height_px, standard_width_px

    if label_orientation == LabelOrientation.ROTATED:
        label_height_px = max(standard_width_px, 1)
        label_width_px = standard_height_px if standard_height_px > 0 else standard_width_px
    else:
        label_width_px = standard_width_px
        label_height_px = standard_height_px

    margin_left_px = margin_in_pixels(context.get('margin_left_raw'), 'LABEL_DEFAULT_MARGIN_LEFT')
    margin_right_px = margin_in_pixels(context.get('margin_right_raw'), 'LABEL_DEFAULT_MARGIN_RIGHT')
    margin_top_px = margin_in_pixels(context.get('margin_top_raw'), 'LABEL_DEFAULT_MARGIN_TOP')
    margin_bottom_px = margin_in_pixels(context.get('margin_bottom_raw'), 'LABEL_DEFAULT_MARGIN_BOTTOM')

    content_width_standard_px = max(standard_width_px - margin_left_px - margin_right_px, 1)
    content_height_standard_px = max(standard_height_px - margin_top_px - margin_bottom_px, 1)

    if label_orientation == LabelOrientation.STANDARD:
        content_width_px = content_width_standard_px
        content_height_limit_px = 0 if is_endless else content_height_standard_px
    else:
        if standard_height_px > 0:
            content_width_px = max(content_height_standard_px, 1)
        else:
            content_width_px = content_width_standard_px
        content_height_limit_px = 0 if is_endless else content_width_standard_px

    font_path, resolved_family, resolved_style = get_font_info(context.get('font_family'), context.get('font_style'))
    font_map = FONTS.fonts.get(resolved_family, {})
    font_size_pt = float(context.get('font_size', current_app.config['LABEL_DEFAULT_FONT_SIZE']))
    font_size_px = points_to_pixels(font_size_pt)

    markdown_page_images = None
    generated_image: Optional[Image.Image] = None

    if label_content == LabelContent.MARKDOWN_IMAGE:
        line_spacing = int(context.get('line_spacing', current_app.config['LABEL_DEFAULT_LINE_SPACING']))
        base_font_pt = max(6, font_size_pt)

        slice_mm_config = float(context.get('markdown_slice_mm', 0) or 0)
        paginate = bool(context.get('markdown_paged')) and slice_mm_config > 0

        if label_orientation == LabelOrientation.ROTATED:
            if slice_mm_config <= 0:
                slice_mm_config = MARKDOWN_DEFAULT_SLICE_MM
            paginate = True

        if label_orientation == LabelOrientation.ROTATED:
            markdown_render_width_px = max(content_width_standard_px, context.get('head_width_px', 0) or 0, 10)
        else:
            markdown_render_width_px = max(content_width_px, 10)
        render_width_px = markdown_render_width_px

        footer_mm = float(context.get('markdown_footer_mm', MARKDOWN_DEFAULT_FOOTER_MM))
        page_number_mm = float(context.get('markdown_page_number_mm', MARKDOWN_DEFAULT_PAGE_NUMBER_MM))

        base_image, forced_page_breaks = render_markdown_to_image(
            context.get('text', '') or '',
            content_width_px=render_width_px,
            dpi=DEFAULT_DPI,
            base_font_pt=base_font_pt,
            line_spacing=line_spacing,
            font_map=font_map,
            preferred_style=resolved_style,
            allow_pagebreaks=paginate
        )

        rotate_for_slicing = label_orientation == LabelOrientation.ROTATED

        if context.get('markdown_page_numbers'):
            footer_mm = max(footer_mm, page_number_mm)

        slice_mm = slice_mm_config if paginate else 0
        context['markdown_paged'] = paginate
        context['markdown_slice_mm'] = slice_mm_config

        if slice_mm <= 0 and footer_mm > 0:
            footer_px = mm_to_pixels(footer_mm, DEFAULT_DPI)
            if footer_px > 0:
                extended = Image.new('RGB', (base_image.width, base_image.height + footer_px), 'white')
                extended.paste(base_image, (0, 0))
                base_image = extended

        forced_breaks_px = forced_page_breaks if paginate else None
        pages = slice_markdown_pages(base_image, slice_mm, footer_mm, DEFAULT_DPI, forced_breaks_px=forced_breaks_px)

        processed_pages = []
        total_pages = len(pages)
        numbering_enabled = bool(context.get('markdown_page_numbers'))
        draw_circle = bool(context.get('markdown_page_circle', True))
        include_total = bool(context.get('markdown_page_count', True))

        for idx, page in enumerate(pages, start=1):
            scaled = scale_image_to_box(page, render_width_px, content_height_limit_px if content_height_limit_px > 0 else 0)
            if numbering_enabled:
                draw_page_number_footer(
                    scaled,
                    idx,
                    total_pages,
                    footer_mm,
                    page_number_mm,
                    DEFAULT_DPI,
                    draw_circle,
                    include_total,
                    font_path
                )
            if rotate_for_slicing:
                scaled = scaled.transpose(Image.ROTATE_90)
            processed_pages.append(apply_image_mode(scaled))

        markdown_page_images = processed_pages if processed_pages else [apply_image_mode(base_image)]
        generated_image = markdown_page_images[0]
    elif label_content in (
        LabelContent.IMAGE_BW,
        LabelContent.IMAGE_GRAYSCALE,
        LabelContent.IMAGE_RED_BLACK,
        LabelContent.IMAGE_COLORED,
    ):
        uploaded = get_uploaded_image(image_file)
        processed_image = apply_image_mode(uploaded)
        if processed_image is None:
            raise ValueError('Empty image data')

        if not is_endless or content_height_limit_px > 0:
            processed_image = scale_image_to_box(
                processed_image,
                content_width_px,
                content_height_limit_px if content_height_limit_px > 0 else 0
            )
        else:
            if content_width_px > 0 and processed_image.width > content_width_px:
                scale = content_width_px / processed_image.width
                new_size = (
                    int(round(processed_image.width * scale)),
                    int(round(processed_image.height * scale))
                )
                processed_image = processed_image.resize(new_size, resample=RESAMPLE_LANCZOS)

            def _crop_white(img):
                bbox = img.convert('L').point(lambda p: 0 if p >= 250 else 255, '1').getbbox()
                return img.crop(bbox) if bbox else img

            processed_image = _crop_white(processed_image)
            canvas_width = int(content_width_px)
            canvas = Image.new('RGB', (canvas_width, processed_image.height), 'white')
            x = max(0, (canvas_width - processed_image.width) // 2)
            canvas.paste(processed_image, (x, 0))
            processed_image = canvas

        generated_image = processed_image

    base_kwargs = dict(
        width=label_width_px,
        height=label_height_px,
        label_content=label_content,
        label_orientation=label_orientation,
        label_type=label_type,
        label_margin=(
            margin_left_px,
            margin_right_px,
            margin_top_px,
            margin_bottom_px
        ),
        fore_color=(255, 0, 0) if 'red' in context['label_size'] and context.get('print_color') == 'red' else (0, 0, 0),
        text=context.get('text'),
        text_align=context.get('align', 'center'),
        qr_size=context.get('qrcode_size'),
        qr_correction=context.get('qrcode_correction'),
        font_path=font_path,
        font_size=max(6, font_size_px),
        line_spacing=int(context.get('line_spacing', current_app.config['LABEL_DEFAULT_LINE_SPACING']))
    )

    if label_content == LabelContent.MARKDOWN_IMAGE:
        labels_sequence = []
        for page_image in markdown_page_images:
            kwargs = dict(base_kwargs)
            kwargs['image'] = page_image
            labels_sequence.append(SimpleLabel(**kwargs))

        for lbl in labels_sequence:
            lbl._markdown_labels = labels_sequence

        return labels_sequence[0]

    kwargs = dict(base_kwargs)
    kwargs['image'] = generated_image
    label = SimpleLabel(**kwargs)
    label._markdown_labels = None
    return label


def create_label_from_request(request):
    context = build_label_context_from_request(request)
    return create_label_from_context(context, image_file=request.files.get('image', None))
