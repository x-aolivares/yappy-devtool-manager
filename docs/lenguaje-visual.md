# Lenguaje visual de los formularios

Referencia de los patrones visuales que nacieron del rediseño de **Compilar**
(2026-10). La idea es que las páginas que comparten el mismo flujo —Sincronizar
schema, Migrar datos, Ejecutar SQL— adopten estos mismos patrones, para que la
app se vea como una sola cosa y no como cinco templates que se parezcan.

Regla que gobernó el trabajo: **esto es presentación, no comportamiento.** Ningún
patrón introduce estado, ni una clase para distinguir "activo" en Angular, ni un
cálculo. El estado sale siempre del DOM.

## Paleta

**No se tocó.** Los tokens viven en dos lugares de `frontend/src/styles.scss` y
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
  Ya existía; la usaban dos páginas que dejaron de estar enrutadas.
- **`.field`** — un bloque de campos y todo lo que cuelga de él. El margen
  inferior separa los grupos; el de la última columna se anula con
  `.field:last-child`.
- **`.field-label`** — la etiqueta suelta sobre el control, en versalitas.
  **Es la alternativa a `.section-title`**: sin la regla horizontal de debajo.

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

## Errores de validación

`.hint-error` se usa en nueve templates de cinco archivos y antes de este trabajo
**no tenía ni una regla CSS**: los errores se veían como texto gris, igual que
una nota explicativa. Ahora lleva la tinta de error y una marca a la izquierda.
Es la clase a usar para cualquier mensaje de validación en línea.

Los errores de página completa van en `.error-box`, que ya existía.

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

El **tope lo pone el CSS**, no la directiva: se escribe el `scrollHeight` completo
y si un `max-height` de la hoja lo recorta, el navegador recorta y aparece el
scroll. Por eso no hay lógica de tope en la clase y el tope se cambia en CSS, con
cualquier unidad, sin recompilar.

CSS tiene `field-sizing: content` para lo mismo, pero Firefox no lo soporta y
ahí el editor volvería a su alto fijo sin avisar.

## Botones

- Sin clase → primario, relleno azul. Es el `button` global.
- `class="secondary"` → contorno. Vive **fuera** del bloque `button` en
  `styles.scss` a propósito: anidado adentro no se podía estilar por separado,
  y un panel con una acción principal y dos acompañantes las necesita
  distintas.

## Checklist para migrar una página

1. Cambiar `.section-title` por `.field-label` en los títulos de grupo internos,
   y dejar `.section-title` (o `+ .section-title--plain`) solo para el título del
   panel.
2. Envolver los grupos en `.columns` / `.field`. **Los `@if` y `@for` no se
   tocan**: la grilla va alrededor, no adentro.
3. Revisar los `<p class="muted hint-error">`: ya se ven como errores.
4. Si tiene textarea de SQL, agregar `appAutoGrow` + `[autoGrowValue]`.
5. No tocar nada de signals, bindings ni handlers.

## Lo que este trabajo NO cambió

Los tokens de color, el tema, las rutas, los servicios, y la lógica de todas las
páginas. Los cambios de comportamiento de esta tanda: ninguno. La única lógica
nueva es `AutoGrowDirective`, que es presentación pura.
