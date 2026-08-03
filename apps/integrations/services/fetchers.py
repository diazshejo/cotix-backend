import base64
import csv
import io
import json
import time
from urllib import error, request
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from django.conf import settings
from rest_framework import serializers

from .crypto import decrypt_json, decrypt_text
from .exceptions import IntegrationConnectionError
from .mappers import extract_array_from_response
from .validators import validate_external_url


REQUEST_TIMEOUT_SECONDS = getattr(settings, "INTEGRATION_REQUEST_TIMEOUT_SECONDS", 15)
CSV_MAX_BYTES = getattr(settings, "INTEGRATION_CSV_MAX_BYTES", 2 * 1024 * 1024)
CSV_MAX_ROWS = getattr(settings, "INTEGRATION_CSV_MAX_ROWS", 5000)
CSV_MAX_FIELDS = getattr(settings, "INTEGRATION_CSV_MAX_FIELDS", 50)
CSV_MAX_CELL_LENGTH = getattr(settings, "INTEGRATION_CSV_MAX_CELL_LENGTH", 1000)


class SafeRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_external_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RestAPIFetcher:
    def __init__(self, integration):
        self.integration = integration

    def fetch_all(self):
        response = self._make_request()
        return extract_array_from_response(response, self.integration.response_array_path)

    def fetch_raw(self):
        return self._make_request()

    def build_headers(self):
        headers = {"Accept": "application/json", "User-Agent": "Cotix-Integrations/1.0"}
        custom_headers = decrypt_json(self.integration.custom_headers_enc, default={}) or {}
        for key, value in custom_headers.items():
            if str(key).lower() in {"host", "content-length"}:
                continue
            headers[str(key)] = str(value)
        credentials = decrypt_text(self.integration.credentials_enc)
        if self.integration.auth_type == "api_key" and credentials:
            header = self.integration.auth_header_name or "X-API-Key"
            headers[header] = credentials
        elif self.integration.auth_type == "bearer" and credentials:
            headers["Authorization"] = f"Bearer {credentials}"
        elif self.integration.auth_type == "basic" and credentials:
            token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _make_request(self):
        validate_external_url(self.integration.endpoint_url)
        body = None
        headers = self.build_headers()
        method = self.integration.http_method
        if method == "POST":
            headers.setdefault("Content-Type", "application/json")
            body = (self.integration.request_body_template or "{}").encode("utf-8")
        req = request.Request(self.integration.endpoint_url, data=body, headers=headers, method=method)
        for attempt, wait_seconds in enumerate((0, 1, 3), start=1):
            if wait_seconds:
                time.sleep(wait_seconds)
            try:
                opener = request.build_opener(SafeRedirectHandler)
                with opener.open(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    content = response.read().decode(charset)
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError as exc:
                        raise IntegrationConnectionError("La respuesta no es JSON valido.") from exc
            except error.HTTPError as exc:
                if exc.code in {408, 429, 500, 502, 503, 504} and attempt < 3:
                    continue
                raise IntegrationConnectionError(f"HTTP {exc.code}: no se pudo leer el endpoint.") from exc
            except (error.URLError, TimeoutError) as exc:
                if attempt < 3:
                    continue
                raise IntegrationConnectionError("Timeout o error de conexion con el endpoint.") from exc


class CSVFetcher:
    def fetch_from_upload(self, uploaded_file):
        self._validate_upload(uploaded_file)
        content = uploaded_file.read()
        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise serializers.ValidationError({"csv_file": ["El archivo CSV debe estar codificado en UTF-8."]}) from exc
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            raise serializers.ValidationError({"csv_file": ["El archivo CSV debe incluir encabezados."]})
        if len(reader.fieldnames) > CSV_MAX_FIELDS:
            raise serializers.ValidationError({"csv_file": [f"El CSV no puede tener mas de {CSV_MAX_FIELDS} columnas."]})
        rows = []
        for index, row in enumerate(reader, start=1):
            if index > CSV_MAX_ROWS:
                raise serializers.ValidationError({"csv_file": [f"El CSV no puede tener mas de {CSV_MAX_ROWS} filas."]})
            clean_row = {}
            for key, value in row.items():
                clean_key = (key or "").strip()
                clean_value = "" if value is None else str(value).strip()
                if len(clean_value) > CSV_MAX_CELL_LENGTH:
                    raise serializers.ValidationError({"csv_file": [f"Una celda supera el limite de {CSV_MAX_CELL_LENGTH} caracteres."]})
                clean_row[clean_key] = clean_value
            rows.append(clean_row)
        return rows

    def _validate_upload(self, uploaded_file):
        name = (getattr(uploaded_file, "name", "") or "").lower()
        content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
        size = getattr(uploaded_file, "size", None)
        if name and not name.endswith(".csv"):
            raise serializers.ValidationError({"csv_file": ["Solo se permiten archivos .csv."]})
        if content_type and content_type not in {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"}:
            raise serializers.ValidationError({"csv_file": ["El archivo debe ser CSV."]})
        if size is not None and size > CSV_MAX_BYTES:
            raise serializers.ValidationError({"csv_file": [f"El CSV no puede superar {CSV_MAX_BYTES} bytes."]})


def add_query_params(url, params):
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))
