"""PDF file processing utilities."""

import os
from flask import current_app
from PIL import Image

from app.utils import pdffile_to_images, get_pdf_page_count, pdffile_to_single_page
from .utils.image_processing import apply_crop_and_rotate, apply_image_mode, scale_image_to_box, RESAMPLE_LANCZOS

DEFAULT_DPI = 300


def get_uploaded_pdf_pages(image_file, context, content_width_px, content_height_limit_px, is_endless):
    """Get all pages from a multipage PDF as a list of processed images."""
    try:
        name, ext = os.path.splitext(image_file.filename)
        if ext.lower() != '.pdf':
            return None

        current_app.logger.info('[pdf-multipage] Loading PDF pages from %s', image_file.filename)

        page_count = get_pdf_page_count(image_file)
        selected_page_numbers = []

        page_from = context.get('page_from')
        page_to = context.get('page_to')
        pdf_page_param = context.get('pdf_page', 1)

        if not page_count:
            # Fallback: load all pages
            images = pdffile_to_images(image_file, DEFAULT_DPI)
            page_count = len(images)

            if page_from is not None or page_to is not None:
                start_page = max(1, min(int(page_from), page_count)) - 1 if page_from else 0
                end_page = max(1, min(int(page_to), page_count)) if page_to else page_count
                pages_to_process = images[start_page:end_page]
                selected_page_numbers = list(range(start_page + 1, end_page + 1))
            else:
                requested_page = int(pdf_page_param) - 1
                requested_page = max(0, min(requested_page, page_count - 1))
                pages_to_process = [images[requested_page]]
                selected_page_numbers = [requested_page + 1]
                context['pdf_page_count'] = page_count
                context['pdf_current_page'] = requested_page + 1

            del images
        else:
            # Efficient: load pages one by one
            if page_from is not None or page_to is not None:
                start_page = max(1, min(int(page_from), page_count)) - 1 if page_from else 0
                end_page = max(1, min(int(page_to), page_count)) if page_to else page_count
                pages_to_load = list(range(start_page, end_page))
                selected_page_numbers = [page_num + 1 for page_num in pages_to_load]
            else:
                requested_page = int(pdf_page_param) - 1
                requested_page = max(0, min(requested_page, page_count - 1))
                pages_to_load = [requested_page]
                selected_page_numbers = [requested_page + 1]
                context['pdf_page_count'] = page_count
                context['pdf_current_page'] = requested_page + 1

            pages_to_process = []
            for page_num in pages_to_load:
                img = pdffile_to_single_page(image_file, DEFAULT_DPI, page_number=page_num)
                if img:
                    pages_to_process.append(img)

        # Process pages
        processed_pages = []
        stretch_length = context.get('image_stretch_length', False)

        for idx, img in enumerate(pages_to_process):
            img = apply_crop_and_rotate(img, context)
            img = apply_image_mode(img, context)

            target_width_px = content_width_px

            if (not is_endless or content_height_limit_px > 0) and not stretch_length:
                img = scale_image_to_box(img, target_width_px, content_height_limit_px if content_height_limit_px > 0 else 0)
            else:
                if target_width_px > 0 and img.width > target_width_px:
                    scale = target_width_px / img.width
                    new_size = (int(round(img.width * scale)), int(round(img.height * scale)))
                    img = img.resize(new_size, resample=RESAMPLE_LANCZOS)

                no_crop = context.get('no_crop', False)
                is_rotated = context.get('image_rotate_90', False)
                if not no_crop and not is_rotated:
                    bbox = img.convert('L').point(lambda p: 0 if p >= 250 else 255, '1').getbbox()
                    if bbox:
                        img = img.crop(bbox)

                if not stretch_length and not is_rotated:
                    canvas_width = int(content_width_px)
                    canvas = Image.new('RGB', (canvas_width, img.height), 'white')
                    x = max(0, (canvas_width - img.width) // 2)
                    canvas.paste(img, (x, 0))
                    img = canvas

            processed_pages.append(img)

        if not processed_pages:
            return None

        context['pdf_selected_pages'] = selected_page_numbers
        return processed_pages

    except Exception as e:
        current_app.logger.error('[pdf-multipage] Error processing PDF: %s', str(e))
        return None
