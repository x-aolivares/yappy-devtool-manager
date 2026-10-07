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

## Estándar de diseño (frontend)

El `frontend/` sigue el sistema visual de Apple: los colores, radios, elevación y
movimiento de las
[Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/design-principles).
**Es el estándar del proyecto, no una preferencia:** un control nuevo se hace con
esta escala, no eligiendo un valor suelto.

### Cómo se aplica

Los tokens viven todos en `frontend/src/styles.scss`. **No escribas un color, un
radio ni un tiempo de transición literal en una plantilla o en un `styles` de
componente** — usá el token. El motivo es concreto: cada valor tiene una versión
distinta en claro y en oscuro, y un literal se ve bien en un tema y cortado en el
otro.

| Qué | Tokens | Valores |
|---|---|---|
| Tinta | `--sys-text`, `--sys-text-secondary` | `#1d1d1f` / `#f5f5f7`; secundario `#6e6e73` / `#a1a1a6` |
| Superficies | `--sys-bg`, `--sys-surface`, `--sys-elevated`, `--sys-hover-bg`, `--sys-selected-bg`, `--sys-code-bg` | fondo `#ffffff` / `#1c1c1e`; superficie `#f5f5f7` / `#000000` |
| Línea | `--sys-border` | `#d2d2d7` / `#38383a` |
| Acento | `--sys-blue`, `--sys-blue-hover`, `--sys-blue-on` | `#0071e3` / `#0077ed`; en oscuro `#0a84ff` / `#409cff` |
| Semánticos | `--sys-success`, `--sys-success-text`, `--sys-success-bg`, `--sys-success-border`, `--sys-warning`, `--sys-warning-text`, `--sys-warning-bg`, `--sys-warning-border`, `--sys-danger`, `--sys-danger-text`, `--sys-danger-bg`, `--sys-danger-border` | verde `#34c759`/`#30d158`, ámbar `#ff9500`/`#ff9f0a`, rojo `#ff3b30`/`#ff453a` |
| Radios | `--sys-radius-sm`, `--sys-radius-md`, `--sys-radius-lg`, `--sys-radius-pill` | 6 / 10 / 14px / píldora |
| Elevación | `--sys-shadow-sm`, `--sys-shadow-md`, `--sys-shadow-lg` | tres niveles; en oscuro van más densos |
| Movimiento | `--sys-ease`, `--sys-dur-fast`, `--sys-dur` | `cubic-bezier(0.4, 0, 0.2, 1)`, 0.15s / 0.25s |
| Medidas | `--sys-control-h`, `--sys-control-pad-x` | 2.5rem (40px) / 1.125rem |
| Foco | `--sys-focus-ring` | halo del anillo de foco |
| Tipografía | `--sys-sans`, `--sys-mono` | tipografía del sistema del usuario |

Son 39 tokens en total y esta tabla los cubre todos: si agregás uno, agregalo
acá.

En modo oscuro, `html[data-theme='dark']` **redefine** los tokens. Un componente
no consulta el tema: consume la misma variable y hereda el valor del tema activo.

### Reglas que ya se pagaron de una romperse

- **El prefijo es `--sys-`, no `--bb-`.** El `--bb-` era la paleta de BBit, que
  este frontend dejó de usar. No lo re-introduzcas ni como alias.
- **Nada de `Cascadia Code` como primera fuente.** Es una fuente de Windows: en
  una Mac el bloque de código caía a Consolas y se veía distinto según el SO.
  Arrancá siempre por `var(--sys-mono)` o `var(--sys-sans)`, que resuelven a la
  tipografía del sistema del usuario — que es el criterio del HIG: nunca una
  fuente incrustada salvo la de marca.
- **Valdema es sólo el wordmark.** Es la única fuente de marca del producto y
  vive en `.sidebar .brand`. No la lleves a otra parte.
- **El `.secondary` y el toggle de tema conservan el `outline` en el foco.** Su
  borde ya es del color de acento, así que el anillo de acento no se distingue
  del control. Es la excepción consciente a la regla del anillo.
- **Todo movimiento nuevo respeta `prefers-reduced-motion`.** Si agregás una
  animación o una transición, agregala también al bloque del final de
  `styles.scss`. Una animación de *entrada* es la que más molesta, porque no se
  puede esquivar mirando.
- **El `--sys-control-h` no sube a 44px.** 44px es el área táctil de iOS y
  duplicaba la altura de cada barra de filtros. Este frontend corre en puntero y
  con muchos controles por pantalla; 40px conserva el aire sin regalar espacio de
  trabajo.

### Lo que este estándar todavía no cubre

El frontend tiene **11 llamadas a `confirm()` nativo en 8 archivos** (`compile.ts`,
`migrate-data.ts`, `params-create.ts`, `params-diff.ts` ×4, `params-edit.ts`,
`params-read.ts`, `schema-sync.ts`, `sessions.ts`). El navegador muestra un alert
que no dice qué va a pasar ni deja recuperar nada, y dos de ellos llegan a decir
*"No se puede deshacer"* (`params-diff.ts:862`, `sessions.ts:81`). Es el principio
de Agency del HIG —*ofrecer perdón, que sea fácil deshacer*— y es donde la app
más se aparta del estándar. Existe ya `shared/notice-modal.ts` con
`role="dialog"` y foco preso, que es la pieza correcta y está sin usar para estas
acciones. Tratarlo aparte.

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
