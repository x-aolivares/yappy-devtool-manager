"""Clientes de AWS compartidos, con cache.

Existe por una razón medida: el primer ``client()`` de una Session nueva de boto3
se paga entero, y no se hereda entre Sessions porque cada una tiene su propio data
loader y su propio cliente. Medido en esta máquina con ``base-profile`` (el que
usan dev/qa/uat): 365 ms el primer ``client('ssm')`` de una Session nueva, y 5 ms
los siguientes de la misma Session. Con el perfil ``default`` el primero llega a
1400 ms.

O sea que ``Session(...)`` + ``client(...)`` escritos al lado de cada llamada es un
``~440 ms`` por ambiente y por pedido que no compra nada. Antes de este módulo eso
pasaba en dos lugares distintos —los parámetros SSM y las ramas de despliegue— y
cada uno tenía su propia copia. Por eso el cache vive acá y no en ninguno de los
dos: el problema es de boto3, no de dominio.

El cliente es inmutable para un ``(servicio, profile, region, endpoint)``, así que
se reutiliza.
"""

from __future__ import annotations

import threading
import time

# TTL y no lru_cache: un cliente con un token SSO vencido puede quedar trabado
# para siempre. Con TTL se regenera solo a los 15 minutos.
CLIENT_TTL_SECONDS = 900

_cache: dict[tuple, tuple[float, object]] = {}
# Un lock por clave y no uno global: ambientes distintos tienen profile/region
# distintos, así que sus clientes se construyen EN PARALELO en vez de encolarse.
# El global solo protege los dos diccionarios. El orden de toma siempre es
# key_lock -> cache_lock, nunca al revés, así que no hay deadlock.
_cache_lock = threading.Lock()
_key_locks: dict[tuple, threading.Lock] = {}


def clear_cache() -> None:
    """Vaciar el cache. Para tests, y para forzar un re-resolve de credenciales."""
    with _cache_lock:
        _cache.clear()
        _key_locks.clear()


def _botocore_config():
    """Timeouts y retries para una herramienta que habla con AWS desde una laptop.

    Los defaults son 60s para los dos, así que una región inalcanzable retiene el
    request un minuto. El modo `standard` de retries hace backoff exponencial en
    vez de insists, y keepalive reutiliza la conexión TCP entre llamadas.
    """
    from botocore.config import Config as _BotoConfig

    return _BotoConfig(
        connect_timeout=5,
        read_timeout=15,
        retries={"max_attempts": 3, "mode": "standard"},
        tcp_keepalive=True,
    )


def _create(service: str, profile: str, region: str, endpoint: str | None):
    import boto3

    session = boto3.session.Session(profile_name=profile, region_name=region)
    config = _botocore_config()
    if endpoint:
        return session.client(service, endpoint_url=endpoint, config=config)
    return session.client(service, config=config)


def get_client(service: str, profile: str, region: str, endpoint_url: str | None = None):
    """Cliente de `service` para (profile, region), reutilizado entre llamadas.

    `endpoint_url` es parte de la clave a propósito: el mismo profile/region puede
    estar hablando con AWS real en un caso y con LocalStack en otro, y un cliente
    del otro endpoint serviría con el nombre de la región equivocado.
    """
    key = (service, profile, region, endpoint_url)

    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < CLIENT_TTL_SECONDS:
            return hit[1]
        key_lock = _key_locks.setdefault(key, threading.Lock())

    with key_lock:
        # Otro thread puede haberlo construido mientras éste esperaba el lock.
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None and time.monotonic() - hit[0] < CLIENT_TTL_SECONDS:
                return hit[1]
        client = _create(service, profile, region, endpoint_url)
        with _cache_lock:
            _cache[key] = (time.monotonic(), client)
        return client


def client_for_cfg(service: str, cfg) -> object:
    """Cliente para un `Config`, resolviendo el endpoint de LocalStack.

    El perfil `localstack` implica `http://localhost:4566` salvo que la config
    diga otra cosa: es el único servicio donde el endpoint no viene explícito.
    """
    endpoint = getattr(cfg, "endpoint_url", None)
    if endpoint is None and cfg.profile == "localstack":
        endpoint = "http://localhost:4566"
    return get_client(service, cfg.profile, cfg.region, endpoint)