# Yappy Web UI

Frontend Angular del devtool web de `yappy-cli-manager`. Se sirve siempre a
través de `yappy web`, que levanta la API y esta UI en un solo comando.

## Estructura

```
src/app/
  core/            # sin dependencias de ninguna feature
    models.ts      # tipos que reflejan los DTOs de web/api/schemas.py
    api.ts         # cliente HTTP + normalización de errores
    env-context.ts # ambiente seleccionado (signal + localStorage)
    environments.store.ts
  shared/
    alert.ts       # render del payload de error {code, message, detail}
  features/
    home/          # estado y diagnóstico
    parameters/    # lookup de config/env.* + resolución en Secrets Manager
    databases/     # schemas, objetos y migración de DDL
    query/         # consola SQL de solo lectura
```

Los bindings de los templates se validan en build (Angular chequea cada
referencia contra la clase del componente), así que un typo en un template
rompe `ng build`, no la app en runtime.

## Develop

```bash
# Normal: levanta API + UI con el proxy ya configurado
yappy web
# -> http://127.0.0.1:4300

# Solo la UI, contra una API que ya está corriendo
npm start
# -> http://127.0.0.1:4300
```

La diferencia entre los dos es el proxy:

- `yappy web` genera `.proxy.generated.json` desde `YappyPort.WEB_API` y se lo
  pasa a `ng serve --proxy-config`. La UI llama a `/api/...` con rutas relativas
  y el dev server las reescribe a `/...` contra el backend. El browser nunca ve
  un cross-origin, así que el CORS no aplica.
- `npm start` a pelo no usa proxy: la UI llama al backend por origen cruzado y
  el CORS del backend resuelve, porque acepta `localhost:4300` y
  `127.0.0.1:4300`.

Un `.proxy.conf.json` commiteado sería una segunda fuente de verdad del puerto y
se desincronizaría en el primer cambio.

## Build

```bash
npm run build
```

La app es SPA sin SSR: el backend sirve el build solo si se configura un static
mount, hoy no hace falta porque en desarrollo siempre corre el dev server.

## Restricciones que la UI debe respetar

- **La consola SQL es de solo lectura.** El backend acepta `SELECT`, `SHOW`,
  `DESCRIBE`, `EXPLAIN` y `WITH`, una sentencia por vez, y el driver corre sin
  multi-statement. La UI lo declara en pantalla para que el error no sea la
  primera explicación.
- **La resolución de secretos es una acción explícita.** El valor crudo de un
  parámetro es el nombre del secreto; no hay búsqueda ni listado.
- **La migración copia solo DDL.** `MigrateRequest` ya acepta `date_column`,
  `date_from` y `date_to` para la migración de datos por rango de fechas, pero
  esa parte todavía no está implementada en el backend.
- **Nada sale de localhost.** El backend se bindea a `127.0.0.1` y no tiene
  autenticación: no lo expongas en una red compartida.
