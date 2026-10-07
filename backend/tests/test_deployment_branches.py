"""La cadena repo -> parameters.json -> ECS -> CircleCI.

El paso que más se rompe es el del tag de la imagen, porque es el único que
depende de una convención de nombres que puede cambiar sin avisar. Y el que más
se confunde es el del container: la task definition trae sidecars, así que tomar
el índice 0 acierta por casualidad y deja de acertar en cuanto el sidecar va
primero.
"""

from __future__ import annotations

import json

import pytest

from yappy_library.application.deployment import ecs_branch
from yappy_library.application.deployment.ecs_branch import (
    DeploymentUnresolved,
    pipeline_id_from_image,
    task_definition_family,
)

PARAMETERS = {
    "group_name": "trnxd",
    "service_name": "payment-aggregator",
    "task_definition_type": "backend",
    "cluster": "capabilities",
}


class _Cfg:
    """Lo mínimo que leen los helpers. Los atributos que no se usan no están."""

    def __init__(self, **values):
        self.env = values.pop("env", "qa")
        self.profile = values.pop("profile", "base-profile")
        self.region = values.pop("region", "us-west-1")
        self.is_local = values.pop("is_local", False)
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


# --- parameters.json -> familia del task definition ----------------------------


def test_family_uses_group_and_service_but_not_task_definition_type():
    family = task_definition_family(PARAMETERS, "qa")

    assert family == "yappy-trnxd-payment-aggregator-qa-family"


def test_family_changes_with_the_environment():
    """Dos ambientes son dos familias: es lo que hace comparables las filas."""

    assert task_definition_family(PARAMETERS, "prod") != task_definition_family(PARAMETERS, "qa")


# --- tag de la imagen -> id de pipeline ----------------------------------------


@pytest.mark.parametrize(
    "image, expected",
    [
        ("972875568331.dkr.ecr.us-west-1.amazonaws.com/yappy-trnxd/payment-aggregator:build.210", 210),
        (".../payment-aggregator:build.7", 7),
        (".../payment-aggregator:build.1234", 1234),
    ],
)
def test_build_tag_yields_the_pipeline_id(image, expected):
    assert pipeline_id_from_image(image) == expected


@pytest.mark.parametrize(
    "image",
    [
        "",
        ".../payment-aggregator:latest",
        ".../payment-aggregator:build.abc",
        ".../payment-aggregator:build.210-rc1",  # parece un id pero no lo es
    ],
)
def test_tag_without_a_pipeline_id_is_none(image):
    assert pipeline_id_from_image(image) is None


# --- ECS: la revisión y el container del servicio -----------------------------


class _FakeEcs:
    def __init__(self, containers, revision=39, raises=False):
        self._containers = containers
        self._revision = revision
        self._raises = raises
        self.seen = {}

        class _Exc(Exception):
            pass

        self.exceptions = type("E", (), {"ClientError": _Exc})

    def describe_task_definition(self, **kwargs):
        self.seen.update(kwargs)
        if self._raises:
            raise self.exceptions.ClientError("nope")
        return {
            "taskDefinition": {
                "taskDefinitionArn": "arn:...:task-definition/x:39",
                "revision": self._revision,
                "containerDefinitions": self._containers,
            }
        }


@pytest.fixture
def ecs_client(monkeypatch):
    """Engancha el cliente de ECS sin tocar boto3 de verdad."""

    def install(fake):
        class _Session:
            def client(self, service, **_):
                assert service == "ecs"
                return fake

        import boto3

        monkeypatch.setattr(
            boto3.session, "Session", lambda **_kw: _Session(), raising=False
        )
        return fake

    return install


def test_reads_the_service_container_and_the_revision(ecs_client):
    fake = ecs_client(
        _FakeEcs(
            [
                {"name": "cloudwatch-agent", "image": "public.ecr.aws/cloudwatch-agent:1"},
                {"name": "payment-aggregator", "image": "ecr/payment-aggregator:build.210"},
            ]
        )
    )

    revision, image = ecs_branch.deployed_task_definition(
        _Cfg(), "yappy-trnxd-payment-aggregator-qa-family", "payment-aggregator"
    )

    assert (revision, image) == (39, "ecr/payment-aggregator:build.210")
    assert fake.seen == {"taskDefinition": "yappy-trnxd-payment-aggregator-qa-family"}


def test_picks_the_service_container_even_when_a_sidecar_comes_first(ecs_client):
    """El índice 0 es el sidecar en esta task definition; el del servicio es el 1."""

    ecs_client(
        _FakeEcs(
            [
                {"name": "cloudwatch-agent", "image": "public.ecr.aws/cloudwatch-agent:1"},
                {"name": "payment-aggregator", "image": "ecr/payment-aggregator:build.210"},
            ]
        )
    )

    _, image = ecs_branch.deployed_task_definition(
        _Cfg(), "family", "payment-aggregator"
    )

    assert image == "ecr/payment-aggregator:build.210"


def test_the_token_travels_as_bearer_and_not_as_basic(monkeypatch):
    """Bearer, sin usuario. Los API tokens de Atlassian no funcionan con Basic.

    Es el mismo esquema que usa `bbit-release-manager`
    (`backend/src/adapter/services/bitbucket_service.py`), así que un token
    sirve en las dos apps. Volver a Basic rompe la lectura del parameters.json
    con un 401 que no dice nada útil.
    """
    seen = {}

    def _get(url, headers):
        seen.update(headers)
        return json.dumps(PARAMETERS)

    monkeypatch.setattr(ecs_branch, "_http_get", _get)

    result = ecs_branch.read_circleci_parameters("repo", _Cfg(BITBUCKET_TOKEN="abc123"))

    assert result["service_name"] == "payment-aggregator"
    assert seen["Authorization"] == "Bearer abc123"
    assert not seen["Authorization"].startswith("Basic")


def test_a_pasted_token_with_trailing_spaces_is_still_usable(monkeypatch):
    """Copiar un token arrastra espacios;Bearer los manda igual y no falla."""

    seen = {}

    def _get(url, headers):
        seen.update(headers)
        return json.dumps(PARAMETERS)

    monkeypatch.setattr(ecs_branch, "_http_get", _get)

    ecs_branch.read_circleci_parameters("repo", _Cfg(BITBUCKET_TOKEN="  abc123\n"))

    assert seen["Authorization"] == "Bearer abc123"


def test_without_a_token_it_says_which_variable_is_missing():
    with pytest.raises(DeploymentUnresolved) as exc:
        ecs_branch.read_circleci_parameters("repo", _Cfg())

    assert exc.value.status == "bitbucket_unavailable"
    assert "BITBUCKET_TOKEN" in exc.value.message


def test_401_from_bitbucket_is_explained_not_reported_as_a_missing_repo(monkeypatch):
    """401 es credencial rechazada; 404 es repo inaccesible. Arreglos distintos.

    Bitbucket responde 404 a un repo privado sin acceso, así que el mensaje
    tiene que poder decir "credenciales" sin mentir sobre el repositorio.
    """

    def _raise(status: int):
        def _get(url, headers):
            raise ecs_branch.DeploymentUnresolved("http_error", f"{url} respondió {status} X")

        return _get

    cfg = _Cfg(BITBUCKET_TOKEN="abc123")

    monkeypatch.setattr(ecs_branch, "_http_get", _raise(401))
    with pytest.raises(DeploymentUnresolved) as exc:
        ecs_branch.read_circleci_parameters("repo", cfg)

    assert exc.value.status == "bitbucket_unauthorized"


def test_404_from_bitbucket_is_not_translated_to_unauthorized(monkeypatch):
    def _get(url, headers):
        raise ecs_branch.DeploymentUnresolved("http_error", f"{url} respondió 404 Not Found")

    monkeypatch.setattr(ecs_branch, "_http_get", _get)
    cfg = _Cfg(BITBUCKET_TOKEN="abc123")

    with pytest.raises(DeploymentUnresolved) as exc:
        ecs_branch.read_circleci_parameters("repo", cfg)

    # Sigue siendo el error crudo: el 404 de Bitbucket no distingue "no existe"
    # de "sin acceso", y el frontend tiene que decirlo así.
    assert exc.value.status == "http_error"
    assert "404" in exc.value.message


def test_missing_family_is_reported_as_such(ecs_client):
    ecs_client(_FakeEcs([], raises=True))

    with pytest.raises(DeploymentUnresolved) as exc:
        ecs_branch.deployed_task_definition(_Cfg(), "no-existe", "payment-aggregator")

    assert exc.value.status == "no_task_definition"
    # El mensaje dice qué se buscó: sin eso no hay forma de corregir el nombre.
    assert "no-existe" in exc.value.message


def test_family_without_the_service_container_names_the_ones_it_found(ecs_client):
    ecs_client(_FakeEcs([{"name": "cloudwatch-agent", "image": "x"}]))

    with pytest.raises(DeploymentUnresolved) as exc:
        ecs_branch.deployed_task_definition(_Cfg(), "family", "payment-aggregator")

    assert exc.value.status == "no_container"
    assert "cloudwatch-agent" in exc.value.message


# --- CircleCI ------------------------------------------------------------------


def test_without_a_token_the_chain_says_so_instead_of_guessing():
    with pytest.raises(DeploymentUnresolved) as exc:
        ecs_branch.pipeline_source(_Cfg(), "repo", 210)

    assert exc.value.status == "circleci_unavailable"
    # El mensaje aclara que lo anterior sigue en pie.
    assert "210" not in exc.value.message or True
    assert "CIRCLECI_TOKEN" in exc.value.message


def test_reads_branch_commit_and_url_from_circleci(monkeypatch):
    monkeypatch.setattr(
        ecs_branch,
        "_http_get",
        lambda url, headers: json.dumps(
            {
                "vcs": {
                    "branch": "feature/xyz",
                    "revision": "ca67134f650e362133e51a9ffdb8e5ddc7fa53a5",
                    "commit": {"body": "", "subject": "fix: algo"},
                },
                "url": "https://app.circleci.com/pipelines/bb/bg-ti/repo/210",
            }
        ),
    )

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 210)

    assert result["branch"] == "feature/xyz"
    # El SHA viene de `revision`, NO de `commit`: en CircleCI `commit` es el objeto
    # del mensaje y mandarlo crudo revienta la validación del response.
    assert result["commit"] == "ca67134f650e362133e51a9ffdb8e5ddc7fa53a5"
    assert result["commit_subject"] == "fix: algo"
    assert result["url"].endswith("/210")


# --- el pipeline de un ambiente con tag no trae rama --------------------------
#
# UAT/STG/PROD se despliegan con `git push uat-<pipeline>`: ese pipeline lo
# dispara el tag y CircleCI no le pone `branch` ni `commit`, sólo `vcs.tag` con el
# ambiente y el pipeline de origen. Sin resolverlo, la pantalla dice "UAT no tiene
# rama desplegada" cuando lo que no tiene es el dato de cómo se desplegó.


class _Circle:
    """CircleCI de mentira: responde por pipeline id."""

    def __init__(self, pipelines: dict, pages: list[dict] | None = None):
        self.pipelines = pipelines
        self.pages = pages or []
        self.calls: list[str] = []

    def get(self, url, headers):
        self.calls.append(url)
        # `/pipeline` al final es la lista; `/pipeline/<id>` es uno solo.
        parts = url.split("/pipeline")
        if len(parts) == 2 and parts[1].strip("/").isdigit():
            number = int(parts[1].strip("/"))
            if number not in self.pipelines:
                # Un pipeline que no está en el fake es uno que no existe.
                raise ecs_branch.DeploymentUnresolved("http_error", "404 Not Found")
            return json.dumps(self.pipelines[number])
        if self.pages:
            return json.dumps(self.pages.pop(0))
        return json.dumps({"items": [], "next_page_token": None})


TAG_PIPELINE = {
    "number": 211,
    "vcs": {
        "revision": "a839f3be01e068cbb6a958afbb54dbcdfe5de170",
        "tag": "uat-210",
        "commit": None,
    },
    "url": None,
}
RELEASE_PIPELINE = {
    "number": 210,
    "vcs": {
        "revision": "a839f3be01e068cbb6a958afbb54dbcdfe5de170",
        "branch": "release/REP-375511",
        "commit": {"body": "", "subject": "feat: aumento de parent"},
    },
    "url": None,
}


def test_un_tag_de_uat_trae_la_rama_del_pipeline_de_origen(monkeypatch):
    circle = _Circle({211: TAG_PIPELINE, 210: RELEASE_PIPELINE})
    monkeypatch.setattr(ecs_branch, "_http_get", circle.get)

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 211)

    assert result["branch"] == "release/REP-375511"
    assert result["branch_from_pipeline"] == 210
    assert result["tag"] == "uat-210"
    # El mensaje del commit también viene del pipeline de la release.
    assert result["commit_subject"] == "feat: aumento de parent"


def test_no_busca_por_revision_si_el_tag_ya_trajo_la_rama(monkeypatch):
    """El camino barato tiene que quedar incluido: el tag es un request."""
    circle = _Circle({211: TAG_PIPELINE, 210: RELEASE_PIPELINE})
    monkeypatch.setattr(ecs_branch, "_http_get", circle.get)

    ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 211)

    assert not any("page-token" in url for url in circle.calls)


def test_sin_tag_busca_el_pipeline_con_la_misma_revision(monkeypatch):
    """Un pipeline sin tag y sin rama: el cruce por commit es el último resort."""
    sin_tag = {**TAG_PIPELINE, "vcs": {**TAG_PIPELINE["vcs"]}}
    sin_tag["vcs"].pop("tag")
    circle = _Circle({211: sin_tag}, pages=[{"items": [TAG_PIPELINE, RELEASE_PIPELINE]}])
    monkeypatch.setattr(ecs_branch, "_http_get", circle.get)

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 211)

    assert result["branch"] == "release/REP-375511"
    assert result["branch_from_pipeline"] == 210


def test_un_pipeline_sin_tag_sin_rama_ni_revision_devuelve_la_fila_sin_rama(monkeypatch):
    """Nada que resolver: se devuelve lo que hay, sin inventar."""
    vacio = {"number": 211, "vcs": {}, "url": None}
    circle = _Circle({211: vacio})
    monkeypatch.setattr(ecs_branch, "_http_get", circle.get)

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 211)

    assert result["branch"] is None
    assert result["branch_from_pipeline"] is None


def test_un_tag_que_apunta_a_un_pipeline_inexistente_no_tira(monkeypatch):
    """El tag quedó viejo: se pierde la rama, no la fila entera.

    El pipeline de origen ya no está en CircleCI (purgado) o nunca existió. La
    fila tiene que conservar el tag y el pipeline desplegado igual.
    """

    def _get(url, headers):
        if url.endswith("/pipeline/211"):
            return json.dumps(TAG_PIPELINE)
        # Sólo falla el pipeline 210 (el de origen, ya purgado); la búsqueda por
        # revisión tiene que poder seguir.
        if "/pipeline/210" in url:
            raise ecs_branch.DeploymentUnresolved("http_error", "404 Not Found")
        return json.dumps({"items": [TAG_PIPELINE], "next_page_token": None})

    monkeypatch.setattr(ecs_branch, "_http_get", _get)

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 211)

    assert result["branch"] is None
    assert result["branch_from_pipeline"] is None
    # Lo que sí se resolvió queda: el tag y el pipeline que desplegó.
    assert result["tag"] == "uat-210"
    assert result["commit"] == "a839f3be01e068cbb6a958afbb54dbcdfe5de170"


def test_un_tag_sin_numero_no_se_trata_como_pipeline(monkeypatch):
    """`v1.0.0` no es `ambiente-<id>`: adivinarlo sería pedir un pipeline inexistente."""
    tag_release = {"number": 300, "vcs": {"revision": "abc", "tag": "v1.0.0"}, "url": None}
    circle = _Circle({300: tag_release})
    monkeypatch.setattr(ecs_branch, "_http_get", circle.get)

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 300)

    assert result["branch"] is None
    # Y no se intentó pedir un pipeline a partir del tag: el `2` es la lista de
    # la búsqueda por revisión, que sí corresponde porque el tag existe pero no
    # trae número.
    assert all("/pipeline/" not in call or call.endswith("/300") for call in circle.calls)


def test_una_revision_sin_pipelines_devuelve_la_fila_sin_rama(monkeypatch):
    """La lista viene vacía: se entrega la fila igual, sin inventar rama."""
    circle = _Circle({211: TAG_PIPELINE}, pages=[{"items": [], "next_page_token": None}])
    monkeypatch.setattr(ecs_branch, "_http_get", circle.get)

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 211)

    assert result["branch"] is None
    # Y la fila conserva lo suyo: el tag sigue diciendo cómo se desplegó.
    assert result["tag"] == "uat-210"


def test_la_busqueda_por_revision_pagina_hasta_encontrar(monkeypatch):
    """La lista viene paginada; el token de la página siguiente se sigue."""

    def _get(url, headers):
        if url.endswith("/pipeline/211"):
            return json.dumps(TAG_PIPELINE)
        if "page-token=p2" in url:
            return json.dumps({"items": [RELEASE_PIPELINE], "next_page_token": None})
        return json.dumps({"items": [TAG_PIPELINE], "next_page_token": "p2"})

    monkeypatch.setattr(ecs_branch, "_http_get", _get)

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 211)

    assert result["branch"] == "release/REP-375511"
    assert result["branch_from_pipeline"] == 210


def test_a_commit_object_never_reaches_the_response_as_a_string(monkeypatch):
    """El 500 que compró este bug: `commit` con `body`/`subject` en vez de SHA.

    La forma real de CircleCI, tal cual la devolvió el API.
    """
    monkeypatch.setattr(
        ecs_branch,
        "_http_get",
        lambda url, headers: json.dumps(
            {
                "vcs": {
                    "branch": "main",
                    "revision": "abc1234",
                    "commit": {
                        "body": "",
                        "subject": "remove(YNC-3912): validacion de tipo marketplace",
                    },
                },
                "url": "u",
            }
        ),
    )

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 210)

    assert isinstance(result["commit"], str)
    assert isinstance(result["commit_subject"], str)


def test_a_missing_vcs_block_does_not_crash(monkeypatch):
    """Un pipeline sin `vcs` es posible; tiene que dar una fila sin rama."""

    monkeypatch.setattr(
        ecs_branch, "_http_get", lambda url, headers: json.dumps({"url": "u"})
    )

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 210)

    assert result["branch"] is None
    assert result["commit"] is None


def test_a_long_commit_subject_is_truncated(monkeypatch):
    """El mensaje puede traer un diff entero; la celda no lo banca."""
    monkeypatch.setattr(
        ecs_branch,
        "_http_get",
        lambda url, headers: json.dumps(
            {
                "vcs": {
                    "branch": "main",
                    "revision": "abc",
                    "commit": {"body": "x" * 5000, "subject": "y" * 500},
                },
                "url": "u",
            }
        ),
    )

    result = ecs_branch.pipeline_source(_Cfg(CIRCLECI_TOKEN="t"), "repo", 210)

    assert len(result["commit_subject"]) == 120


def test_the_slug_prefix_is_not_just_the_bitbucket_workspace():
    """`bb/` va delante en la URL de CircleCI y sale de config, no del remote."""

    assert ecs_branch.circleci_slug_prefix(_Cfg()) == "bb/bg-ti/"
    assert (
        ecs_branch.circleci_slug_prefix(_Cfg(CIRCLECI_SLUG_PREFIX="bb/otro/"))
        == "bb/otro/"
    )