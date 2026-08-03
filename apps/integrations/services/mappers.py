from decimal import Decimal, InvalidOperation

from .exceptions import IntegrationMappingError, IntegrationValidationError


REQUIRED_FIELDS = {"sku", "name", "unit_price"}


FIELD_ALIASES = {
    "sku": ["sku", "codigo", "code", "item_code", "product_code", "id", "external_id"],
    "name": ["name", "nombre", "descripcion", "description", "producto", "product_name"],
    "description": ["description", "descripcion", "detalle"],
    "unit_price": ["unit_price", "price", "precio", "precio_unitario", "valor"],
    "unit": ["unit", "unidad", "uom"],
    "category": ["category", "categoria", "linea"],
}


def get_path_value(data, path, default=None):
    if not path:
        return data
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict):
            current = current.get(part, default)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return default
    return current


def extract_array_from_response(response, response_array_path=""):
    if response_array_path:
        value = get_path_value(response, response_array_path)
        if not isinstance(value, list):
            raise IntegrationMappingError("No se encontro un array en response_array_path.")
        return value
    if isinstance(response, list):
        return response
    for candidate in ("data.items", "data.products", "items", "products", "productos", "result", "results", "data"):
        value = get_path_value(response, candidate)
        if isinstance(value, list):
            return value
    raise IntegrationMappingError("No se pudo detectar el array de productos.")


def detected_fields(records, limit=20):
    fields = []
    seen = set()
    for record in records[:limit]:
        if not isinstance(record, dict):
            continue
        for key in record.keys():
            if key not in seen:
                seen.add(key)
                fields.append(key)
    return fields


def suggest_mapping(records):
    fields = detected_fields(records)
    lower = {field.lower(): field for field in fields}
    mapping = {}
    for cotix_field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in lower:
                mapping[cotix_field] = lower[alias]
                break
    return mapping


def apply_filters(records, filters):
    filtered = records
    for filter_item in filters or []:
        field = filter_item.get("field")
        operator = filter_item.get("operator", "eq")
        expected = filter_item.get("value")
        if not field:
            continue
        filtered = [record for record in filtered if _matches_filter(record, field, operator, expected)]
    return filtered


def _normalize(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().lower()


def _matches_filter(record, field, operator, expected):
    value = get_path_value(record, field)
    if operator == "eq":
        return _normalize(value) == _normalize(expected)
    if operator == "neq":
        return _normalize(value) != _normalize(expected)
    if operator == "contains":
        return _normalize(expected) in _normalize(value)
    if operator == "gt":
        return Decimal(str(value)) > Decimal(str(expected))
    if operator == "gte":
        return Decimal(str(value)) >= Decimal(str(expected))
    if operator == "lt":
        return Decimal(str(value)) < Decimal(str(expected))
    if operator == "lte":
        return Decimal(str(value)) <= Decimal(str(expected))
    return True


def map_record(record, mapping):
    missing = sorted(REQUIRED_FIELDS - set(mapping.keys()))
    if missing:
        raise IntegrationMappingError(f"Mapeo incompleto: {', '.join(missing)}.")
    mapped = {}
    for target, source_path in mapping.items():
        mapped[target] = get_path_value(record, source_path, "")
    sku = str(mapped.get("sku") or "").strip()
    name = str(mapped.get("name") or "").strip()
    if not sku:
        raise IntegrationValidationError("Producto sin SKU/external_id.")
    if not name:
        raise IntegrationValidationError("Producto sin nombre.")
    try:
        unit_price = Decimal(str(mapped.get("unit_price"))).quantize(Decimal("0.0001"))
    except (InvalidOperation, TypeError):
        raise IntegrationValidationError("Precio no numerico.")
    if unit_price < 0:
        raise IntegrationValidationError("Precio negativo.")
    return {
        "external_id": sku,
        "sku": sku.upper(),
        "name": name,
        "description": str(mapped.get("description") or "").strip(),
        "unit_price": unit_price,
        "unit": str(mapped.get("unit") or "unidad").strip() or "unidad",
        "category": str(mapped.get("category") or "").strip(),
    }


def preview_records(records, mapping, limit=10):
    output = []
    errors = []
    for index, record in enumerate(records[:limit]):
        try:
            output.append(map_record(record, mapping))
        except Exception as exc:
            errors.append({"row": index + 1, "error": str(exc)})
    return output, errors
