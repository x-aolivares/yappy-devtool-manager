# Lenguaje visual de los formularios

Referencia de los patrones visuales de las páginas de base de datos: **Compilar**,
**Sincronizar schema**, **Migrar datos** y **Ejecutar SQL**. Las cuatro están
migradas; la idea es que la app se vea como una sola cosa y no como cuatro
templates que se parezcan.

Regla que gobierna el trabajo: **esto es presentación, no comportamiento.** Ningún
patrón introduce estado, ni una clase para distinguir "activo" en Angular, ni un
cálculo. El estado sale siempre del DOM.

## Paleta

**No se toca.** Los tokens viven en `frontend/src/styles.scss` y ya tienen lo que
hace falta. El inventario completo está en `AGENTS.md`; esta sección es sólo la
idea.

| Token | Rol |
|---|---|
| `--sys-surface` / `--bg` | Fondo de la página |
| `--sys-bg` / `--panel` | Fondo de los paneles |
| `--sys-border` | El borde de 1px de todo |
| `--sys-blue` / `--accent` | Acento: estado activo y acción primaria |
| `--sys-blue-on` / `--accent-on` | Texto sobre el acento |
| `--sys-hover-bg`, `--sys-selected-bg` | Estados de hover y seleccionado |

Ojo con una inversión que confunde: en dark, `--sys-bg` es el **panel** y
`--sys-surface` es la **página**. Están al revés de lo intuitivo.

El tema se aplica con `data-theme` en `<html>`, y lo mueve
`ThemeService.setTheme()`. Es el único lugar donde se cambia.

### El prefijo es `--sys-`, y `styles.scss` tiene UN `:root`

Dos cosas que ya se pagaron de romperse y que conviene no volver a romper:

- **El prefijo es `--sys-`.** El `--bb-` era la paleta de BBit, que este frontend
  dejó de usar. No existe más en `styles.scss`; las ocho apariciones que quedaban
  en este documento eran de una paleta anterior.
- **Un solo bloque `:root`.** `styles.scss` tuvo dos, y el segundo ganaba por orden
  de cascada: pisaba `--sys-control-h` a `2.75rem` (44px, el valor que
  `AGENTS.md` prohíbe por nombre) y el cuerpo a `1rem`. El efecto era que todo se
  veía ~10-14% más grande y había que bajar el zoom del navegador a 80% para que
  se leyera normal. Los tokens se declaran una vez; si un valor nuevo necesita
  pisar otro, es una decisión de diseño y va anotada acá, no en un `:root` de más.

## La regla del `.field-label`

> **Un field lleva `.field-label` sólo si el control de adentro no trae etiqueta
> propia.**

Es la decisión que antes no estaba escrita y que hacía que Compilar tuviera 5
fields y 4 labels, sin que nadie supiera por qué. Los dos casos:

- `app-schema-select`, `<select>` y `<input>` pelados **no** traen etiqueta →
  hay que ponerla.
- `app-env-controls`, `app-env-picker` y `app-region-controls` ya dibujan su
  propia `.pill-label` → poner otra sería repetir lo mismo en dos versalitas
  distintas.

Lo mismo aplica a los `<label for>`: si existen, son la etiqueta del campo, y el
`field-label` es ese mismo `<label>`, no un `<div>` aparte.

## Prosa: qué se quitó y por qué

Las cuatro páginas tenían un bloque de explanatory text por campo. Salió casi
todo. Lo que queda, y el criterio:

**Se queda**
- **Errores.** `.hint-error`, `.error-box`.
- **Estados.** Spinners, "cargando X", "el origen no tiene tablas".
- **Por qué el botón está deshabilitado.** `generateHint()` en Sincronizar schema
  y `submitHint()` en Migrar datos: son un `computed` con un mensaje por rama.
- **Advertencias de algo destructivo.** El `replaceNotice()` de Compilar
  (el DROP se lleva las filas) y el `hint-error` del script de Sincronizar.
- **Una frase de seguridad que no está en otro lado.** En Compilar, el
  `DROP ... IF EXISTS` que explica por qué el script se puede correr dos veces.

**Se fue**
- "Se lee el objeto del ambiente de origen y Generar deja en el editor…".
- "Elegí dos: el primero es el de origen y el segundo el de destino." — la
  información **sigue visible**: `app-region-controls` reenvía
  `roleLabels: ['Origen','Destino']` y `env-picker` las pinta como badge
  `.env-role` en cada píldora.
- "Las dos fechas son inclusivas: `<code>Hasta 2026-03-31</code>` también trae…".
  Los labels de los inputs dicen "inclusive", y el backend es el que renderiza
  el tope **exclusivo** un día después.
- Los párrafos introductorios de las cuatro páginas, reducidos a una línea.

**Las preguntas que se responden con el control, no con texto**: qué hace el
segundo ambiente (los badges), si el rango incluye el último día (el label del
input), por qué no se puede filtrar una tabla (no hay columnas de fecha, y se
lo dice en el lugar donde se lo intentaría).

## Segmentado

Un grupo de opciones se lee como **una barra con divisiones**, no como botones
sueltos. Hay dos primitivas y las dos son CSS puro.

### De radios (`Ambiente | Script`, `Stored Procedure | Tabla`)

```html
<div class="radio-row">
  <label>
    <input type="radio" name="grupo" value="a" [checked]="x() === 'a'" (change)="x.set('a')" />
    Opción A
  </label>
  <label>
    <input type="radio" name="grupo" value="b" [checked]="x() === 'b'" (change)="x.set('b')" />
    Opción B
  </label>
</div>
```

El `input` se oculta a la vista y **el `<label>` hace de cara del segmento**. El
activo se lee con `label:has(input:checked)`.

Dos cosas que no hay que romper:

- El input oculto va con `opacity: 0` y tamaño de un píxel, **nunca con
  `display: none`**: si desaparece del flujo deja de ser enfocable y el teclado
  pierde el control. El foco visible se dibuja en el label con
  `label:has(input:focus-visible)`.
- El `[checked]` y el `(change)` siguen siendo la única fuente de verdad. No se
  agrega `[class.selected]`: sería estado duplicado.

### De píldoras (ambientes, servicios)

`app-env-controls` y `app-env-picker` ya emiten `<button>` nativo con
`.selected` y `aria-pressed`. No hay que tocar el template: se joined por CSS en
`.env-list` — sin `gap`, cada segmento con `margin-left: -1px`, y radio solo en
las esquinas externas.

`flex-wrap: wrap` se mantiene a propósito, para que nunca desborde en pantalla
angosta. El costo asumido: si el grupo da vuelta a la siguiente fila, la unión
se pierde en el salto.

### Lo que NO es un segmentado

**Las casillas de selección múltiple no se convierten en segmentado.** El alcance
de Sincronizar schema (Tablas / Stored procedures) y la lista de tablas de
Migrar datos son multi-selección: un segmentado implica elección única y
mentiría sobre la semántica. Se quedan como `.checkbox-row` nativos.

## Paneles y campos

```html
<div class="panel">
  <div class="columns">
    <div class="field">
      <div class="field-label">Tipo de objeto</div>
      <div class="radio-row">…</div>
    </div>
    <div class="field">…</div>
  </div>
</div>
```

- **`.panel`** — la caja con borde de 1px. Es el contenedor, no cada grupo.
- **`.columns`** — grilla de dos columnas que colapsa a una sola bajo `47.5rem`.
- **`.field`** — un bloque de campos y todo lo que cuelga de él. El margen
  inferior separa los grupos; el de la última columna se anula con
  `.field:last-child`.
- **`.field-label`** — la etiqueta suelta sobre el control, en versalitas. **Es la
  alternativa a `.section-title`**, que conserva la regla horizontal y hoy sólo
  se usa con el modificador `--plain`.

## Fila repetida de un formulario

Cuando la unidad de una lista lleva sus propios controles adentro, la forma
correcta es una **tabla**, no una pila de cajas. El caso es Migrar datos: cada
tabla del origen es una fila con su casilla de selección y su columna de fecha. El
filtro de fechas no vive en la fila: es uno para la migración entera y va arriba.

Lo que había antes era una caja por tabla marcada. Con seis tablas marcadas
ocupaba media pantalla para repetir seis veces el mismo shape, y no dejaba
comparar de un vistazo qué tabla tenía rango y cuál no.

**`.filter-table`** — dos columnas: `Tabla` (casilla + nombre) y `Columna de
fecha` (el `<select>`). El `min-width: 44rem` sigue deciding cómo se comporta en
pantalla angosta: por debajo de ese ancho la tabla hace scroll horizontal en vez
de aplastar los controles. Va envuelta en `.table-scroll`.

### El filtro es de la página; la columna es de la fila

El filtro empezó con cuatro columnas —`Columna de fecha`, `Modo` y `Rango`, además
de la de la tabla— y las tres últimas salieron de la fila. La razón no es
estética: **el período es la misma pregunta para todas las tablas** ("copiáselo de
marzo") y la columna no ("`fecha` en una, `created_at` en otra, ninguna en una
tercera").

Con el filtro en cada fila había que elegir el mismo modo y las mismas fechas seis
veces, y quedaban seis posibilidades de que una se quedara distinta. Eso es
justo el error que la página existe para evitar, y el costo de evitarlo era
insignificante al lado del riesgo.

Quedó un panel **Filtro por fecha** arriba de la tabla, con los tres modos —Un
día, Un mes, Rango— y una sola ventana. Debajo, la tabla con dos columnas. El
`.filter-table--narrow` es la misma tabla con menos columnas, no otra regla.

**El modo es UN booleano, no dos columnas**

El wireframe tenía "solo un día" y "rango de fecha" como dos columnas con un ✓ y
un ✗. Son **el mismo booleano partido en dos**, y además el dibujo se
contradecía: la fila con "un solo día ✓" mostraba dos campos de fecha y la de
"rango ✓" mostraba uno.

Quedó como **un segmentado de `.radio-row`** —el mismo del resto de la app—
con `name="migrate-data-mode"` fijo, porque ahora es un grupo único y no uno por
fila.

### Tres modos, y sólo el rango admite un límite suelto

Un día y un mes son ventanas cerradas: no hay respuesta parcial, así que un campo
vacío es "todavía no está decidido". El rango sí admite **un solo límite**, porque
"desde marzo en adelante" es una migración más y no hace falta inventar un 2099
para expresarla.

Un modo sin campo cargado **no** es "sin filtro": es "no decidido todavía", y
frena el envío mientras haya alguna tabla con columna elegida. La forma honesta de
decir "la tabla entera" sigue siendo `Todo (sin filtro)` en el `<select>` de esa
tabla.

El modo se puede cambiar sin perder la ventana: al cambiar, la ventana resuelta se
escribe en los campos del modo nuevo (a *rango*, los dos límites; a *día*, el
inferior; a *mes*, el mes de ese inferior). Perder el mes que el usuario acababa
de elegir porque quiso ver uno de sus días sería un retroceso sin motivo.

## Paginación de tablas

Es el único patrón de esta guía **que sí introduce estado**, y la excepción está
justificada: paginar sin estado no es posible. Todo lo demás sale del DOM; esto sale
de `shared/paginate.ts`.

**Las tablas de casillas se paginan, pero el contador no.**

Sincronizar schema y Migrar datos tienen tablas de selección, y son las más largas
de la app: un esquema real tiene cientos de objetos. Paginar una tabla de casillas
tiene un riesgo que una grilla de datos no tiene — **una fila desmarcada en otra
página no se ve** — y no hay error que lo delate.

Lo que lo cubre es que el contador del título (`N de 60 marcadas`) siempre habla del
**total**, nunca de la página, y que `toggleAll` y `selection` operan sobre la lista
**entera**: "Todas" marca las 60 aunque se vean 25, y la migración manda las 60. Si
alguno de los dos leyera la página, el script sincronizaría 25 objetos de los 60 que
el usuario marcó y no habría forma de enterarse.

### Dos piezas, una de estado y otra de dibujo.

- `paginate(source)` devuelve el estado: `visible()`, `visibleIndices()`, `current()`,
  `canPrev()`, `canNext()` y los controles `next/prev/goTo/setPageSize/reset`.
- `app-pagination-bar [p]` dibuja los controles.

### El tamaño siempre editable; la navegación sólo si hay algo que recorrer

La barra se escondía entera cuando había una sola página, y eso tapaba justo el
control que hacía falta: **con 7 filas no hay paginación, así que el selector
desaparecía** — pero "una sola página" de 25 filas puede seguir desbordando la
pantalla, y bajarla a 5 es lo único que la deja entera a la vista. Un control que
desaparece justo cuando lo necesitás no es un control.

Así que con una sola página quedan el rango y el selector de filas, y se van la
etiqueta de página y los dos botones, que no tienen a dónde ir. Sin filas, no hay
barra: ahí no hay nada que ajustar.

Por eso el mínimo de `PAGE_SIZES` es **5** y no 10: con 7 filas y un mínimo de 10
seguiría sin haber nada que ajustar.

**La barra va debajo de la tabla, no arriba.** Es la posición que ya usan los filtros
de las sesiones, y además es la que no empuja el `<thead>` sticky de `.data-table`
hacia abajo: con la barra arriba, scrollear una tabla larga dejaba el encabezado
pegado al borde de arriba y la barra flotando en el medio de la primera pantalla.

### El filtro va ANTES de paginar

La regla que más se rompe y más caro sale, porque **no hay error**: sólo una lista
que no es la que se buscó. Si el filtro corriera sobre las 25 filas de la página
actual, buscar algo que está en la fila 60 daría cero resultados en la página 1 y
encontraría la fila en la página 3. Las dos páginas filtran la lista entera, y
`page.total()` cuenta lo filtrado.

Y filtrar **devuelve a la primera página**: si no, el filtro deja 3 filas y la
grilla queda en la página 3 de 1, con la barra diciendo "Página 3 de 1" y el cuerpo
vacío.

### Filtrar es mirar, no elegir

El filtro de las tablas de casillas **no cambia lo que se manda**. `toggleAll`,
`allSelected` y `selection`/`jsonPage` hablan de la lista **entera**: "Todas" marca
los 119 aunque el filtro muestre dos, y la migración manda las 119.

La razón es que un filtro es un espejo, no una selección: si "Todos" marcara sólo lo
visible, filtrar por `payment` y tocar "Todos" desmarcaría cien objetos que el
usuario nunca vio desaparecer. Y el contador del título habla siempre del total,
también con el filtro puesto, por el mismo motivo.

La grilla de SQL filtra en otro lugar: **por los valores de todas las columnas**, no
por los nombres, que es lo único que sirve con quinientas filas y veinte columnas. Por
eso el input lleva una aclaración al lado.

### `visibleIndices()` para los `@for` que indexan

La tabla de diff de JSON marca filas por posición y por texto editado, con el
índice del `@for`. Con paginación, ese `$index` es de la página: marcar la segunda
fila de la página 3 desmarcaría la segunda de la página 1. Por eso el `@for` itera
sobre `visibleIndices()` y usa `@let row = rows()[i]` — el índice que sale es
absoluto.

## Título de panel con acciones a la derecha

`.section-title` es `display:flex` con `space-between`, así que admite controles a
la derecha. Con `.section-title--plain` se le saca la regla de abajo, que es lo
que pide el wireframe cuando los botones van en la misma línea:

```html
<div class="section-title section-title--plain">
  <strong>Script a ejecutar en {{ destino }}</strong>
  <span class="actions">
    <app-copy-button [text]="script()" />
    <button type="button">Compilar</button>
  </span>
</div>
```

Los tres hijos de `.section-title` funcionan: los usan Compilar (texto + acciones)
y Leer Parámetros (texto + metadata + badge).

## Avisos que no caben en la línea donde ocurren

Una celda de tabla no es lugar para un párrafo. El ejemplo que forzó la regla:
un error de introspección con ~230 caracteres **y** el `border-left` de
`.hint-error` —que es una regla de bloque, pensada para un párrafo— adentro de
un `<td>`. La tabla se rompía sólo cuando el error aparecía, que es
justamente cuando más feo se veía.

La regla que quedó:

- **En la fila, un marcador corto.** Un badge clickeable. Cero texto largo.
- **El detalle, en un modal.** `app-notice-modal` (`shared/notice-modal.ts`),
  genérico: el cuerpo va por `<ng-content>`.

Los cuatro avisos cortos que quedan dentro de la tabla son de una línea y no
tienen problema: *"Sin columnas de fecha"*, *"Buscando columnas…"*. Los que no
dijeran nada —una fila sin marcar, una fila en modo "Todo (sin filtro)"— tienen
la celda vacía, que es más honesto que un texto de relleno.

### Cerrar un modal no destraba nada

El modal es **puramente de presentación**. El estado que frena la acción —en este
caso `canSubmit()` leyendo `state.error`— no se toca al cerrar. Un modal que se
dispara al aparecer un error y se cierra con la ✕ es el patrón, y se controla con
dos contadores:

- `errorSeq` sube en cada fallo nuevo;
- `dismissedSeq` queda en el valor del `errorSeq` al cerrar.

El modal se abre si `errorSeq > dismissedSeq`. Consecuencia: cerrar el aviso
**pega** hasta que algo nuevo rompa, en vez de reabrirse en el próximo change
detection.

## Confirmar es un modal con dos salidas, no un `confirm()`

El `confirm()` del navegador era el último confirmador que quedaba: 11 llamadas en
8 archivos. No dice qué va a pasar, no deja recuperar nada, y dos de los textos
llegaban a decir *"No se puede deshacer"*.

`app-notice-modal` ya era la pieza correcta y estaba sin usar para esto, así que
**se le agregaron los dos modos que le faltaban** en vez de crear otro modal:

| `tone` | Botones | Para qué |
|---|---|---|
| `aviso` (default) | uno, `confirmLabel` | Mostrar un error. Es lo que ya usaban tres páginas. |
| `default` | Cancelar + confirmar | *"¿Ejecutar el script?"* |
| `danger` | Cancelar + confirmar, confirmar en tinta de error | *"¿Eliminar DEFINITIVAMENTE?"* |

`aviso` es el default a propósito: los usos que sólo muestran no tienen que
declarar nada, y agregar el segundo modo no los tocó.

Tres reglas que no son negociables:

- **Escape, ✕ y el backdrop siempre cancelan.** Nunca confirman. Un Escape de más
  no puede disparar un borrado, y un borrado es lo único que no se arregla con
  otro clic.
- **Cancelar a la izquierda, confirmar a la derecha.** En un diálogo, la acción por
  defecto de la interfaz es la segura.
- **El botón de confirmar en `danger` va teñido, no macizo.** Un rojo lleno
  necesita una tinta de texto sobre el que no existe token semántico, y un literal
  ahí se ve bien en un tema y cortado en el otro.

### El modal es asíncrono, y eso parte el método en dos

`confirm()` se contestaba en la línea siguiente. El modal no: la respuesta llega
por `output`. Así que **el código no puede seguir linealmente**, y la página que lo
abre parte su acción en dos.

```ts
// 1. El click abre el modal y guarda qué ejecutar.
readonly pending = signal<{ texto: string; titulo: string; peligro: boolean } | null>(null);

// 2. La respuesta ejecuta lo guardado.
confirmar() {
  const p = this.pending();
  this.pending.set(null);
  if (!p) return;
  // ... la acción
}
```

**Lo que se guarda es un snapshot, no una relectura de signals.** En `compile.ts` y
`schema-sync.ts` eso no es una preferencia: entre el click y el clic en "Ejecutar"
el usuario puede tocar el editor, y releyendo se ejecutaría algo distinto de lo
que el diálogo le mostró. Es la misma razón por la que el aviso dice *"Corre
exactamente lo que está en el editor"*.

### Los `\n` de un `confirm()` no son párrafos

Varios textos eran concatenaciones largas con `\n\n` en el medio. En el alert del
navigator se veían de cualquier manera; en HTML, `\n` no es un salto de línea.
Cada texto se parte en `string[]` y el template dibuja un `<p>` por elemento, más
`white-space: pre-line` en el cuerpo por si un texto trae un salto simple.

### El foco necesita `tabindex="-1"`

El diálogo toma el foco al abrirse para que el lector de pantalla lo anuncie.
`focus()` sobre un `<div>` **sin `tabindex` es un no-op silencioso**: el foco se
queda en la página de atrás y el modal no se anuncia. El comentario del componente
ya mentía sobre eso. Con `tabindex="-1"` funciona.

## El modal de espera tiene una salida, y dice cuál es

`app-busy-modal` (`shared/busy-modal.ts`) tapa la pantalla mientras una operación
corre. Antes no tenía salida: ni botón, ni Escape, ni backdrop. La razón estaba
anotada en el componente y era correcta para lo que hace —cerrar el modal no
cancela nada, y un backdrop que cerrara mentiría sobre lo que está pasando— pero
dejaba la app sin salida para lo que el navegador sí puede cortar: la conexión.
Un `fetch` esperando un túnel que no responde no se puede abandonar desde la UI.

Ahora tiene un botón, y lo que hace es **abortar el request** (`core/cancel.ts`):
la señal viaja en el `HttpContext`, un interceptor la escucha y desuscribe, que es
lo que hace que el backend de `fetch` corte la conexión. El modal sigue sin cerrar
con Escape ni con el fondo, y sigue sin tomar el foco.

### El rótulo depende de si la operación escribe

Es la parte que no es cosmetics y la razón por la que el botón tiene dos textos:

| La operación… | El botón dice | Porque… |
|---|---|---|
| sólo lee (Consultar, Buscar, Diff, Generar, Simular) | **Cancelar** | Cortar la conexión es todo lo que hay: no hay nada escrito que deshacer. |
| escribe (Guardar, Migrar, Crear, Compilar, Sincronizar) | **Dejar de esperar** + una línea que aclara que la escritura puede igual terminar | Abortar el request corta la conexión, **no** el `REPLACE INTO` que el backend ya está corriendo. Un botón que dice "Cancelar" sobre eso promete una cosa falsa. |

La línea de aclaración va antes del botón, en `--text-xs` y en `--muted`: es la
advertencia, no la acción.

Las páginas pasan esa decisión con `[writes]`, no con el texto del rótulo: lo que
cada página sabe es si lo que está corriendo escribe, y el texto vive en un solo
lugar.

### El modal lleva un reloj, y no es cuenta regresiva

El modal muestra cuánto se lleva esperando (`0 s`, y `2:33` pasado el minuto), en
`--sys-mono`, `--text-xs`, `--muted` y con cifras tabulares para que el ancho no
dance cada segundo.

Es lo que hace decidible el botón: sin reloj, "Consultando…" no distingue entre
una consulta lenta y una conexión colgada. **No hay cuenta regresiva** porque no
existe un plazo tras el cual la operación esté mal —Compilar un esquema de tres
cientas tablas es lento y es lo normal—, así que el número sólo sube.

Dos detalles que no son de estilo:

- **Es `aria-hidden`.** El contenedor del modal es `role="status"` con
  `aria-live="polite"`, y anunciar un número que cambia cada segundo sería ruido
  para quien lee con lector de pantalla. El reloj es para el que está mirando.
- **Vive en un `effect` sobre `open()`, no en un `ngOnInit`.** El componente está
  en el DOM de la página esté el modal abierto o no; un `setInterval` que no se
  limpia en el `onCleanup` sigue contando y escribiendo en una signal huérfana.

### Cortar la espera no es un fallo

El error del rechazo es un `RequestCancelled` propio, no un `status` de HTTP. Las
páginas lo reconocen con `isCancellation` y no muestran caja de error: el usuario
cortó la espera, no falló nada. Y `CancelSlot.finish(signal)` devuelve `false`
para la respuesta de una operación vieja, que es lo que impide que un `catch`
llegado tarde apague el modal de la operación que la reemplazó.

El foco vuelve al elemento que lo tenía cuando se abrió el modal. El caso que lo
hace necesario es quien tabuló hasta "Cancelar": ese botón se va con el modal y
sin esto el foco caía al `<body>`.

**Los spinners en línea no llevan botón.** Son parte de un panel ya abierto —
cargar columnas de una tabla, cargar esquemas, cargar sesiones— y un modal para
cortar una lectura de milisegundos tapa la pantalla por nada.

## Errores de validación

`.muted.hint-error` se usa en nueve templates de cinco archivos y antes de este
trabajo **no tenía ni una regla CSS**: los errores se veían como texto gris,
igual que una nota explicativa.

El selector es **compuesto a propósito**. Los nueve usos ya vienen con `muted`, y
`.muted.hint-error` gana por especificidad en vez de depender del orden en el
archivo: con un `.hint-error` solo, alcanzar a `.muted` dependía de que nadie
escribiera un `.muted` más abajo.

## Editor de SQL

```html
<textarea appAutoGrow [autoGrowValue]="script()" rows="12" [value]="script()"
          (input)="script.set($any($event.target).value)"></textarea>
```

`AutoGrowDirective` (`frontend/src/app/shared/auto-grow.ts`) hace que el alto
siga al contenido. Dos caminos de redimensionado, y **ambos hacen falta**:

- `input`, que es el tecleo y el pegado;
- `autoGrowValue`, que es el valor que Angular escribe por `[value]`. Generar un
  script no dispara `input`, así que sin ese input el editor se quedaría con el
  alto anterior justo después de Generar.

**El tope lo pone el CSS**, con el selector por atributo
`textarea[appAutoGrow] { max-height: 32rem }`, no la directiva: se escribe el
`scrollHeight` completo y si el `max-height` lo recorta, el navegador recorta y
aparece el scroll. Por eso el tope se cambia en CSS, con cualquier unidad.

El selector es **por atributo y no global** a propósito: un `max-height` en
`textarea` pelaría con el `textarea.value-box` de Leer Parámetros, que está
diseñado para crecer con el contenido que copiás.

Dos interacciones que conviene conocer:

- `resize: vertical` sigue activo, y **choca** con el auto-grow: si arrastrás el
  textarea más alto que su contenido, al siguiente `input` vuelve al alto del
  contenido. Sirve para encogerlo a propósito, no para estirarlo.
- CSS tiene `field-sizing: content` para lo mismo, pero Firefox no lo soporta y
  ahí el editor volvería a su alto fijo sin avisar.

**No todas las textareas lo llevan.** `textarea.value-box` de Leer Parámetros
muestra un valor puntual, no un script, y tiene su propio alto.

## Botones

- Sin clase → primario, relleno azul. Es el `button` global.
- `class="secondary"` → contorno. Vive **fuera** del bloque `button` en
  `styles.scss` a propósito: anidado adentro no se podía estilar por separado, y
  un panel con una acción principal y dos acompañantes las necesita distintas.

## Dos fuentes de verdad para el mismo estado

El estado "deshabilitado" de un segmentado tiene dos caminos: el `[style.opacity]`
inline que las páginas pasan, y el `label:has(input:disabled)` del CSS. El inline
gana por estar en el elemento. Los dos usan **0.5** a propósito: donde están
ambos no se nota, y donde hay uno solo el estado se ve igual en todas las
páginas.

## Checklist para migrar una página

1. Cambiar `.section-title` por `.field-label` en los títulos de grupo internos,
   y dejar `.section-title` (o `+ .section-title--plain`) sólo para el título del
   panel.
2. Aplicar **la regla del field-label** de más arriba: no duplicar la etiqueta
   que el componente ya trae.
3. Envolver los grupos en `.columns` / `.field`. **Los `@if` y `@for` no se
   tocan**: la grilla va alrededor, no adentro.
4. Revisar los `<p class="muted hint-error">`: ya se ven como errores.
5. Si tiene textarea de SQL, agregar `appAutoGrow` + `[autoGrowValue]`.
6. Subir las acciones al header del panel con `.section-title--plain`.
7. No tocar nada de signals, bindings ni handlers.

## Tests: afirmar sobre el mecanismo

**Un assert sobre el texto de una leyenda se rompe cada vez que se limpia una
línea de copy.** Es la razón por la que esta tanda tocó menos specs de los que se
esperaba, y la razón por la que `sql.ts` y `params-read.ts` —que no tenían un
solo test— recibieron specs **antes** de ser restiladas.

Las tres formas de afirmar bien, en orden de preferencia:

1. **Sobre lo que se manda.** `expect(sent[0].dry_run).toBe(true)`.
2. **Sobre lo que se renderiza.** `expect(el.querySelector('app-region-controls')).toBeNull()`.
3. **Sobre un `computed`.** `expect(comp.showMigrate()).toBe(false)`.

La forma que **no** funciona es buscar texto en el `textContent` de la página
entera, porque las palabras viven en más de un lado. El ejemplo: un test pedía
`not.toContain('Migrar info')` para verificar que el panel no se renderizaba, y
fallaba porque la palabra también estaba en la intro de la página. El mismo
problema con los títulos numerados: `not.toContain('Esquema (del origen)')` pasó
a ser vacuo cuando el label pasó a ser `Esquema del origen` — la cadena con
paréntesis ya no existía en ningún lado.

Los `toContain` del texto de las `notes` del backend sí son intocables: son texto
de negocio, no caption de UI.

## Lo que este trabajo NO cambió

Los servicios, las rutas, el tema y la lógica de negocio de las páginas. Los
cambios de comportamiento son tres y están anotados arriba: el `confirm()` nativo
pasó a ser un modal con dos salidas, `NoticeModalComponent` ganó los modos
`default` y `danger`, y la densidad de la interfaz volvió a la escala de
`AGENTS.md` porque el segundo `:root` de `styles.scss` dejó de pisarla.

