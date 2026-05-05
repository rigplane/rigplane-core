"""TLS certificate management for the web server."""

from __future__ import annotations

import datetime
import ipaddress
import logging
import pathlib
import socket
import ssl
import subprocess

__all__ = ["build_ssl_context", "generate_self_signed"]

logger = logging.getLogger(__name__)

_DEFAULT_SSL_DIR = pathlib.Path.home() / ".icom-lan" / "ssl"
_DEFAULT_CERT = _DEFAULT_SSL_DIR / "cert.pem"
_DEFAULT_KEY = _DEFAULT_SSL_DIR / "key.pem"


def _generate_with_cryptography(
    cert_path: pathlib.Path,
    key_path: pathlib.Path,
    days: int,
    hostname: str,
) -> None:
    """Generate a self-signed cert using the ``cryptography`` library."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "icom-lan"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "icom-lan"),
        ]
    )

    san_entries: list[x509.GeneralName] = [
        x509.DNSName(hostname),
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]

    # Try to resolve the hostname to an IP for SAN
    try:
        host_ip = socket.gethostbyname(hostname)
        if host_ip != "127.0.0.1":
            san_entries.append(x509.IPAddress(ipaddress.ip_address(host_ip)))
    except OSError:
        pass

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _generate_with_openssl(
    cert_path: pathlib.Path,
    key_path: pathlib.Path,
    days: int,
    hostname: str,
) -> None:
    """Generate a self-signed cert using the ``openssl`` CLI."""
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-days",
            str(days),
            "-nodes",
            "-subj",
            f"/CN={hostname}/O=icom-lan",
        ],
        check=True,
        capture_output=True,
    )


def generate_self_signed(
    cert_path: pathlib.Path = _DEFAULT_CERT,
    key_path: pathlib.Path = _DEFAULT_KEY,
    days: int = 365,
    hostname: str | None = None,
) -> tuple[pathlib.Path, pathlib.Path]:
    """Generate a self-signed certificate if it doesn't already exist.

    Returns:
        ``(cert_path, key_path)`` tuple.
    """
    if cert_path.exists() and key_path.exists():
        logger.debug("reusing existing TLS cert: %s", cert_path)
        return cert_path, key_path

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    san_host = hostname or socket.gethostname()

    try:
        _generate_with_cryptography(cert_path, key_path, days, san_host)
        logger.info("generated self-signed TLS cert (cryptography): %s", cert_path)
    except ImportError:
        _generate_with_openssl(cert_path, key_path, days, san_host)
        logger.info("generated self-signed TLS cert (openssl): %s", cert_path)

    return cert_path, key_path


def build_ssl_context(
    cert_path: pathlib.Path | str | None = None,
    key_path: pathlib.Path | str | None = None,
) -> ssl.SSLContext:
    """Build an SSL context for the web server.

    If *cert_path* / *key_path* are ``None``, a self-signed certificate is
    auto-generated under ``~/.icom-lan/ssl/``.
    """
    if cert_path is None or key_path is None:
        cert_path, key_path = generate_self_signed()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx
