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

Estructura esperada en disco (configurable vía `KAFKA_PATH` en `backend/config/env.base`):

```
{KAFKA_PATH}/kafka-core/        # bin/, config/, libs/
{KAFKA_PATH}/kafka-ui/          # main.jar (Kafdrop)
{KAFKA_PATH}/temp-logs/         # logs de los procesos
```

Por defecto Kafka se instala dentro del repo (bajo `devkit/kafka`):

```
/c/Development/yappy-cli-manager/devkit/kafka/kafka-core/
/c/Development/yappy-cli-manager/devkit/kafka/kafka-ui/
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
bash install.sh           # Bootstrap: instala el paquete + configura PATH
source ~/.bashrc
yappy setup               # Instala deps (backend + frontend), config, kafka
```

`install.sh` es **solo bootstrap**: su único trabajo es hacer que el comando `yappy`
exista. No instala dependencias, porque eso vive en `yappy setup`
(`backend/cli/yappy_cli/cli.py`) y duplicar esa lógica en un script la haría divergir del
código. La primera corrida de `pip install -e .` tiene que vivir en el script por
la paradoja de bootstrap: el comando que instalaría las dependencias no puede
correr hasta que el comando mismo existe.

Después de cada commit:

```bash
yappy update              # git pull + pip install -e .
```

`yappy setup` hace todo automáticamente:

1. **Recursos del backend** — migra `build/`, `config/` y `data/` heredados desde la raíz hacia `backend/`, conservando los archivos locales ignorados; la suite vive en `backend/tests/`
2. **Shell integration** — agrega `eval "$(yappy init bash)"` al `.bashrc`
3. **Config files** — crea `backend/config/env.base` desde `backend/config/env.base.example` si no existe (los archivos de entorno como `env.dev`/`env.qa` deben crearse manualmente copiando un `.example`)
4. **Dependencias backend** — `python -m pip install -e .` (resuelve desde `docs/requirements.txt`, declarado dinámico en `pyproject.toml`)
5. **Dependencias frontend** — `npm install` en `frontend/` (resuelve desde `frontend/package.json`)
6. **Herramientas externas** — verifica que `aws` y `session-manager-plugin` estén instalados
7. **Kafka** — descarga server + UI si no están
8. **Perfil AWS** — verifica que el perfil configurado exista

Después del setup, editá los archivos de config con tus valores:

```bash
yappy edit           # Abre el proyecto en VS Code
# Editar backend/config/env.base (los archivos por entorno deben crearse a mano, p. ej. copiando backend/config/env.environment.example a backend/config/env.dev)
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

### Web de Region Sync

```bash
yappy web --build                    # Compilar el frontend Angular + abrir la web
yappy web --no-browser               # Servir sin abrir el navegador
```

La web (diff de DB y de parámetros/secretos entre ambientes) se sirve desde
`frontend/` (Angular). El frontend **no se commitea compilado**: si no existe
`frontend/dist/browser`, `yappy web` sirve las páginas legacy sin romper.
Para compilar el frontend hace falta **Node.js >= 24.15**:

```bash
cd frontend && npm install && npm run build
```

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
