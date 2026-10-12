"""Pre-calienta los clientes de AWS al arrancar la web.

El primer `client()` de cada (servicio, ambiente) cuesta ~440 ms y no se hereda
entre Sessions de boto3. Sin esto, ese costo lo pagaba el primer request que
tocara cada ambiente — o sea, la primera vez que alguien abre una página.

Calentar antes no compra nada de la nada: lo que compra es **mover ese costo del
click al arranque**. Y es seguro hacerlo de antemano porque crear un cliente de
boto3 no toca la red: se verificó bloqueando `socket.create_connection`, y
`get_client` devolvió en 597 ms sin una sola conexión. Es un objeto local; las
credenciales se resuelven en el primer API call, no antes.

**Qué NO se hace acá, a propósito.** No se renueva la sesión de AWS SSO. El
profile `base-profile` es SSO, y renovar eso es `aws sso login`: abre un
navegador y espera un callback. Desde un hilo del server eso es un cuelgue
silencioso. El re-calentador de acá renueva *clientes*, que es lo que se puede
hacer en silencio. Cuando la sesión SSO venció, la respuesta correcta sigue
siendo un error que diga que corras `yappy login aws`.
"""

from __future__ import annotations

from yappy_library.adapters import aws_clients
from yappy_library.adapters.logging import info, warn
from yappy_library.config import Config

#: Los servicios que la web consulta a AWS. `ssm` y `secretsmanager` los usan las
#: páginas de parámetros; `ecs`, la de ramas desplegadas. Si se agrega un servicio
#: hay que agregarlo acá o su primer request paga el arranque en vez del click.
CLIENT_SERVICES = ("ssm", "secretsmanager", "ecs")


def client_specs(services=CLIENT_SERVICES) -> list[tuple]:
    """`(servicio, profile, region, endpoint)` de cada ambiente que vale la pena.

    Se saltea a los locales: no tienen Parameter Store ni ECS. Si un ambiente está
    en la lista sin ser AWS, `is_local` lo va a avisar por su cuenta cuando se
    use — acá no se decide a quién se le pregunta, sólo a quién se le prepara el
    cliente.
    """
    specs = []
    for env in Config.known_environments():
        try:
            cfg = Config.with_env(env)
        except Exception:  # noqa: BLE001 — un ambiente roto no impide calentar los otros
            continue
        if cfg.is_local:
            continue
        endpoint = cfg.endpoint_url
        if endpoint is None and cfg.profile == "localstack":
            endpoint = "http://localhost:4566"
        for service in services:
            specs.append((service, cfg.profile, cfg.region, endpoint))
    return specs


def _report(specs, failures, first: bool, elapsed: float) -> None:
    """Avanza una vez al arrancar y después sólo si algo se rompió.

    El re-calentador corre cada 10 minutos durante toda la vida del proceso: si
    hablara en cada pasada, el log de `yappy web` se llenaría de líneas que nadie
    pidió y taparía lo que sí importa cuando algo sale mal.
    """
    for spec, exc in failures:
        warn(f"No se pudo calentar {spec[0]} en {spec[1]}/{spec[2]}: {exc}")
    if first and specs:
        info(
            f"Clientes de AWS listos: {len(specs) - len(failures)}/{len(specs)} "
            f"en {elapsed * 1000:.0f} ms"
        )


def warm(on_result=None) -> bool:
    """Levanta el re-calentador, que calienta al arrancar y después renueva.

    No bloquea. Devuelve False si ya había uno corriendo o si no hay nada que
    calentar.
    """
    specs = client_specs()
    if not specs:
        info("Sin ambientes de AWS para calentar clientes.")
        return False
    return aws_clients.start_refresher(specs, on_result=on_result or _report)