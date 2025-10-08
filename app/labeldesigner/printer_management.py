"""Printer configuration management."""

import os
import json
from flask import current_app

from .printer import PrinterQueue
from .remote_printer import RemotePrinterQueue


def get_printers_json_path():
    """Get path to printers.json file."""
    path = current_app.config.get('PRINTERS_JSON_PATH')
    if path:
        return path
    instance_path = current_app.instance_path
    os.makedirs(instance_path, exist_ok=True)
    return os.path.join(instance_path, 'printers.json')


def load_printers_from_json():
    """Load printers from JSON file."""
    json_path = get_printers_json_path()
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_printers_to_json(printers):
    """Save printers to JSON file."""
    json_path = get_printers_json_path()
    with open(json_path, 'w') as f:
        json.dump(printers, f, indent=2)


def get_available_printers():
    """Get list of configured printers."""
    printers = current_app.config.get('PRINTERS')
    if printers is not None:
        return printers

    printers = load_printers_from_json()
    if printers:
        return printers

    return [{
        'id': 'default',
        'name': 'Default Printer',
        'type': 'local',
        'model': current_app.config['PRINTER_MODEL'],
        'device': current_app.config['PRINTER_PRINTER'],
        'default': True
    }]


def get_default_printer():
    """Get the default printer configuration."""
    printers = get_available_printers()
    for printer in printers:
        if printer.get('default', False):
            return printer
    return printers[0] if printers else None


def create_printer_queue(label_size, printer_id=None):
    """Create printer queue for specified or default printer."""
    printers = get_available_printers()

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

    if printer_config['type'] == 'remote':
        return RemotePrinterQueue(
            remote_url=printer_config['url'],
            label_size=label_size
        )
    else:
        return PrinterQueue(
            model=printer_config['model'],
            device_specifier=printer_config['device'],
            label_size=label_size
        )


def update_printer_status_support(printer_id, supports_status):
    """Update printer configuration to cache status support."""
    if current_app.config.get('PRINTERS') is not None:
        return

    printers = load_printers_from_json()
    updated = False

    for printer in printers:
        if printer.get('id') == printer_id:
            if printer.get('supports_status') != supports_status:
                printer['supports_status'] = supports_status
                updated = True
            break

    if updated:
        save_printers_to_json(printers)
        current_app.logger.info("Updated printer %s status support: %s", printer_id, supports_status)
        current_app.logger.info("Updated printer %s status support: %s", printer_id, supports_status)
