"""Markdown content slicing and pagination."""

from typing import List, Optional, Dict, Tuple
from PIL import Image, ImageDraw
from flask import current_app

from .dimensions import mm_to_pixels

MARKDOWN_DEFAULT_SLICE_WINDOW_MM = 6.0
MARKDOWN_DEFAULT_MIN_BLANK_RUN = 4


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