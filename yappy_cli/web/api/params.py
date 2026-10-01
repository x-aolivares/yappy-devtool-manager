"""SSM parameter / Secrets Manager endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...sync import params as p
from ..deps import env_config
from ..schemas import (
    ApplyParamsRequest,
    CreateMultiParamsRequest,
    ExecuteParamsRequest,
    ExecuteParamsResponse,
    ParameterReadInfo,
    ParamsApplyResponse,
    ParamsDiffRequest,
    ParamsDiffResponse,
    ParamsMultiResponse,
    ParamsReadResponse,
    ReadParamsEntry,
)

router = APIRouter(tags=["params"])


def _has_paired_secret(cfg_a, cfg_b, name: str) -> bool:
    """True when an SSM param is backed by a secret in either region.

    This supports both the legacy same-name secret pattern and the newer
    secret-alias pattern where the SSM value itself equals the secret name.
    """
    for cfg in (cfg_a, cfg_b):
        try:
            p.read_secret(cfg, name)
            return True
        except p.ParamNotFound:
            pass
        except Exception:  # noqa: BLE001 — binary/no-value secrets don't pair
            pass

        try:
            param_value, _ = p.read_parameter(cfg, name)
        except p.ParamNotFound:
            continue
        except Exception:  # noqa: BLE001 — unreadable parameter is not a valid pair
            continue

        secret_ref = str(param_value).strip()
        if not secret_ref or secret_ref == name:
            continue
        try:
            p.read_secret(cfg, secret_ref)
            return True
        except p.ParamNotFound:
            continue
        except Exception:  # noqa: BLE001 — binary/no-value secrets don't pair
            continue
    return False


@router.post(
    "/api/params/diff",
    operation_id="params_diff",
    response_model=ParamsDiffResponse,
)
def api_params_diff(req: ParamsDiffRequest):
    if req.env_a == req.env_b:
        raise HTTPException(status_code=400, detail="env_a y env_b deben ser distintos")
    if req.service not in ("ssm", "secretsmanager"):
        raise HTTPException(status_code=400, detail="service debe ser 'ssm' o 'secretsmanager'")
    if req.with_secret and req.service != "ssm":
        raise HTTPException(
            status_code=400,
            detail="El modo 'es un secreto' aplica solo cuando el servicio es SSM.",
        )

    cfg_a = env_config(req.env_a)
    cfg_b = env_config(req.env_b)

    try:
        if req.with_secret:
            result = p.diff_params_pair(cfg_a, cfg_b, req.name, req.env_a, req.env_b)
        else:
            result = p.diff_params(
                cfg_a, cfg_b, req.service, req.name, req.env_a, req.env_b,
                include_deletes=req.include_deletes,
            )
            if req.service == "ssm" and _has_paired_secret(cfg_a, cfg_b, req.name):
                result = p.diff_params_pair(
                    cfg_a, cfg_b, req.name, req.env_a, req.env_b
                )
                result.notes = [
                    "Existe un secreto con el mismo nombre en Secrets Manager: "
                    "se resuelve y se trabaja en modo par (secreto → parámetro).",
                    *result.notes,
                ]
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de AWS: {exc}") from exc


@router.post(
    "/api/params/apply",
    operation_id="params_apply",
    response_model=ParamsApplyResponse,
)
def api_params_apply(req: ApplyParamsRequest):
    if req.env_a == req.env_b:
        raise HTTPException(status_code=400, detail="env_a y env_b deben ser distintos")
    if req.service not in ("ssm", "secretsmanager"):
        raise HTTPException(status_code=400, detail="service debe ser 'ssm' o 'secretsmanager'")
    if req.with_secret and req.service != "ssm":
        raise HTTPException(
            status_code=400,
            detail="El modo 'es un secreto' aplica solo cuando el servicio es SSM.",
        )
    if req.target not in ("a", "b"):
        raise HTTPException(status_code=400, detail="target debe ser 'a' o 'b'")

    cfg_a = env_config(req.env_a)
    env_config(req.env_b)
    cfg_target = cfg_a if req.target != "b" else env_config(req.env_b)

    if req.with_secret:
        steps = []
        if req.write_secret:
            steps.append(
                {"step": "secreto", "command": p.build_secret_script(req.name, req.new_secret_value, cfg_a)}
            )
        if req.write_param:
            steps.append(
                {"step": "parámetro", "command": p.build_ssm_script(req.name, req.new_value, req.value_type, cfg_a)}
            )
        script = "\n\n".join(st["command"] for st in steps)
    else:
        script = (
            p.build_ssm_script(req.name, req.new_value, req.value_type, cfg_target)
            if req.service == "ssm"
            else p.build_secret_script(req.name, req.new_value, cfg_target)
        )
        steps = [{"step": "update", "command": script}]

    return {
        "env_a": req.env_a,
        "env_b": req.env_b,
        "service": req.service,
        "name": req.name,
        "script": script,
        "steps": steps,
    }


@router.post(
    "/api/params/apply-execute",
    operation_id="params_apply_execute",
    response_model=ExecuteParamsResponse,
)
def api_params_apply_execute(req: ExecuteParamsRequest):
    if req.env_a == req.env_b:
        raise HTTPException(status_code=400, detail="env_a y env_b deben ser distintos")
    if req.service not in ("ssm", "secretsmanager"):
        raise HTTPException(status_code=400, detail="service debe ser 'ssm' o 'secretsmanager'")
    if req.op not in ("update", "delete"):
        raise HTTPException(status_code=400, detail="op debe ser 'update' o 'delete'")
    if req.target not in ("a", "b"):
        raise HTTPException(status_code=400, detail="target debe ser 'a' o 'b'")
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Se requiere confirmación explícita para ejecutar.")
    if req.with_secret and req.service != "ssm":
        raise HTTPException(
            status_code=400,
            detail="El modo 'es un secreto' aplica solo cuando el servicio es SSM.",
        )
    if req.with_secret and req.op == "delete":
        raise HTTPException(
            status_code=400,
            detail="El modo 'es un secreto' no admite eliminaciones.",
        )

    cfg_a = env_config(req.env_a)
    env_config(req.env_b)
    cfg_target = cfg_a if req.target != "b" else env_config(req.env_b)

    try:
        if req.with_secret:
            step_results = []
            if req.write_secret:
                # El secreto SIEMPRE primero: si falla, no se toca el parámetro.
                step_results.append(
                    {
                        "step": "secreto",
                        "message": p.update_secret(cfg_a, req.name, req.new_secret_value)["message"],
                    }
                )
            if req.write_param:
                step_results.append(
                    {
                        "step": "parámetro",
                        "message": p.put_parameter(cfg_a, req.name, req.new_value, req.value_type)["message"],
                    }
                )
            return {
                "ok": True,
                "message": " · ".join(st["message"] for st in step_results),
                "env_a": req.env_a,
                "env_b": req.env_b,
                "service": req.service,
                "op": req.op,
                "name": req.name,
                "steps": step_results,
            }
        if req.service == "ssm":
            if req.op == "update":
                result = p.put_parameter(cfg_target, req.name, req.new_value, req.value_type)
            else:
                result = p.delete_parameter(cfg_a, req.name)
        else:
            if req.op == "update":
                result = p.update_secret(cfg_target, req.name, req.new_value)
            else:
                result = p.delete_secret(cfg_a, req.name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de ejecución: {exc}") from exc

    return {
        "ok": True,
        "message": result["message"],
        "env_a": req.env_a,
        "env_b": req.env_b,
        "service": req.service,
        "op": req.op,
        "name": req.name,
    }


@router.post(
    "/api/params/multi",
    operation_id="params_multi",
    response_model=ParamsMultiResponse,
)
def api_params_multi(req: CreateMultiParamsRequest):
    if req.service not in ("ssm", "secretsmanager", "ssm+secret"):
        raise HTTPException(
            status_code=400,
            detail="service debe ser 'ssm', 'secretsmanager' o 'ssm+secret'.",
        )
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="Ingresá el nombre del parámetro.")
    if not req.envs:
        raise HTTPException(status_code=400, detail="Elegí al menos una región destino.")
    if req.value_type not in ("String", "StringList", "SecureString"):
        raise HTTPException(
            status_code=400,
            detail="value_type debe ser String, StringList o SecureString.",
        )
    if not req.dry_run and not req.confirm:
        raise HTTPException(status_code=400, detail="Se requiere confirmación explícita para ejecutar.")

    secret_name = (req.secret_name or req.name).strip()
    secret_value = req.secret_value or req.value
    create_secret = bool(req.create_secret or req.service == "ssm+secret")
    if req.service == "secretsmanager":
        create_secret = True
        secret_name = req.secret_name.strip() or req.name.strip()
        secret_value = req.secret_value or req.value
    if req.service == "ssm+secret" or (req.service == "ssm" and req.create_secret):
        create_secret = True
        secret_name = req.secret_name.strip() or req.name.strip()
        secret_value = req.secret_value or req.value
        req.value = secret_name

    value_type = "SecureString" if create_secret else req.value_type

    results = []
    for env in req.envs:
        try:
            cfg = env_config(env)
        except HTTPException as exc:
            results.append({"env": env, "ok": False, "error": str(exc.detail)})
            continue
        try:
            if req.dry_run:
                scripts = []
                if req.service == "secretsmanager":
                    scripts.append(p.build_secret_script(secret_name, secret_value, cfg))
                else:
                    if create_secret:
                        scripts.append(p.build_secret_script(secret_name, secret_value, cfg))
                    scripts.append(p.build_ssm_script(req.name, req.value, value_type, cfg))
                results.append({"env": env, "ok": True, "script": "\n\n".join(scripts)})
            else:
                errors = []
                messages = []
                if req.service == "secretsmanager":
                    try:
                        messages.append(p.update_secret(cfg, secret_name, secret_value)["message"])
                    except Exception as exc:
                        errors.append(f"secreto: {exc}")
                else:
                    if create_secret:
                        try:
                            messages.append(p.update_secret(cfg, secret_name, secret_value)["message"])
                        except Exception as exc:
                            errors.append(f"secreto: {exc}")
                    try:
                        messages.append(
                            p.put_parameter(cfg, req.name, req.value, value_type)["message"]
                        )
                    except Exception as exc:
                        errors.append(f"parámetro: {exc}")
                if errors:
                    results.append(
                        {"env": env, "ok": False, "error": " | ".join(errors)}
                    )
                else:
                    results.append(
                        {"env": env, "ok": True, "message": " ".join(messages)}
                    )
        except Exception as exc:
            results.append({"env": env, "ok": False, "error": f"Error de ejecución: {exc}"})

    return {
        "name": req.name,
        "value_type": value_type,
        "create_secret": create_secret,
        "dry_run": req.dry_run,
        "results": results,
        "ok_count": sum(1 for r in results if r["ok"]),
        "err_count": sum(1 for r in results if not r["ok"]),
    }


@router.get(
    "/api/params/get",
    operation_id="params_get",
    response_model=ParameterReadInfo,
)
def api_params_get(env: str, name: str):
    cfg = env_config(env)
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Ingresá el nombre del parámetro.")
    try:
        value, type_ = p.read_parameter(cfg, name)
    except p.ParamNotFound as exc:
        raise HTTPException(
            status_code=404, detail=f"No existe el parámetro '{name}' en {env}."
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de AWS: {exc}") from exc
    return {"env": env, "name": name, "value": value, "value_type": type_}


@router.post(
    "/api/params/read",
    operation_id="params_read",
    response_model=ParamsReadResponse,
)
def api_params_read(env: str, body: list[ReadParamsEntry | str]):
    cfg = env_config(env)
    try:
        entries = [e if isinstance(e, str) else e.model_dump() for e in body]
        results = p.read_many(cfg, entries)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de AWS: {exc}") from exc
    return {
        "env": env,
        "results": results,
        "ok_count": sum(1 for r in results if r.get("ok")),
        "err_count": sum(1 for r in results if not r.get("ok")),
    }