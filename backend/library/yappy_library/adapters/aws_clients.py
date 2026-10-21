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
from typing import Any

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


def _lookup(key: tuple):
    """`(cliente cacheado, lock de la clave)`.

    El cliente es `None` si hay que construirlo. Se devuelven los dos juntos para
    no tener que adivinar de qué tipo es lo que quedó: antes el Lock se usaba de
    discriminante, que es un tipo de Sentinel que se rompe en silencio.
    """
    with _cache_lock:
        hit = _cache.get(key)
        # El `fresh` en una variable aparte no le devuelve el narrowing a
        # mypy: no puede saber que `hit` es `None` cuando `fresh` es falso.
        client = (
            hit[1]
            if hit is not None and time.monotonic() - hit[0] < CLIENT_TTL_SECONDS
            else None
        )
        key_lock = _key_locks.setdefault(key, threading.Lock())
        return client, key_lock


def _build(key: tuple, key_lock: threading.Lock, force: bool = False):
    """Construye el cliente bajo el lock de su clave y lo publica en el cache.

    Con `force` se reconstruye aunque el que está en cache siga fresco: es lo que
    usa el re-calentador. El cliente viejo **no** se saca antes de construir el
    nuevo, así que un request que llegue durante el cruce recibe el viejo y no
    paga los ~440 ms.
    """
    with key_lock:
        if not force:
            # Otro thread puede haberlo construido mientras éste esperaba el lock.
            with _cache_lock:
                hit = _cache.get(key)
                if hit is not None and time.monotonic() - hit[0] < CLIENT_TTL_SECONDS:
                    return hit[1]
        service, profile, region, endpoint = key
        client = _create(service, profile, region, endpoint)
        with _cache_lock:
            _cache[key] = (time.monotonic(), client)
        return client


def get_client(service: str, profile: str, region: str, endpoint_url: str | None = None):
    """Cliente de `service` para (profile, region), reutilizado entre llamadas.

    `endpoint_url` es parte de la clave a propósito: el mismo profile/region puede
    estar hablando con AWS real en un caso y con LocalStack en otro, y un cliente
    del otro endpoint serviría con el nombre de la región equivocado.
    """
    key = (service, profile, region, endpoint_url)
    cached, key_lock = _lookup(key)
    return cached if cached is not None else _build(key, key_lock)


def client_for_cfg(service: str, cfg) -> Any:
    """Cliente para un `Config`, resolviendo el endpoint de LocalStack.

    El perfil `localstack` implica `http://localhost:4566` salvo que la config
    diga otra cosa: es el único servicio donde el endpoint no viene explícito.
    """
    endpoint = getattr(cfg, "endpoint_url", None)
    if endpoint is None and cfg.profile == "localstack":
        endpoint = "http://localhost:4566"
    return get_client(service, cfg.profile, cfg.region, endpoint)


# --- Calentado y re-calentado ---------------------------------------------
#
# El TTL solo, sin re-calentador, mete un pico de ~440 ms dentro de un request
# cualquiera cada 15 minutos: el cliente expiró justo cuando alguien hizo clic.
# Con el re-calentador, las entradas se renuevan por debajo de su vencimiento y el
# TTL queda como red de seguridad para un cliente trabado, no como una bomba de
# tiempo en el camino crítico.

#: Cada cuánto se renueva. Dos tercios del TTL: hay margen para dos intentos
#: fallidos seguidos antes de que la entrada llegue a vencer sola.
REFRESH_INTERVAL_SECONDS = CLIENT_TTL_SECONDS * 2 // 3

_refresher: dict = {"thread": None, "stop": None, "on_result": None}


def _key_for(spec) -> tuple:
    service, profile, region, endpoint = spec
    return (service, profile, region, endpoint)


def prewarm(specs, force: bool = False) -> list[tuple]:
    """Construye los clientes de `specs`. Devuelve los fallos, no los lanza.

    Un perfil roto no puede impedir que los demás queden listos, así que cada
    especificación se intenta por separado y el error se devuelve para que el
    llamador lo muestre.
    """
    failures = []
    for spec in specs:
        key = _key_for(spec)
        try:
            cached, key_lock = _lookup(key)
            if cached is None or force:
                _build(key, key_lock, force=force)
        except Exception as exc:  # noqa: BLE001 — un perfil roto no frena al resto
            failures.append((spec, exc))
    return failures


def _refresher_loop(specs, stop: threading.Event, interval: float, on_result) -> None:
    announced = False
    while not stop.is_set():
        started = time.perf_counter()
        failures = prewarm(specs, force=True)
        elapsed = time.perf_counter() - started
        if on_result is not None and (not announced or failures):
            on_result(specs, failures, first=not announced, elapsed=elapsed)
            announced = True
        # `wait` y no `sleep`: es lo que hace que el stop sea inmediato en vez de
        # tener que esperar el resto del intervalo.
        stop.wait(interval)


def start_refresher(specs, on_result=None, interval: float | None = None) -> bool:
    """Calienta los clientes en segundo plano y los mantiene renovados.

    Devuelve False si ya había un re-calentador corriendo. No bloquea: el server
    levanta igual y el primer request que se adelante al calentamiento paga lo que
    pagaría siempre, que es la ruta correcta igual.
    """
    if not specs:
        return False
    with _cache_lock:
        if _refresher["thread"] is not None and _refresher["thread"].is_alive():
            return False
        stop = threading.Event()
        thread = threading.Thread(
            target=_refresher_loop,
            args=(list(specs), stop, interval or REFRESH_INTERVAL_SECONDS, on_result),
            name="yappy-aws-warm",
            daemon=True,
        )
        _refresher.update(thread=thread, stop=stop, on_result=on_result)
        thread.start()
        return True


def stop_refresher() -> None:
    """Frena el re-calentador. Idempotente; seguro de llamar si nunca hubo uno."""
    with _cache_lock:
        stop = _refresher["stop"]
        thread = _refresher["thread"]
        _refresher.update(thread=None, stop=None, on_result=None)
    if stop is not None:
        stop.set()
    if thread is not None:
        thread.join(timeout=5)


def refresher_running() -> bool:
    thread = _refresher["thread"]
    return thread is not None and thread.is_alive()