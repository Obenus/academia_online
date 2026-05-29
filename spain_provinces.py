"""Provincias de España (península, islas, Ceuta y Melilla)."""

CITY_OTHER_VALUE = '__otra__'
CITY_OTHER_LABEL = 'Otra ciudad'

SPANISH_PROVINCES = [
    'A Coruña',
    'Álava',
    'Albacete',
    'Alicante',
    'Almería',
    'Asturias',
    'Ávila',
    'Badajoz',
    'Baleares',
    'Barcelona',
    'Burgos',
    'Cáceres',
    'Cádiz',
    'Cantabria',
    'Castellón',
    'Ceuta',
    'Ciudad Real',
    'Córdoba',
    'Cuenca',
    'Girona',
    'Granada',
    'Guadalajara',
    'Guipúzcoa',
    'Huelva',
    'Huesca',
    'Jaén',
    'La Rioja',
    'Las Palmas',
    'León',
    'Lleida',
    'Lugo',
    'Madrid',
    'Málaga',
    'Melilla',
    'Murcia',
    'Navarra',
    'Ourense',
    'Palencia',
    'Pontevedra',
    'Salamanca',
    'Santa Cruz de Tenerife',
    'Segovia',
    'Sevilla',
    'Soria',
    'Tarragona',
    'Teruel',
    'Toledo',
    'Valencia',
    'Valladolid',
    'Vizcaya',
    'Zamora',
    'Zaragoza',
]


def parse_city_from_form(form) -> str:
    """Resuelve ciudad desde select de provincia u «Otra ciudad»."""
    selected = (form.get('city') or '').strip()
    if selected == CITY_OTHER_VALUE:
        other = (form.get('city_other') or '').strip()
        return other or CITY_OTHER_LABEL
    return selected


def city_form_from_request(form) -> dict:
    """Estado del formulario tras POST (conserva selección y texto libre)."""
    selected = (form.get('city') or '').strip()
    other = (form.get('city_other') or '').strip()
    return {
        'selected': selected,
        'other': other,
        'is_other': selected == CITY_OTHER_VALUE,
    }


def city_form_state(stored_city: str) -> dict:
    """Estado para plantillas: provincia seleccionada y texto libre si aplica."""
    stored = (stored_city or '').strip()
    if not stored:
        return {'selected': '', 'other': '', 'is_other': False}
    if stored in SPANISH_PROVINCES:
        return {'selected': stored, 'other': '', 'is_other': False}
    return {'selected': CITY_OTHER_VALUE, 'other': stored, 'is_other': True}
