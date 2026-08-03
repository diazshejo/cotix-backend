import ipaddress
import socket
from urllib.parse import urlparse

from rest_framework import serializers


PRIVATE_HOSTNAMES = {"localhost", "metadata.google", "metadata.google.internal"}


def validate_external_url(value):
    parsed = urlparse(value or "")
    if parsed.scheme not in {"https", "http"}:
        raise serializers.ValidationError("La URL debe usar http o https.")
    if parsed.username or parsed.password:
        raise serializers.ValidationError("La URL no debe incluir credenciales.")
    if not parsed.hostname:
        raise serializers.ValidationError("La URL debe incluir host.")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        # V1 permite http solo en tests/local; el bloqueo SSRF posterior rechaza localhost.
        raise serializers.ValidationError("Usa https para endpoints externos.")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in PRIVATE_HOSTNAMES or hostname.endswith(".local"):
        raise serializers.ValidationError("La URL apunta a un host no permitido.")
    addresses = []
    try:
        addresses.append(ipaddress.ip_address(hostname))
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(hostname, None)]
        except socket.gaierror:
            addresses = []
    for address in addresses:
        if address.is_private or address.is_loopback or address.is_link_local or address.is_multicast:
            raise serializers.ValidationError("La URL apunta a una red interna no permitida.")
    return value
