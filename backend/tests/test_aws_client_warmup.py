"""Calentado de clientes al arrancar, y renovación por debajo.

La razón de que esto exista: el primer `client()` de cada (servicio, ambiente)
cuesta ~440 ms y no calienta. Sin calentado, lo pagaba el primer request que
tocara cada ambiente — la primera vez que alguien abre una página. Con TTL solo,
además, cada 15 minutos un request cualquiera se comía el costo entero.

Estos tests fijan las dos propiedades:

1. Calentar mueve el costo al arranque, no lo elimina de la ruta del click.
2. El re-calentador renueva ANTES de que venza el TTL, y renueva en segundo plano.
"""

from __future__ import annotations

import threading
import time

import pytest

from yappy_api import warmup
from yappy_library.adapters import aws_clients
from yappy_library.config import Config

SPECS = [
    ("ssm", "prof-dev", "us-east-1", None),
    ("ssm", "prof-qa", "us-west-2", None),
    ("ecs", "prof-dev", "us-east-1", None),
]


class _Cfg:
    def __init__(self, profile, region, is_local=False, endpoint_url=None):
        self.env = "x"
        self.profile = profile
        self.region = region
        self.is_local = is_local
        self.endpoint_url = endpoint_url


def _stub_envs(monkeypatch, cfgs):
    monkeypatch.setattr(Config, "known_environments", classmethod(lambda cls: list(cfgs)))
    monkeypatch.setattr(Config, "with_env", classmethod(lambda cls, env: cfgs[env]))


@pytest.fixture
def builds(monkeypatch):
    """Contabiliza cada construcción de cliente, sin tocar boto3."""
    made = []
    lock = threading.Lock()

    def _create(service, profile, region, endpoint):
        with lock:
            made.append((service, profile, region, endpoint))
        return object()

    aws_clients.clear_cache()
    monkeypatch.setattr(aws_clients, "_create", _create)
    return made


@pytest.fixture(autouse=True)
def _no_thread_leaks():
    yield
    aws_clients.stop_refresher()
    aws_clients.clear_cache()


@pytest.fixture
def loops():
    """Eventos de parada de los hilos que arma cada test a mano.

    Sin esto un `interval=60` sobrevive a su test y sigue reconstruyendo
    clientes mientras corren los siguientes, y las contas dan cualquier cosa.
    """
    events = []

    def _spawn(specs, on_result, interval=0.01):
        stop = threading.Event()
        events.append(stop)
        thread = threading.Thread(
            target=aws_clients._refresher_loop,
            args=(specs, stop, interval, on_result),
            daemon=True,
        )
        thread.start()
        return stop, thread

    yield _spawn
    for event in events:
        event.set()


# --- client_specs ----------------------------------------------------------


def test_las_especificaciones_cubren_servicio_por_ambiente(monkeypatch):
    _stub_envs(monkeypatch, {
        "dev": _Cfg("prof-dev", "us-east-1"),
        "qa": _Cfg("prof-qa", "us-west-2"),
    })

    specs = warmup.client_specs()

    assert ("ssm", "prof-dev", "us-east-1", None) in specs
    assert ("ecs", "prof-qa", "us-west-2", None) in specs
    assert len(specs) == 2 * len(warmup.CLIENT_SERVICES)


def test_un_ambiente_local_no_se_calienta(monkeypatch):
    """No tiene Parameter Store ni ECS: el cliente se descartaría sin usarse."""
    _stub_envs(monkeypatch, {
        "qa": _Cfg("prof-qa", "us-west-2"),
        "local": _Cfg("prof-qa", "us-west-2", is_local=True),
    })

    specs = warmup.client_specs()

    assert all(spec[1] == "prof-qa" for spec in specs)
    assert len(specs) == len(warmup.CLIENT_SERVICES)


def test_localstack_arranca_con_su_endpoint(monkeypatch):
    """El perfil `localstack` sin endpoint en config igual necesita el 4566."""
    _stub_envs(monkeypatch, {"qa": _Cfg("localstack", "us-west-2")})

    assert all(spec[3] == "http://localhost:4566" for spec in warmup.client_specs())


def test_un_ambiente_roto_no_impide_listar_los_otros(monkeypatch):
    def _with_env(cls, env):
        if env == "roto":
            raise ValueError("No config for environment 'roto'")
        return _Cfg("prof-qa", "us-west-2")

    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["roto", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", classmethod(_with_env))

    specs = warmup.client_specs()

    assert specs and all(spec[1] == "prof-qa" for spec in specs)


# --- prewarm ---------------------------------------------------------------


def test_prewarm_construye_todo_lo_que_no_esta(builds):
    failures = aws_clients.prewarm(SPECS)

    assert failures == []
    assert len(builds) == len(SPECS)


def test_prewarm_es_idempotente(builds):
    aws_clients.prewarm(SPECS)
    aws_clients.prewarm(SPECS)

    assert len(builds) == len(SPECS), "el segundo prewarm reconstruyó lo que ya estaba"


def test_un_perfil_roto_no_frena_a_los_demas(monkeypatch, builds):
    def _create(service, profile, region, endpoint):
        if profile == "prof-roto":
            raise RuntimeError("ProfileNotFound")
        builds.append((service, profile, region, endpoint))
        return object()

    monkeypatch.setattr(aws_clients, "_create", _create)
    specs = SPECS + [("ssm", "prof-roto", "us-east-1", None)]

    failures = aws_clients.prewarm(specs)

    assert len(failures) == 1
    assert failures[0][0][1] == "prof-roto"
    assert len(builds) == len(SPECS), "un perfil roto dejó sin calentar al resto"


# --- el re-calentador ------------------------------------------------------


def test_renueva_antes_de_que_venza_el_ttl():
    """El intervalo tiene que dejar margen: renovar cuando ya venció es tarde.

    Si el intervalo fuera >= TTL, el cliente caería por la ruta del TTL y el
    request que lo pidiera pagaría los ~440 ms, que es justo lo que esto arregla.
    El margen es el holgura para que una pasada fallida no tire la entrada.
    """
    slack = aws_clients.CLIENT_TTL_SECONDS - aws_clients.REFRESH_INTERVAL_SECONDS

    assert slack > 0, "renovar en o después del vencimiento no evita la caída"
    assert slack >= 300, f"margen de {slack}s: una pasada fallida alcanza a expirar"


def test_la_pasada_renueva_todas_las_claves(builds, loops):
    """Cada pasada reconstruye, aunque las entradas sigan dentro del TTL.

    Si `force` no existiera la pasada no renovaria nada: las entradas envejecen
    dentro de los 15 minutos y el cache las daria por buenas, que es exactamente
    el TTL-por-camino-critico que esto viene a sacar.

    Se cuenta `_create` y no el callback: el callback solo habla la primera vez y
    cuando algo falla, a proposito, para no llenar el log cada 10 minutos.
    """
    stop, thread = loops(SPECS, lambda *a, **k: None)

    deadline = time.time() + 5
    while len(builds) < 2 * len(SPECS) and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=5)

    assert len(builds) >= 2 * len(SPECS), (
        f"el hilo no renovo en la segunda pasada: construyo {len(builds)} veces"
    )


def test_un_request_no_paga_durante_la_renovacion(monkeypatch, loops):
    """El cliente viejo sigue disponible mientras se construye el nuevo.

    Si el re-calentador sacara la entrada del cache antes de construir, habria una
    ventana en la que un request la encuentra vacia y paga los ~440 ms.
    """
    made = []
    gate = threading.Event()

    def _slow_create(service, profile, region, endpoint):
        made.append(profile)
        if profile == "prof-qa":
            gate.wait(timeout=5)  # la renovacion queda trabada a mitad de camino
        return object()

    aws_clients.clear_cache()
    monkeypatch.setattr(aws_clients, "_create", _slow_create)

    # Cliente viejo, ya en cache.
    aws_clients.get_client("ssm", "prof-qa", "us-west-2", None)
    assert made == ["prof-qa"]

    stop, thread = loops([("ssm", "prof-qa", "us-west-2", None)], None, interval=60)

    deadline = time.time() + 3
    while len(made) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert len(made) == 2, "la renovacion ni empezo"

    # Con la renovacion trabada, el request se lleva el cliente viejo sin volver
    # a construir: `made` no crece.
    aws_clients.get_client("ssm", "prof-qa", "us-west-2", None)
    assert len(made) == 2, "el request cayo en una construccion a medio hacer"

    gate.set()
    stop.set()
    thread.join(timeout=5)


def test_start_refresher_no_se_duplica(builds):
    assert aws_clients.start_refresher(SPECS, interval=0.01) is True
    assert aws_clients.start_refresher(SPECS, interval=0.01) is False

    assert aws_clients.refresher_running()


def test_start_refresher_sin_specs_no_arranca():
    assert aws_clients.start_refresher([]) is False
    assert not aws_clients.refresher_running()


def test_stop_refrester_es_idempotente(builds):
    aws_clients.stop_refresher()  # nunca hubo uno
    aws_clients.start_refresher(SPECS, interval=0.01)
    aws_clients.stop_refresher()
    aws_clients.stop_refresher()

    assert not aws_clients.refresher_running()


# --- warm() ----------------------------------------------------------------


def test_warm_no_arranca_sin_ambientes_de_aws(monkeypatch):
    _stub_envs(monkeypatch, {"local": _Cfg("prof-x", "us-east-1", is_local=True)})

    assert warmup.warm() is False
    assert not aws_clients.refresher_running()


def test_warm_levanta_el_recalentador(builds, monkeypatch):
    _stub_envs(monkeypatch, {"qa": _Cfg("prof-qa", "us-west-2")})

    assert warmup.warm() is True
    # El calentamiento inicial tiene que haber ocurrido sin que nadie lo pida.
    deadline = time.time() + 5
    while not builds and time.time() < deadline:
        time.sleep(0.02)

    assert len(builds) == len(warmup.CLIENT_SERVICES)


def test_warm_avisa_una_vez_y_no_repite_en_cada_pasada(builds, monkeypatch, capsys):
    """El hilo corre cada 10 minutos: si hablara en cada pasada, taparía el log."""
    _stub_envs(monkeypatch, {"qa": _Cfg("prof-qa", "us-west-2")})
    reports = []
    monkeypatch.setattr(
        warmup.aws_clients, "start_refresher",
        lambda specs, on_result=None, interval=None: (
            reports.append(on_result)
            or threading.Thread(
                target=on_result, args=(specs, [], True, 1.0), daemon=True
            ).start()
            or True
        ),
    )

    warmup.warm()

    out = capsys.readouterr().out
    assert "Clientes de AWS listos" in out

    # Las pasadas siguientes sólo hablan si algo falla.
    reports[0](warmup.client_specs(), [], first=False, elapsed=1.0)
    capsys.readouterr()
    reports[0](warmup.client_specs(), [], first=False, elapsed=1.0)
    assert "Clientes de AWS listos" not in capsys.readouterr().out

    # Y un fallo siempre se dice, aunque no sea la primera pasada.
    reports[0](
        [("ssm", "prof-x", "us-east-1", None)],
        [(("ssm", "prof-x", "us-east-1", None), RuntimeError("boom"))],
        first=False,
        elapsed=1.0,
    )
    assert "boom" in capsys.readouterr().out