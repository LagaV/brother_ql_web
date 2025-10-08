"""Dimension calculation utilities."""

from flask import current_app
from brother_ql.devicedependent import label_type_specs

DEFAULT_DPI = 300


def mm_to_pixels(mm_value, dpi):
    """Convert millimeters to pixels at given DPI."""
    try:
        mm_float = float(mm_value)
    except (TypeError, ValueError):
        mm_float = 0.0
    return int(round(mm_float / 25.4 * dpi))


def get_label_spec(label_size):
    """Get label specifications for a given label size."""
    try:
        return label_type_specs[label_size]
    except KeyError as exc:
        raise LookupError("Unknown label_size") from exc


def get_label_dimensions(label_size):
    """Get printable dimensions for a label size."""
    spec = get_label_spec(label_size)
    dims = spec['dots_printable']
    return dims[0], dims[1]
    try:
        mm = float(raw_value) / 10.0
    except (TypeError, ValueError):
        fallback = current_app.config[default_config_key]
        try:
            mm = float(fallback) / 10.0
        except (TypeError, ValueError):
            mm = 0.0
    return int(round(mm * dpi / 25.4))


def get_label_spec(label_size):
    """Get label specifications for a given label size."""
    try:
        return label_type_specs[label_size]
    except KeyError as exc:
        raise LookupError("Unknown label_size") from exc


def get_label_dimensions(label_size):
    """Get printable dimensions for a label size."""
    spec = get_label_spec(label_size)
    dims = spec['dots_printable']
    return dims[0], dims[1]
