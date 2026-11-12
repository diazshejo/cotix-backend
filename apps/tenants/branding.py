from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings


def tenant_logo_path(tenant):
    """Return only a logo path owned by this tenant; never trust an arbitrary URL."""
    try:
        path = urlparse(tenant.logo_url or "").path
    except ValueError:
        return ""
    extension = Path(path).suffix.lower()
    if extension not in {".png", ".jpg", ".jpeg"}:
        return ""
    expected = f"{settings.MEDIA_URL}logos/{tenant.pk}{extension}"
    return expected if path == expected else ""


def tenant_logo_url(tenant, request):
    path = tenant_logo_path(tenant)
    return request.build_absolute_uri(path) if path and request else ""
