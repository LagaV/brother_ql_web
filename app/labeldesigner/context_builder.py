"""Build label context from HTTP requests and JSON payloads."""

from flask import current_app
from .dimensions import get_label_spec

MARKDOWN_DEFAULT_PAGE_NUMBER_MM = 4.0
MARKDOWN_DEFAULT_SLICE_MM = 90.0
MARKDOWN_MIN_PAGE_NUMBER_FOOTER_MM = 6.0


def build_label_context_from_request(request):
    """Build label context dictionary from Flask request."""
    d = request.values
    current_app.logger.info('[build_context] Received params: %s', dict(d))
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
        'image_rotate_90': int(d.get('image_rotate_90', 0)) == 1,
        'image_stretch_length': int(d.get('image_stretch_length', 0)) == 1,
        'image_crop_left': to_float(d.get('image_crop_left', None), 0),
        'image_crop_right': to_float(d.get('image_crop_right', None), 0),
        'image_crop_top': to_float(d.get('image_crop_top', None), 0),
        'image_crop_bottom': to_float(d.get('image_crop_bottom', None), 0),
        'font_size': to_int(d.get('font_size', None), current_app.config['LABEL_DEFAULT_FONT_SIZE']),
        'line_spacing': to_int(d.get('line_spacing', None), current_app.config['LABEL_DEFAULT_LINE_SPACING']),
        'font_family': d.get('font_family', current_app.config['LABEL_DEFAULT_FONT_FAMILY']),
        'font_style': d.get('font_style', current_app.config['LABEL_DEFAULT_FONT_STYLE']),
        'print_color': d.get('print_color', 'black'),
        'markdown_paged': int(d.get('markdown_paged', 0)) == 1,
        'markdown_slice_mm': to_float(d.get('markdown_slice_mm', None), 0),
        'markdown_page_numbers': int(d.get('markdown_page_numbers', 1)) == 1,
        'markdown_page_circle': int(d.get('markdown_page_circle', 1)) == 1,
        'markdown_page_number_mm': to_float(d.get('markdown_page_number_mm', None), MARKDOWN_DEFAULT_PAGE_NUMBER_MM),
        'markdown_page_count': int(d.get('markdown_page_count', 1)) == 1,
        'markdown_page': to_int(d.get('markdown_page', None), 1) if d.get('markdown_page') else None,
        'head_width_px': width,
        'no_crop': int(d.get('no_crop', 0)) == 1,
        'label_width': to_int(d.get('label_width', None), 0),
        'label_height': to_int(d.get('label_height', None), 0),
        # Border areas
        'enable_left_area': int(d.get('enable_left_area', 0)) == 1,
        'enable_right_area': int(d.get('enable_right_area', 0)) == 1,
        'enable_top_area': int(d.get('enable_top_area', 0)) == 1,
        'enable_bottom_area': int(d.get('enable_bottom_area', 0)) == 1,
        'enable_left_bar': int(d.get('enable_left_bar', 0)) == 1,
        'enable_left_text': int(d.get('enable_left_text', 0)) == 1,
        'enable_right_bar': int(d.get('enable_right_bar', 0)) == 1,
        'enable_right_text': int(d.get('enable_right_text', 0)) == 1,
        'enable_top_bar': int(d.get('enable_top_bar', 0)) == 1,
        'enable_top_text': int(d.get('enable_top_text', 0)) == 1,
        'enable_bottom_bar': int(d.get('enable_bottom_bar', 0)) == 1,
        'enable_bottom_text': int(d.get('enable_bottom_text', 0)) == 1,
        'left_area_mm': to_float(d.get('left_area_mm', None), 0),
        'right_area_mm': to_float(d.get('right_area_mm', None), 0),
        'top_area_mm': to_float(d.get('top_area_mm', None), 0),
        'bottom_area_mm': to_float(d.get('bottom_area_mm', None), 0),
        'left_bar_mm': to_float(d.get('left_bar_mm', None), 0),
        'right_bar_mm': to_float(d.get('right_bar_mm', None), 0),
        'top_bar_mm': to_float(d.get('top_bar_mm', None), 0),
        'bottom_bar_mm': to_float(d.get('bottom_bar_mm', None), 0),
        'left_bar_color': d.get('left_bar_color', 'black'),
        'right_bar_color': d.get('right_bar_color', 'black'),
        'top_bar_color': d.get('top_bar_color', 'black'),
        'bottom_bar_color': d.get('bottom_bar_color', 'black'),
        'left_bar_text_size_pt': to_float(d.get('left_bar_text_size_pt', None), 0),
        'right_bar_text_size_pt': to_float(d.get('right_bar_text_size_pt', None), 0),
        'top_bar_text_size_pt': to_float(d.get('top_bar_text_size_pt', None), 0),
        'bottom_bar_text_size_pt': to_float(d.get('bottom_bar_text_size_pt', None), 0),
        'top_text_size_pt': to_float(d.get('top_text_size_pt', None), 0),
        'bottom_text_size_pt': to_float(d.get('bottom_text_size_pt', None), 0),
        'left_bar_text': d.get('left_bar_text', ''),
        'right_bar_text': d.get('right_bar_text', ''),
        'left_text': d.get('left_text', ''),
        'right_text': d.get('right_text', ''),
        'top_bar_text': d.get('top_bar_text', ''),
        'bottom_bar_text': d.get('bottom_bar_text', ''),
        'top_text': d.get('top_text', ''),
        'bottom_text': d.get('bottom_text', ''),
        'top_divider': int(d.get('top_divider', 0)) == 1,
        'bottom_divider': int(d.get('bottom_divider', 0)) == 1,
        'bottom_show_page_numbers': int(d.get('bottom_show_page_numbers', 0)) == 1,
        'bottom_page_number_mm': to_float(d.get('bottom_page_number_mm', None), 4),
        'divider_distance_px': to_int(d.get('divider_distance_px', None), 1),
        'pdf_page': to_int(d.get('pdf_page', None), 1),
        'page_from': to_int(d.get('page_from', None), 0) if d.get('page_from') else None,
        'page_to': to_int(d.get('page_to', None), 0) if d.get('page_to') else None
    }

    if print_type == 'markdown' and orientation == 'rotated':
        context['markdown_paged'] = True
        if context['markdown_slice_mm'] <= 0:
            context['markdown_slice_mm'] = MARKDOWN_DEFAULT_SLICE_MM

    return context


def build_label_context_from_json(data):
    """Build label context dictionary from JSON payload."""
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
        'markdown_page_numbers': bool(data.get('page_numbers', True)),
        'markdown_page_circle': bool(data.get('page_circle', True)),
        'markdown_page_number_mm': float(data.get('page_number_mm', MARKDOWN_DEFAULT_PAGE_NUMBER_MM)),
        'markdown_page_count': bool(data.get('page_count', True)),
        'markdown_page': int(data.get('markdown_page', 1)) if data.get('markdown_page') else None,
        'head_width_px': width,
    }

    if orientation == 'rotated':
        context['markdown_paged'] = True
        if context.get('markdown_slice_mm', 0) <= 0:
            context['markdown_slice_mm'] = MARKDOWN_DEFAULT_SLICE_MM

    return context