"""SSM Parameter Store / Secrets Manager comparison between two regions.

Direction is always B -> A: A is the target. When the value is JSON in both
regions the generated "patch" only touches the keys that changed, instead of
overwriting the whole parameter.
"""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


class ParamNotFound(ValueError):
    pass


def _boto_session(profile: str, region: str):
    import boto3

    return boto3.session.Session(profile_name=profile, region_name=region)


def _boto_client(service: str, cfg):
    session = _boto_session(cfg.profile, cfg.region)
    endpoint = getattr(cfg, "endpoint_url", None)
    if endpoint is None and cfg.profile == "localstack":
        endpoint = "http://localhost:4566"
    if endpoint:
        return session.client(service, endpoint_url=endpoint)
    return session.client(service)


def read_parameter(cfg, name: str) -> tuple[str, str]:
    """Return ``(value, type)`` of an SSM parameter, or raise ParamNotFound."""
    client = _boto_client("ssm", cfg)
    try:
        resp = client.get_parameter(Name=name, WithDecryption=True)
    except client.exceptions.ParameterNotFound as exc:
        raise ParamNotFound(name) from exc
    return resp["Parameter"]["Value"], resp["Parameter"]["Type"]


def read_secret(cfg, name: str) -> str:
    """Return the string value of a Secrets Manager secret, or raise ParamNotFound."""
    client = _boto_client("secretsmanager", cfg)
    try:
        resp = client.get_secret_value(SecretId=name)
    except client.exceptions.ResourceNotFoundException as exc:
        raise ParamNotFound(name) from exc
    if "SecretString" in resp:
        return resp["SecretString"]
    if "SecretBinary" in resp:
        raise ValueError(
            f"Secret '{name}' is binary; only string secrets are supported."
        )
    raise ValueError(f"Secret '{name}' has no value")


def _resolve_pair_secret(cfg, param_name: str | None, param_value: str | None):
    """Return the Secrets Manager secret name/value used by a secret-backed SSM param.

    A paired secret may be named exactly like the SSM parameter or, in the
    newer pattern, be referenced by the SSM parameter value itself.
    """
    candidates: list[str] = []
    if param_name and param_name.strip():
        candidates.append(param_name.strip())
    if param_value and str(param_value).strip():
        value = str(param_value).strip()
        if not candidates or value != candidates[0]:
            candidates.append(value)

    for candidate in dict.fromkeys(candidates):
        try:
            return candidate, read_secret(cfg, candidate)
        except ParamNotFound:
            continue
        except Exception:  # noqa: BLE001 - invalid/binary secret should not break pairing detection
            continue
    return None, None


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def build_ssm_script(name: str, value: str, value_type: str, cfg_a) -> str:
    return (
        "aws ssm put-parameter "
        f"--name {_shell_quote(name)} "
        f"--value {_shell_quote(value)} "
        f"--type {value_type or 'String'} "
        "--overwrite "
        f"--profile {_shell_quote(cfg_a.profile)} "
        f"--region {_shell_quote(cfg_a.region)}"
    )


def build_secret_script(name: str, value: str, cfg_a) -> str:
    return (
        "aws secretsmanager update-secret "
        f"--secret-id {_shell_quote(name)} "
        f"--secret-string {_shell_quote(value)} "
        f"--profile {_shell_quote(cfg_a.profile)} "
        f"--region {_shell_quote(cfg_a.region)}"
    )


def build_ssm_delete_script(name: str, cfg) -> str:
    return (
        "aws ssm delete-parameter "
        f"--name {_shell_quote(name)} "
        f"--profile {_shell_quote(cfg.profile)} "
        f"--region {_shell_quote(cfg.region)}"
    )


def build_secret_delete_script(name: str, cfg) -> str:
    return (
        "aws secretsmanager delete-secret "
        f"--secret-id {_shell_quote(name)} "
        "--force-delete-without-recovery "
        f"--profile {_shell_quote(cfg.profile)} "
        f"--region {_shell_quote(cfg.region)}"
    )


# --- Executors (mutating operations, only called on explicit user click) ---


def put_parameter(cfg, name: str, value: str, value_type: str = "String") -> dict:
    """Overwrite/create an SSM parameter on the target region."""
    client = _boto_client("ssm", cfg)
    vtype = value_type or "String"
    try:
        resp = client.put_parameter(
            Name=name, Value=value, Type=vtype, Overwrite=True
        )
    except client.exceptions.ValidationException as exc:
        # Overwriting an Advanced-tier parameter requires repeating its tier.
        if "tier" not in str(exc).lower():
            raise
        resp = client.put_parameter(
            Name=name, Value=value, Type=vtype, Overwrite=True, Tier="Advanced"
        )
    return {
        "ok": True,
        "message": (
            f"Parámetro '{name}' actualizado en {cfg.region} "
            f"(versión {resp.get('Version', '?')})."
        ),
    }


def update_secret(cfg, name: str, value: str) -> dict:
    """Update a Secrets Manager secret; create it first if it does not exist."""
    client = _boto_client("secretsmanager", cfg)
    try:
        client.update_secret(SecretId=name, SecretString=value)
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(Name=name, SecretString=value)
        message = f"Secreto '{name}' no existía; se creó en {cfg.region}."
    else:
        message = f"Secreto '{name}' actualizado en {cfg.region}."
    return {"ok": True, "message": message}


def delete_parameter(cfg, name: str) -> dict:
    """Permanently delete an SSM parameter on the target region."""
    client = _boto_client("ssm", cfg)
    client.delete_parameter(Name=name)
    return {"ok": True, "message": f"Parámetro '{name}' eliminado en {cfg.region}."}


def delete_secret(cfg, name: str) -> dict:
    """Permanently delete a Secrets Manager secret (no recovery)."""
    client = _boto_client("secretsmanager", cfg)
    client.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
    return {"ok": True, "message": f"Secreto '{name}' eliminado en {cfg.region}."}


def _get(node, key):
    if isinstance(node, dict):
        return node.get(key)
    return node[key] if 0 <= key < len(node) else None


def _diff_tree(a, b):
    """Return a tree of changes (only differing paths) between two JSON values.

    Each entry is ``key -> (op, payload)`` with op in
    ``add | del | set | patch``.
    """
    if type(a) is dict and type(b) is dict:
        out = {}
        for key in set(a) | set(b):
            if key not in a:
                out[key] = ("add", copy.deepcopy(b[key]))
            elif key not in b:
                out[key] = ("del", None)
            elif type(a[key]) is dict and type(b[key]) is dict:
                sub = _diff_tree(a[key], b[key])
                if sub:
                    out[key] = ("patch", sub)
            elif type(a[key]) is list and type(b[key]) is list:
                sub = _diff_tree(a[key], b[key])
                if sub:
                    out[key] = ("patch", sub)
            elif a[key] != b[key]:
                out[key] = ("set", copy.deepcopy(b[key]))
        return out

    if type(a) is list and type(b) is list:
        if a == b:
            return {}
        out = {}
        for i in range(max(len(a), len(b))):
            if i >= len(a):
                out[i] = ("add", copy.deepcopy(b[i]))
            elif i >= len(b):
                out[i] = ("del", None)
            elif type(a[i]) is dict and type(b[i]) is dict:
                sub = _diff_tree(a[i], b[i])
                if sub:
                    out[i] = ("patch", sub)
            elif type(a[i]) is list and type(b[i]) is list:
                sub = _diff_tree(a[i], b[i])
                if sub:
                    out[i] = ("patch", sub)
            elif a[i] != b[i]:
                out[i] = ("set", copy.deepcopy(b[i]))
        return out

    return {}


def apply_patch(source, tree):
    """Apply the change tree onto ``source``; untouched parts keep A's values."""
    if isinstance(source, dict):
        out = copy.deepcopy(source)
        for key, (op, payload) in tree.items():
            if op == "del":
                out.pop(key, None)
            elif op in ("add", "set"):
                out[key] = copy.deepcopy(payload)
            elif op == "patch":
                out[key] = apply_patch(out.get(key, {}), payload)
        return out

    if isinstance(source, list):
        out = copy.deepcopy(source)
        # Deletions first (from the end) to keep earlier indexes valid.
        for key in sorted(tree, reverse=True):
            op, payload = tree[key]
            if op == "del":
                if key < len(out):
                    del out[key]
        for key in sorted(tree):
            op, payload = tree[key]
            if op in ("add", "set"):
                while key >= len(out):
                    out.append(None)
                out[key] = copy.deepcopy(payload)
            elif op == "patch":
                while key >= len(out):
                    out.append({})
                out[key] = apply_patch(out[key], payload)
        return out

    return copy.deepcopy(source)


def _flatten(tree, a, b, path: str = "$"):
    rows = []
    for key in sorted(tree):
        op, payload = tree[key]
        old = _get(a, key)
        new = payload if op != "del" else None
        if op == "patch":
            rows.extend(
                _flatten(payload, a[key], b[key], f"{path}.{key}")
            )
        else:
            rows.append(
                {
                    "path": f"{path}.{key}",
                    "op": op,
                    "old": old,
                    "new": new,
                }
            )
    return rows


def compare_values(value_a: str, value_b: str):
    """Compare two string parameter values.

    Returns ``(status, is_json, changes, patch_value)``:
    - ``status``: ``equal`` | ``different``
    - ``changes``: human-readable list of ``{path, op, old, new}`` (empty if equal)
    - ``patch_value``: value to write on A (JSON with only the changed keys
      when both sides are JSON, otherwise B's full value).
    """
    def parse(v: str):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None

    a = parse(value_a)
    b = parse(value_b)
    if a is not None and b is not None:
        tree = _diff_tree(a, b)
        if not tree:
            return "equal", True, [], value_a
        return "different", True, _flatten(tree, a, b), json.dumps(
            apply_patch(a, tree), indent=2, ensure_ascii=False
        )
    if value_a == value_b:
        return "equal", False, [], value_a
    return (
        "different",
        False,
        [{"path": "$", "op": "set", "old": value_a, "new": value_b}],
        value_b,
    )


@dataclass
class ParamDiffResult:
    env_a: str
    env_b: str
    service: str
    name: str
    status: str
    value_a: str | None = None
    value_b: str | None = None
    value_type_a: str | None = None
    value_type_b: str | None = None
    is_json: bool = False
    changes: list = field(default_factory=list)
    patch_value: str | None = None
    script: str | None = None
    notes: list = field(default_factory=list)
    # Secret-pair mode (SSM parameter + paired Secrets Manager secret).
    pair: bool = False
    secret_value_a: str | None = None
    secret_value_b: str | None = None
    secret_changes: list = field(default_factory=list)
    secret_patch_value: str | None = None
    param_apply: str | None = None
    secret_apply: str | None = None
    param_status: str = ""  # none | missing_in_a | missing_in_b | equal | different
    secret_status: str = ""
    param_needs_write: bool = False
    secret_needs_write: bool = False
    steps: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "env_a": self.env_a,
            "env_b": self.env_b,
            "service": self.service,
            "name": self.name,
            "status": self.status,
            "value_a": self.value_a,
            "value_b": self.value_b,
            "value_type_a": self.value_type_a,
            "value_type_b": self.value_type_b,
            "is_json": self.is_json,
            "changes": self.changes,
            "patch_value": self.patch_value,
            "script": self.script,
            "notes": self.notes,
            "pair": self.pair,
            "secret_value_a": self.secret_value_a,
            "secret_value_b": self.secret_value_b,
            "secret_changes": self.secret_changes,
            "secret_patch_value": self.secret_patch_value,
            "param_apply": self.param_apply,
            "secret_apply": self.secret_apply,
            "param_status": self.param_status,
            "secret_status": self.secret_status,
            "param_needs_write": self.param_needs_write,
            "secret_needs_write": self.secret_needs_write,
            "steps": self.steps,
        }


def _build_script(service: str, name: str, value: str, value_type: str | None, cfg_a) -> str:
    if service == "ssm":
        return build_ssm_script(name, value, value_type or "String", cfg_a)
    return build_secret_script(name, value, cfg_a)


def diff_params(
    cfg_a,
    cfg_b,
    service: str,
    name: str,
    env_a: str,
    env_b: str,
    include_deletes: bool = False,
) -> ParamDiffResult:
    """Compare a parameter/secret named ``name`` between two regions.

    ``env_b`` is the source (origen) and ``env_a`` the target (destino), so
    differences always flow ``env_b -> env_a``. When the parameter only exists
    in the destination, ``include_deletes`` toggles generation of a delete
    script (alone it is only reported).
    """
    if service not in ("ssm", "secretsmanager"):
        raise ValueError("service must be 'ssm' or 'secretsmanager'")
    read = read_parameter if service == "ssm" else read_secret

    def safe_read(cfg):
        try:
            return read(cfg, name)
        except ParamNotFound:
            return None

    a = safe_read(cfg_a)
    b = safe_read(cfg_b)

    if service == "ssm":
        value_a, type_a = a if a else (None, None)
        value_b, type_b = b if b else (None, None)
    else:
        value_a, type_a = (a, None) if a else (None, None)
        value_b, type_b = (b, None) if b else (None, None)

    if value_a is None and value_b is None:
        return ParamDiffResult(
            env_a=env_a, env_b=env_b, service=service, name=name,
            status="none",
            notes=[f"No existe en ninguna de las dos regiones."],
        )

    if value_a is None:
        return ParamDiffResult(
            env_a=env_a, env_b=env_b, service=service, name=name,
            status="missing_in_a",
            value_b=value_b, value_type_b=type_b,
            script=_build_script(service, name, value_b, type_b, cfg_a),
            notes=[f"Existe solo en {env_b} (origen) — debe crearse en {env_a} (destino)."],
        )

    if value_b is None:
        if include_deletes:
            script = (
                build_secret_delete_script(name, cfg_a)
                if service == "secretsmanager"
                else build_ssm_delete_script(name, cfg_a)
            )
            notes = [
                f"Existe solo en {env_a} (destino) — se elimina para que quede "
                f"igual a {env_b} (origen)."
            ]
        else:
            script = None
            notes = [
                f"Existe en {env_a} (destino) pero no en {env_b} (origen) — "
                "no hay nada que sincronizar (origen → destino). "
                "Marcá la opción 'Incluir eliminaciones' para generar el borrado."
            ]
        return ParamDiffResult(
            env_a=env_a, env_b=env_b, service=service, name=name,
            status="missing_in_b",
            value_a=value_a, value_type_a=type_a,
            script=script,
            notes=notes,
        )

    status, is_json, changes, patch_value = compare_values(value_a, value_b)

    if status == "equal":
        return ParamDiffResult(
            env_a=env_a, env_b=env_b, service=service, name=name,
            status="equal",
            value_a=value_a, value_b=value_b,
            value_type_a=type_a, value_type_b=type_b,
            is_json=is_json,
            notes=["No hay cambios."],
        )

    new_value = patch_value if is_json else value_b
    return ParamDiffResult(
        env_a=env_a, env_b=env_b, service=service, name=name,
        status="different",
        value_a=value_a, value_b=value_b,
        value_type_a=type_a, value_type_b=type_b,
        is_json=is_json,
        changes=changes,
        patch_value=patch_value,
        script=_build_script(service, name, new_value, type_b, cfg_a),
        notes=[f"Hay cambios de {env_b} (origen) → {env_a} (destino)."],
    )


def diff_params_pair(
    cfg_a,
    cfg_b,
    name: str,
    env_a: str,
    env_b: str,
) -> ParamDiffResult:
    """Compare an SSM parameter AND its paired same-name secret between regions.

    Some parameters back their value with a secret of the same name in
    Secrets Manager (``value`` = identifier/alias, ``secret-value`` = the real
    secret material). Both resources are read independently and compared;
    writes are ALWAYS ordered secret → parameter, never in a single combined
    operation.
    """
    def safe_read(reader, cfg):
        try:
            return reader(cfg, name)
        except ParamNotFound:
            return None

    def where(x_a, x_b):
        if x_a is not None and x_b is not None:
            return "both"
        if x_a is not None:
            return "a"
        if x_b is not None:
            return "b"
        return "none"

    def cmp(a, b):
        if a is not None and b is not None:
            return compare_values(a, b)
        return "none", False, [], None

    pa = safe_read(read_parameter, cfg_a)
    pb = safe_read(read_parameter, cfg_b)

    param_a = pa[0] if pa else None
    param_b = pb[0] if pb else None
    type_a = pa[1] if pa else None
    type_b = pb[1] if pb else None

    secret_name_a, secret_value_a = _resolve_pair_secret(cfg_a, name, param_a)
    secret_name_b, secret_value_b = _resolve_pair_secret(cfg_b, name, param_b)
    sa = secret_value_a
    sb = secret_value_b

    param_where = where(param_a, param_b)
    secret_where = where(sa, sb)

    p_status, p_is_json, p_changes, p_patch = cmp(param_a, param_b)
    s_status, s_is_json, s_changes, s_patch = cmp(sa, sb)

    def _side_state(v_a, v_b, status):
        if v_a is None:
            return "missing_in_a" if v_b is not None else "none"
        if v_b is None:
            return "missing_in_b"
        return "different" if status == "different" else "equal"

    param_status = _side_state(param_a, param_b, p_status)
    secret_status = _side_state(sa, sb, s_status)

    param_needs_write = param_status in ("different", "missing_in_a")
    secret_needs_write = secret_status in ("different", "missing_in_a")

    param_apply = (
        (p_patch if p_is_json and p_status == "different" else param_b)
        if param_status in ("different", "missing_in_a")
        else None
    )
    secret_apply = (
        (s_patch if s_is_json and s_status == "different" else sb)
        if secret_status in ("different", "missing_in_a")
        else None
    )

    extra_notes = []
    if param_a and secret_name_a and param_a == secret_name_a and secret_name_a != name:
        extra_notes.append(
            f"Se detectó que el valor del parámetro apunta al secreto '{secret_name_a}' en {env_a}."
        )
    if param_b and secret_name_b and param_b == secret_name_b and secret_name_b != name:
        extra_notes.append(
            f"Se detectó que el valor del parámetro apunta al secreto '{secret_name_b}' en {env_b}."
        )

    if param_where == "none" and secret_where == "none":
        return ParamDiffResult(
            env_a=env_a, env_b=env_b, service="ssm", name=name,
            status="none", pair=True,
            param_status=param_status, secret_status=secret_status,
            notes=extra_notes or ["No existe el parámetro ni el secreto en ninguna de las dos regiones."],
        )

    missing = [w for w in ("parametro", "secreto")
               if (param_status if w == "parametro" else secret_status) == "missing_in_a"]
    if missing:
        status = "missing_in_a"
        notes = [
            f"En {env_a} (destino) falta crear: {', '.join(missing)} — "
            f"se crean desde {env_b} (origen)."
        ]
    elif param_needs_write or secret_needs_write:
        status = "different"
        notes = [
            f"Hay cambios de {env_b} (origen) → {env_a} (destino). "
            "Se actualiza primero el secreto y luego el parámetro."
        ]
    elif "missing_in_b" in (param_status, secret_status):
        status = "missing_in_b"
        stray = [w for w in ("parametro", "secreto")
                 if (param_status if w == "parametro" else secret_status) == "missing_in_b"]
        notes = [
            f"En {env_b} (origen) no existen: {', '.join(stray)}. "
            "No hay nada que sincronizar (origen → destino); no se elimina nada en este modo."
        ]
    else:
        status = "equal"
        notes = ["No hay cambios."]

    steps = []
    if secret_needs_write:
        steps.append({"step": "secreto", "command": build_secret_script(name, secret_apply or "", cfg_a)})
    if param_needs_write:
        steps.append({"step": "parámetro", "command": build_ssm_script(name, param_apply or "", type_b or type_a or "String", cfg_a)})

    return ParamDiffResult(
        env_a=env_a, env_b=env_b, service="ssm", name=name,
        status=status,
        value_a=param_a, value_b=param_b,
        value_type_a=type_a, value_type_b=type_b or type_a,
        is_json=bool(p_is_json),
        changes=p_changes,
        patch_value=p_patch,
        script="\n\n".join(st["command"] for st in steps) or None,
        notes=extra_notes + notes,
        pair=True,
        secret_value_a=sa, secret_value_b=sb,
        secret_changes=s_changes,
        secret_patch_value=s_patch,
        param_apply=param_apply, secret_apply=secret_apply,
        param_status=param_status, secret_status=secret_status,
        param_needs_write=param_needs_write,
        secret_needs_write=secret_needs_write,
        steps=steps,
    )

# --- Batch reader (web "Leer Parámetros" section) -------------------------


def _normalize_entries(entries) -> list[dict]:
    if not isinstance(entries, list) or not entries:
        raise ValueError("La lista de parámetros no puede estar vacía.")
    if len(entries) > 100:
        raise ValueError(f"Máximo 100 entradas por pedido (recibiste {len(entries)}).")
    norms = []
    for entry in entries:
        if isinstance(entry, str):
            name, is_secret = entry, False
        elif isinstance(entry, dict):
            name = entry.get("key") or entry.get("name")
            is_secret = bool(entry.get("is_secret", False))
        else:
            raise ValueError(
                "Cada entrada debe ser una clave de texto o un objeto "
                "{'key': '<nombre>', 'is_secret': true|false}."
            )
        if not name or not str(name).strip():
            raise ValueError("Cada entrada necesita una clave no vacía.")
        norms.append(
            {
                "name": str(name).strip(),
                "is_secret": is_secret,
            }
        )
    return norms


def _read_ssm_batch(client, names: list[str]) -> dict[str, tuple[str, str]]:
    """Fetch SSM parameters in chunks of 10 (AWS limit) -> name: (value, type)."""
    found = {}
    for i in range(0, len(names), 10):
        resp = client.get_parameters(Names=names[i : i + 10], WithDecryption=True)
        for item in resp.get("Parameters", []):
            found[item["Name"]] = (item.get("Value"), item.get("Type"))
    return found


def read_many(cfg, entries) -> list[dict]:
    """Read a batch of SSM parameters / Secrets Manager secrets for an environment.

    Every entry is ``{"key" (or "name"), "is_secret"}`` or a plain key string.
    With ``is_secret: true`` the value comes from Secrets Manager. Non-secret
    entries are read from SSM; if a key isn't found there it's retried against
    Secrets Manager (auto-detect), so callers can pass a plain list of keys
    without knowing which are secrets. Results are returned in the same order as
    the input; a failure on one entry never stops the rest.
    """
    norms = _normalize_entries(entries)

    needs_ssm = any(not n["is_secret"] for n in norms)
    ssm = _boto_client("ssm", cfg) if needs_ssm else None

    ssm_names = [n["name"] for n in norms if not n["is_secret"]]
    found_ssm: dict[str, tuple[str, str]] = {}
    ssm_error: str | None = None
    if ssm is not None:
        try:
            found_ssm = _read_ssm_batch(ssm, ssm_names)
        except Exception as exc:  # noqa: BLE001 — report on every ssm entry
            ssm_error = str(exc)

    results: list[dict] = []
    secret_pending: list[tuple[int, str]] = []
    auto_pending: list[tuple[int, str]] = []
    for n in norms:
        base = {
            "key": n["name"],
            "is_secret": n["is_secret"],
            "service": "secretsmanager" if n["is_secret"] else "ssm",
            "value": None,
            "value_type": None,
            "ok": False,
            "error": None,
        }
        if n["is_secret"]:
            secret_pending.append((len(results), n["name"]))
        elif ssm_error is not None:
            base["error"] = ssm_error
        elif n["name"] in found_ssm:
            value, vtype = found_ssm[n["name"]]
            base.update(value=value, value_type=vtype, ok=True)
        else:
            base["error"] = "No existe"
            auto_pending.append((len(results), n["name"]))
        results.append(base)

    sm_pending = secret_pending + auto_pending
    if sm_pending:
        sms = _boto_client("secretsmanager", cfg)

        def _read_secret(name: str) -> tuple[str | None, str | None]:
            try:
                resp = sms.get_secret_value(SecretId=name)
            except sms.exceptions.ResourceNotFoundException:
                return None, "No existe"
            except Exception as exc:  # noqa: BLE001
                return None, str(exc)
            if "SecretString" in resp:
                return resp["SecretString"], None
            if "SecretBinary" in resp:
                return None, f"El secreto '{name}' es binario; solo se soportan string."
            return None, f"El secreto '{name}' no tiene valor."

        names = [name for _, name in sm_pending]
        with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
            outcomes = list(pool.map(_read_secret, names))
        auto_indices = {idx for idx, _ in auto_pending}
        for (idx, _name), (value, error) in zip(sm_pending, outcomes):
            if idx in auto_indices:
                if error is None and value is not None:
                    results[idx].update(
                        value=value,
                        ok=True,
                        is_secret=True,
                        service="secretsmanager",
                    )
            else:
                results[idx]["value"] = value
                if error is None and value is not None:
                    results[idx]["ok"] = True
                else:
                    results[idx]["error"] = error

    return results
