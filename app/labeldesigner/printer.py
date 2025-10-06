from brother_ql.backends import backend_factory, guess_backend
from brother_ql import BrotherQLRaster, create_label
from .label import LabelOrientation, LabelType, LabelContent
import logging

logger = logging.getLogger(__name__)


class PrinterQueue:

    _printQueue = []
    _cutQueue = []

    def __init__(
            self,
            model,
            device_specifier,
            label_size):
        self.model = model
        self.device_specifier = device_specifier
        self.label_size = label_size

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, value):
        self._model = value

    @property
    def device_specifier(self):
        return self._device_specifier

    @device_specifier.setter
    def device_specifier(self, value):
        self._device_specifier = value
        selected_backend = guess_backend(self._device_specifier)
        self._backend_class = backend_factory(
            selected_backend)['backend_class']

    @property
    def label_size(self):
        return self._label_size

    @label_size.setter
    def label_size(self, value):
        self._label_size = value

    def add_label_to_queue(self, label, count, cut_once=False):
        for cnt in range(0, count):
            cut = (cut_once == False) or (cut_once and cnt == count-1)

            self._printQueue.append(
                {'label': label,
                 'cut': cut
                 })

    def add_label_sequence(self, labels, copies, cut_once=False):
        if not labels:
            return
        total_labels = len(labels)
        for copy in range(copies):
            for idx, lbl in enumerate(labels):
                is_last = (copy == copies - 1) and (idx == total_labels - 1)
                cut = (not cut_once) or (cut_once and is_last)
                self._printQueue.append({'label': lbl, 'cut': cut})

    def process_queue(self):
        qlr = BrotherQLRaster(self._model)

        for queue_entry in self._printQueue:
            if queue_entry['label'].label_type == LabelType.ENDLESS_LABEL:
                # Check if image is pre-rotated (rotated markdown)
                if hasattr(queue_entry['label'], 'pre_rotated') and queue_entry['label'].pre_rotated:
                    rotate = 0  # Don't rotate, image is already landscape
                elif queue_entry['label'].label_orientation == LabelOrientation.STANDARD:
                    rotate = 0
                else:
                    rotate = 90
            else:
                rotate = 'auto'

            img = queue_entry['label'].generate()

            if queue_entry['label'].label_content == LabelContent.IMAGE_BW: 
                dither = False
            else:
                dither = True

            create_label(
                qlr,
                img,
                self.label_size,
                red='red' in self.label_size,
                dither=dither,
                cut=queue_entry['cut'],
                rotate=rotate)

        self._printQueue.clear()

        be = self._backend_class(self._device_specifier)
        be.write(qlr.data)
        be.dispose()
        del be

    def get_printer_status(self):
        """
        Query printer for current status including media type.
        Returns dict with status info or None if not supported.

        Returns:
            dict: {
                'media_type': str,      # e.g., '62', '29', etc.
                'media_width_mm': int,  # Width in mm
                'media_color': str,     # 'white' or 'red'
                'errors': list,         # Any error messages
                'supported': bool       # True if status query worked
            }
            None: If printer doesn't support status queries
        """
        try:
            from brother_ql.reader import interpret_response

            # Create backend connection
            be = self._backend_class(self._device_specifier)

            # Send status request: ESC i S (0x1B 0x69 0x53)
            status_request = bytes([0x1B, 0x69, 0x53])
            be.write(status_request)

            # Read 32-byte status response with timeout
            # Note: not all backends support read() method
            if not hasattr(be, 'read'):
                logger.info(f"Backend {self._backend_class} does not support status reading")
                be.dispose()
                return None

            status_bytes = be.read(32)
            be.dispose()

            if not status_bytes or len(status_bytes) < 32:
                logger.warning(f"Incomplete status response: {len(status_bytes) if status_bytes else 0} bytes")
                return None

            # Interpret the status response
            status = interpret_response(status_bytes)

            # Extract media information from status
            result = {
                'supported': True,
                'errors': []
            }

            # Parse media type and width from status bytes
            # Byte 10: Media type
            # Byte 11: Media width (in mm)
            media_width_mm = status_bytes[10] if len(status_bytes) > 10 else 0
            media_type_byte = status_bytes[11] if len(status_bytes) > 11 else 0

            # Map media width to label size identifier
            width_to_label = {
                12: '12',
                29: '29',
                38: '38',
                50: '50',
                54: '54',
                62: '62',
                102: '102',
                103: '103d',
            }

            result['media_width_mm'] = media_width_mm
            result['media_type'] = width_to_label.get(media_width_mm, str(media_width_mm))

            # Check for red/black media (bit in media type byte)
            # This is a simplified check - actual detection may vary by model
            result['media_color'] = 'white'  # Default to white, actual detection TBD

            # Check for errors in status
            if status and isinstance(status, dict):
                if 'errors' in status:
                    result['errors'] = status['errors']
                # Add any additional status fields
                for key in ['printer_state', 'phase', 'notification']:
                    if key in status:
                        result[key] = status[key]

            logger.info(f"Printer status: {result}")
            return result

        except ImportError:
            logger.warning("brother_ql.reader module not available for status reading")
            return None
        except AttributeError as e:
            logger.info(f"Status query not supported by backend: {e}")
            return None
        except Exception as e:
            logger.error(f"Error querying printer status: {e}", exc_info=True)
            return None
