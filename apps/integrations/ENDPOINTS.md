# Integrations API V1

Base path: `/api/integrations/catalog/`

Auth: JWT requerido en todos los endpoints. Todas las respuestas son tenant-scoped.

## Listar integraciones

`GET /api/integrations/catalog/`

Devuelve integraciones del tenant autenticado.

## Crear integracion

`POST /api/integrations/catalog/`

Solo tenants con plan `premium` o `business`.

Campos principales:

```json
{
  "name": "ERP principal",
  "integration_type": "rest_api",
  "endpoint_url": "https://api.example.com/products",
  "http_method": "GET",
  "auth_type": "api_key",
  "auth_header_name": "X-API-Key",
  "credentials": "secret",
  "field_mapping": {
    "sku": "codigo",
    "name": "descripcion",
    "unit_price": "precio",
    "unit": "unidad"
  },
  "response_array_path": "data.items",
  "pre_import_filters": [
    {"field": "activo", "operator": "eq", "value": "true"}
  ],
  "sync_mode": "manual",
  "is_active": true
}
```

`credentials` y `custom_headers` son write-only. La API nunca devuelve `credentials_enc`, `custom_headers_enc` ni `connection_string_enc`.

## Editar integracion

`PATCH /api/integrations/catalog/{id}/`

Activar una integracion (`is_active=true`) requiere plan `premium` o `business`.

## Eliminar integracion

`DELETE /api/integrations/catalog/{id}/`

Eliminacion logica.

## Probar conexion

`POST /api/integrations/catalog/{id}/test/`

REST API: ejecuta request de lectura y devuelve muestra. CSV: requiere multipart con `csv_file`.

Respuesta:

```json
{
  "status": "success",
  "products_found": 10,
  "sample_products": [],
  "detected_fields": ["codigo", "descripcion", "precio"],
  "suggested_mapping": {"sku": "codigo", "name": "descripcion", "unit_price": "precio"},
  "response_time_ms": 120
}
```

Errores visibles son redactados para no exponer secretos.

## Preview

`GET /api/integrations/catalog/{id}/preview/`

Para REST API.

`POST /api/integrations/catalog/{id}/preview/`

Para CSV con multipart `csv_file`.

Devuelve registros raw, preview mapeado, errores de mapping, campos detectados y mapping sugerido. No importa productos.

## Sync manual

`POST /api/integrations/catalog/{id}/sync/`

Requiere integracion activa y plan `premium` o `business`. CSV requiere multipart `csv_file`.

Devuelve un `IntegrationSyncLog` con `results`:

```json
{
  "results": {
    "total_fetched": 100,
    "created": 5,
    "updated": 10,
    "skipped": 85,
    "errors": 0
  }
}
```

## Historial

`GET /api/integrations/catalog/{id}/logs/`

Lista logs del tenant para esa integracion.

`GET /api/integrations/catalog/{id}/logs/{log_id}/`

Detalle de un log.
