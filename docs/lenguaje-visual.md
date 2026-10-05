# Lenguaje visual de los formularios

Referencia de los patrones visuales de las páginas de base de datos: **Compilar**,
**Sincronizar schema**, **Migrar datos** y **Ejecutar SQL**. Las cuatro están
migradas; la idea es que la app se vea como una sola cosa y no como cuatro
templates que se parezcan.

Regla que gobierna el trabajo: **esto es presentación, no comportamiento.** Ningún
patrón introduce estado, ni una clase para distinguir "activo" en Angular, ni un
cálculo. El estado sale siempre del DOM.

## Paleta

**No se toca.** Los tokens viven en dos lugares de `frontend/src/styles.scss` y
ya tienen lo que hace falta:

| Token | Rol |
|---|---|
| `--bb-surface` / `--bg` | Fondo de la página |
| `--bb-bg` / `--panel` | Fondo de los paneles |
| `--bb-border` | El borde de 1px de todo |
| `--bb-blue` / `--accent` | Acento: estado activo y acción primaria |
| `--bb-blue-on` / `--accent-on` | Texto sobre el acento |
| `--bb-hover-bg`, `--bb-selected-bg` | Estados de hover y seleccionado |

Ojo con una inversión que confunde: en dark, `--bb-bg` es el **panel** y
`--bb-surface` es la **página**. Están al revés de lo intuitivo.

El tema se aplica con `data-theme` en `<html>`, y lo mueve
`ThemeService.setTheme()`. Es el único lugar donde se cambia.

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

Los tokens de color, el tema, las rutas, los servicios, y la lógica de todas las
páginas. Los cambios de comportamiento de esta tanda: ninguno. La única lógica
nueva es `AutoGrowDirective`, que es presentación pura.

