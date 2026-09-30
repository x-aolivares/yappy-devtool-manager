# Plan: App Web para yappy-cli-manager

## Estructura de carpetas del repo (confirmada, implementada)

```
aws-cli-manager/
  library/      # capa de dominio compartida: config, sesiones AWS, tunnels SSM, DB, Kafka
    aws/
    db/
    ssm/
    kafka/
    api/
    config.py
    base.py
    process_tracker.py
    logger.py
    deprecation.py
  cli/          # capa delgada de comandos Typer, consume library/
    cli.py
    verbs/
    workflow/
  web/          # nuevo — app web (backend + frontend), consume library/ de solo lectura
    api/          # backend FastAPI, arquitectura hexagonal
    frontend/     # Angular standalone + CSS nativo
  config/       # env.base + env.<ambiente>, leídos por library/config.py
  devkit/       # binarios de Kafka (descargados, gitignored)
  tests/
```

Este refactor (extraer `library/` desde el antiguo `yappy_cli/` y adelgazar `cli/`) ya se
ejecutó y se verificó sin regresión: `pytest` da el mismo resultado antes y después
(50 passed, 1 failed preexistente no relacionado). El comando `yappy` sigue funcionando
igual (entry point `cli.cli:app`). Las carpetas legacy huérfanas (`yappy_cli/` antiguo tras
la migración, `src/`, `yappy_devkit/`, `frontend/` viejo con solo `node_modules`, `build/`)
fueron eliminadas tras confirmar que no había código fuente real en riesgo.

## Principio rector: no afectar el DevTool actual

Este incremento de la web es **estrictamente aditivo** sobre `library/` y `cli/`. `library/`
sigue siendo la fuente de verdad para sesiones AWS, tunnels y Kafka; la web (`web/api/` +
`web/frontend/`) es un consumidor adicional, no un reemplazo. Reglas de aislamiento:

1. **`web/` no modifica `library/` ni `cli/`**: todo el código nuevo vive en `web/`. Se
   importa `library` como dependencia de solo lectura (`from library.config import Config`,
   etc.).
2. **Process tracking separado — riesgo confirmado**: `library/process_tracker.py` guarda los
   PIDs trackeados en `~/.yappy/tracker/*.json` de forma **global**, y `BaseCommand.kill_ssm()`
   (sin `pid`) mata **todos** los tunnels ahí trackeados. Si la web reusa esas mismas funciones
   tal cual, un `yappy ssm kill` ejecutado desde el CLI podría matar tunnels abiertos por la web
   (y viceversa un cleanup de la web podría matar tunnels del CLI).
   **Mitigación**: `web/api/` NO debe llamar a `BaseCommand.ssm_tunnel()` / `kill_ssm()`
   directamente sobre el tracker compartido. Opciones:
   - Trackear los procesos de la web con un `resource` distinto (ej. `"tunnel-web"`) y un
     tracker dir separado (`~/.yappy/tracker-web/`), reimplementando el mismo patrón pero
     aislado.
   - O, más simple: la web abre sus propios `subprocess.Popen` de `aws ssm start-session`
     directamente en `web/api/infrastructure/`, sin pasar por `process_tracker` de `library`
     en absoluto.
   Se decide en el momento de implementar `infrastructure/`, pero **queda descartado** reusar
   `kill_ssm()` sin `pid` desde la web.
3. **Puertos sin colisión**: ver enum centralizado (sección 5) — todo puerto nuevo del API/UI
   web se elige fuera del rango ya usado por CLI/Kafka/DB tunnels.
4. **Config de solo lectura**: la web lee `config/env.*` con la misma lógica que
   `Config.known_environments()`, pero no escribe sobre `config/.env.local` (ese archivo lo
   gestiona `library/db/tunnel.py` para sus propios refreshers del CLI). La web usa su propio
   estado en memoria o un archivo separado si necesita persistir tokens.
5. **Sin cambios de comportamiento en comandos existentes**: ningún comando `yappy ...` cambia
   de firma, default o salida como resultado de este trabajo. Cualquier función que la web
   necesite de `library` se consume por composición (import directo), sin tocar la función
   original.
6. **Verificación de no-regresión**: antes de considerar cerrado cada incremento, correr la
   suite existente (`pytest`) para confirmar que sigue en verde sin cambios.

## Lo que ya existe y funciona (`library/` + `cli/`)

| Módulo | Qué hace |
|---|---|
| `library/aws/session.py` | SSO login + MFA credentials |
| `library/ssm/tunnel.py`, `library/base.py` | Port-forwarding a cluster, bastion, producer, kafdrop, databricks; `kill_ssm`; tracking de procesos vía `process_tracker.py` |
| `library/db/tunnel.py` | Genera token IAM de RDS (`botocore`, con fallback a awscli), abre tunnel SSM hacia Aurora, auto-refresh cada 12 min, escribe `.env.local` |
| `library/config.py` | Lee `config/env.base` + `config/env.<nombre>` con `dotenv_values`. `known_environments()` ya lista los ambientes disponibles escaneando `config/env.*` — **reusable directo** para el combo de ambientes en la web |
| `library/kafka/manager.py`, `library/kafka/setup.py` | up/down/clean de Kafka + Kafdrop |
| `cli/cli.py`, `cli/verbs/`, `cli/workflow/` | Comandos Typer que orquestan `library/` (capa delgada, sin lógica de dominio propia) |

---

## 1. Backend web (Python + FastAPI, arquitectura hexagonal)

Vive en `web/api/`, consumiendo `library/` como dependencia externa:

```
web/api/
  domain/                    # entidades y reglas de negocio puras (sin deps externas)
    entities.py               # Environment, Parameter, Schema, DbObject, QueryResult
    ports.py                  # interfaces: ParameterRepository, DbRepository, SecretResolver, LocalMysqlController
    exceptions.py              # ParameterNotFoundError, SecretResolutionError, DbConnectionError, SchemaNotFoundError, LocalMysqlUnavailableError
  application/                # casos de uso, orquestan dominio + puertos
    list_parameters.py
    resolve_secret.py
    list_environments.py
    list_schemas.py
    migrate_object.py          # SP/tabla/función/data desde ambiente -> local
    run_query.py
  infrastructure/              # adaptadores concretos
    aws_ssm_adapter.py          # AWS SSM Parameter Store
    aws_secrets_adapter.py       # AWS Secrets Manager
    mysql_adapter.py             # driver MySQL (queries, DDL, introspección)
    local_mysql_service.py        # levanta/gestiona MySQL nativo local (reemplazo de Workbench)
    env_config_adapter.py         # reusa library.config.Config.known_environments()
  routers/                      # FastAPI routers (capa de entrada HTTP)
    environments.py
    parameters.py
    databases.py
    query.py
  error_handlers.py             # exception handlers centralizados
  schemas.py                     # DTOs Pydantic
  ports_registry.py               # enum centralizado de puertos (ver sección 5)
  main.py                         # FastAPI app factory
```

### Manejo de excepciones (requisito explícito)

- Excepciones de dominio propias definidas en `domain/exceptions.py`.
- `error_handlers.py` con `@app.exception_handler` por tipo de excepción, mapeando a un HTTP status + payload consistente:
  ```json
  { "code": "SECRET_RESOLUTION_ERROR", "message": "...", "detail": "..." }
  ```
- Nada de excepciones sin capturar llegando al cliente.

---

## 2. Feature: Consulta de parámetros por ambiente

- **Origen de ambientes**: leer `config/env.*` (excluyendo `.example` y `env.base`) — misma lógica que `library.config.Config.known_environments()`, reusada en `env_config_adapter.py`.
- **Detección JSON vs valor plano**: backend intenta `json.loads` con try/except sobre el valor del parámetro.
- **Si es valor plano**: botón "resolver en Secrets Manager" → endpoint que busca el secreto y lo resuelve usando el profile/región del ambiente seleccionado (mismo profile que usa `Config`).
- **Para `local`**: acción de "levantar MySQL nativo" vía `LocalMysqlService` — ejecuta el comando de arranque (servicio Windows o `mysqld` standalone, a confirmar) + healthcheck. Resuelve el problema actual con DBeaver.
- **Manejo de errores**: todo endpoint envuelto en casos de uso que lanzan excepciones de dominio, capturadas centralmente.

---

## 3. Feature: Exploración de bases de datos por ambiente (estilo migración)

- Conexión reusa el mecanismo de `library/db/tunnel.py`: token IAM + tunnel SSM (lo abre si no existe uno activo, respetando el aislamiento de tracking de la sección "Principio rector").
- `GET /databases/{env}/schemas` → dropdown de schemas disponibles.
- Al seleccionar schema: listar stored procedures, tablas, funciones.
- Acción "migrar hacia local": trae DDL y/o data del objeto elegido y lo aplica contra el MySQL local.
- Se implementa limpio dentro de `infrastructure/mysql_adapter.py` + `application/migrate_object.py`.

---

## 4. Feature: Consultas SQL directas

- Endpoint que recibe SQL crudo contra el ambiente + schema seleccionados (soporta SELECT con joins, etc.) y devuelve resultados tabulares.
- **Riesgo a marcar**: ejecuta SQL arbitrario contra ambientes reales (dev/qa/uat). Mitigaciones sugeridas:
  - Restringir por whitelist de statement (`SELECT` only) a nivel de aplicación, salvo que se quiera permitir DML explícitamente.
  - Bind exclusivo a `127.0.0.1` (no expuesto fuera de localhost).

---

## 5. Enum centralizado de puertos

```python
# web/api/ports_registry.py
from enum import IntEnum

class YappyPort(IntEnum):
    WEB_API = 8300          # nuevo, sin choque con lo existente
    WEB_UI_DEV = 4300        # ng serve
    DB_TUNNEL = 8100         # ya usado en env.dev (DB_PORT)
    KAFDROP = 9001
    DATABRICKS = 4433
    AWS_SSM_LOCAL = 53360    # AWS_PORT
    KAFKA_BROKER = 9092      # confirmar contra library/kafka/setup.py
    KAFKA_UI = 9000          # confirmar
```

Pendiente: revisar `library/kafka/setup.py` para confirmar los puertos reales de Kafka antes de fijar el enum definitivo. Regla general: cualquier puerto nuevo del API/UI web se elige fuera del rango ya usado (propuesta: 8300 API / 4300 UI dev).

---

## 6. Frontend (Angular standalone + CSS nativo, paleta GitHub-like)

Vive en `web/frontend/`:

```
web/frontend/src/app/
  layout/
    shell/                 # home + sidebar layout
    sidebar/               # listado de features a la izquierda
  features/
    parameters/
    database-explorer/
    query-console/
  core/
    services/               # ApiService por feature, http client
    models/
  shared/
    components/              # botón, tabla, dropdown, badge json/plain
styles/
  _variables.css             # paleta GitHub
  _base.css
```

- Componentes **standalone** de Angular (sin NgModules).
- CSS nativo por componente (`:host` + variables CSS globales para la paleta).
- Sidebar fijo con navegación a Home + las 3 features.

### Paleta de colores (estilo GitHub)

| Uso | Dark | Light |
|---|---|---|
| Fondo | `#0d1117` | `#ffffff` |
| Superficie/cards | `#161b22` | `#f6f8fa` |
| Bordes | `#30363d` | `#d0d7de` |
| Texto | `#c9d1d9` | `#24292f` |
| Acento (links/botones) | `#2f81f7` | `#0969da` |
| Éxito | `#2ea043` | `#1a7f37` |
| Error/peligro | `#f85149` | `#cf222e` |
| Advertencia | `#d29922` | `#9a6700` |

---

## Decisiones tomadas (cerradas con el maintainer)

1. **Parámetros = `config/env.*`**. No hay Parameter Store de por medio. El
   placeholder `EnvFileParameterAdapter` no era un placeholder: era la
   implementación correcta. Los ambientes se leen de la misma fuente.
2. **Resolver secreto = el valor del parámetro ES el nombre del secreto**. Sin
   prefijos, sin convención, sin listar nada. `GetSecretValue(SecretId=<valor
   crudo del parámetro>)` y nada más. Solo se llama cuando el usuario aprieta
   el botón — el listado de parámetros jamás toca AWS. **No** hay búsqueda por
   coincidencia de valor (imposible: no vas a obtener todos los secretos).
3. **Migración: DDL primero, data después.** La segunda etapa migra data de
   varias tablas en un rango de fechas, así que el contrato de migración se
   diseña desde el inicio con `mode`, lista de tablas, columna de fecha y rango
   (`date_from` / `date_to`), aunque la primera versión solo implemente DDL.
4. **Query console: solo lectura** (`SELECT` / `SHOW` / `DESCRIBE`). Whitelist
   en la capa de aplicación; cualquier otra cosa levanta `UnsafeQueryError`.
   DML (`INSERT`/`UPDATE`/`DELETE`) queda para un toggle explícito más adelante.
5. **El API nunca puede morir.** `library/` usa `die()` → `sys.exit()` en todos
   lados; llamar eso desde un handler de FastAPI mata el worker de uvicorn.
   Regla: **el API no llama directo a nada de `library/` que pueda hacer
   `sys.exit()`** — lo que pueda morir (token RDS, túnel SSM) corre en un
   proceso hijo propio y el API solo lee su exit code. Un `die()` se muere el
   hijo, nunca el API.
6. **Entorno de desarrollo: Windows con Git Bash.** El comando MySQL local
   concreto sigue sin definirse; hasta entonces `LOCAL_MYSQL_START_CMD` queda
   configurable por config en vez de hardcodear Windows/Mac/Docker.

---

## Estado de implementación

| Pieza | Estado |
|---|---|
| `domain/` (entities, ports, exceptions) | Hecho |
| `ports_registry.py` (enum centralizado) | Hecho (incluye `LOCAL_MYSQL`) |
| `error_handlers.py` + payload `{code, message, detail}` | Hecho |
| `container.py` (composición de dependencias) | Hecho |
| `infrastructure/env_config_adapter.py` | Hecho |
| `infrastructure/param_config_adapter.py` | Hecho |
| `infrastructure/aws_secrets_adapter.py` | Hecho |
| `application/list_environments.py` | Hecho |
| `application/list_parameters.py` | Hecho |
| `application/resolve_secret.py` | Hecho |
| `routers/environments.py`, `routers/parameters.py` | Hecho |
| `cli/verbs/web.py` (`yappy web` = API + UI) | Hecho |
| `cli/verbs/stop.py` (`yappy stop web`) | Hecho |
| `infrastructure/mysql_connection.py` (CA, token IAM, mapeo de errores) | Hecho |
| `infrastructure/mysql_adapter.py` (introspección, queries, DDL) | Hecho |
| `infrastructure/local_mysql_service.py` | Hecho |
| `application/list_schemas.py` / `list_objects.py` | Hecho |
| `application/migrate_object.py` (DDL) | Hecho |
| `application/run_query.py` (solo lectura) | Hecho |
| `application/local_mysql.py` | Hecho |
| `routers/databases.py`, `routers/query.py`, `routers/migrate.py`, `routers/local_mysql.py` | Hecho |
| `domain/sql_guard.py` (whitelist de solo lectura) | Hecho |
| `proxy_config.py` (proxy generado desde el enum) | Hecho |
| Tests de `web/api` (111) y de la CLI web (16) | Hecho |
| `web/frontend/` (Angular 22, standalone + signals) | Hecho |

### Decisiones tomadas al implementar

- **PyMySQL sí soporta IAM auth de RDS.** El plugin `mysql_clear_password` está
  implementado en `_process_auth` (auth-switch path) de PyMySQL 2.2.8, así que
  `pymysql.connect(password=<token>)` funciona contra RDS sin hacks. `auth_plugin`
  no es un kwarg de conexión y `defer_connect` no sirve como seam, porque
  `_auth_plugin_name` solo se setea desde el greeting del server.
- **TLS es obligatorio con RDS** aunque el túnel termine en 127.0.0.1: el token
  IAM *es* una contraseña, y mandarla en claro filtraría acceso a IAM. Se usa
  `ssl_ca` + `ssl_verify_cert=True`, y el bundle se busca en
  `RDS_CA_PATH` o `~/.aws/rds-ca-*.pem`.
- **El probe del puerto va antes de resolver el CA.** Un puerto cerrado es el
  fallo más común, y reportarlo (con el comando `yappy run db <env> -d`)
  importa más que el CA faltante; si no, el segundo error tapa al primero.
- **El guard de solo lectura vive en el use case, no solo en el adapter.** Es
  una regla de dominio: si solo el adapter la aplicara, cambiar de adapter
  eliminaría la garantía en silencio. El adapter mantiene su copia como
  defensa en profundidad. Además el driver va con multi-statement deshabilitado.
- **`tunnel_manager.py` queda descartado.** `mysql_adapter` es agnóstico al
  túnel: se conecta a `127.0.0.1:DB_PORT` y un healthcheck TCP devuelve
  `DbConnectionError` con la instrucción de levantarlo. Así el web nunca toca
  el process tracker de la library y `kill_ssm()` no puede ver procesos del web.
- **El target schema por defecto es `<env>_<schema>`**, salvo que se configure
  `LOCAL_DB_NAME`: sin el prefijo, migrar dev y qa al mismo schema local
  colisionaría.
- **El proxy del dev server se genera desde `YappyPort`**, no se commitea como
  JSON. Un puerto escrito en dos lugares se desincroniza en el primer cambio, y
  el síntoma es un 404 en la UI sin explicación. Sin proxy (`npm start` a pelo)
  la UI funciona igual vía CORS, que acepta `localhost` y `127.0.0.1`.

## Frontend

Angular 22, componentes standalone, signals, CSS nativo. Estructura:

```
web/frontend/src/app/
  core/        models.ts (espejo de los DTOs), api.ts, env-context.ts
  shared/      alert.ts (render del payload de error)
  features/    home, parameters, databases, query
```

Decisiones:

- **Los tipos se replican a mano** desde `web/api/schemas.py` en vez de generarse
  del OpenAPI: evita una dependencia de build y deja explícito qué espera la UI.
- **Cada feature es un `loadComponent` diferido.** El shell y el core se Bajan
  siempre; entrar a Inicio no arrastra el editor de SQL.
- **Los bindings de los templates se validan en build.** Un typo en un template
  rompe `ng build`, no la app en runtime.
- **El ambiente es un signal global** con persistencia en localStorage. Cambiar
  de ambiente no debe reiniciar la vista actual, porque comparar dev contra qa
  es el flujo principal.
- **El historial de consultas se guarda en localStorage** y solo lo que la
  persona escribió, nunca los resultados. No va a ningún servidor.

---

## Preguntas abiertas (restantes)

1. **MySQL local**: falta el comando exacto que se usa hoy a mano para
   levantarlo (¿servicio de Windows? ¿`mysqld` standalone? ¿XAMPP?). Ya no es
   bloqueante: el web lee `LOCAL_MYSQL_START_CMD` de `config/env.base` y, si no
   está, dice qué clave configurar. Falta completarlo con el valor real.
2. **RDS CA bundle**: confirmar si existe `~/.aws/rds-ca-rsa2048-g1.pem` en la
   máquina, o si hay que definir `RDS_CA_PATH`. Si no está, la conexión a
   ambientes falla con un mensaje explícito.
3. **Data migration**: qué columna de fecha se usa por tabla. ¿Se infiere
   (`created_at` / `updated_at`) o el usuario la elige por tabla en el UI? El
   contrato (`MigrateRequest.date_column` / `date_from` / `date_to`) ya está
   definido, así que esto no bloquea la implementación.
