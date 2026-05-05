import re

from rest_framework import serializers


CURRENCIES = {"GTQ", "USD"}
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
NIT_RE = re.compile(r"^(CF|[0-9]{5,12}-?[0-9Kk])$")
PHONE_RE = re.compile(r"^\+?[0-9 ()-]{8,30}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
QUOTE_PREFIX_RE = re.compile(r"^[A-Z0-9]{1,10}$")


def clean_text(value):
    if value is None:
        return value
    return str(value).strip()


def validate_currency(value):
    value = clean_text(value).upper()
    if value not in CURRENCIES:
        raise serializers.ValidationError("La moneda solo puede ser GTQ o USD.")
    return value


def validate_hex_color(value):
    value = clean_text(value)
    if not HEX_COLOR_RE.match(value):
        raise serializers.ValidationError("El color debe tener formato HEX #RRGGBB.")
    return value


def validate_nit(value):
    value = clean_text(value).upper()
    if value and not NIT_RE.match(value):
        raise serializers.ValidationError("El NIT no tiene un formato valido.")
    return value


def validate_phone(value):
    value = clean_text(value)
    if value and not PHONE_RE.match(value):
        raise serializers.ValidationError("El telefono no tiene un formato valido.")
    return value


def validate_slug(value):
    value = clean_text(value).lower()
    if not SLUG_RE.match(value):
        raise serializers.ValidationError("El slug debe estar en minusculas, sin espacios y solo con letras, numeros o guiones.")
    return value


def validate_quote_prefix(value):
    value = clean_text(value).upper()
    if not QUOTE_PREFIX_RE.match(value):
        raise serializers.ValidationError("El prefijo debe estar en mayusculas, sin espacios, maximo 10 caracteres.")
    return value
