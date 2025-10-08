"""Flask routes for label designer - route handlers only."""

import base64
import uuid
from flask import current_app, render_template, request, make_response, jsonify

from brother_ql.devicedependent import label_type_specs, label_sizes, two_color_support
from brother_ql.devicedependent import ROUND_DIE_CUT_LABEL

from . import bp
from app.utils import image_to_png_bytes
from app import FONTS

from .context_builder import build_label_context_from_request, build_label_context_from_json
from .label_factory import create_label_from_context, create_label_from_request
from .printer_management import (
    get_available_printers,
    create_printer_queue,
    load_printers_from_json,
    save_printers_to_json,
    update_printer_status_support
)

LINE_SPACINGS = (100, 150, 200, 250, 300)
DEFAULT_DPI = 300

LABEL_SIZES = [(
    name,
    label_type_specs[name]['name'],
    (label_type_specs[name]['kind'] in (ROUND_DIE_CUT_LABEL,)),
    label_type_specs[name]['dots_printable'][0]
) for name in label_sizes]


@bp.route('/printers')
def printers_page():
    """Printer management page."""
    return render_template('printers.html')


@bp.route('/')
def index():
    """Main label designer page."""
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
                           default_margin_right=current_app.config['LABEL_DEFAULT_MARGIN_RIGHT'])


@bp.route('/api/font/styles', methods=['POST', 'GET'])
def get_font_styles():
    """Get available font styles for a font family."""
    font_family_name = request.values.get('font', current_app.config['LABEL_DEFAULT_FONT_FAMILY'])

    # Normalize common UI shorthand (e.g. 'Noto' -> prefer 'Noto Sans' family)
    if font_family_name == 'Noto':
        candidates = ['Noto Sans', 'Noto Serif', 'Noto']
    else:
        candidates = [font_family_name]

    selected_styles = {}
    for cand in candidates:
        cand_map = FONTS.fonts.get(cand, {})
        if not cand_map:
            continue
        # Filter out symbol/emoji entries
        for style, path in cand_map.items():
            if "symbols" in style.lower() or "emoji" in style.lower():
                continue
            if "symbols" in path.lower() or "emoji" in path.lower():
                continue
            selected_styles[style] = path
        if selected_styles:
            break

    # Ensure 'Regular' is first in the returned list if present
    styles = sorted(selected_styles.keys(), key=lambda x: (x != 'Regular', x.lower()))
    return jsonify(styles)


@bp.route('/api/preview', methods=['POST', 'GET'])
def get_preview_from_image():
    """Generate preview of label."""
    try:
        context = build_label_context_from_request(request)
        label = create_label_from_context(context, image_file=request.files.get('image', None))
        labels = getattr(label, '_markdown_labels', None) or getattr(label, '_pdf_page_labels', None)
        label_list = labels if labels else [label]
        images = [lbl.generate() for lbl in label_list]

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
    """Generate markdown preview."""
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
    """API to print a label."""
    return_dict = {'success': False}

    try:
        printer = create_printer_queue(
            request.values.get('label_size', '62'),
            request.values.get('printer_id', None)
        )
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
    """Print markdown content via JSON API."""
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


@bp.route('/api/printers', methods=['GET'])
def api_list_printers():
    """List all configured printers."""
    printers = get_available_printers()
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
    """Get full printer configurations for management UI."""
    # Check if using config-based printers (read-only)
    if current_app.config.get('PRINTERS') is not None:
        return jsonify({
            'printers': current_app.config.get('PRINTERS'),
            'readonly': True,
            'message': 'Printers are configured in config file (read-only)'
        })

    printers = load_printers_from_json()
    return jsonify({'printers': printers, 'readonly': False})


@bp.route('/api/printers/manage', methods=['POST'])
def api_add_printer():
    """Add a new printer."""
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
    """Update an existing printer."""
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
    """Delete a printer."""
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
            update_printer_status_support(printer_id or printer.get('id'), False)
            return jsonify({
                'success': False,
                'error': 'Printer does not support status queries',
                'supported': False
            })

        update_printer_status_support(printer_id or printer.get('id'), True)

        if status.get('media_color') == 'red' and status.get('media_type'):
            status['media_type'] = status['media_type'] + '_red'

        return jsonify({'success': True, 'status': status})

    except Exception as e:
        current_app.logger.error("Error querying printer status: %s", e, exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'supported': False
        }), 500
        current_app.logger.error("Error querying printer status: %s", e, exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'supported': False
        }), 500
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


