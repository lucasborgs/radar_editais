"""Guard anti-SSRF para fetches de URLs influenciadas por usuário (PR1.1).

Todo `requests.get/head` cujo destino o usuário controla (ex.: `edital_link`
da fila de discovery, URL de onboarding do perfil) passa por aqui: valida o
scheme, resolve o DNS e rejeita destinos privados/internos ANTES de conectar,
revalidando cada hop de redirect (o `requests` segue redirects sem re-checar
o destino, o que permitiria `público → 302 → 169.254.169.254`).

Limite conhecido: sem pinning do IP validado, um DNS rebinding com TTL=0 entre
a validação e o connect ainda é teoricamente possível (TOCTOU). Aceito para o
beta — o guard cobre loopback/RFC1918/link-local/metadata, os vetores práticos.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

MAX_REDIRECTS = 5
_DEFAULT_TIMEOUT = 15


class PrivateAddressError(ValueError):
    """URL resolve para endereço privado/interno — bloqueada pelo anti-SSRF."""


def _addr_is_public(ip_s: str) -> bool:
    addr = ipaddress.ip_address(ip_s)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def assert_public_url(url: str) -> None:
    """Levanta ValueError se a URL não for http(s) apontando para IP público.

    Valida os IPs RESOLVIDOS (não só o hostname): cobre `http://127.0.0.1`,
    `http://169.254.169.254` (metadata de cloud) e hostnames públicos que
    resolvem para RFC1918.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL bloqueada: scheme '{parsed.scheme}' não permitido")
    host = parsed.hostname
    if not host:
        raise ValueError("URL bloqueada: sem hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"URL bloqueada: DNS não resolve ({host})") from e
    for info in infos:
        if not _addr_is_public(info[4][0]):
            raise PrivateAddressError(
                f"URL bloqueada: {host} resolve para endereço privado/interno"
            )


def safe_request(method: str, url: str, **kwargs) -> requests.Response:
    """`requests.request` com anti-SSRF no destino e em CADA hop de redirect."""
    kwargs.setdefault("timeout", _DEFAULT_TIMEOUT)
    kwargs["allow_redirects"] = False
    for _ in range(MAX_REDIRECTS + 1):
        assert_public_url(url)
        resp = requests.request(method, url, **kwargs)
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("location")
            if not location:
                return resp
            url = urljoin(url, location)
            continue
        return resp
    raise ValueError(f"URL bloqueada: mais de {MAX_REDIRECTS} redirects")


def safe_get(url: str, **kwargs) -> requests.Response:
    return safe_request("GET", url, **kwargs)


def safe_head(url: str, **kwargs) -> requests.Response:
    return safe_request("HEAD", url, **kwargs)
