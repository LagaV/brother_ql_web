import requests
import io
import base64
import logging
from PIL import Image

logger = logging.getLogger(__name__)

class RemotePrinterQueue:
    """Forwards print jobs to a remote brother_ql_web instance"""

    def __init__(self, remote_url, label_size):
        self.remote_url = remote_url.rstrip('/')
        self.label_size = label_size
        self._printQueue = []

    def add_label_to_queue(self, label, count, cut_once=False):
        for cnt in range(0, count):
            cut = (not cut_once) or (cut_once and cnt == count-1)
            self._printQueue.append({'label': label, 'cut': cut})

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
        """Send each label to remote printer via /api/print endpoint"""
        for idx, queue_entry in enumerate(self._printQueue, 1):
            img = queue_entry['label'].generate()

            # Add a very light grey pixel in the last row to prevent cropping
            # This ensures the full image height is preserved on the remote server
            if img.height > 0 and img.width > 0:
                pixels = img.load()
                # Set last pixel to very light grey (249, almost white but prevents getbbox from cropping)
                pixels[img.width - 1, img.height - 1] = (249, 249, 249)

            # Convert PIL Image to PNG bytes
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            buffered.seek(0)

            # Prepare multipart form data
            files = {
                'image': ('label.png', buffered, 'image/png')
            }

            # Send explicit font parameters to work around remote server that requires them
            # even for image-only prints (until remote server is updated with the fix)
            data = {
                'label_size': self.label_size,
                'orientation': 'standard',
                'margin_top': '0',
                'margin_bottom': '0',
                'margin_left': '0',
                'margin_right': '0',
                'print_type': 'image',
                'image_mode': 'grayscale',
                'print_count': '1',
                'cut_once': '1' if queue_entry['cut'] else '0',
                # Font parameters - required by older remote servers even for images
                'font_family': 'DejaVu Serif',
                'font_style': 'Book',
                # Prevent remote server from cropping whitespace (for paged prints)
                'no_crop': '1'
            }

            try:
                url = f"{self.remote_url}/labeldesigner/api/print"
                logger.info(f"Sending label {idx}/{len(self._printQueue)} to remote printer: {url}")
                logger.debug(f"Request data: {data}")
                logger.debug(f"Image size: {img.size}, mode: {img.mode}")

                response = requests.post(url, files=files, data=data, timeout=30)
                response.raise_for_status()

                logger.info(f"Remote printer response: {response.text}")
                logger.debug(f"Response status: {response.status_code}")

                # Check if the response indicates success
                try:
                    result = response.json()
                    if not result.get('success', False):
                        error_msg = result.get('message', 'Unknown error')
                        raise Exception(f"Remote printer failed: {error_msg}")
                except ValueError:
                    # Not JSON response, assume success if status is 200
                    pass

            except requests.exceptions.RequestException as e:
                logger.error(f"Remote printer error: {str(e)}")
                raise Exception(f"Remote printer error: {str(e)}")

        self._printQueue.clear()
