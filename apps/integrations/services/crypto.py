import base64
import hashlib
import hmac
import json
import os
from hashlib import pbkdf2_hmac

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signing import BadSignature, Signer


try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - exercised in environments without optional dependency.
    Fernet = None
    InvalidToken = Exception


def _key_material():
    configured = getattr(settings, "INTEGRATION_ENCRYPTION_KEY", "")
    raw_key = configured or settings.SECRET_KEY
    if not raw_key or raw_key == "change-me-only-for-local-development":
        # Local/dev fallback is deterministic for tests but still avoids plaintext persistence.
        raw_key = "cotix-local-integration-key"
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet():
    if Fernet is None:
        return None
    return Fernet(_key_material())


def encrypt_json(value):
    if value in (None, "", {}, []):
        return ""
    payload = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    fernet = _fernet()
    if fernet is not None:
        return "fernet:" + fernet.encrypt(payload).decode("utf-8")
    return "local:" + _fallback_encrypt(payload)


def decrypt_json(value, default=None):
    if not value:
        return default
    try:
        if value.startswith("fernet:"):
            fernet = _fernet()
            if fernet is None:
                raise ImproperlyConfigured("Instala cryptography para descifrar credenciales Fernet.")
            payload = fernet.decrypt(value.removeprefix("fernet:").encode("utf-8"))
        elif value.startswith("local:"):
            payload = _fallback_decrypt(value.removeprefix("local:"))
        else:
            fernet = _fernet()
            if fernet is None:
                payload = _fallback_decrypt(value)
            else:
                payload = fernet.decrypt(value.encode("utf-8"))
        return json.loads(payload.decode("utf-8"))
    except InvalidToken as exc:
        raise ImproperlyConfigured("No se pudieron descifrar credenciales de integracion.") from exc


def encrypt_text(value):
    return encrypt_json({"value": value}) if value not in (None, "") else ""


def decrypt_text(value, default=""):
    decoded = decrypt_json(value, default={})
    return decoded.get("value", default) if isinstance(decoded, dict) else default


class LegacySignedPayload:
    """Compatibilidad local si una DB de desarrollo fue creada antes de instalar cryptography."""

    @staticmethod
    def dumps(value):
        salt = settings.SECRET_KEY.encode("utf-8")
        derived = pbkdf2_hmac("sha256", json.dumps(value).encode("utf-8"), salt, 1000)
        return Signer().sign(base64.urlsafe_b64encode(derived).decode("ascii"))

    @staticmethod
    def loads(value):
        try:
            Signer().unsign(value)
        except BadSignature:
            return None
        return None


def _fallback_key():
    return base64.urlsafe_b64decode(_key_material())


def _fallback_stream(nonce, length):
    key = _fallback_key()
    output = b""
    counter = 0
    while len(output) < length:
        output += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return output[:length]


def _fallback_encrypt(payload):
    nonce = os.urandom(16)
    stream = _fallback_stream(nonce, len(payload))
    ciphertext = bytes(a ^ b for a, b in zip(payload, stream))
    tag = hmac.new(_fallback_key(), nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + tag + ciphertext).decode("ascii")


def _fallback_decrypt(value):
    raw = base64.urlsafe_b64decode(value.encode("ascii"))
    nonce, tag, ciphertext = raw[:16], raw[16:48], raw[48:]
    expected = hmac.new(_fallback_key(), nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ImproperlyConfigured("No se pudieron descifrar credenciales de integracion.")
    stream = _fallback_stream(nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream))
