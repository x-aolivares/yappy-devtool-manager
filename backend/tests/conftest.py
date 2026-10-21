"""Isolation for tests that exercise code which reaches the user's machine.

`yappy web`'s entry point is `yappy_api.run.run()`, and it does two things that
must never happen because somebody ran the test suite: read
`~/.yappy/tracker`, and (when it finds a web-owned entry whose supervisor pid is
gone) **stop that process**. Several tests call `run()` for other reasons — the
shutdown ones, the warmup ordering one — and they would be doing both.

So the tracker is redirected to a per-test temp directory for the whole suite.
Every test that cares about the tracker already monkeypatches `_TRACKER_DIR`
itself and keeps winning, because a test body's `monkeypatch.setattr` is applied
after this fixture. This changes no assertion anywhere: it removes the machine
from the blast radius.

**And so is the config directory**, for the same reason and with the same rule.
About twenty tests ask for the environment `dev` *by name* — it was the default
of the endpoints and it is an arbitrary name for the CLI ones — and the name
only resolves if the machine has an `env.dev`. It doesn't have to: that
environment can be renamed or dropped, and then the whole suite went red with
`No config for environment 'dev'` for a reason that has nothing to do with what
those tests check. So the suite reads its own config dir: a copy of the
machine's `env.*` — the values the tests already rely on, like the port catalog
— plus a synthetic `env.dev` for the machine that doesn't have one.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from yappy_api import shutdown
from yappy_library.adapters import process_tracker
from yappy_library.config import Config

#: Antes de que nada importe `yappy_api.app`.
#:
#: Sin esto, un `frontend/dist/browser` que exista en el checkout —basta con
#: haber compilado una vez— hace que la app registre la SPA y su catch-all, y
#: toda la suite de `TestClient` pasa a hablar con Angular en vez de con la API.
#: El síntoma es engañoso: un `POST` a un endpoint real contesta `index.html` con
#: un 405, y se lee como un endpoint roto.
#:
#: El valor es un directorio vacío a propósito: `_create_app` registra la SPA
#: cuando el directorio existe, así que el camino de "no hay build" se prueba
#: con un directorio que no existe (los tests lo setean por su cuenta).
os.environ.setdefault("YAPPY_WEB_DIST", os.path.join(os.path.dirname(__file__), "empty-dist"))

#: El `env.dev` que la suite usa cuando la máquina no tiene uno.
#:
#: Un ambiente **remoto**, a propósito: `AWS_ENVIRONMENT=dev` y no `local`, para
#: que los tests que lo piden sigan viendo un ambiente con AWS y no uno local.
#: Los valores son de mentira: lo que estos tests miran es que el ambiente
#: exista y tenga la forma de uno.
SYNTHETIC_DEV = """\
AWS_PROFILE=base-profile
AWS_ENVIRONMENT=dev
AWS_REGION=us-west-2
AWS_INSTANCE=i-00000000000000000
AWS_CLUSTER=test000000
DB_PORT=8100
"""


#: Los compartidos. De mentira, como los demás: lo que los tests miran es la
#: forma del archivo, no los valores.
SYNTHETIC_BASE = """\
ORG_DOMAIN=yappy
AWS_PROFILE=base-profile
AWS_USER=test-user
AWS_PORT=53360
KAFKA_PATH=C:/kafka
WORKSPACE_PATH=C:/workspace
CLUSTER_DOMAIN=yappycloud.com
CLUSTER_PORTS=cap=8402:core,8403:commerce;cap2=8412:personal;int=
"""

#: Los otros dos ambientes remotos, con la misma forma.
SYNTHETIC_QA = SYNTHETIC_DEV.replace("AWS_ENVIRONMENT=dev", "AWS_ENVIRONMENT=qa")
SYNTHETIC_UAT = SYNTHETIC_DEV.replace("AWS_ENVIRONMENT=dev", "AWS_ENVIRONMENT=uat")

#: El ambiente local: conexión directa a un MySQL de la máquina, sin túnel SSM.
SYNTHETIC_LOCAL = """\
AWS_ENVIRONMENT=local
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=root
"""


@pytest.fixture(scope="session")
def suite_config_dir(tmp_path_factory) -> Path:
    """El directorio de configuración de la suite.

    **Se escribe de cero, no se copia de la máquina.** Antes copiaba los
    `env.*` de la máquina, y eso tenía dos problemas:

    - El resultado de la suite dependía de valores que viven en archivos
      gitignored. Una máquina con un `env.qa` distinto —o sin `env.uat`—
      hacía fallar un test que en otra máquina pasaba, y al revés.
    - La copia arrastraba las credenciales reales de la máquina
      (`CIRCLECI_TOKEN`, `BITBUCKET_TOKEN`) a un directorio temporal, en
      cada corrida, sin que nadie lo pidiera.

    Ahora los archivos se escriben con valores fijos. El que un test necesite
    de verdad, lo declara en su propio fixture: los que dependen del catálogo
    de puertos usan su propio `FakeConfig`, no éste.
    """
    target = tmp_path_factory.mktemp("config")
    (target / "env.base").write_text(SYNTHETIC_BASE, encoding="utf-8")
    (target / "env.dev").write_text(SYNTHETIC_DEV, encoding="utf-8")
    (target / "env.qa").write_text(SYNTHETIC_QA, encoding="utf-8")
    (target / "env.uat").write_text(SYNTHETIC_UAT, encoding="utf-8")
    (target / "env.local").write_text(SYNTHETIC_LOCAL, encoding="utf-8")
    return target


@pytest.fixture(autouse=True)
def isolated_config(suite_config_dir, monkeypatch):
    """Cada test lee la config de la suite, no la de la máquina."""
    monkeypatch.setattr(Config, "_config_dir", suite_config_dir)


@pytest.fixture(autouse=True)
def isolated_tracker(tmp_path, monkeypatch):
    """Point the process tracker at an empty temp dir, for every test."""
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", tmp_path / ".yappy" / "tracker")
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})


@pytest.fixture(autouse=True)
def clean_shutdown_flag(monkeypatch):
    """A clean shutdown flag for every test.

    ``yappy_api.shutdown`` holds a process-global ``Event`` that, once set, makes
    both SSE streams close on their first turn. That is exactly the kind of state
    a single test must not leave behind: the suite's later stream tests would all
    close instantly and fail, with the failure showing up far from the test that
    lit it — and, worse, a test asserting "the stream closes" would pass for the
    wrong reason.
    """
    monkeypatch.setattr(shutdown, "_flag", threading.Event())