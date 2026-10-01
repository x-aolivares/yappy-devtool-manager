# yappy-cli-manager — Agent Instructions

## Versioning

Before committing any changes, bump the version in `pyproject.toml`:

| Tipo de cambio | Formato | Rango | Ejemplo |
|---|---|---|---|
| Ajuste pequeño (bug fix, tweak) | `0.2.x` | x: 0–9 | `0.2.0` → `0.2.1` |
| Ajuste significativo | `0.x.0` | x: 0–9 | `0.2.0` → `0.3.0` |
| Nueva funcionalidad mayor | `x.0.0` | x: 0–∞ | `0.2.0` → `1.0.0` |

## Principles

- **Modular** — cada dominio en su propio módulo (`aws/`, `db/`, `ssm/`, `kafka/`, `workflow/`)
- **Retrocompatible** — no romper comandos existentes. Si un cambio altera comportamiento, version mayor
- **Versionable** — todo cambio se versiona, se commitea y se pushea

## Workflow

When a user requests an adjustment:

1. Make the code change
2. Update `version` in `pyproject.toml`
3. Run the gate: `python -m pytest` (includes the ruff gate) and
   `python -m ruff check backend backend/tests`
4. `git add -A && git commit -m "tipo(REP-XXXXXX): descripción concisa"`
5. `git push`

El issue name (`REP-XXXXXX`) va en el scope del mensaje — ver
[Commit message format](#commit-message-format).

## Lint gate (ruff)

`ruff` runs with a deliberately narrow selection — `F821`, `F811`, `E9` — defined
in `pyproject.toml` and enforced by `backend/tests/test_ruff_gate.py`, so it runs with
`pytest`. It exists because a `NameError` shipped in `yappy setup` (it called
`_win_to_posix` while the function is `win_to_posix`) and the command had never
completed for anyone.

Style rules are intentionally **not** enforced. Notably `F841` must stay off:
`backend/cli/yappy_cli/workflow/executor.py` calls `session.multiple.pf(...)` for its side
effect (opening port-forwards) and discards the return value, so "fixing" that
warning would tear down working tunnels.

If you add a rule, update `backend/tests/test_ruff_gate.py::test_ruff_config_is_scoped_to_correctness_rules`
in the same commit, and say in the description why the rule is safe here.

## Commit message format

Use conventional commits (tipo en inglés, descripción en español) **con el issue name en el scope**:

```
tipo(REP-XXXXXX): descripción concisa en español
```

- `feat(REP-000004):` — new feature
- `fix(REP-000005):` — bug fix
- `refactor(REP-000003):` — code restructuring
- `docs:` — documentation only
- `chore:` — tooling, config, dependencies

El **issue name** es el código `REP-` que ya identifica el trabajo en este proyecto: es el mismo
que va en la rama (`feature/REP-000003`, `release/REP-000003`). Números de GitHub (`#12`) son
otra cosa y no van en el scope.

Reglas del scope:

- **Obligatorio** para `feat`, `fix` y `refactor`: todo cambio de comportamiento lleva su issue.
- **Opcional** para `chore` y `docs`: esas cosas no siempre tienen issue asociado.
- Sale de la rama actual: `git branch --show-current` → `feature/REP-000003` → scope `REP-000003`.
  Si el commit va a un `release/`, se usa el código del release.
- Si el trabajo no tiene issue asignado todavía, **abrilo primero** en el tracker y recién ahí
  commiteá. No inventar un código.
- Un commit que cubre varios issues va con el principal en el scope; los demás se mencionan en el
  cuerpo.

El mensaje debe ser descriptivo: sujeto corto en español y, si el cambio es grande, un cuerpo
con viñetas detallando qué se tocó.

> El historial anterior a esta regla usaba el sufijo `(#2)` al final del asunto
> (ej: `feat: portar paleta BBit con modo oscuro (#2)`). Ese formato queda superado: el issue name
> va ahora en el scope. No reescribas historia para adaptarlo.

Ejemplos:

```
feat(REP-000004): endpoint que lista los esquemas de un ambiente
fix(REP-000004): el selector de esquemas no recarga al cambiar de ambiente
refactor(REP-000003): agrupar los recursos del backend bajo backend/
chore: bumpear ruff a 0.15
```

## Config files

Files under `backend/config/env.*` (without `.example`) are gitignored. Legacy
root-level `config/env.*` files remain ignored during migration.
Never commit real credentials or environment-specific values.
Always update the `backend/config/*.example` templates when the config shape changes.

## Dependencias compartidas (sync con bbit-release-manager)

Este paquete y `bbit-release-manager` viven en el mismo entorno editable y
comparten convenciones. Mantené el **stack común** alineado en `docs/requirements.txt`:

- `typer`, `rich`, `python-dotenv` (runtime)
- `pytest`, `coverage` (`docs/requirements-dev.txt`)

El runtime distintivo (AWS/Kafka/DB en este repo; Bitbucket/CircleCI/SSM en
bbit) puede y debe diferir — son dominios distintos. Al tocar una dep común,
actualizala en AMBOS repos y commitealos juntos para no generar drift.
