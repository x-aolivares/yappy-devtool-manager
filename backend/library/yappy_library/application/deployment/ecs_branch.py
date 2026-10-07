"""Qué rama está desplegada en cada ambiente, para un repositorio de ECS/Fargate.

Son cuatro saltos y cada uno depende del anterior:

1. **Bitbucket** — ``.circleci/parameters.json`` del repo. De ahí salen
   ``group_name`` y ``service_name``, que arman el nombre de la familia.
2. **ECS** — ``describe_task_definition`` de esa familia en la región del
   ambiente. Del ``containerDefinitions`` sale el tag de la imagen.
3. **El tag** — ``:build.<n>``, donde ``n`` es el número de pipeline de CircleCI.
4. **CircleCI** — ese pipeline dice en qué rama se compiló.

La cadena está verificada contra AWS real (us-west-1, cuenta 972875568331) el
2026-10-07 con ``yappy-trnxd-backend-payment-aggregator``: la familia
``yappy-trnxd-payment-aggregator-qa-family`` está en la revisión 39 y su imagen
es ``...:build.210``, el mismo pipeline que se abre a mano en el navegador.

De la imagen se copia lo que se **ve** en la caja, no el crudo: el que pega en
una terminal quiere lo mismo que estaba leyendo.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from ...config import Config

BITBUCKET_API = "https://api.bitbucket.org/2.0"
CIRCLECI_API = "https://circleci.com/api/v2"
HTTP_TIMEOUT = 20

# Tope de páginas al buscar por revisión. Son 3 pipelines por commit en la
# práctica, así que 5 es holgado; el tope existe para que un `page_token` que
# no avance no se convierta en un loop infinito.
_MAX_PIPELINE_PAGES = 5

# El id del pipeline va en el tag de la imagen. Se ancla al final a propósito: un
# tag como `build.210-rc1` no es un pipeline, y prefiero no inventarlo.
_BUILD_TAG = re.compile(r":build\.(\d+)\s*$")


class DeploymentUnresolved(RuntimeError):
    """Una etapa de la cadena no se pudo resolver.

    `status` es un código corto que la UI usa para pintar la fila, y el mensaje es
    para una persona: va a la pantalla tal cual, sin traducir ni reinterpretar.
    """

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _http_get(url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise DeploymentUnresolved(
            "http_error", f"{url} respondió {exc.code} {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DeploymentUnresolved("network_error", f"No se pudo llegar a {url}: {exc.reason}") from exc


def bitbucket_workspace(cfg) -> str:
    return cfg.get("BITBUCKET_WORKSPACE") or "bg-ti"


def circleci_slug_prefix(cfg) -> str:
    """El prefijo del slug de CircleCI, que no es el workspace de Bitbucket.

    En la URL de CircleCI el proyecto aparece como ``bb/bg-ti/<repo>``: el ``bb``
    identifica a Bitbucket como VCS y no es parte del workspace. Sale del
    CircleCI token/proyecto, no del remote de git, así que va en config.
    """
    configured = cfg.get("CIRCLECI_SLUG_PREFIX")
    if configured:
        return configured.rstrip("/") + "/"
    return f"bb/{bitbucket_workspace(cfg)}/"


def read_circleci_parameters(repo: str, cfg) -> dict:
    """.circleci/parameters.json del repo, leído de Bitbucket.

    `HEAD` como commit: el archivo se lee de la rama default, que es donde vive
    el `parameters.json` que genera el deploy. Fijar una rama obligaría a saber
    cuál, y ese dato es justo lo que se está intentando averiguar.
    """
    workspace = bitbucket_workspace(cfg)
    token = cfg.get("BITBUCKET_TOKEN")
    if not token:
        raise DeploymentUnresolved(
            "bitbucket_unavailable",
            "Falta BITBUCKET_TOKEN en la config para leer .circleci/parameters.json.",
        )
    # `Bearer <token>` y nada más: los API tokens de Atlassian no van con Basic.
    # El mismo esquema usa `bbit-release-manager` (backend/src/adapter/services/
    # bitbucket_service.py), así que el token sirve en las dos apps.
    url = f"{BITBUCKET_API}/repositories/{workspace}/{repo}/src/HEAD/.circleci/parameters.json"
    try:
        body = _http_get(
            url,
            {
                "Authorization": f"Bearer {token.strip()}",
                "Accept": "application/json",
            },
        )
    except DeploymentUnresolved as exc:
        if exc.status == "http_error" and " 401" in exc.message:
            # 401 es credencial rechazada, no "el repo no existe": son arreglos
            # opuestos y confundirlos manda al lugar equivocado.
            raise DeploymentUnresolved(
                "bitbucket_unauthorized",
                "Bitbucket rechazó el token (401). Verificá que BITBUCKET_TOKEN siga "
                "vigente y que no tenga espacios copiados de más.",
            ) from exc
        raise
    try:
        parameters = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DeploymentUnresolved("bad_parameters", f"El parameters.json no es JSON: {exc}") from exc
    for key in ("group_name", "service_name"):
        if key not in parameters:
            raise DeploymentUnresolved(
                "bad_parameters", f"El parameters.json no trae '{key}'."
            )
    return parameters


def task_definition_family(parameters: dict, env: str) -> str:
    """`yappy-<group>-<service>-<env>-family`.

    Ojo: el `task_definition_type` del parameters.json ("backend") aparece en el
    nombre del repo pero NO en el de la familia. Por eso no se arma con todos los
    campos: sólo con los dos que la familia usa.
    """
    return (
        f"yappy-{parameters['group_name']}-{parameters['service_name']}-{env}-family"
    )


def pipeline_id_from_image(image: str) -> int | None:
    match = _BUILD_TAG.search(image.strip())
    return int(match.group(1)) if match else None


def deployed_task_definition(cfg, family: str, service_name: str) -> tuple[int, str]:
    """Revisión activa y del container del servicio de esa familia.

    Devolver la revisión y la imagen. La revisión es el `:39` de la URL de la
    consola; sin ella no se puede contrastar el resultado contra lo que ve la
    persona del otro lado.
    """
    import boto3

    session = boto3.session.Session(profile_name=cfg.profile, region_name=cfg.region)
    ecs = session.client("ecs")
    try:
        described = ecs.describe_task_definition(taskDefinition=family)["taskDefinition"]
    except ecs.exceptions.ClientError as exc:
        raise DeploymentUnresolved(
            "no_task_definition",
            f"ECS no tiene la familia '{family}' en {cfg.region}. "
            f"El servicio puede no existir en este ambiente todavía.",
        ) from exc

    # El container del servicio, NO el primero: las task definitions de estos
    # proyectos traen sidecars (cloudwatch-agent, y a veces el de_NEW_RELIC) y
    # tomar el índice 0 es acertar por casualidad.
    containers = described.get("containerDefinitions", [])
    service_container = next(
        (c for c in containers if c.get("name") == service_name), None
    )
    if service_container is None:
        names = ", ".join(c.get("name", "?") for c in containers) or "ninguno"
        raise DeploymentUnresolved(
            "no_container",
            f"La familia '{family}' no tiene un container '{service_name}'. "
            f"Containers: {names}.",
        )
    return int(described.get("revision", 0)), service_container.get("image", "")


def pipeline_source(cfg, repo: str, pipeline_id: int) -> dict:
    """Rama, commit y de qué pipeline vino, para un pipeline de CircleCI.

    Sin `CIRCLECI_TOKEN` esto no es un error de red: es que la cadena no está
    completa, y la UI tiene que poder mostrar lo que sí se resolvió (familia,
    revisión, pipeline) junto con lo que falta.
    """
    token = cfg.get("CIRCLECI_TOKEN")
    if not token:
        raise DeploymentUnresolved(
            "circleci_unavailable",
            "Falta CIRCLECI_TOKEN en la config: se sabe qué pipeline desplegó, "
            "pero no en qué rama se compiló.",
        )
    slug = circleci_slug_prefix(cfg)
    pipeline = _pipeline(repo, slug, token, pipeline_id)
    return _source_from_pipeline(pipeline, repo, slug, token)


def _pipeline(repo: str, slug: str, token: str, pipeline_id: int) -> dict:
    url = f"{CIRCLECI_API}/project/{slug}{repo}/pipeline/{pipeline_id}"
    body = _http_get(url, {"Circle-Token": token, "Accept": "application/json"})
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DeploymentUnresolved("bad_pipeline", f"CircleCI no devolvió JSON: {exc}") from exc
    return data


def _source_from_pipeline(pipeline: dict, repo: str, slug: str, token: str) -> dict:
    """Lee la rama del pipeline, y la resuelve si el pipeline no la trae.

    El pipeline que se despliega en UAT/STG/PROD lo dispara un tag
    (`git push uat-243`), no un webhook de rama. CircleCI no pone `branch` ni
    `commit` en esos pipelines: sólo `vcs.tag` con el ambiente y el pipeline de
    origen (`uat-210`). Sin esto, la pantalla dice "UAT no tiene rama" cuando lo
    que no tiene es el dato de cómo lo desplegaron — que sí se sabe.

    Dos vías, en orden de baratos a caros:

    1. `vcs.tag` viene con el id del pipeline que originó el build (`uat-210` →
       pipeline 210). Un request y ya está.
    2. Si el tag no está o no trae número, se busca entre los pipelines del
       proyecto cuál tiene la misma `revision` y trae `branch`. Son 3 pipelines
       por commit en la práctica, pero la lista se pagina.
    """
    vcs = pipeline.get("vcs") or {}
    revision = vcs.get("revision")
    result = {
        "branch": vcs.get("branch"),
        # `vcs.commit` NO es el hash: es un objeto con `body` y `subject`, que es
        # el mensaje del commit. El SHA está en `vcs.revision`. Tomar `commit`
        # crudo rompía la respuesta con un 500 apenas un ambiente traía objeto.
        "commit": revision,
        "commit_subject": _commit_subject(vcs.get("commit")),
        "tag": vcs.get("tag") or None,
        "source_pipeline_id": None,
        "branch_from_pipeline": None,
        "url": pipeline.get("url"),
    }
    if result["branch"]:
        return result

    from_tag = _branch_from_tag(result["tag"], repo, slug, token)
    if from_tag and from_tag.get("branch"):
        result.update(branch_from_pipeline=from_tag["pipeline_id"], branch=from_tag["branch"])
        if not result["commit_subject"]:
            result["commit_subject"] = from_tag["commit_subject"]
        return result

    by_revision = _branch_from_revision(revision, repo, slug, token)
    if by_revision:
        result.update(branch_from_pipeline=by_revision["pipeline_id"], branch=by_revision["branch"])
        if not result["commit_subject"]:
            result["commit_subject"] = by_revision["commit_subject"]
    return result


# El tag de despliegue es `<ambiente>-<pipeline_id>`. Se ancla a esa forma porque
# un tag puede llamarse cualquier otra cosa y adivinar mal convertiría un
# `uat-210` en un pipeline inexistente.
_DEPLOY_TAG = re.compile(r"^[a-z0-9]+-(\d+)$", re.IGNORECASE)


def _branch_from_tag(tag: str | None, repo: str, slug: str, token: str) -> dict | None:
    """`uat-210` → el pipeline 210, que sí tiene la rama de la release.

    Devuelve `{}` si el tag no trae un número de pipeline o si ese pipeline no
    se puede leer: son las dos formas de que esta vía no sirva. `None` es sólo
    "no había tag", que ni siquiera intenta nada.
    """
    if not tag:
        return None
    match = _DEPLOY_TAG.match(tag.strip())
    if not match:
        return None
    source_id = int(match.group(1))
    try:
        source = _pipeline(repo, slug, token, source_id)
    except DeploymentUnresolved:
        # Un tag que apunta a un pipeline que ya no existe no es motivo para
        # perder lo que sí se resolvió. Se devuelve un resultado vacío para que
        # el que llama siga con la búsqueda por revisión, en vez de cortar.
        return {}
    vcs = source.get("vcs") or {}
    if not vcs.get("branch"):
        return None
    return {
        "pipeline_id": source_id,
        "branch": vcs["branch"],
        "commit_subject": _commit_subject(vcs.get("commit")),
    }


def _branch_from_revision(revision: str | None, repo: str, slug: str, token: str) -> dict | None:
    """Último recurso: el pipeline con la misma revisión que trae `branch`.

    Los pipelines de una release son tres en la práctica —el de la rama de
    trabajo, el de `release/*` y el del tag por ambiente—, así que la lista es
    corta. Se paginan igual porque el proyecto acumula cientos con el tiempo.
    """
    if not revision:
        return None
    page_token: str | None = None
    for _ in range(_MAX_PIPELINE_PAGES):
        url = f"{CIRCLECI_API}/project/{slug}{repo}/pipeline"
        if page_token:
            url += f"?page-token={page_token}"
        body = _http_get(url, {"Circle-Token": token, "Accept": "application/json"})
        try:
            page = json.loads(body)
        except json.JSONDecodeError:
            return None
        # Se puede inferir por el id del tag, así que `number` descendente gana.
        for item in page.get("items", []):
            vcs = item.get("vcs") or {}
            if vcs.get("revision") != revision or not vcs.get("branch"):
                continue
            return {
                "pipeline_id": item.get("number"),
                "branch": vcs["branch"],
                "commit_subject": _commit_subject(vcs.get("commit")),
            }
        page_token = page.get("next_page_token")
        if not page_token:
            break
    return None


def _commit_subject(commit) -> str | None:
    """El `subject` del commit de CircleCI, que es el mensaje de una línea.

    Se muestra aparte del SHA porque es lo que dice qué cambió, y es el dato que
    uno lee de un vistazo para saber si el ambiente tiene lo que debería. Se
    recorta porque el `body` completo puede tener cientos de líneas de diff y
    no va en una celda.
    """
    if isinstance(commit, str):
        return commit[:120] or None
    if isinstance(commit, dict):
        subject = commit.get("subject")
        if isinstance(subject, str) and subject.strip():
            return subject.strip()[:120]
    return None


def environment_deployment(repo: str, env: str, parameters: dict) -> dict:
    """La fila de un ambiente. Nunca tira: el fallo es un dato de la fila.

    Cada etapa suma al resultado antes de la siguiente, así un token de CircleCI
    faltante no borra la familia ni la revisión que ya se habían traído. Una tabla
    donde una fila fallida viniera vacía no sirve para comparar ambientes.
    """
    row = {
        "env": env,
        "region": None,
        "status": "error",
        "task_definition_family": None,
        "task_definition_revision": None,
        "image": None,
        "pipeline_id": None,
        "branch": None,
        "commit": None,
        "commit_subject": None,
        "tag": None,
        "source_pipeline_id": None,
        "branch_from_pipeline": None,
        "pipeline_url": None,
        "message": None,
    }
    try:
        cfg = Config.with_env(env)
    except Exception as exc:
        row.update(status="config_error", message=str(exc))
        return row

    row["region"] = cfg.region

    # Un ambiente local no tiene ECS: no es que falle, es que no aplica. Se dice
    # explícito para que la columna no parezca un hueco sin explicar.
    if getattr(cfg, "is_local", False):
        row.update(status="not_applicable", message="Ambiente local: no tiene ECS.")
        return row

    service_name = parameters["service_name"]
    family = task_definition_family(parameters, env)
    row["task_definition_family"] = family

    try:
        revision, image = deployed_task_definition(cfg, family, service_name)
    except DeploymentUnresolved as exc:
        row.update(status=exc.status, message=exc.message)
        return row
    row["task_definition_revision"] = revision
    row["image"] = image

    pipeline_id = pipeline_id_from_image(image)
    if pipeline_id is None:
        row.update(
            status="no_pipeline_id",
            message=(
                f"La imagen '{image}' no tiene el tag build.<pipeline>, "
                "así que no se puede ubicar en CircleCI."
            ),
        )
        return row
    row["pipeline_id"] = pipeline_id

    try:
        source = pipeline_source(cfg, repo, pipeline_id)
    except DeploymentUnresolved as exc:
        row.update(status=exc.status, message=exc.message)
        return row
    row.update(status="ok", message=None, **source)
    return row


def deployment_report(repo: str, envs: list[str]) -> dict:
    parameters = read_circleci_parameters(repo, Config())
    return {
        "repo": repo,
        "group_name": parameters["group_name"],
        "service_name": parameters["service_name"],
        "cluster": parameters.get("cluster"),
        "task_definition_type": parameters.get("task_definition_type"),
        "environments": [environment_deployment(repo, env, parameters) for env in envs],
    }