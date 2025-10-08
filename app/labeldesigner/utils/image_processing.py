"""Image processing utilities for label designer."""

import os
from PIL import Image
from flask import current_app

from app.utils import (
    convert_image_to_bw,
    convert_image_to_grayscale,
    convert_image_to_red_and_black,
    imgfile_to_image
)

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_LANCZOS = Image.LANCZOS

DEFAULT_DPI = 300


def apply_crop_and_rotate(image, context):
    """Apply crop and rotation to image based on context settings."""
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
        from ..dimensions import mm_to_pixels
        crop_left_px = mm_to_pixels(crop_left, DEFAULT_DPI)
        crop_right_px = mm_to_pixels(crop_right, DEFAULT_DPI)
        crop_top_px = mm_to_pixels(crop_top, DEFAULT_DPI)
        crop_bottom_px = mm_to_pixels(crop_bottom, DEFAULT_DPI)

        width, height = image.size
        left = crop_left_px
        top = crop_top_px
        right = width - crop_right_px
        bottom = height - crop_bottom_px

        if right > left and bottom > top:
            image = image.crop((left, top, right, bottom))
            current_app.logger.info('[crop] After crop: %dx%d', image.width, image.height)

    # Apply rotation if enabled (rotate 90° counter-clockwise)
    rotate_enabled = context.get('image_rotate_90', False)
    if rotate_enabled:
        image = image.rotate(-90, expand=True)
        current_app.logger.info('[apply_crop_rotate] After rotation: %dx%d', image.width, image.height)

    return image


def scale_image_to_box(image, max_width, max_height):
    """Scale image to fit within the given box while maintaining aspect ratio."""
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


def get_uploaded_image(image_file, context):
    """Process uploaded image file and apply transformations."""
    try:
        name, ext = os.path.splitext(image_file.filename)
        if ext.lower() in ('.png', '.jpg', '.jpeg'):
            from app.utils import pdffile_to_image
            image = imgfile_to_image(image_file)
            image = apply_crop_and_rotate(image, context)
            return apply_image_mode(image, context)
        if ext.lower() == '.pdf':
            from app.utils import pdffile_to_image
            image = pdffile_to_image(image_file, DEFAULT_DPI)
            image = apply_crop_and_rotate(image, context)
            if context['image_mode'] == 'grayscale':
                return convert_image_to_grayscale(image)
            return convert_image_to_bw(image, context['image_bw_threshold'])
        return None
    except AttributeError:
        return None


def apply_image_mode(image, context):
    """Apply color mode conversion based on context settings."""
    if image is None:
        return None
    if context['image_mode'] == 'grayscale':
        return convert_image_to_grayscale(image)
    if context['image_mode'] == 'red_and_black':
        return convert_image_to_red_and_black(image)
    if context['image_mode'] == 'colored':
        return image
    return convert_image_to_bw(image, context['image_bw_threshold'])
