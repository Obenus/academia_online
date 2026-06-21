"""Detección de región de facturación (España vs internacional)."""
from flask import request


def detect_billing_region(default='es'):
    """es | intl — CF-IPCountry / X-Country-Code; si no hay señal, España (default)."""
    explicit = (request.args.get('region') or '').strip().lower()
    if explicit in ('es', 'intl'):
        return explicit
    country = (
        request.headers.get('CF-IPCountry')
        or request.headers.get('X-Country-Code')
        or request.headers.get('X-Appengine-Country')
        or ''
    ).upper()
    if country == 'ES':
        return 'es'
    if country and country not in ('', 'XX', 'T1'):
        return 'intl'
    d = (default or 'es').strip().lower()
    return d if d in ('es', 'intl') else 'es'


def billing_region_label(region):
    return 'España' if region == 'es' else 'Internacional'
