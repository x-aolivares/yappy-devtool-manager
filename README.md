# yappy-cli-manager

CLI toolkit para el flujo de desarrollo diario en Yappy: sesiones AWS, túneles SSM,
conexión a bases de datos Aurora, Kafka local y más.

## Requisitos previos

Instalar en este orden:

### 1. Python 3.14+

```
python --version   # >= 3.14
```

### 2. AWS CLI v2

```
aws --version
```

Descargar desde: https://aws.amazon.com/cli/

### 3. Session Manager Plugin

```
session-manager-plugin --version
```

Descargar desde: https://docs.aws.amazon.com/systems-manager/latest/userguide/install-plugin.html

### 4. Kafka

Estructura esperada en disco (configurable vía `KAFKA_PATH` en `config/env.base`):

```
{KAFKA_PATH}/kafka-core/        # bin/, config/, libs/
{KAFKA_PATH}/kafka-ui/          # main.jar (Kafdrop)
{KAFKA_PATH}/temp-logs/         # logs de los procesos
```

Por defecto Kafka se instala dentro del repo (bajo `devkit/kafka`):

```
C:\Development\yappy-cli-manager\devkit\kafka\kafka-core/
C:\Development\yappy-cli-manager\devkit\kafka\kafka-ui/
```

### 5. Java 17+ (solo para Kafdrop UI)

```
java --version
```

---

## Uso (devs)

La herramienta la instala y mantiene el maintainer. **No clonés ni instalés nada.**

¿Necesitás un cambio o un comando nuevo? Decile al maintainer y él lo integra.

## Onboarding (maintainer)

```bash
git clone git@github.com:x-aolivares/yappy-cli-manager.git
cd yappy-cli-manager
bash install.sh           # Instala + configura PATH automáticamente
source ~/.bashrc
yappy setup               # Configura shell, verifica dependencias, crea config/env.base
```

Después de cada commit:

```bash
yappy update              # git pull + pip install -e .
```

`yappy setup` hace todo automáticamente:

1. **Shell integration** — agrega `eval "$(yappy init bash)"` al `.bashrc`
2. **Config files** — crea `config/env.base` desde `config/env.base.example` si no existe (los archivos de entorno como `env.dev`/`env.qa` deben crearse manualmente copiando un `.example`)
3. **Dependencias** — verifica que `aws` y `session-manager-plugin` estén instalados
4. **Perfil AWS** — verifica que el perfil configurado exista

Después del setup, editá los archivos de config con tus valores:

```bash
yappy edit           # Abre el proyecto en VS Code
# Editar config/env.base (los archivos por entorno deben crearse a mano, p. ej. copiando config/env.environment.example a config/env.dev)
```

---

## Uso

### AWS

```bash
yappy aws session        # Iniciar sesion SSO (aws sso login)
yappy aws mfa             # Generar token MFA + configurar perfil
```

### Base de datos

```bash
yappy db up dev           # Tunnel + token (bloquea)
yappy db up dev -d        # Tunnel + token (background)
yappy db up qa -r         # Tunnel + auto-refresh cada 12 min
yappy db up qa -r -d      # Tunnel + auto-refresh en background
yappy db refresh dev      # Solo regenerar token y guardar en .env.local
```

### SSM Tunnels (port-forwarding)

```bash
yappy ssm connect 8080 qa cap              # Tunnel a cluster (bloquea)
yappy ssm connect 8080 qa cap -d           # Tunnel a cluster (background)
yappy ssm connect 8080,9090,3000 qa cap    # Multiples puertos (bloquea)
yappy ssm connect "8080 9090 3000" qa cap  # Multiples puertos (bloquea)
yappy ssm producer qa                # Tunnel a producer (bloquea)
yappy ssm producer qa -d             # Tunnel a producer (background)
yappy ssm kafdrop qa                 # Tunnel a Kafdrop UI
yappy ssm databricks dev             # Tunnel a Databricks
yappy ssm kill                       # Matar todos los túneles SSM activos
```

### Kafka local

```bash
yappy kafka up server                # Iniciar Kafka (KRaft)
yappy kafka up server -d             # Iniciar Kafka en background
yappy kafka up ui                    # Iniciar Kafdrop UI
yappy kafka up ui -d                 # Kafdrop UI en background
yappy kafka up clean                 # Resetear storage de Kafka
yappy kafka down                     # Detener Kafka
```

### Workflows compuestos

```bash
yappy workflow debug-local qa        # Sesion AWS + tunnel DB + Kafka + agents
```

### Utilidades

```bash
yappy version                        # Version instalada
yappy config                         # Mostrar config actual
yappy config dev                     # Mostrar config de un ambiente
yappy home                           # Ir al directorio del proyecto
yappy setup                          # Onboarding inicial
yappy reload                         # Reinstalar paquete + recargar .bashrc
yappy edit                           # Abrir proyecto en VS Code
yappy py-purge                       # Limpiar cache pip
```

### Web app

```bash
pip install -r docs/requirements-web.txt
cd web/frontend && npm install && cd ../..   # una sola vez

yappy web                             # API + UI juntos (lo normal)
yappy web api                         # Solo el backend
yappy web ui                          # Solo el frontend
yappy stop web                        # Limpiar procesos web si la terminal murió
```

`yappy web` levanta el backend FastAPI en `127.0.0.1:8300` y el dev server de
Angular en `127.0.0.1:4300`, y baja ambos con un solo Ctrl+C. Los puertos salen
del enum centralizado `web/api/ports_registry.py`, no están hardcodeados.

El API está **bound a `127.0.0.1` únicamente y sin autenticación**: es una
herramienta interna de desarrollo, no lo expongas en `0.0.0.0` sin agregar
autenticación primero.

La UI tiene cuatro secciones:

- **Parámetros** — busca en `config/env.*` y resuelve un valor contra Secrets
  Manager. El valor crudo del parámetro *es* el nombre del secreto: no hay
  prefijos, ni búsqueda, ni listing.
- **Bases de datos** — lista schemas y objetos (tablas, vistas, SPs, funciones,
  triggers) y migra el DDL de un ambiente a tu MySQL local. Solo estructura; la
  migración de datos todavía no está.
- **Consultas SQL** — solo lectura: `SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN` y
  `WITH`, una sentencia por vez. El driver además va sin multi-statement, así
  que apilar dos consultas no es una vía para esquivarlo.
- **Inicio** — diagnóstico: si el túnel de BD está abierto, si tu MySQL local
  responde, y qué config falta cuando algo no va.

**El túnel de base de datos lo levanta la CLI, no la web.** Antes de explorar o
consultar un ambiente:

```bash
yappy run db dev -d      # en otra terminal
```

La web solo se conecta a `127.0.0.1:$DB_PORT`. Si el puerto está cerrado lo dice
con el comando exacto, en vez de un error de conexión.

Para migrar a MySQL local definí en `config/env.base` las credenciales
(`LOCAL_DB_USER` / `LOCAL_DB_PASSWORD`) y, si querés que la web arranque el
servidor por vos, `LOCAL_MYSQL_START_CMD`. Sin ese comando el backend no adivina
un nombre de servicio: dice qué clave configurar.

Endpoints disponibles:

```bash
curl http://127.0.0.1:8300/health
curl http://127.0.0.1:8300/environments
curl http://127.0.0.1:8300/parameters/dev

# Resolver en Secrets Manager el valor de un parámetro.
# El valor crudo del parámetro ES el nombre del secreto.
curl -X POST http://127.0.0.1:8300/parameters/dev/resolve \
  -H 'Content-Type: application/json' \
  -d '{"key":"DB_SECRET"}'

# Explorador de bases
curl http://127.0.0.1:8300/databases/dev/schemas
curl http://127.0.0.1:8300/databases/dev/schemas/sales/objects
curl http://127.0.0.1:8300/databases/dev/connection   # ¿túnel abierto?

# Migración: preview primero, después apply
curl -X POST http://127.0.0.1:8300/databases/dev/migrate/preview \
  -H 'Content-Type: application/json' \
  -d '{"schema":"sales","tables":["orders"]}'
curl -X POST http://127.0.0.1:8300/databases/dev/migrate \
  -H 'Content-Type: application/json' \
  -d '{"schema":"sales","tables":["orders"]}'

# Consulta de solo lectura
curl -X POST http://127.0.0.1:8300/query/dev \
  -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT COUNT(*) FROM orders","schema":"sales"}'

# MySQL local
curl http://127.0.0.1:8300/local-mysql
```

Conectar a RDS requiere el CA bundle en `~/.aws/rds-ca-*.pem` o `RDS_CA_PATH` en
`config/env.<ambiente>`. No es opcional: el token de IAM *es* una contraseña, y
mandarlo sin cifrar filtraría acceso a IAM.

Los ambientes salen de `config/env.*` (los `.example` se ignoran). Si la lista
viene vacía, creá `config/env.<ambiente>` copiando `config/env.environment.example`.

Errores siempre en el mismo formato: `{"code": "...", "message": "...", "detail": "..."}`.

---

## Nueva sintaxis (Docker-like)

La CLI soporta una sintaxis nueva inspirada en Docker, con retrocompatibilidad total:
los comandos viejos (`yappy db up`, `yappy ssm ...`, `yappy kafka up ...`) siguen funcionando.

```bash
yappy run db dev              # Tunnel + token (bloquea)
yappy run db dev -d           # Tunnel + token (background)
yappy run db qa -r            # Tunnel + auto-refresh cada 12 min
yappy run db qa -r -d         # Tunnel + auto-refresh en background

yappy run tunnel 8080 qa cap  # Tunnel a cluster
yappy run tunnel producer qa  # Tunnel a producer
yappy run tunnel kafdrop qa   # Tunnel a Kafdrop UI
yappy run tunnel databricks dev  # Tunnel a Databricks

yappy run kafka server -d     # Kafka en background
yappy run kafka ui -d         # Kafdrop UI en background
yappy run kafka clean         # Resetear storage de Kafka
yappy run workflow dev        # Ejecutar el workflow executor

yappy stop kafka              # Detener Kafka server
yappy stop kafka ui           # Detener Kafdrop UI
yappy stop tunnel             # Matar túneles SSM activos

yappy login aws               # Iniciar sesion SSO
yappy login mfa <user> <token>  # Generar credenciales MFA

yappy exec aws dev s3 ls      # Ejecutar aws con el profile/region del entorno

yappy logs db dev             # Logs del tunnel de DB
yappy logs kafka server -f    # Seguir logs de Kafka server
yappy logs tunnel -n 100      # Ultimas 100 lineas de los túneles
```

---

## Flujo tipico: debug local

```bash
# 1. Iniciar sesion AWS
yappy aws session

# 2. Tunnel a base de datos + auto-refresh
yappy db up qa -d

# 3. (Opcional) Tunnel a cluster para probar endpoints
yappy ssm connect 8080 qa cap -d

# 4. Iniciar Kafka
yappy kafka up server -d

# 5. Iniciar Kafdrop
yappy kafka up ui -d

# 6. Levantar kafka-agents (pendiente integracion)

# 7. Al terminar, limpiar
yappy ssm kill
yappy kafka down
```

## Detener procesos en background

| Que inicio | Como detener |
|---|---|
| `yappy db up ... -d` | `yappy ssm kill` |
| `yappy ssm connect ... -d` | `yappy ssm kill` |
| `yappy kafka up server -d` | `yappy kafka down` |
| `yappy kafka up ui -d` | `yappy kafka down` |
