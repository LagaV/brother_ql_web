import os
import json

import base64

from flask import current_app, render_template, request, make_response, jsonify

from brother_ql.devicedependent import label_type_specs, label_sizes, two_color_support
from brother_ql.devicedependent import ENDLESS_LABEL, DIE_CUT_LABEL, ROUND_DIE_CUT_LABEL

from . import bp
from app.utils import convert_image_to_bw, convert_image_to_grayscale, convert_image_to_red_and_black, pdffile_to_image, pdffile_to_images, imgfile_to_image, image_to_png_bytes
from app import FONTS
from app.markdown_render import render_markdown_to_image

from .label import SimpleLabel, LabelContent, LabelOrientation, LabelType
from .printer import PrinterQueue
from .remote_printer import RemotePrinterQueue
from PIL import Image, ImageDraw, ImageFont
from typing import Dict, List, Optional, Tuple

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
MARKDOWN_MIN_PAGE_NUMBER_FOOTER_MM = 6.0


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


def slice_markdown_pages(image, slice_mm, footer_mm, dpi,
                         forced_breaks_px: Optional[List[int]] = None,
                         table_boundaries_px: Optional[List[int]] = None,
                         boundary_types: Optional[Dict[int, str]] = None):
    footer_px = mm_to_pixels(footer_mm, dpi)
    window_px = mm_to_pixels(MARKDOWN_DEFAULT_SLICE_WINDOW_MM, dpi)

    def _slice_fragment(fragment: Image.Image, start_offset_px: int, carry_boundary: bool) -> Tuple[List[Tuple[Image.Image, bool, bool, int, int]], bool]:
        if fragment.height <= 0:
            return [], carry_boundary

        if slice_mm <= 0:
            page = fragment
            if footer_px > 0:
                canvas = Image.new('RGB', (fragment.width, fragment.height + footer_px), 'white')
                canvas.paste(fragment, (0, 0))
                page = canvas
            return [(page, carry_boundary, False, fragment.height, 0)], False

        effective_footer_px = footer_px if footer_px > 0 else 1
        row_blank, row_heavy, row_density = compute_row_stats(
            fragment,
            white_threshold=250,
            max_ink_frac=0.01,
            downsample_x=4
        )
        local_boundaries: Optional[List[int]] = None
        if table_boundaries_px:
            local = [b - start_offset_px for b in table_boundaries_px
                     if start_offset_px < b <= start_offset_px + fragment.height]
            if local:
                local_boundaries = sorted(local)

        slices = slice_exact_pages(
            fragment,
            slice_mm,
            dpi,
            footer_px=effective_footer_px,
            smart=True,
            window_px=window_px,
            min_blank_run=MARKDOWN_DEFAULT_MIN_BLANK_RUN,
            row_blank=row_blank,
            row_heavy=row_heavy,
            row_density=row_density,
            table_boundaries=local_boundaries,
            start_offset_px=start_offset_px,
            boundary_types=boundary_types
        )

        pages_with_flags: List[Tuple[Image.Image, bool, bool, int, int]] = []
        current_carry = carry_boundary
        for page_image, top_flag, bottom_flag, content_height, border_overlap in slices:
            pages_with_flags.append((page_image, top_flag or current_carry, bottom_flag, content_height, border_overlap))
            current_carry = bottom_flag

        return pages_with_flags, current_carry

    fragments: List[Tuple[Image.Image, bool, bool, int, int]] = []
    start = 0
    height = image.height
    carry = False

    break_positions = sorted(set(forced_breaks_px)) if forced_breaks_px else []
    for raw_break in break_positions:
        break_y = int(raw_break)
        if break_y <= start or break_y >= height:
            continue
        fragment = image.crop((0, start, image.width, break_y))
        fragment_slices, carry = _slice_fragment(fragment, start, carry)
        fragments.extend(fragment_slices)
        start = break_y

    if start < height or not fragments:
        fragment = image.crop((0, start, image.width, height))
        fragment_slices, carry = _slice_fragment(fragment, start, carry)
        fragments.extend(fragment_slices)

    if not fragments:
        return [image]

    final_pages: List[Image.Image] = []
    for page_img, top_boundary, bottom_boundary, content_height, border_overlap in fragments:
        # Border lines are now handled by including overlap in the crop
        if bottom_boundary or top_boundary:
            draw = ImageDraw.Draw(page_img)

            if bottom_boundary and content_height > 0 and border_overlap > 0:
                # The content includes border overlap at the bottom
                # content_height = actual_content + border_overlap (3 pixels)
                # The horizontal border line is in the overlap region
                # Erase only the vertical line extensions below the border
                # The border itself is ~1px thick, so erase from content_height-1
                erase_start = content_height - 1
                erase_start = max(0, min(erase_start, page_img.height))
                if erase_start < page_img.height:
                    draw.rectangle((0, erase_start, page_img.width, page_img.height), fill='white')

        final_pages.append(page_img)

    return final_pages


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


def compute_row_stats(image: Image.Image, white_threshold: int = 250, max_ink_frac: float = 0.01, downsample_x: int = 4) -> tuple[List[bool], List[bool], List[float]]:
    gray = image.convert('L')
    width, height = gray.size

    try:
        mask = gray.point(lambda p: 0 if p >= white_threshold else 255, mode='1')
        bbox = mask.getbbox()
    except Exception:
        bbox = None

    if bbox is not None:
        left, _, right, _ = bbox
        left = max(0, left)
        right = min(width, right)
        if right > left:
            gray = gray.crop((left, 0, right, height))
            width = right - left

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
    row_density: List[float] = []
    offset = 0
    for _ in range(height):
        ink = 0
        for x in range(stride):
            if data[offset + x] < white_threshold:
                ink += 1
        row_blank.append(ink <= allowance)
        row_heavy.append(ink >= heavy_threshold)
        row_density.append(ink / float(stride))
        offset += stride

    return row_blank, row_heavy, row_density


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


def find_previous_boundary(row_blank: List[bool],
                           row_heavy: Optional[List[bool]],
                           approx_y: int,
                           lower_bound: int,
                           min_blank_run: int) -> Optional[int]:
    if approx_y <= lower_bound:
        return None

    best: Optional[int] = None
    min_blank_run = max(1, min_blank_run)

    for y in range(approx_y - 1, lower_bound - 1, -1):
        if row_heavy is not None and y < len(row_heavy) and row_heavy[y]:
            return min(len(row_blank), y + 1)

        start = max(lower_bound, y - min_blank_run + 1)
        all_blank = True
        for k in range(start, y + 1):
            if k >= len(row_blank) or not row_blank[k]:
                all_blank = False
                break
        if all_blank:
            best = y + 1
            break

    return best


def slice_exact_pages(image: Image.Image, mm_height: float, dpi: int, footer_px: int = 0,
                      smart: bool = True, window_px: int = 0, min_blank_run: int = 4,
                      row_blank: Optional[List[bool]] = None,
                      row_heavy: Optional[List[bool]] = None,
                      row_density: Optional[List[float]] = None,
                      table_boundaries: Optional[List[int]] = None,
                      start_offset_px: int = 0,
                      boundary_types: Optional[Dict[int, str]] = None) -> List[tuple[Image.Image, bool, bool, int, int]]:
    if mm_height <= 0:
        return [(image, False, False, image.height, 0)]
    page_px = int(round(mm_height / 25.4 * dpi))
    if page_px <= 0:
        return [(image, False, False, image.height, 0)]

    content_px = max(page_px - footer_px, 1)
    pages: List[tuple[Image.Image, bool, bool, int, int]] = []

    effective_row_blank: Optional[List[bool]] = None
    effective_row_heavy: Optional[List[bool]] = None
    effective_row_density: Optional[List[float]] = row_density
    if smart:
        if row_blank is not None and row_heavy is not None and row_density is not None:
            effective_row_blank = row_blank
            effective_row_heavy = row_heavy
            effective_row_density = row_density
        else:
            effective_row_blank, effective_row_heavy, effective_row_density = compute_row_stats(image, white_threshold=250, max_ink_frac=0.01, downsample_x=4)

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

    boundary_idx = 0
    boundaries = table_boundaries or []

    last_boundary = False

    while True:
        if y >= total:
            if not pages and total == 0:
                blank = Image.new('RGB', (image.width, page_px), (255, 255, 255))
                pages.append((blank, last_boundary, False, 0, 0))
            break

        target_cut = min(y + content_px, total)
        remaining = total - y

        min_payload_px = max(int(content_px * 0.25), max(8, min_blank_run))
        used_boundary = False

        if remaining <= content_px:
            # Remaining content fits in one page, but still check for table boundaries
            cut_y = total
            if boundaries:
                candidate_idx = boundary_idx
                while candidate_idx < len(boundaries) and boundaries[candidate_idx] <= y:
                    candidate_idx += 1

                last_valid = None
                scan_idx = candidate_idx
                while scan_idx < len(boundaries) and boundaries[scan_idx] <= total:
                    last_valid = boundaries[scan_idx]
                    scan_idx += 1

                if last_valid is not None and last_valid > y:
                    cut_y = last_valid
                    used_boundary = True
                    boundary_idx = scan_idx
        else:
            candidate_idx = boundary_idx
            used_boundary = False
            if boundaries:
                while candidate_idx < len(boundaries) and boundaries[candidate_idx] <= y:
                    candidate_idx += 1

                last_valid = None
                upper_limit = min(y + content_px, total)
                scan_idx = candidate_idx
                while scan_idx < len(boundaries) and boundaries[scan_idx] < upper_limit:
                    last_valid = boundaries[scan_idx]
                    scan_idx += 1

                if last_valid is not None and last_valid > y:
                    cut_y = last_valid
                    used_boundary = True
                    boundary_idx = scan_idx

            if not used_boundary and smart and effective_row_blank is not None:
                window = window_px if window_px > 0 else content_px
                cut_y = find_safe_cut_y_rows(effective_row_blank, target_cut, window, max(1, min_blank_run))
                if effective_row_heavy is not None and cut_y == target_cut:
                    table_cut = find_table_separator_row(effective_row_heavy, target_cut, window)
                    if table_cut is not None and table_cut > y:
                        cut_y = min(table_cut, total)

                if cut_y == target_cut:
                    boundary = find_previous_boundary(effective_row_blank, effective_row_heavy, target_cut, y, max(1, min_blank_run))
                    if boundary is not None and boundary > y:
                        cut_y = min(boundary, total)
                if cut_y == target_cut and effective_row_density is not None:
                    window = window_px if window_px > 0 else content_px
                    search_lower = max(y, target_cut - window)
                    best_row = None
                    best_density = -1.0
                    for row in range(target_cut - 1, search_lower - 1, -1):
                        if row < 0 or row >= len(effective_row_density):
                            continue
                        density = effective_row_density[row]
                        if density > best_density:
                            best_density = density
                            best_row = row
                    if best_row is not None and best_density >= 0.95 and best_row + 1 > y:
                        cut_y = min(best_row + 1, total)
            elif not used_boundary:
                cut_y = target_cut

        if not used_boundary and cut_y - y < min_payload_px and remaining > min_payload_px:
            cut_y = min(y + min_payload_px, total)

        if cut_y <= y:
            if y >= total:
                break
            cut_y = min(target_cut, y + max(1, min_blank_run))

        # Skip tiny trailing slices (likely just whitespace)
        slice_height = cut_y - y
        min_meaningful_height = max(8, min_blank_run)
        if slice_height < min_meaningful_height and cut_y >= total:
            break

        # Check if this boundary should draw border lines
        # Only draw borders for 'row' boundaries (mid-table), not 'table_start' or 'table_end'
        boundary_used = used_boundary
        border_overlap = 0
        actual_content_height = cut_y - y
        if boundary_types and boundaries:
            # Find the global boundary position for this local cut
            global_cut_pos = start_offset_px + cut_y
            boundary_type = boundary_types.get(global_cut_pos, 'row')
            # Don't draw border if cutting at table start or end
            if boundary_type in ('table_start', 'table_end'):
                boundary_used = False
            elif boundary_type == 'row' and used_boundary:
                # For row boundaries, extend crop to include the border line
                # INNERGRID is 0.6pt ≈ 2.5px at 300dpi, use 3px to be safe
                border_overlap = 3

        page = Image.new('RGB', (image.width, page_px), (255, 255, 255))
        if cut_y > y:
            # Extend the crop to include border if needed
            crop_end = min(cut_y + border_overlap, total)
            crop_box = (0, y, image.width, crop_end)
            page.paste(image.crop(crop_box), (0, 0))

        content_height = actual_content_height + border_overlap
        current_app.logger.info('[slice-debug] slice result y=%s cut_y=%s boundary_used=%s crop_height=%s border_overlap=%s',
                                y, cut_y, boundary_used, content_height, border_overlap)
        pages.append((page, last_boundary, boundary_used, content_height, border_overlap))

        y = cut_y
        last_boundary = boundary_used

    return pages

LABEL_SIZES = [(
    name,
    label_type_specs[name]['name'],
    (label_type_specs[name]['kind'] in (
        ROUND_DIE_CUT_LABEL,)),
    label_type_specs[name]['dots_printable'][0]
) for name in label_sizes]


@bp.route('/printers')
def printers_page():
    """Printer management page"""
    return render_template('printers.html')


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
    font_family_name = request.values.get(
        'font', current_app.config['LABEL_DEFAULT_FONT_FAMILY'])
    
    # Debug: what font family is requested
    current_app.logger.info(f"[API_FONT_STYLES-DEBUG] Requested font family: {font_family_name}")

    # Implement robust font family lookup here, similar to get_font_info
    # to ensure we get the correct 'Noto Sans' map.
    if font_family_name == 'Noto':
        font_map_candidates = [
            FONTS.fonts.get('Noto Sans', {}),
            FONTS.fonts.get('Noto Serif', {}),
            FONTS.fonts.get('Noto', {}),
            FONTS.fonts.get('Noto Color Emoji', {}), # Add to candidates but will be filtered later
            FONTS.fonts.get('Noto Sans Symbols', {})  # Add to candidates but will be filtered later
        ]
    else:
        font_map_candidates = [FONTS.fonts.get(font_family_name, {})]

    selected_font_map = {}
    for candidate_map in font_map_candidates:
        if candidate_map:
            # Filter out symbol/emoji styles when returning to UI
            filtered_map = {style: path for style, path in candidate_map.items() 
                            if "symbols" not in style.lower() and "emoji" not in style.lower() 
                            and "symbols" not in path.lower() and "emoji" not in path.lower()}
            # If we find a good general text font family, use its styles and prioritize them
            if filtered_map:
                selected_font_map.update(filtered_map)

    # Ensure unique styles and sort them alphabetically, with 'Regular' always first if present
    unique_styles = sorted(list(selected_font_map.keys()), key=lambda x: (x != 'Regular', x.lower()))
    
    current_app.logger.info(f"[API_FONT_STYLES-DEBUG] Returning styles for '{font_family_name}': {unique_styles}")
    
    # Return just the keys (styles) for the dropdown, as expected by main.js
    return jsonify(unique_styles)


@bp.route('/api/preview', methods=['POST', 'GET'])
def get_preview_from_image():
    try:
        context = build_label_context_from_request(request)
        label = create_label_from_context(context, image_file=request.files.get('image', None))
        labels = getattr(label, '_markdown_labels', None) or getattr(label, '_pdf_page_labels', None)
        label_list = labels if labels else [label]
        images = [lbl.generate() for lbl in label_list]
        current_app.logger.info('[preview] Generated %d images, first image dimensions: %dx%d', len(images), images[0].width, images[0].height)

        # For rotated markdown previews, the images are already landscape (wide)
        # No need to rotate them - they're ready to display
        # (The original orientation is stored but we set it to STANDARD for printing)

        return_format = request.values.get('return_format', 'png')

        if return_format == 'base64':
            import base64
            pages = [base64.b64encode(image_to_png_bytes(img)).decode('ascii') for img in images]

            # Prepare response with source dimensions and PDF page info if available
            if len(pages) == 1:
                response_data = {'image': pages[0]}
                if 'source_width_mm' in context and 'source_height_mm' in context:
                    response_data['source_width_mm'] = context['source_width_mm']
                    response_data['source_height_mm'] = context['source_height_mm']
                if 'pdf_page_count' in context:
                    response_data['pdf_page_count'] = context['pdf_page_count']
                    response_data['pdf_current_page'] = context['pdf_current_page']
                    current_app.logger.info('[preview] Returning PDF metadata - page %d of %d',
                                           context['pdf_current_page'], context['pdf_page_count'])
                return jsonify(response_data)
            else:
                response_data = {'pages': pages}
                if 'source_width_mm' in context and 'source_height_mm' in context:
                    response_data['source_width_mm'] = context['source_width_mm']
                    response_data['source_height_mm'] = context['source_height_mm']
                if 'pdf_page_count' in context:
                    response_data['pdf_page_count'] = context['pdf_page_count']
                    response_data['pdf_current_page'] = context['pdf_current_page']
                return jsonify(response_data)
        else:
            response = make_response(image_to_png_bytes(images[0]))
            response.headers.set('Content-type', 'image/png')
            return response
    except ValueError as e:
        # Return empty response for image mode without uploaded file
        current_app.logger.info('Preview skipped: %s', str(e))
        if request.values.get('return_format') == 'base64':
            return jsonify({'image': None, 'error': str(e)})
        else:
            return jsonify({'error': str(e)}), 400
    except Exception as e:
        current_app.logger.error('Preview failed: %s', str(e), exc_info=True)
        if request.values.get('return_format') == 'base64':
            return jsonify({'error': str(e)})
        else:
            return jsonify({'error': str(e)}), 500


@bp.route('/api/markdown/preview', methods=['POST'])
def markdown_preview_api():
    payload = request.get_json(force=True, silent=True)
    if payload is None:
        return jsonify({'error': 'Invalid or missing JSON payload'}), 400

    try:
        context = build_label_context_from_json(payload)
        label = create_label_from_context(context)
        labels = getattr(label, '_markdown_labels', None) or getattr(label, '_pdf_page_labels', None)
        label_list = labels if labels else [label]
        images = [lbl.generate() for lbl in label_list]

        # For rotated markdown, images are already landscape - no rotation needed

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
        page_from = int(request.values.get('page_from', 0)) if request.values.get('page_from') else None
        page_to = int(request.values.get('page_to', 0)) if request.values.get('page_to') else None
        markdown_page = int(request.values.get('markdown_page', 0)) if request.values.get('markdown_page') else None
    except Exception as e:
        return_dict['message'] = str(e)
        current_app.logger.error('Exception happened: %s', e)
        return return_dict

    markdown_sequence = getattr(label, '_markdown_labels', None)
    pdf_sequence = getattr(label, '_pdf_page_labels', None)
    label_sequence = markdown_sequence if markdown_sequence else pdf_sequence

    if label_sequence:
        filtered_sequence = None

        # Filter by page range if specified (1-indexed)
        if page_from is not None or page_to is not None:
            if pdf_sequence:
                def within_pdf_range(lbl):
                    page_number = getattr(lbl, '_pdf_original_page_number', None)
                    if page_number is None:
                        return True
                    if page_from is not None and page_number < page_from:
                        return False
                    if page_to is not None and page_number > page_to:
                        return False
                    return True

                filtered_sequence = [lbl for lbl in pdf_sequence if within_pdf_range(lbl)]
                if not filtered_sequence:
                    message = f'No PDF pages found for range {page_from}-{page_to}' if page_to else f'No PDF pages found from {page_from} onwards'
                    return_dict['message'] = message
                    return return_dict

                page_numbers = [getattr(lbl, '_pdf_original_page_number', None) for lbl in filtered_sequence]
                current_app.logger.info('Printing PDF pages %s (requested range %s-%s)', page_numbers, page_from, page_to)
            else:
                total_pages = len(label_sequence)
                start = (page_from - 1) if page_from else 0
                end = page_to if page_to else total_pages
                # Clamp to valid range
                start = max(0, min(start, total_pages - 1))
                end = max(1, min(end, total_pages))
                if start < end:
                    filtered_sequence = label_sequence[start:end]
                    current_app.logger.info('Printing pages %d-%d of %d total pages', start + 1, end, total_pages)
                else:
                    return_dict['message'] = f'Invalid page range: {page_from}-{page_to}'
                    return return_dict
        else:
            if pdf_sequence:
                pdf_page_param = request.values.get('pdf_page')
                if pdf_page_param:
                    try:
                        requested_page = int(pdf_page_param)
                    except ValueError:
                        requested_page = None
                    if requested_page:
                        filtered_sequence = [
                            lbl for lbl in pdf_sequence
                            if getattr(lbl, '_pdf_original_page_number', None) == requested_page
                        ]
                        if filtered_sequence:
                            current_app.logger.info('Printing current PDF page %d (no explicit range)', requested_page)
            elif markdown_sequence and markdown_page:
                total_pages = len(markdown_sequence)
                if total_pages > 0:
                    index = max(0, min(total_pages - 1, markdown_page - 1))
                    filtered_sequence = [markdown_sequence[index]]
                    current_app.logger.info('Printing current markdown page %d of %d (no explicit range)', index + 1, total_pages)

        if filtered_sequence is None:
            filtered_sequence = label_sequence
        elif not filtered_sequence:
            filtered_sequence = label_sequence

        printer.add_label_sequence(filtered_sequence, print_count, cut_once)
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
        page_from = int(payload.get('page_from')) if payload.get('page_from') else None
        page_to = int(payload.get('page_to')) if payload.get('page_to') else None
        markdown_page = int(payload.get('markdown_page')) if payload.get('markdown_page') else None

        printer = create_printer_queue(context['label_size'])
        markdown_sequence = getattr(label, '_markdown_labels', None)
        pdf_sequence = getattr(label, '_pdf_page_labels', None)
        label_sequence = markdown_sequence if markdown_sequence else pdf_sequence

        if label_sequence:
            filtered_sequence = None

            if page_from is not None or page_to is not None:
                if pdf_sequence:
                    def within_pdf_range(lbl):
                        page_number = getattr(lbl, '_pdf_original_page_number', None)
                        if page_number is None:
                            return True
                        if page_from is not None and page_number < page_from:
                            return False
                        if page_to is not None and page_number > page_to:
                            return False
                        return True

                    filtered_sequence = [lbl for lbl in pdf_sequence if within_pdf_range(lbl)]
                    if not filtered_sequence:
                        message = f'No PDF pages found for range {page_from}-{page_to}' if page_to else f'No PDF pages found from {page_from} onwards'
                        return jsonify({'success': False, 'error': message}), 400

                    page_numbers = [getattr(lbl, '_pdf_original_page_number', None) for lbl in filtered_sequence]
                    current_app.logger.info('Printing PDF pages %s (requested range %s-%s)', page_numbers, page_from, page_to)
                else:
                    total_pages = len(label_sequence)
                    start = (page_from - 1) if page_from else 0
                    end = page_to if page_to else total_pages
                    start = max(0, min(start, total_pages - 1))
                    end = max(1, min(end, total_pages))
                    if start < end:
                        filtered_sequence = label_sequence[start:end]
                        current_app.logger.info('Printing pages %d-%d of %d total pages', start + 1, end, total_pages)
                    else:
                        return jsonify({'success': False, 'error': f'Invalid page range: {page_from}-{page_to}'}), 400
            else:
                if pdf_sequence:
                    pdf_page_param = payload.get('pdf_page')
                    if pdf_page_param:
                        try:
                            requested_page = int(pdf_page_param)
                        except (TypeError, ValueError):
                            requested_page = None
                        if requested_page:
                            filtered_sequence = [
                                lbl for lbl in pdf_sequence
                                if getattr(lbl, '_pdf_original_page_number', None) == requested_page
                            ]
                            if filtered_sequence:
                                current_app.logger.info('Printing current PDF page %d (no explicit range)', requested_page)
                elif markdown_sequence and markdown_page:
                    total_pages = len(markdown_sequence)
                    if total_pages > 0:
                        index = max(0, min(total_pages - 1, markdown_page - 1))
                        filtered_sequence = [markdown_sequence[index]]
                        current_app.logger.info('Printing current markdown page %d of %d (no explicit range)', index + 1, total_pages)

            if filtered_sequence is None:
                filtered_sequence = label_sequence
            elif not filtered_sequence:
                filtered_sequence = label_sequence

            printer.add_label_sequence(filtered_sequence, print_count, cut_once)
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
        'label_size': d.get('label_size', '62'),
        'printer_id': d.get('printer_id', None)
    }

    return create_printer_queue(context['label_size'], context['printer_id'])


def get_printers_json_path():
    """Get path to printers.json file"""
    path = current_app.config.get('PRINTERS_JSON_PATH')
    if path:
        return path
    instance_path = current_app.instance_path
    os.makedirs(instance_path, exist_ok=True)
    return os.path.join(instance_path, 'printers.json')


def load_printers_from_json():
    """Load printers from JSON file"""
    json_path = get_printers_json_path()
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_printers_to_json(printers):
    """Save printers to JSON file"""
    json_path = get_printers_json_path()
    with open(json_path, 'w') as f:
        json.dump(printers, f, indent=2)


def _update_printer_status_support(printer_id, supports_status):
    """
    Update the printer configuration to cache whether it supports status queries.

    Args:
        printer_id: ID of the printer
        supports_status: bool indicating if printer supports status queries
    """
    # Don't update if printers are configured in config file (read-only)
    if current_app.config.get('PRINTERS') is not None:
        return

    printers = load_printers_from_json()
    updated = False

    for printer in printers:
        if printer.get('id') == printer_id:
            # Only update if value changed or not set
            if printer.get('supports_status') != supports_status:
                printer['supports_status'] = supports_status
                updated = True
            break

    if updated:
        save_printers_to_json(printers)
        logger.info(f"Updated printer {printer_id} status support: {supports_status}")


def get_available_printers():
    """Get list of configured printers"""
    # Check if PRINTERS is set in config (takes precedence)
    printers = current_app.config.get('PRINTERS')
    if printers is not None:
        return printers

    # Load from JSON file
    printers = load_printers_from_json()
    if printers:
        return printers

    # Fallback to legacy config
    return [{
        'id': 'default',
        'name': 'Default Printer',
        'type': 'local',
        'model': current_app.config['PRINTER_MODEL'],
        'device': current_app.config['PRINTER_PRINTER'],
        'default': True
    }]


def get_default_printer():
    """Get the default printer configuration"""
    printers = get_available_printers()
    for printer in printers:
        if printer.get('default', False):
            return printer
    return printers[0] if printers else None


def create_printer_queue(label_size, printer_id=None):
    """Create printer queue for specified or default printer"""
    printers = get_available_printers()

    # Find requested printer or use default
    printer_config = None
    if printer_id:
        for p in printers:
            if p.get('id') == printer_id:
                printer_config = p
                break

    if not printer_config:
        printer_config = get_default_printer()

    if not printer_config:
        raise ValueError("No printer configured")

    # Create appropriate queue type
    if printer_config['type'] == 'remote':
        return RemotePrinterQueue(
            remote_url=printer_config['url'],
            label_size=label_size
        )
    else:  # local
        return PrinterQueue(
            model=printer_config['model'],
            device_specifier=printer_config['device'],
            label_size=label_size
        )


def build_label_context_from_request(request):
    d = request.values
    # Debug: log all received parameters
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
        'label_width': to_int(d.get('label_width', None), 0),  # Explicit width from remote printer
        'label_height': to_int(d.get('label_height', None), 0),  # Explicit height from remote printer
        # Border areas with enable flags
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
        'pdf_page': to_int(d.get('pdf_page', None), 1),  # PDF page number for navigation
        'page_from': to_int(d.get('page_from', None), 0) if d.get('page_from') else None,  # Page range start
        'page_to': to_int(d.get('page_to', None), 0) if d.get('page_to') else None  # Page range end
    }

    # Debug: log rotation setting
    current_app.logger.info('[build_context] image_rotate_90 set to: %s (from param: %s)', context['image_rotate_90'], d.get('image_rotate_90'))

    # Debug log for remote images
    if d.get('label_width') or d.get('label_height'):
        current_app.logger.info('[context] Received explicit dimensions: label_width=%s, label_height=%s',
                               d.get('label_width'), d.get('label_height'))

    if print_type == 'markdown' and orientation == 'rotated':
        context['markdown_paged'] = True
        slice_mm = context['markdown_slice_mm']
        if slice_mm <= 0:
            context['markdown_slice_mm'] = MARKDOWN_DEFAULT_SLICE_MM

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
        'markdown_page_numbers': bool(data.get('page_numbers', True)),
        'markdown_page_circle': bool(data.get('page_circle', True)),
        'markdown_page_number_mm': float(data.get('page_number_mm', MARKDOWN_DEFAULT_PAGE_NUMBER_MM)),
        'markdown_page_count': bool(data.get('page_count', True)),
        'markdown_page': int(data.get('markdown_page', 1)) if data.get('markdown_page') else None,
        'head_width_px': width,
        # Border areas
        'header_left_bar_mm': float(data.get('header_left_bar_mm', 0)),
        'header_right_bar_mm': float(data.get('header_right_bar_mm', 0)),
        'header_bar_color': data.get('header_bar_color', 'black'),  # 'black' or 'red'
        'header_left_text': data.get('header_left_text', ''),
        'header_right_text': data.get('header_right_text', ''),
        'header_top_text': data.get('header_top_text', ''),
        'header_bottom_text': data.get('header_bottom_text', ''),
        'header_top_height_mm': float(data.get('header_top_height_mm', 0)),
        'header_bottom_height_mm': float(data.get('header_bottom_height_mm', 0)),
        'header_bar_text_size_pt': float(data.get('header_bar_text_size_pt', 0)),
        'header_top_divider': bool(data.get('header_top_divider', False)),
        'header_bottom_divider': bool(data.get('header_bottom_divider', False))
    }

    if orientation == 'rotated':
        context['markdown_paged'] = True
        if context['markdown_slice_mm'] <= 0:
            context['markdown_slice_mm'] = MARKDOWN_DEFAULT_SLICE_MM

    return context


def create_label_from_context(context, image_file=None):
    def get_font_info(font_family_name, font_style_name):
        try:
            # Treat empty strings as None
            if not font_family_name:
                font_family_name = current_app.config['LABEL_DEFAULT_FONT_FAMILY']
            if not font_style_name:
                font_style_name = current_app.config['LABEL_DEFAULT_FONT_STYLE'] or 'Regular'

            # Handle common font family inconsistencies (e.g., UI sends 'Noto', but fc-list returns 'Noto Sans')
            if font_family_name == 'Noto':
                # Try 'Noto Sans' first, then 'Noto Serif', then just 'Noto'
                font_map_candidates = [
                    FONTS.fonts.get('Noto Sans', {}),
                    FONTS.fonts.get('Noto Serif', {}),
                    FONTS.fonts.get('Noto', {})
                ]
            else:
                font_map_candidates = [FONTS.fonts.get(font_family_name, {})]
            
            resolved_font_path = None
            resolved_family = font_family_name # Keep original family name for further context
            resolved_style = font_style_name
            matched_candidate_map = None

            for candidate_map in font_map_candidates:
                if not candidate_map: continue

                # Try exact match for style
                resolved_font_path = candidate_map.get(font_style_name)
                if resolved_font_path:
                    matched_candidate_map = candidate_map
                    resolved_style = font_style_name
                    break

                # If not found, try fallback to 'Regular' for the same family
                if font_style_name != 'Regular':
                    resolved_font_path = candidate_map.get('Regular')
                    if resolved_font_path:
                        matched_candidate_map = candidate_map
                        resolved_style = 'Regular'
                        break

            if not resolved_font_path:
                raise LookupError(f"Couldn't find font '{font_family_name}' with style '{font_style_name}' or 'Regular' fallback.")

            # Update resolved_family to the actual family name that provided the font path
            # This assumes that the font_map_candidates are iterated such that the first match is preferred
            # If 'Noto Sans' provides the font, we use 'Noto Sans' as the resolved_family
            for actual_family_name, styles_map in FONTS.fonts.items():
                if resolved_font_path in styles_map.values():
                    resolved_family = actual_family_name
                    break

            # If we still couldn't find a style in the matched candidate, pick the first available
            if not resolved_style and matched_candidate_map:
                resolved_style = next(iter(matched_candidate_map.keys()), 'Regular')

        except KeyError:
            raise LookupError(f"Couldn't find the font family '{font_family_name}'")
        
        # Debugging what font info is returned
        current_app.logger.info(f"[FONT_INFO_DEBUG] Returning font_path='{resolved_font_path}', resolved_family='{resolved_family}', resolved_style='{resolved_style}'")
        
        return resolved_font_path, resolved_family, resolved_style

    def get_uploaded_image(image):
        try:
            name, ext = os.path.splitext(image.filename)
            if ext.lower() in ('.png', '.jpg', '.jpeg'):
                image = imgfile_to_image(image)
                # Apply crop and rotate before color conversion
                image = apply_crop_and_rotate(image)
                if context['image_mode'] == 'grayscale':
                    return convert_image_to_grayscale(image)
                if context['image_mode'] == 'red_and_black':
                    return convert_image_to_red_and_black(image)
                if context['image_mode'] == 'colored':
                    return image
                return convert_image_to_bw(image, context['image_bw_threshold'])
            if ext.lower() == '.pdf':
                image = pdffile_to_image(image, DEFAULT_DPI)
                # Apply crop and rotate before color conversion
                image = apply_crop_and_rotate(image)
                if context['image_mode'] == 'grayscale':
                    return convert_image_to_grayscale(image)
                return convert_image_to_bw(image, context['image_bw_threshold'])
            return None
        except AttributeError:
            return None

    def get_uploaded_pdf_pages(image):
        """Get all pages from a multipage PDF as a list of processed images"""
        try:
            name, ext = os.path.splitext(image.filename)
            if ext.lower() != '.pdf':
                return None

            current_app.logger.info('[pdf-multipage] Loading PDF pages from %s', image.filename)

            # First, get page count without rendering
            from app.utils import get_pdf_page_count, pdffile_to_single_page
            page_count = get_pdf_page_count(image)
            selected_page_numbers = []

            # Determine which pages to load based on request
            page_from = context.get('page_from')
            page_to = context.get('page_to')
            pdf_page_param = context.get('pdf_page', 1)
            current_app.logger.info('[pdf-multipage] Request params - page_from: %s, page_to: %s, pdf_page: %s',
                                   page_from, page_to, pdf_page_param)

            if not page_count:
                # Fallback: PyPDF2 failed, must load all pages to get count
                # This is slow but necessary for encrypted/problematic PDFs
                current_app.logger.warning('[pdf-multipage] Could not get page count, using full load as fallback')
                try:
                    images = pdffile_to_images(image, DEFAULT_DPI)
                    page_count = len(images)
                    current_app.logger.info('[pdf-multipage] Loaded all %d pages via fallback', page_count)

                    # Determine which pages we actually need
                    if page_from is not None or page_to is not None:
                        # Page range for printing
                        start_page = max(1, min(int(page_from), page_count)) - 1 if page_from else 0
                        end_page = max(1, min(int(page_to), page_count)) if page_to else page_count
                        pages_to_process = images[start_page:end_page]
                        selected_page_numbers = list(range(start_page + 1, end_page + 1))
                        current_app.logger.info('[pdf-multipage] Selected pages %d-%d from loaded pages',
                                               start_page + 1, end_page)
                    else:
                        # Single page for preview
                        requested_page = int(pdf_page_param) - 1
                        requested_page = max(0, min(requested_page, page_count - 1))
                        pages_to_process = [images[requested_page]]
                        selected_page_numbers = [requested_page + 1]
                        current_app.logger.info('[pdf-multipage] Selected page %d from loaded pages',
                                               requested_page + 1)
                        context['pdf_page_count'] = page_count
                        context['pdf_current_page'] = requested_page + 1

                    # Free memory - discard images we don't need
                    del images

                except Exception as pdf_error:
                    current_app.logger.error('[pdf-multipage] PDF conversion failed: %s', str(pdf_error))
                    return None
            else:
                # Efficient path: PyPDF2 worked, load pages one-by-one on demand
                # Determine which pages to load
                if page_from is not None or page_to is not None:
                    # Page range for printing
                    start_page = max(1, min(int(page_from), page_count)) - 1 if page_from else 0  # Convert to 0-indexed
                    end_page = max(1, min(int(page_to), page_count)) if page_to else page_count  # Keep 1-indexed for range
                    pages_to_load = list(range(start_page, end_page))
                    selected_page_numbers = [page_num + 1 for page_num in pages_to_load]
                    current_app.logger.info('[pdf-multipage] Page range calculation: page_from=%s, page_to=%s, start_page=%d, end_page=%d',
                                           page_from, page_to, start_page, end_page)
                    current_app.logger.info('[pdf-multipage] pages_to_load=%s (count=%d)', pages_to_load, len(pages_to_load))
                    current_app.logger.info('[pdf-multipage] PDF has %d pages, will load pages %d-%d on-demand',
                                           page_count, start_page + 1, end_page)
                else:
                    # Single page for preview
                    requested_page = int(pdf_page_param) - 1
                    requested_page = max(0, min(requested_page, page_count - 1))
                    pages_to_load = [requested_page]
                    selected_page_numbers = [requested_page + 1]
                    current_app.logger.info('[pdf-multipage] PDF has %d pages, loading page %d for preview',
                                           page_count, requested_page + 1)
                    # Store page metadata for preview response
                    context['pdf_page_count'] = page_count
                    context['pdf_current_page'] = requested_page + 1

                # Load pages on demand
                pages_to_process = []
                try:
                    for page_num in pages_to_load:
                        img = pdffile_to_single_page(image, DEFAULT_DPI, page_number=page_num)
                        if img:
                            pages_to_process.append(img)
                        else:
                            current_app.logger.warning('[pdf-multipage] Failed to load page %d', page_num + 1)
                except Exception as load_error:
                    current_app.logger.error('[pdf-multipage] Error loading pages: %s', str(load_error))
                    return None

            # Process the loaded pages (common path for both fallback and efficient)
            processed_pages = []
            stretch_length = context.get('image_stretch_length', False)

            try:
                for idx, img in enumerate(pages_to_process):
                    current_app.logger.info('[pdf-multipage] Processing page %d/%d',
                                           idx + 1, len(pages_to_process))

                    # Process this page immediately before loading the next one
                    # Apply crop and rotate before color conversion
                    img = apply_crop_and_rotate(img)
                    current_app.logger.info('[pdf-process] After crop_and_rotate: %dx%d', img.width, img.height)

                    # Apply color conversion
                    if context['image_mode'] == 'grayscale':
                        img = convert_image_to_grayscale(img)
                    else:
                        img = convert_image_to_bw(img, context['image_bw_threshold'])
                    current_app.logger.info('[pdf-process] After color conversion: %dx%d', img.width, img.height)

                    # Apply scaling based on stretch_length and label settings
                    # Always use content_width_px which accounts for margins
                    is_rotated = context.get('image_rotate_90', False)
                    target_width_px = content_width_px

                    current_app.logger.info('[pdf-scale] Before scaling: %dx%d px, is_endless=%s, stretch_length=%s, target_width_px=%d (rotated=%s), content_height_limit_px=%d',
                                           img.width, img.height, is_endless, stretch_length, target_width_px, is_rotated, content_height_limit_px)

                    if (not is_endless or content_height_limit_px > 0) and not stretch_length:
                        img = scale_image_to_box(
                            img,
                            target_width_px,
                            content_height_limit_px if content_height_limit_px > 0 else 0
                        )
                        current_app.logger.info('[pdf-scale] Used scale_image_to_box, result: %dx%d px', img.width, img.height)
                    else:
                        # Scale to fit width only
                        if target_width_px > 0 and img.width > target_width_px:
                            scale = target_width_px / img.width
                            new_size = (
                                int(round(img.width * scale)),
                                int(round(img.height * scale))
                            )
                            current_app.logger.info('[pdf-scale] Scaling to fit width: scale=%.3f, new_size=%dx%d', scale, new_size[0], new_size[1])
                            img = img.resize(new_size, resample=RESAMPLE_LANCZOS)
                            current_app.logger.info('[pdf-scale] After resize: %dx%d', img.width, img.height)
                        else:
                            current_app.logger.info('[pdf-scale] No width scaling needed: img.width=%d <= target_width_px=%d', img.width, target_width_px)

                        # Crop whitespace if not disabled (but skip for rotated images as it can remove actual content)
                        no_crop = context.get('no_crop', False)
                        is_rotated = context.get('image_rotate_90', False)
                        if not no_crop and not is_rotated:
                            def _crop_white(img_to_crop):
                                bbox = img_to_crop.convert('L').point(lambda p: 0 if p >= 250 else 255, '1').getbbox()
                                return img_to_crop.crop(bbox) if bbox else img_to_crop
                            img = _crop_white(img)
                            current_app.logger.info('[pdf-scale] After whitespace crop: %dx%d', img.width, img.height)
                        elif is_rotated:
                            current_app.logger.info('[pdf-scale] Skipping whitespace crop for rotated image')

                        # Add canvas padding unless stretch_length or rotation is enabled
                        if not stretch_length and not context.get('image_rotate_90', False):
                            canvas_width = int(content_width_px)
                            canvas = Image.new('RGB', (canvas_width, img.height), 'white')
                            x = max(0, (canvas_width - img.width) // 2)
                            canvas.paste(img, (x, 0))
                            img = canvas
                            current_app.logger.info('[pdf-scale] After canvas padding: %dx%d', img.width, img.height)

                        current_app.logger.info('[pdf-scale] FINAL image dimensions: %dx%d', img.width, img.height)

                    processed_pages.append(img)
                    current_app.logger.info('[pdf-multipage] Processed page %d/%d', idx + 1, len(pages_to_process))

                if not processed_pages:
                    return None

                current_app.logger.info('[pdf-multipage] Successfully processed %d pages', len(processed_pages))
                context['pdf_selected_pages'] = selected_page_numbers
                return processed_pages

            except Exception as page_error:
                current_app.logger.error('[pdf-multipage] Error processing pages: %s', str(page_error))
                return None

        except Exception as e:
            current_app.logger.error('[pdf-multipage] Error processing PDF: %s', str(e))
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

    def apply_crop_and_rotate(image):
        """Apply crop and rotation to image based on context settings"""
        if image is None:
            return None

        current_app.logger.info('[apply_crop_rotate] INPUT image dimensions: %dx%d', image.width, image.height)

        # Store original dimensions in mm for display
        original_width_mm = image.size[0] * 25.4 / DEFAULT_DPI
        original_height_mm = image.size[1] * 25.4 / DEFAULT_DPI
        context['source_width_mm'] = round(original_width_mm, 1)
        context['source_height_mm'] = round(original_height_mm, 1)

        # Apply crop if any crop values are set
        crop_left = context.get('image_crop_left', 0)
        crop_right = context.get('image_crop_right', 0)
        crop_top = context.get('image_crop_top', 0)
        crop_bottom = context.get('image_crop_bottom', 0)

        if crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0:
            # Convert mm to pixels
            crop_left_px = mm_to_pixels(crop_left, DEFAULT_DPI)
            crop_right_px = mm_to_pixels(crop_right, DEFAULT_DPI)
            crop_top_px = mm_to_pixels(crop_top, DEFAULT_DPI)
            crop_bottom_px = mm_to_pixels(crop_bottom, DEFAULT_DPI)

            # Calculate crop box
            width, height = image.size
            left = crop_left_px
            top = crop_top_px
            right = width - crop_right_px
            bottom = height - crop_bottom_px

            current_app.logger.info('[crop] Before crop: %dx%d, crop_mm(L=%d,R=%d,T=%d,B=%d), crop_px(L=%d,R=%d,T=%d,B=%d), box=(%d,%d,%d,%d)',
                                   width, height, crop_left, crop_right, crop_top, crop_bottom,
                                   crop_left_px, crop_right_px, crop_top_px, crop_bottom_px,
                                   left, top, right, bottom)

            # Ensure valid crop box
            if right > left and bottom > top:
                image = image.crop((left, top, right, bottom))
                current_app.logger.info('[crop] After crop: %dx%d', image.width, image.height)
            else:
                current_app.logger.warning('[crop] Invalid crop box: right=%d <= left=%d or bottom=%d <= top=%d', right, left, bottom, top)

        # Apply rotation if enabled (rotate 90° counter-clockwise)
        rotate_enabled = context.get('image_rotate_90', False)
        current_app.logger.info('[apply_crop_rotate] Rotation check: image_rotate_90=%s, type=%s', rotate_enabled, type(rotate_enabled).__name__)
        if rotate_enabled:
            # Rotate -90 degrees (counter-clockwise) with expand=True to resize canvas
            image = image.rotate(-90, expand=True)
            current_app.logger.info('[apply_crop_rotate] After rotation: %dx%d', image.width, image.height)

        current_app.logger.info('[apply_crop_rotate] OUTPUT image dimensions: %dx%d', image.width, image.height)
        return image

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

    # Only load fonts if needed for text-based content
    if label_content in (LabelContent.TEXT_ONLY, LabelContent.QRCODE_ONLY, LabelContent.TEXT_QRCODE, LabelContent.MARKDOWN_IMAGE):
        font_path, resolved_family, resolved_style = get_font_info(context.get('font_family'), context.get('font_style'))
        font_map = FONTS.fonts.get(resolved_family, {})
    else:
        # Image-only labels don't need fonts
        font_path = None
        resolved_family = None
        resolved_style = None
        font_map = {}

    font_size_pt = float(context.get('font_size', current_app.config['LABEL_DEFAULT_FONT_SIZE']))
    font_size_px = points_to_pixels(font_size_pt)

    markdown_page_images = None
    generated_image: Optional[Image.Image] = None

    # Initialize final dimensions (may be overridden for rotated markdown)
    final_label_orientation = label_orientation
    final_label_width_px = label_width_px
    final_label_height_px = label_height_px

    current_app.logger.info('[label-dims] orientation=%s, label_size=%dx%d, content=%dx%d',
                           label_orientation, label_width_px, label_height_px, content_width_px, content_height_limit_px)

    if label_content == LabelContent.MARKDOWN_IMAGE:
        line_spacing = int(context.get('line_spacing', current_app.config['LABEL_DEFAULT_LINE_SPACING']))
        base_font_pt = max(6, font_size_pt)

        slice_mm_config = float(context.get('markdown_slice_mm', 0) or 0)
        paginate = bool(context.get('markdown_paged'))

        if label_orientation == LabelOrientation.ROTATED:
            if slice_mm_config <= 0:
                slice_mm_config = MARKDOWN_DEFAULT_SLICE_MM
            paginate = True
        elif paginate and slice_mm_config <= 0:
            # For standard mode, if paged is enabled but no slice size, use default
            slice_mm_config = MARKDOWN_DEFAULT_SLICE_MM

        # Calculate border area reductions in pixels
        left_area_px = mm_to_pixels(float(context.get('left_area_mm', 0)), DEFAULT_DPI)
        right_area_px = mm_to_pixels(float(context.get('right_area_mm', 0)), DEFAULT_DPI)
        top_area_px = mm_to_pixels(float(context.get('top_area_mm', 0)), DEFAULT_DPI)
        bottom_area_px = mm_to_pixels(float(context.get('bottom_area_mm', 0)), DEFAULT_DPI)

        if label_orientation == LabelOrientation.ROTATED:
            # For rotated mode:
            # - Rendering canvas width = slice width minus L/R borders
            # - Rendering canvas height = label width minus T/B borders
            # - After slicing, add_border_areas() will expand each slice to full label width
            slice_width_px = mm_to_pixels(slice_mm_config, DEFAULT_DPI)
            markdown_render_width_px = max(slice_width_px - left_area_px - right_area_px, 10)
            
            label_width_mm = standard_width_px * 25.4 / DEFAULT_DPI
            # Slice at CONTENT height (borders will be ADDED to reach full label width)
            actual_slice_height_mm = max(label_width_mm - float(context.get('top_area_mm', 0)) - float(context.get('bottom_area_mm', 0)), 1)
            
            current_app.logger.info('[markdown-rotate] render_width=%dpx (%.1fmm - borders), slice_height=%.1fmm (%.1fmm label - borders)',
                                   markdown_render_width_px, slice_mm_config, actual_slice_height_mm, label_width_mm)
        else:
            # For standard orientation:
            # - Rendering canvas width = content width minus L/R borders
            # - Rendering canvas height = configured height minus T/B borders
            # - After slicing, add_border_areas() will expand each slice to full configured height
            markdown_render_width_px = max(content_width_px - left_area_px - right_area_px, 10)
            # Slice at CONTENT height (borders will be ADDED to reach configured height)
            actual_slice_height_mm = max(slice_mm_config - float(context.get('top_area_mm', 0)) - float(context.get('bottom_area_mm', 0)), 1)
            
            current_app.logger.info('[markdown-standard] render_width=%dpx (content - borders), slice_height=%.1fmm (%.1fmm config - borders)',
                                   markdown_render_width_px, actual_slice_height_mm, slice_mm_config)
        
        render_width_px = markdown_render_width_px

        page_number_mm = float(context.get('markdown_page_number_mm', MARKDOWN_DEFAULT_PAGE_NUMBER_MM))

        # Page numbering uses bottom_area_mm when enabled
        numbering_enabled = bool(context.get('markdown_page_numbers'))
        if numbering_enabled:
            # Ensure bottom area is at least page_number_mm
            bottom_area_mm = max(float(context.get('bottom_area_mm', 0)), page_number_mm, MARKDOWN_MIN_PAGE_NUMBER_FOOTER_MM)
            context['bottom_area_mm'] = bottom_area_mm

        base_image, forced_page_breaks, table_boundaries_data = render_markdown_to_image(
            context.get('text', '') or '',
            content_width_px=render_width_px,
            dpi=DEFAULT_DPI,
            base_font_pt=base_font_pt,
            line_spacing=line_spacing,
            font_map=font_map,
            preferred_style=resolved_style,
            allow_pagebreaks=paginate
        )
        table_boundaries_px, boundary_types = table_boundaries_data
        current_app.logger.info('[slice-debug] global table boundaries: %s', table_boundaries_px[:10])

        rotate_for_slicing = label_orientation == LabelOrientation.ROTATED

        # Use actual_slice_height_mm for slicing (label width for rotated mode)
        slice_mm = actual_slice_height_mm if paginate else 0
        context['markdown_paged'] = paginate
        context['markdown_slice_mm'] = slice_mm_config

        # Footer is now handled by bottom_area_mm, no separate footer extension needed

        forced_breaks_px = forced_page_breaks if paginate else None
        boundaries_px = table_boundaries_px
        pages = slice_markdown_pages(base_image, slice_mm, 0, DEFAULT_DPI,
                                     forced_breaks_px=forced_breaks_px,
                                     table_boundaries_px=boundaries_px,
                                     boundary_types=boundary_types)

        processed_pages = []
        total_pages = len(pages)
        draw_circle = bool(context.get('markdown_page_circle', True))
        include_total = bool(context.get('markdown_page_count', True))

        # First pass: determine max dimensions
        scaled_pages = []
        max_height = 0
        for idx, page in enumerate(pages, start=1):
            scaled = scale_image_to_box(page, render_width_px, content_height_limit_px if content_height_limit_px > 0 else 0)

            # Add border areas if configured (page numbers are now part of bottom area)
            from app.markdown_render import add_border_areas
            # Use bottom page numbers if bottom area is configured and enabled
            use_bottom_page_numbers = bool(context.get('bottom_show_page_numbers', False))

            scaled = add_border_areas(
                scaled,
                dpi=DEFAULT_DPI,
                # Enable flags
                enable_left_area=context.get('enable_left_area', False),
                enable_right_area=context.get('enable_right_area', False),
                enable_top_area=context.get('enable_top_area', False),
                enable_bottom_area=context.get('enable_bottom_area', False),
                enable_left_bar=context.get('enable_left_bar', False),
                enable_left_text=context.get('enable_left_text', False),
                enable_right_bar=context.get('enable_right_bar', False),
                enable_right_text=context.get('enable_right_text', False),
                enable_top_bar=context.get('enable_top_bar', False),
                enable_top_text=context.get('enable_top_text', False),
                enable_bottom_bar=context.get('enable_bottom_bar', False),
                enable_bottom_text=context.get('enable_bottom_text', False),
                # Area dimensions
                left_area_mm=context.get('left_area_mm', 0),
                right_area_mm=context.get('right_area_mm', 0),
                top_area_mm=context.get('top_area_mm', 0),
                bottom_area_mm=context.get('bottom_area_mm', 0),
                # Bar settings
                left_bar_mm=context.get('left_bar_mm', 0),
                right_bar_mm=context.get('right_bar_mm', 0),
                left_bar_color=context.get('left_bar_color', 'black'),
                right_bar_color=context.get('right_bar_color', 'black'),
                left_bar_text=context.get('left_bar_text', ''),
                right_bar_text=context.get('right_bar_text', ''),
                top_bar_mm=context.get('top_bar_mm', 0),
                bottom_bar_mm=context.get('bottom_bar_mm', 0),
                top_bar_color=context.get('top_bar_color', 'black'),
                bottom_bar_color=context.get('bottom_bar_color', 'black'),
                top_bar_text=context.get('top_bar_text', ''),
                bottom_bar_text=context.get('bottom_bar_text', ''),
                # Text settings
                left_text=context.get('left_text', ''),
                right_text=context.get('right_text', ''),
                top_text=context.get('top_text', ''),
                bottom_text=context.get('bottom_text', ''),
                # Font settings
                font_path=font_path,
                page_num=idx,
                total_pages=total_pages,
                left_bar_text_size_pt=context.get('left_bar_text_size_pt', 0),
                right_bar_text_size_pt=context.get('right_bar_text_size_pt', 0),
                top_bar_text_size_pt=context.get('top_bar_text_size_pt', 0),
                bottom_bar_text_size_pt=context.get('bottom_bar_text_size_pt', 0),
                top_text_size_pt=context.get('top_text_size_pt', 0),
                bottom_text_size_pt=context.get('bottom_text_size_pt', 0),
                default_font_size_pt=font_size_pt,
                # Dividers
                top_divider=context.get('top_divider', False),
                bottom_divider=context.get('bottom_divider', False),
                divider_distance_px=context.get('divider_distance_px', 1),
                # Page numbers
                draw_page_numbers=use_bottom_page_numbers,
                page_number_circle=True,
                page_number_mm=context.get('bottom_page_number_mm', 4)
            )

            scaled_pages.append(scaled)
            max_height = max(max_height, scaled.height)

        # Set label dimensions
        if rotate_for_slicing:
            # For rotated mode: use image dimensions (landscape)
            final_label_width_px = scaled_pages[0].width
            final_label_height_px = max_height
            # Keep ROTATED orientation, but mark as pre_rotated so printer doesn't rotate again
            final_label_orientation = LabelOrientation.ROTATED
            current_app.logger.info('[markdown-rotate] %d pages, dimensions: %dx%d (landscape), ROTATED orientation with pre_rotated=True',
                                   len(scaled_pages), final_label_width_px, final_label_height_px)
        else:
            # For standard mode: DON'T override label dimensions, let auto-resize work
            # The image is content_width_px, label.py will add margins automatically
            current_app.logger.info('[markdown-standard] %d pages, image dimensions: %dx%d (label will auto-size with margins)',
                                   len(scaled_pages), scaled_pages[0].width, max_height)

        for scaled in scaled_pages:
            processed_pages.append(apply_image_mode(scaled))

        markdown_page_images = processed_pages if processed_pages else [apply_image_mode(base_image)]
        generated_image = markdown_page_images[0]
    elif label_content in (
        LabelContent.IMAGE_BW,
        LabelContent.IMAGE_GRAYSCALE,
        LabelContent.IMAGE_RED_BLACK,
        LabelContent.IMAGE_COLORED,
    ):
        # Check if image file exists - this is expected for image mode without upload
        if not image_file:
            # Return a blank/placeholder image instead of erroring
            current_app.logger.info('[image-mode] No image file uploaded yet')
            generated_image = Image.new('RGB', (content_width_px, 100), 'white')
            # Return early with blank image
        else:
            # Check if it's a multipage PDF
            name, ext = os.path.splitext(image_file.filename)
            stretch_length = context.get('image_stretch_length', False)

            if ext.lower() == '.pdf':
                pdf_pages = get_uploaded_pdf_pages(image_file)
                if pdf_pages and len(pdf_pages) >= 1:
                    # PDF pages are already processed (cropped, rotated, color-converted) by get_uploaded_pdf_pages()
                    # Just use them directly without additional scaling
                    generated_image = pdf_pages[0]  # For preview, show first page
                    # Store all pages for printing
                    context['pdf_page_images'] = pdf_pages
                else:
                    # Single page PDF or failed to load
                    uploaded = get_uploaded_image(image_file)
                    processed_image = apply_image_mode(uploaded)
                    if processed_image is None:
                        raise ValueError('Empty image data')
                    generated_image = processed_image
            else:
                # Regular image file (PNG, JPG, etc.)
                uploaded = get_uploaded_image(image_file)
                processed_image = apply_image_mode(uploaded)
                if processed_image is None:
                    raise ValueError('Empty image data')

                # If stretch_length is enabled, treat as endless (no height limit)
                if (not is_endless or content_height_limit_px > 0) and not stretch_length:
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

                    # Only crop whitespace if not explicitly disabled (e.g., for paged prints from remote)
                    no_crop = context.get('no_crop', False)
                    if not no_crop:
                        def _crop_white(img):
                            bbox = img.convert('L').point(lambda p: 0 if p >= 250 else 255, '1').getbbox()
                            return img.crop(bbox) if bbox else img

                        processed_image = _crop_white(processed_image)

                    # When stretch_length or rotation is enabled, use actual image dimensions (no padding)
                    if not stretch_length and not context.get('image_rotate_90', False):
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
            # Use final_label_orientation for rotated markdown
            kwargs['label_orientation'] = final_label_orientation

            # For rotated mode: override dimensions (landscape image with pre_rotated flag)
            # For standard mode: keep original label dimensions, let auto-resize add margins
            if rotate_for_slicing:
                kwargs['width'] = final_label_width_px
                kwargs['height'] = final_label_height_px
                kwargs['pre_rotated'] = True
            # else: use base_kwargs width/height (label_width_px, label_height_px)

            labels_sequence.append(SimpleLabel(**kwargs))

        for lbl in labels_sequence:
            lbl._markdown_labels = labels_sequence

        return labels_sequence[0]

    # Handle multipage PDF images
    if 'pdf_page_images' in context and context['pdf_page_images']:
        current_app.logger.info('[create_label] Creating labels for %d PDF pages', len(context['pdf_page_images']))
        labels_sequence = []
        selected_page_numbers = context.get('pdf_selected_pages') or []
        for idx, page_image in enumerate(context['pdf_page_images']):
            current_app.logger.info('[create_label] PDF page %d image dimensions: %dx%d', idx+1, page_image.width, page_image.height)
            kwargs = dict(base_kwargs)
            kwargs['image'] = page_image

            # For rotated images, don't override dimensions - let the label handle margins properly
            # The image is already scaled to fit the label width (standard_width_px)
            if context.get('image_rotate_90', False):
                current_app.logger.info('[create_label] PDF page %d: Rotated image, using label dimensions with margins', idx+1)

            label_obj = SimpleLabel(**kwargs)
            original_page_number = selected_page_numbers[idx] if idx < len(selected_page_numbers) else (idx + 1)
            label_obj._pdf_original_page_number = original_page_number
            labels_sequence.append(label_obj)

        for lbl in labels_sequence:
            lbl._pdf_page_labels = labels_sequence

        current_app.logger.info('[create_label] Returning first PDF page label')
        return labels_sequence[0]

    kwargs = dict(base_kwargs)
    kwargs['image'] = generated_image
    # Use explicit dimensions if provided (from remote printer)
    # For rotated orientation, these are the POST-rotation dimensions
    if context.get('label_width', 0) > 0 and context.get('label_height', 0) > 0:
        if label_orientation == LabelOrientation.ROTATED:
            # For rotated labels, the width/height are already swapped by the remote client
            # We receive portrait image but these dimensions are for the final landscape output
            kwargs['width'] = context['label_width']
            kwargs['height'] = context['label_height']
            kwargs['pre_rotated'] = True  # Prevent auto-resize
            current_app.logger.info('[remote-rotated] Using explicit dimensions for rotated: %dx%d',
                                   context['label_width'], context['label_height'])
        else:
            kwargs['width'] = context['label_width']
            kwargs['height'] = context['label_height']
            kwargs['pre_rotated'] = True
            current_app.logger.info('[remote-standard] Using explicit dimensions: %dx%d',
                                   context['label_width'], context['label_height'])
    label = SimpleLabel(**kwargs)
    label._markdown_labels = None
    label._pdf_page_labels = None
    return label


def create_label_from_request(request):
    context = build_label_context_from_request(request)
    return create_label_from_context(context, image_file=request.files.get('image', None))


# Printer Management API Endpoints

@bp.route('/api/printers', methods=['GET'])
def api_list_printers():
    """List all configured printers"""
    printers = get_available_printers()
    # Don't expose full device strings for security, just basics
    safe_printers = [
        {
            'id': p.get('id'),
            'name': p.get('name'),
            'type': p.get('type'),
            'default': p.get('default', False)
        }
        for p in printers
    ]
    return jsonify({'printers': safe_printers})


@bp.route('/api/printers/manage', methods=['GET'])
def api_get_printers_full():
    """Get full printer configurations for management UI"""
    # Check if using config-based printers (read-only)
    if current_app.config.get('PRINTERS') is not None:
        return jsonify({
            'printers': current_app.config.get('PRINTERS'),
            'readonly': True,
            'message': 'Printers are configured in config file (read-only)'
        })

    printers = load_printers_from_json()
    return jsonify({
        'printers': printers,
        'readonly': False
    })


@bp.route('/api/printers/manage', methods=['POST'])
def api_add_printer():
    """Add a new printer"""
    try:
        # Check if read-only mode
        if current_app.config.get('PRINTERS') is not None:
            return jsonify({'success': False, 'error': 'Printers configured in config file (read-only)'}), 403

        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        # Validate required fields
        if not data.get('name'):
            return jsonify({'success': False, 'error': 'Printer name is required'}), 400

        printer_type = data.get('type', 'local')
        if printer_type not in ['local', 'remote']:
            return jsonify({'success': False, 'error': 'Invalid printer type'}), 400

        if printer_type == 'local':
            if not data.get('model') or not data.get('device'):
                return jsonify({'success': False, 'error': 'Model and device are required for local printers'}), 400
        else:  # remote
            if not data.get('url'):
                return jsonify({'success': False, 'error': 'URL is required for remote printers'}), 400

        # Load existing printers
        printers = load_printers_from_json()

        # Generate unique ID
        import uuid
        new_id = str(uuid.uuid4())

        # Create new printer
        new_printer = {
            'id': new_id,
            'name': data['name'],
            'type': printer_type,
            'default': data.get('default', False)
        }

        if printer_type == 'local':
            new_printer['model'] = data['model']
            new_printer['device'] = data['device']
        else:
            new_printer['url'] = data['url']

        # If this is set as default, unset others
        if new_printer['default']:
            for p in printers:
                p['default'] = False

        printers.append(new_printer)
        save_printers_to_json(printers)

        return jsonify({'success': True, 'printer': new_printer})
    except Exception as e:
        current_app.logger.error('Failed to add printer: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/printers/manage/<printer_id>', methods=['PUT'])
def api_update_printer(printer_id):
    """Update an existing printer"""
    # Check if read-only mode
    if current_app.config.get('PRINTERS') is not None:
        return jsonify({'success': False, 'error': 'Printers configured in config file (read-only)'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400

    printers = load_printers_from_json()

    # Find printer
    printer_index = None
    for i, p in enumerate(printers):
        if p.get('id') == printer_id:
            printer_index = i
            break

    if printer_index is None:
        return jsonify({'success': False, 'error': 'Printer not found'}), 404

    # Update printer
    printer = printers[printer_index]
    if 'name' in data:
        printer['name'] = data['name']
    if 'default' in data:
        is_default = data['default']
        printer['default'] = is_default
        # If setting as default, unset others
        if is_default:
            for i, p in enumerate(printers):
                if i != printer_index:
                    p['default'] = False

    if printer['type'] == 'local':
        if 'model' in data:
            printer['model'] = data['model']
        if 'device' in data:
            printer['device'] = data['device']
    else:  # remote
        if 'url' in data:
            printer['url'] = data['url']

    save_printers_to_json(printers)

    return jsonify({'success': True, 'printer': printer})


@bp.route('/api/printers/manage/<printer_id>', methods=['DELETE'])
def api_delete_printer(printer_id):
    """Delete a printer"""
    # Check if read-only mode
    if current_app.config.get('PRINTERS') is not None:
        return jsonify({'success': False, 'error': 'Printers configured in config file (read-only)'}), 403

    printers = load_printers_from_json()

    # Find and remove printer
    printer_index = None
    for i, p in enumerate(printers):
        if p.get('id') == printer_id:
            printer_index = i
            break

    if printer_index is None:
        return jsonify({'success': False, 'error': 'Printer not found'}), 404

    was_default = printers[printer_index].get('default', False)
    printers.pop(printer_index)

    # If deleted printer was default, set first remaining as default
    if was_default and printers:
        printers[0]['default'] = True

    save_printers_to_json(printers)

    return jsonify({'success': True})


@bp.route('/api/printer/status', methods=['GET'])
def api_printer_status():
    """
    Query printer for current status including media type.

    Query parameters:
        printer_id: ID of the printer to query (optional, uses default if not provided)

    Returns:
        {
            'success': bool,
            'status': {
                'media_type': str,      # e.g., '62', '29', '62_red'
                'media_width_mm': int,
                'media_color': str,     # 'white' or 'red'
                'errors': list,
                'supported': bool
            }
        }
    """
    from .remote_printer import get_remote_printer_status

    printer_id = request.args.get('printer_id')

    # Get printer configuration
    printers = get_available_printers()
    if not printers:
        return jsonify({'success': False, 'error': 'No printers configured'}), 404

    # Find the requested printer or use default
    printer = None
    if printer_id:
        for p in printers:
            if p.get('id') == printer_id:
                printer = p
                break
        if not printer:
            return jsonify({'success': False, 'error': 'Printer not found'}), 404
    else:
        # Use default printer
        for p in printers:
            if p.get('default', False):
                printer = p
                break
        if not printer and printers:
            printer = printers[0]

    if not printer:
        return jsonify({'success': False, 'error': 'No printer available'}), 404

    # Check if this is a remote printer
    if printer.get('type') == 'remote':
        remote_url = printer.get('url')
        status = get_remote_printer_status(remote_url)
        if status is None:
            return jsonify({
                'success': False,
                'error': 'Remote printer does not support status queries or is unreachable',
                'supported': False
            })
        return jsonify({'success': True, 'status': status})

    # Local printer
    model = printer.get('model')
    device = printer.get('device')

    if not model or not device:
        return jsonify({'success': False, 'error': 'Printer not properly configured'}), 400

    try:
        # Create a temporary printer queue just for status query
        from .printer import PrinterQueue
        queue = PrinterQueue(model, device, current_app.config['LABEL_DEFAULT_SIZE'])

        status = queue.get_printer_status()

        if status is None:
            # Cache that this printer doesn't support status
            _update_printer_status_support(printer_id or printer.get('id'), False)
            return jsonify({
                'success': False,
                'error': 'Printer does not support status queries',
                'supported': False
            })

        # Cache that this printer supports status
        _update_printer_status_support(printer_id or printer.get('id'), True)

        # Append _red suffix if red media detected
        if status.get('media_color') == 'red' and status.get('media_type'):
            status['media_type'] = status['media_type'] + '_red'

        return jsonify({'success': True, 'status': status})

    except Exception as e:
        logger.error(f"Error querying printer status: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'supported': False
        }), 500
