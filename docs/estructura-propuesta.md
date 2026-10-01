# Estructura del proyecto y reglas de migración

## Objetivo

Separar el backend en código reutilizable, API HTTP y CLI, manteniendo Angular
como frontend independiente. La primera migración debe conservar el
comportamiento actual; las mejoras funcionales y visuales van después, en
cambios separados.

Esta estructura es el estándar aplicado en `release/REP-325073`. La integración
a `master` queda sujeta a revisión y a los criterios de aceptación de este
documento.

## Estructura

```text
.
├── backend/
│   ├── library/
│   │   └── yappy_library/
│   │       ├── application/
│   │       │   ├── session.py
│   │       │   └── database/sync/   # Diff, DDL, params, SQL execution
│   │       ├── domain/models.py
│   │       ├── ports/               # Add explicit contracts when substitution is needed
│   │       ├── adapters/
│   │       │   ├── database/        # Credentials and local/AWS tunnel connections
│   │       │   ├── kafka/           # Runtime service and installation
│   │       │   ├── ssm/             # Shared SSM targets
│   │       │   ├── logging.py
│   │       │   ├── processes.py
│   │       │   └── process_tracker.py
│   │       ├── config.py
│   │       └── paths.py             # Repository resource discovery
│   ├── api/
│   │   └── yappy_api/
│   │       ├── app.py              # Ensamble de FastAPI
│   │       ├── run.py              # Arranque del servidor
│   │       ├── deps.py             # Dependencias HTTP/FastAPI
│   │       ├── routes/
│   │       │   ├── envs.py
│   │       │   ├── db.py
│   │       │   ├── params.py
│   │       │   └── sessions.py
│   │       ├── schemas.py          # Request/response HTTP
│   │       ├── sessions.py
│   │       └── static/             # Assets legacy preservados; Angular es el UI activo
│   ├── cli/
│   │   └── yappy_cli/
│   │       ├── cli.py              # App Typer y registro de comandos
│   │       ├── aws/                # Comandos AWS
│   │       ├── db/                 # Comandos de túnel DB/refresher
│   │       ├── kafka/              # Comandos Kafka
│   │       ├── ssm/                # Comandos SSM
│   │       ├── verbs/              # run, stop, login, exec, logs
│   │       └── workflow/           # Comandos de flujo
├── frontend/
│   ├── package.json
│   ├── angular.json
│   └── src/
│       ├── styles.scss             # Entrada global de estilos
│       ├── styles/
│       │   ├── _tokens.scss        # Colores, tipografía, espacios, capas
│       │   ├── _reset.scss         # Normalización global
│       │   └── _global.scss        # Estilos base compartidos
│       └── app/
│           ├── core/               # Servicios y estado de aplicación
│           ├── shared/             # Componentes reutilizables
│           ├── pages/              # Páginas Angular actuales
│           ├── features/           # Destino para features agrupadas por dominio
│           │   └── region-sync/
│           └── api-gen/            # Cliente generado desde OpenAPI
├── config/                         # Configuración local y templates
├── docs/
├── devkit/                         # Herramientas locales, como Kafka
├── tests/                          # Tests backend; se puede subdividir por capa
├── install.sh                      # Bootstrap mínimo para crear el comando
└── pyproject.toml                  # Packaging y entry point Python únicos
```

`tests/` se mantiene en la raíz para evitar mover tests durante la extracción de
paquetes. Se puede subdividir por capa en una tarea posterior, sin mezclarlo con
cambios de comportamiento.

## Límites de cada capa

| Capa | Responsabilidad | No debería contener |
|---|---|---|
| `library` | Casos de uso, reglas reutilizables, puertos y adapters para AWS, DB local/remota, HTTP, Kafka y SSM. | Decoradores FastAPI, parsing de argumentos Typer, componentes Angular. |
| `api` | Rutas HTTP, validación y schemas request/response, autenticación/dependencias HTTP y traducción a status codes. Llama a la library. | Reglas de negocio duplicadas o lógica de comandos CLI. |
| `cli` | Comandos (`yappy web`, `yappy run db qa`), parsing de argumentos, mensajes y códigos de salida. Llama a la library; `yappy web` arranca la API. | Implementaciones propias de DB/AWS o handlers HTTP. |
| `frontend` | Navegación, componentes, estado y presentación Angular. Consume la API por HTTP. | Acceso directo a DB, AWS o reglas de backend. |

Crear un endpoint significa definir una ruta HTTP y su contrato en `api`.
La operación reutilizable que ejecuta esa ruta vive en `library`. Un cliente
HTTP reutilizable para consumir otro servicio puede vivir en
`library/adapters/http`.

## Dirección de dependencias

```text
frontend ──HTTP──> api ──> library
                         ▲
cli ─────────────────────┘
cli --comando web--> api (solo para iniciar el servidor)
```

- `library` es independiente: no importa `api`, `cli` ni Angular.
- `api` y `cli` pueden consumir la library; no copian su lógica.
- Angular se integra con el backend mediante los contratos HTTP de `api`.
- Los adapters encapsulan diferencias de infraestructura. Por ejemplo, un caso
  de uso recibe un puerto de base de datos y puede ejecutarse con un adapter
  local o con uno que use el túnel AWS/SSM.
- La selección entre adapters se hace en el composition root correspondiente
  (`api/app.py` o `cli/cli.py`), usando la configuración existente.

## Estilos del frontend

- `src/styles.scss` importa los estilos globales, tokens y reset.
- Los tokens globales definen la base visual compartida mediante variables CSS o
  Sass (colores, tipografía, escala de espacios, bordes y capas).
- Cada feature y componente mantiene su estilo junto a su código, por ejemplo
  `features/region-sync/db-diff/db-diff.component.scss`.
- Los estilos globales definen fundamentos y elementos realmente compartidos;
  el layout y los detalles visuales de una pantalla pertenecen a sus estilos
  locales.
- Los estilos globales no deben convertirse en una hoja de overrides para
  componentes específicos.

## Regla para features y releases

Antes de implementar una funcionalidad, clasificar cada responsabilidad:

1. **Reutilizable o independiente del transporte** → `library`.
2. **Contrato/handler HTTP** → `api`.
3. **Comando, argumentos, salida o ciclo de vida de proceso** → `cli`.
4. **Presentación e interacción de usuario** → `frontend`.

La library no debe convertirse en un cajón de sastre: algo va allí cuando es
lógica compartida, un caso de uso o un adapter con un contrato claro; no por el
solo hecho de que sea código Python.

## Orden de trabajo

### Paso 1: migrar sin upgrades

- Inventariar los módulos actuales y asignarlos a `library`, `api` o `cli`.
- Mover/extractar código y corregir imports, manteniendo comandos, endpoints,
  schemas y resultados observables.
- Evitar refactors internos no necesarios, cambios de contrato, features nuevos
  y rediseños visuales durante esta fase.
- Mantener Angular funcional; establecer la separación de estilos globales y
  locales sin cambiar la apariencia como parte de una mejora posterior.

### Paso 2: demostrar paridad

- Tests unitarios para los casos de uso de la library.
- Tests de API para status codes, payloads y errores HTTP.
- Tests de CLI para los comandos y sus argumentos.
- Build/test de Angular y smoke tests de integración.
- Verificar los comandos existentes (`yappy setup`, `yappy web` y los comandos
  de recursos) y que no queden imports de la estructura anterior.

### Paso 3: upgrades separados

Solo después de que la estructura migrada pase sus checks y conserve el
comportamiento, planificar mejoras de funcionalidad, API, CLI o UI en cambios
separados. Así un fallo de migración no se mezcla con regresiones de producto.

## Integración a `master`

- Crear `release/REP-{ISSUE-NUMBER}` desde `master` actualizado.
- No integrar mientras falten requisitos, paridad funcional o verificaciones.
- Integrar a `master` únicamente tras revisar el diff y confirmar los criterios
  de aceptación.
- Preferir **squash merge** para que cada entrega quede como una unidad revisable.

## Criterio permanente de aceptación

Cada feature/release debe demostrar que la lógica reutilizable está en
`library`, mientras `api`, `cli` y `frontend` contienen únicamente lo propio de
su frontera. La migración inicial se considera terminada cuando el nuevo árbol
funciona sin cambiar el comportamiento público existente.
