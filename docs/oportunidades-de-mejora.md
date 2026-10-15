# Oportunidades de mejora

Diagnóstico de deuda estructural, escrito después de cerrar cinco fallos
silenciosos en el flujo de recursos (`feature/REP-000014`). No es una lista de
tareas: es la respuesta a **qué clase de problema viene produciendo el
proyecto**, para atacar la clase y no la instancia.

Todo lo que sigue está medido sobre el árbol. Los números se pueden
reproducir con los comandos del final.

---

## 1. La verdad sobre qué proceso vive está en un archivo que cualquiera puede pisar

**Medido**

- `~/.yappy/tracker/<pid>.json` lo leen o escriben **11 módulos**:
  `adapters/kafka/service.py`, `adapters/processes.py`,
  `adapters/database/connection.py`, `application/workflow/{actions,catalog_write,models,service,supervisor}.py`,
  `api/yappy_api/{reconcile,routes/workflow}.py`,
  `cli/yappy_cli/db/tunnel.py`, `cli/yappy_cli/verbs/logs.py`.
- `update_process_state` es read-modify-write **sin lock**. No hay `fcntl`,
  `msvcrt`, `portalocker` ni `filelock` en ninguna parte de la librería.
- El flujo real lo hace concurrente: la CLI levanta un túnel mientras la web
  para "todo"; el supervisor resuelve un `up` mientras el stop hace untrack.

**Qué cuesta**

Es la raíz de **tres de los cinco** fallos silenciosos cerrados en esta tanda.
Lo importante es que **todo lo que se arregló fue parcheando síntomas sobre esta
decisión**:

| Arreglo | Síntoma que tapó |
|---|---|
| guard de ancestros | `taskkill /T` se llevaba por delante al server |
| `PROCESS_QUERY_LIMITED_INFORMATION` | la liveness leía vivos como muertos |
| `exe` + `create_time` en el tracker | un pid reciclado se hacía pasar por tuyo |
| reconciliación al arrancar | la basura se acumulaba entre sesiones |

La pregunta de fondo — *dónde vive la verdad sobre qué está corriendo* — sigue
sin respuesta. Hoy la respuesta es "en un JSON que escribe el proceso que
arrancó y sobreescribe quien llegue último". Un guard más no lo arregla.

**Cómo se attackaría**: decidir el dueño de la verdad (el tracker sigue siendo
una buena respuesta) y que **escritura tenga una sola puerta**, con lock y
read-modify-write atómico. Todo lo demás — guard, identidad, reconciliación —
deja de ser un parche más y pasa a ser consumidor de un invariant.

---

## 2. La propiedad de una fila está repartida en tres lugares con tres criterios

**Medido**

```
service.py::_owner            → decide por `cluster`
process_tracker.py:276        → ordena por `st_mtime_ns` del archivo
models.py::is_foreign         → decide por `owner`
```

Tres respuestas distintas a la misma pregunta: *¿de quién es esta fila?*

**Qué cuesta**

Es la razón por la que **#8 no tiene arreglo local**: `ensure_port_free` no
tiene con qué decidir, porque el "dueño" se calcula de tres maneras según la
pantalla que pregunte. Y el criterio de `mtime` hace que el dueño **cambie** si
alguien reescribe el archivo — un supervisor resolviendo, un
`update_process_state`. No es determinista.

**Cómo se atacaría**: un solo lugar que responda, con un criterio que no dependa
de cuándo se tocó un archivo en disco.

---

## 3. `die()` es un primitive de control de flujo dentro de librería que la web maneja

**Medido**: **64 ocurrencias** de `die(` en backend, contando la definición.

`die()` hace `sys.exit`, que es `BaseException`. En un endpoint sync de
Starlette eso **no falla el request**: sube al event loop, `asyncio.Task.__step`
lo re-lanza y escapa de `run_until_complete`. Uvicorn se va, sin traceback.

**Estado**: cuatro rutas web-alcanzables ya no lo hacen, y hay un guard AST que
falla si vuelve en una de las nueve funciones web-alcanzables. Pero el primitive
sigue siendo **legal en cualquier parte**: el próximo que escriba `die()` en una
función nueva no se entera hasta que corra la suite.

---

## 4. No hay canal push, y el poll está reimplementado seis veces

**Medido**: `deployments`, `params-diff`, `params-read`, `sql`,
`workflow-resources` y `copy-button` tienen su propio `setTimeout`. Ninguno
comparte nada.

El backend tiene un **hilo supervisor** que resuelve el `up`/`error` de un
arranque, y no tiene ninguna forma de avisarle al navegador. Por eso la fila no
puede pasar a "arriba" antes de que caiga un poll.

Es una decisión consciente y está documentada. El costo real es otro: **la
frecuencia de poll es la única palanca de frescura de toda la app**, y haberla
puesto en seis lugares significa que no hay un solo lugar donde ajustarla.

---

## 5. `backend/build/` — 89 archivos `.py` muertos

**Medido**: 89 archivos, **gitignored**, **0 trackeados**, y explícitamente
excluidos en `pyproject.toml` (`exclude = [... "backend/build" ...]`).

No rompen el packaging. Pero aparecen en **cada** `grep -rn backend/` y en
cualquier lectura de contexto por un agente. Durante `REP-000014` hubo que
filtrarlos manualmente una y otra vez.

**Costo**: bajo en impacto, alto en fricción diaria.

---

## El meta-punto: los cinco fallos eran silenciosos

Sin crash. Sin traceback. Sin bandera roja.

- `_own_family` parecía hacer su trabajo durante semanas.
- `os.kill(pid, 0)` parecía un chequeo de liveness.
- `stop_pid` devolviendo `True` parecía éxito.
- El andamaje `[wf]` parecía información.
- `ensure_port_free` devolviendo en silencio parecía "el puerto está libre".

**Cada arreglo de esta tanda salió de leer el log con escepticismo, no de que
una alarma sonara.** Eso es lo que más le cuesta al proyecto: no le falta
función, le falta que algo duela cuando algo anda mal.

Dos de las acciones de esta tanda atacan exactamente eso y valen como patrón:
el log de "Parar todo" diciendo qué paró, y la reconciliación del tracker
dejando una línea por decisión más un resumen auditable.

---

## Lo que NO es un problema

Para que la lista sea creíble:

- **La capa funciona.** `adapters/` no importa `api/`. Verificado.
- **El gate de ruff es una decisión defendida y documentada**, no un descuido.
  Y **un type checker no habría atrapado ni uno de los cinco bugs**:
  `os.getppid()`, `os.kill()`, `die()` y el `mtime` como criterio de propiedad
  son los cuatro type-correct. Agregar mypy sería teatro.
- **El flujo de trabajo de git es genuinamente bueno.** Unidades de trabajo,
  convenciones, disciplina de versión. Es la razón por la que todo esto entró
  limpio.

---

## Orden sugerido por costo del próximo incidente

1. **Un lugar para la verdad del estado de procesos** (§1). Es la raíz; todo lo
   demás se vuelve parche hasta que se decida.
2. **Un solo criterio de propiedad de fila** (§2). Cierra #8.
3. **Algo que duela** (meta-punto). Un log o un assert que se queje.
4. Extraer el poll a un mecanismo compartido (§4).
5. Borrar `backend/build/` (§5). Diez minutos, fricción diaria.

---

## Cómo reproducir las mediciones

```bash
# 11 módulos que tocan el tracker
grep -rln "process_tracker" backend/library backend/api backend/cli --include=*.py | grep -v build

# sin lock de archivo
grep -rn "fcntl|msvcrt|portalocker|filelock" backend/library --include=*.py

# backend/build
find backend/build -name '*.py' | wc -l
git check-ignore -q backend/build && echo "gitignored"
git ls-files backend/build | wc -l

# die()  (64: 63 llamadas + la definición)
grep -rn "die(" backend/library backend/api backend/cli --include=*.py | grep -v build | wc -l

# los tres criterios de propiedad
grep -n "key=_mtime" backend/library/yappy_library/adapters/process_tracker.py
grep -n "def _owner" backend/library/yappy_library/application/workflow/service.py
grep -n "def is_foreign" backend/library/yappy_library/application/workflow/models.py
```