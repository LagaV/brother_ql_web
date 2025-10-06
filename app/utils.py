# -*- coding: utf-8 -*-

from PIL import Image
from PIL.ImageOps import colorize
from io import BytesIO
from pdf2image import convert_from_bytes


def convert_image_to_bw(image, threshold):
    fn = lambda x : 255 if x > threshold else 0
    return image.convert('L').point(fn, mode='1') # convert to black and white

def convert_image_to_grayscale(image):
    fn = lambda x : 255 if x > threshold else 0
    return image.convert('L') # convert to greyscale

def convert_image_to_red_and_black(image):
    return colorize(image.convert('L'), black='black', white='white', mid='red')


def imgfile_to_image(file):
    s = BytesIO()
    file.seek(0)  # Reset file stream position
    file.save(s)
    im = Image.open(s)
    return im


def pdffile_to_image(file, dpi):
    s = BytesIO()
    file.seek(0)  # Reset file stream position
    file.save(s)
    s.seek(0)
    im = convert_from_bytes(
        s.read(),
        dpi = dpi
    )[0]
    return im


def get_pdf_page_count(file):
    """Get the number of pages in a PDF without converting"""
    try:
        from PyPDF2 import PdfReader
        s = BytesIO()
        file.seek(0)
        file.save(s)
        s.seek(0)
        reader = PdfReader(s)
        return len(reader.pages)
    except Exception as e:
        from flask import current_app
        current_app.logger.warning('[get_pdf_page_count] Could not get page count: %s', str(e))
        return None

def pdffile_to_single_page(file, dpi, page_number=0):
    """Convert a specific page of a PDF to an image (0-indexed)"""
    try:
        s = BytesIO()
        file.seek(0)
        file.save(s)
        s.seek(0)
        pdf_bytes = s.read()

        from flask import current_app
        current_app.logger.info('[pdffile_to_single_page] Converting page %d at %d DPI', page_number + 1, dpi)

        images = convert_from_bytes(
            pdf_bytes,
            dpi = dpi,
            first_page = page_number + 1,
            last_page = page_number + 1,
            thread_count = 1,
            fmt = 'jpeg'
        )

        if images:
            current_app.logger.info('[pdffile_to_single_page] Successfully converted page %d', page_number + 1)
            return images[0]
        return None
    except Exception as e:
        from flask import current_app
        current_app.logger.error('[pdffile_to_single_page] Failed to convert page %d: %s', page_number + 1, str(e), exc_info=True)
        raise

def pdffile_to_images(file, dpi):
    """Convert all pages of a PDF to a list of images"""
    try:
        s = BytesIO()
        # Reset file pointer in case it was already read
        file.seek(0)
        file.save(s)
        s.seek(0)
        pdf_bytes = s.read()

        # Log PDF size for debugging
        from flask import current_app
        current_app.logger.info('[pdffile_to_images] PDF size: %d bytes, converting at %d DPI', len(pdf_bytes), dpi)

        # Try conversion with optimizations
        current_app.logger.info('[pdffile_to_images] Starting PDF conversion...')
        images = convert_from_bytes(
            pdf_bytes,
            dpi = dpi,
            thread_count = 2,  # Use multiple threads
            fmt = 'jpeg'  # Use JPEG format for faster conversion
        )
        current_app.logger.info('[pdffile_to_images] Successfully converted %d pages', len(images))
        return images
    except Exception as e:
        from flask import current_app
        current_app.logger.error('[pdffile_to_images] Failed to convert PDF: %s', str(e), exc_info=True)
        raise


def image_to_png_bytes(im):
    image_buffer = BytesIO()
    im.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    return image_buffer.read()