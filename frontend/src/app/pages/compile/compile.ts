import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { CompileResponse, EnvironmentInfo, ExecuteSqlResponse } from '../../api-gen/models';
import { EnvironmentService } from '../../core/services/environment.service';
import { DbService } from '../../core/services/db.service';
import { toApiError } from '../../core/services/api-error';
import { CancelSlot, isCancellation } from '../../core/cancel';
import { objectLabel } from '../../core/format';
import { BusyModal } from '../../components/busy-modal/busy-modal';
import { StatusBadge } from '../../components/status-badge/status-badge';
import { CopyButton } from '../../components/copy-button/copy-button';
import { EnvControls } from '../../components/env-controls/env-controls';
import { RegionControls } from '../../components/region-controls/region-controls';
import { SchemaSelect } from '../../components/schema-select/schema-select';
import { AutoGrowDirective } from '../../components/auto-grow/auto-grow';
import { NoticeModal } from '../../components/notice-modal/notice-modal';

/**
 * Compilar: ejecutar SQL contra un ambiente, tomando el SQL de donde sea.
 *
 * Lo único que cambia es el **origen**:
 *
 * - **Ambiente** — se leen dos ambientes, origen y destino. **Generar** arma el
 *   script desde el objeto del origen y lo deja en el editor.
 * - **Script** — un solo ambiente, el destino. El SQL es el que pega el usuario;
 *   no hay nada que generar ni con qué comparar.
 *
 * El destino siempre es `envA`, en los dos modos: en modo ambiente viene del par
 * de `app-region-controls`, en modo script del picker de un solo ambiente. Por eso
 * `canExecute` y el botón de Compilar no necesitan saber en qué modo están.
 *
 * En ambos casos la escritura es **Compilar**. **Generar** solo arma texto: no
 * escribe en ninguna base. El editor no distingue un script generado de uno
 * escrito a mano — se ejecuta tal cual está, previa confirmación.
 *
 * Compilar un objeto es un **reemplazo**, no una fusión: el origen es la verdad y
 * el script deja el destino igual al origen. Por eso todo lleva un
 * `DROP ... IF EXISTS` adelante y por eso el aviso de `replaceNotice` está a la
 * vista: el DROP de una tabla se lleva sus filas. Quien quiere conservar los datos
 * del destino edita el script a mano y deja solo los ALTER que faltan: no queda
 * una página que se los arme.
 */
@Component({
  selector: 'app-compile-page',
  imports: [
    EnvControls,
    RegionControls,
    SchemaSelect,
    StatusBadge,
    BusyModal,
    CopyButton,
    AutoGrowDirective,
    NoticeModal,
  ],
  templateUrl: './compile.html',
})
export class CompilePage {
  private readonly envService = inject(EnvironmentService);
  private readonly dbService = inject(DbService);

  /** Exposed to the template for its labels. */
  protected readonly objectLabel = objectLabel;

  readonly environments = signal<EnvironmentInfo[] | null>(null);
  /** De dónde sale el SQL: otro ambiente, o un script del usuario. */
  readonly source = signal<'env' | 'script'>('env');
  readonly envB = signal(''); // origen; solo tiene sentido en modo ambiente
  readonly envA = signal(''); // destino: donde compila, en los dos modos
  readonly schema = signal('');
  readonly objectName = signal('');
  readonly objectType = signal<'table' | 'procedure'>('table');

  readonly objects = signal<string[] | null>(null);
  readonly objectsLoading = signal(false);
  readonly objectsError = signal<string | null>(null);

  /** The script the user executes, generated or written by hand. */
  readonly script = signal('');
  /** True only while the textarea holds something `/api/compile` produced. */
  private readonly generated = signal(false);

  readonly busy = signal(false);
  readonly busyText = signal('');
  /**
   * Si lo que está corriendo escribe.
   *
   * "Generar" sólo arma texto —lee el objeto de los dos ambientes y arma el
   * script—, pero "Compilar" ejecuta ese script en el destino, que puede ser un
   * `DROP TABLE`. El modal no puede llamar "Cancelar" a lo segundo.
   */
  readonly busyWrites = signal(false);
  readonly error = signal<string | null>(null);
  readonly result = signal<CompileResponse | null>(null);
  readonly executed = signal<ExecuteSqlResponse | null>(null);

  /**
   * La operación en vuelo, para que el botón del modal corte la espera.
   *
   * Comparar un objeto de una tabla entre dos regiones es abrir dos conexiones
   * y leer los dos DDL: con el túnel lento, esperar sin poder abandonar es la
   * forma normal de que la pantalla parezca colgada.
   */
  private readonly op = new CancelSlot();

  /** `app-env-controls` habla arrays y `envA` es un string: se adapta acá. */
  protected readonly destEnvs = computed(() => (this.envA() ? [this.envA()] : []));

  readonly objectTypePlural = computed(() =>
    this.objectType() === 'procedure' ? 'stored procedures' : 'tablas',
  );
  readonly objectPlaceholder = computed(() => {
    if (this.objects()?.length) return 'Seleccione un objeto';
    return `Sin ${this.objectTypePlural()} en ${this.schema() || 'el esquema'}`;
  });

  /**
   * Solo tiene sentido con dos ambientes: en modo script el destino es único y
   * cualquier coincidencia con el `envB` que quedó del otro modo es historia
   * vieja, no un error que haya que mostrar.
   */
  readonly sameEnv = computed(
    () => this.source() === 'env' && !!this.envB() && this.envB() === this.envA(),
  );

  /** Generar needs the whole chain: origin + destination, schema, type, object. */
  readonly canGenerate = computed(
    () =>
      !!this.envB() &&
      !!this.envA() &&
      !this.sameEnv() &&
      !!this.schema() &&
      !!this.objectType() &&
      !!this.objectName(),
  );

  /**
   * Compilar does NOT need the object chain: it runs whatever is in the editor.
   * It does need the destination, and that is `envA` in both modes — which is why
   * this check and the button don't care which origin is selected.
   */
  readonly canExecute = computed(() => !!this.envA() && this.script().trim().length > 0);

  /**
   * La ejecución pendiente de confirmar.
   *
   * Guarda el destino y el script junto con el texto, y no se recalculan al
   * confirmar: entre el click y el clic en "Ejecutar" el usuario puede cambiar el
   * editor o el ambiente, y lo que tiene que correr es lo que se le mostró en el
   * diálogo, no lo que quedó después.
   */
  readonly pendingRun = signal<{
    titulo: string;
    parrafos: string[];
    env: string;
    code: string;
    fromScript: boolean;
    schema: string;
  } | null>(null);

  /**
   * Aviso de reemplazo: compilar borra el objeto del destino y lo vuelve a crear.
   *
   * Para una tabla eso arrastra las filas, y es el motivo por el que este aviso
   * no es un detalle de la letra chica del `DROP`. También reemplaza el aviso
   * anterior ("no hay nada que aplicar"), que ya no puede darse: con
   * `DROP ... IF EXISTS` adelante hay script siempre, y era justamente ese aviso
   * el que dejaba al usuario mirando un `CREATE TABLE` pelado contra una tabla
   * que ya estaba, para que terminara en 1050.
   */
  readonly replaceNotice = computed<string | null>(() => {
    const r = this.result();
    if (!r) return null;
    if (r.status === 'missing_in_a') return null; // no hay nada que borrar
    if (r.object_type !== 'table') return null; // un procedure no tiene filas

    return (
      `Compilar ${r.schema_name}.${r.object_name} borra la tabla y todas sus filas en ` +
      `${r.env_a}, y la vuelve a crear con la definición de ${r.env_b}. ` +
      'Si querés conservar los datos del destino, editá el script y dejá solo ' +
      'los ALTER que faltan.'
    );
  });

  /** Discards responses for an object list the user already navigated away from. */
  private requestSeq = 0;

  constructor() {
    this.envService.list().then(
      (envs) => this.environments.set(envs.environments),
      (err) => this.error.set('No se pudieron cargar los ambientes: ' + toApiError(err).message),
    );

    // La lista de objetos depende del origen, del esquema y del tipo: se recarga
    // entera y se descarta la selección, que ya no existe en la nueva lista. En
    // modo script no hay ambiente de origen, así que no hay nada que listar.
    effect(() => {
      const env = this.source() === 'env' ? this.envB() : '';
      const schema = this.schema();
      const objectType = this.objectType();

      this.objectName.set('');
      this.loadObjects(env, schema, objectType);
    });

    // Un script generado describe un objeto y un destino concretos. Si el usuario
    // cambia cualquiera de los dos —o el origen entero—, ejecutarlo sería correrlo
    // en el lugar equivocado, así que se descarta. Un script escrito a mano es
    // suyo: no se toca.
    effect(() => {
      this.source();
      this.envA();
      this.envB();
      this.schema();
      this.objectType();
      this.objectName();

      // `generated` se lee sin trackear a propósito: si se leyera trackeado, el
      // effect volvería a dispararse cuando `generate()` lo pone en true y
      // borraría el script que acaba de sembrar.
      if (!untracked(this.generated)) return;
      this.generated.set(false);
      this.script.set('');
      this.result.set(null);
      this.executed.set(null);
    });
  }

  private loadObjects(env: string, schema: string, objectType: 'table' | 'procedure'): void {
    if (!env || !schema) {
      this.requestSeq++;
      this.objects.set(null);
      this.objectsError.set(null);
      this.objectsLoading.set(false);
      return;
    }

    const seq = ++this.requestSeq;
    this.objectsLoading.set(true);
    this.objectsError.set(null);

    this.dbService.listObjects(env, schema, objectType).then(
      (res) => {
        if (seq !== this.requestSeq) return;
        this.objects.set(res.objects ?? []);
        this.objectsLoading.set(false);
      },
      (err) => {
        if (seq !== this.requestSeq) return;
        this.objects.set(null);
        this.objectsError.set(toApiError(err).message);
        this.objectsLoading.set(false);
      },
    );
  }

  /**
   * Cambia el origen del SQL. Es un corte y no un ajuste: lo que había en el
   * editor describía un objeto de un ambiente de origen, así que se descarta en
   * vez de quedar ahí listo para ejecutarse en otro lado. El esquema también se
   * va porque cambia de significado —de "el del objeto de origen" a "el USE
   * opcional"— y dejarlo puesto mandaría la ejecución a un schema callado.
   */
  setSource(source: 'env' | 'script'): void {
    if (source === this.source()) return;
    this.source.set(source);
    this.schema.set('');
    this.generated.set(false);
    this.script.set('');
    this.result.set(null);
    this.executed.set(null);
  }

  /** En modo script el destino es el único ambiente: va al mismo `envA`. */
  onDestEnvChange(envs: string[]): void {
    this.envA.set(envs.length === 1 ? (envs[0] ?? '') : '');
  }

  generate() {
    const schema = this.schema();
    const objectName = this.objectName();
    if (!this.envA() || !this.envB()) {
      this.error.set('Seleccioná el ambiente de origen y el de destino.');
      return;
    }
    if (this.sameEnv()) {
      this.error.set('El origen y el destino tienen que ser distintos.');
      return;
    }
    if (!schema || !objectName) {
      this.error.set('Completá el schema y elegí el objeto.');
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.executed.set(null);
    this.busyText.set(
      `Generando el script de ${objectLabel(this.objectType())} ${schema}.${objectName} de ${this.envB()} a ${this.envA()}...`,
    );
    this.busyWrites.set(false);

    const run = this.op.begin();
    this.dbService
      .compile(
        {
          env_b: this.envB(),
          env_a: this.envA(),
          object_type: this.objectType(),
          schema_name: schema,
          object_name: objectName,
        },
        run,
      )
      .then(
        (d) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          this.result.set(d);
          // Cuando no hay nada que aplicar (ya es igual, o el DDL difiere pero la
          // estructura no) el editor se siembra con la definición que ya tiene el
          // destino: es el punto de partida para escribir el cambio a mano. Es
          // contenido generado, así que se descarta igual si cambia un selector.
          this.script.set(d.script || d.code_b || '');
          this.generated.set(true);
        },
        (err) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          // Cortar la espera no es un fallo: el usuario lo pidió. Ver `core/cancel`.
          if (isCancellation(err)) return;
          this.error.set(toApiError(err).message);
        },
      );
  }

  run() {
    const env = this.envA();
    const code = this.script();
    if (!env) {
      this.error.set('Elegí el ambiente destino.');
      return;
    }
    if (!code.trim()) {
      this.error.set('Pegá el SQL que querés ejecutar.');
      return;
    }

    const fromScript = this.source() === 'script';
    const schema = this.schema();
    const objectName = this.objectName();
    const target =
      !fromScript && schema && objectName ? `${schema}.${objectName} en ${env}` : `el script en ${env}`;

    // El script que genera Compilar arranca con un DROP, y hay que decirlo antes
    // de que corra, no después. Un `DROP TABLE` se lleva las filas del destino.
    //
    // El aviso va como párrafo aparte, no pegado con `\n\n`: el cuerpo del modal es
    // HTML, así que los saltos de línea los decide el marcado. Ver `styles.scss`,
    // `.notice-modal__body p`.
    const drops = /\bDROP\s+(TABLE|PROCEDURE|FUNCTION|TRIGGER)\b/i.test(code);
    const aviso = drops
      ? `Ojo: el script borra lo que haya en ${env} — en una tabla, la tabla y todas sus ` +
        'filas. Compilar reemplaza; para conservar los datos del destino usá el Diff de base ' +
        'de datos.'
      : '';

    this.pendingRun.set({
      titulo: `¿Ejecutar ${target}?`,
      parrafos: ['Corre exactamente lo que está en el editor.', aviso].filter(Boolean),
      env,
      code,
      fromScript,
      schema,
    });
  }

  /** Confirmó en el modal: recién acá se ejecuta el script contra el destino. */
  confirmar(): void {
    const p = this.pendingRun();
    this.pendingRun.set(null);
    if (!p) return;

    const { env, code, fromScript, schema } = p;

    this.busy.set(true);
    this.error.set(null);
    this.busyText.set(`Ejecutando el script en ${env}...`);
    // Esto escribe en el destino y el script puede empezar con un DROP: el
    // botón del modal no dice "Cancelar" sino "Dejar de esperar". Ver
    // `BusyModal`.
    this.busyWrites.set(true);

    const run = this.op.begin();
    this.dbService
      .executeSql(
        {
          env: env,
          // En modo script el texto no viene de un objeto con nombre propio: se
          // declara como lo que es para no etiquetar mal la respuesta.
          object_type: fromScript ? 'script' : this.objectType(),
          schema_name: schema,
          code: code,
        },
        run,
      )
      .then(
        (d) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          this.executed.set(d);
        },
        (err) => {
          if (!this.op.finish(run)) return;
          this.busy.set(false);
          // Cortar la espera no es un fallo: el usuario lo pidió. Ver `core/cancel`.
          if (isCancellation(err)) return;
          this.error.set(toApiError(err).message);
        },
      );
  }

  /** ✕, Escape, backdrop o "Cancelar": no se ejecuta nada. */
  cancelar(): void {
    this.pendingRun.set(null);
  }

  /**
   * Dejar de esperar lo que esté corriendo: el `Generar` que sólo arma el script
   * o el `Compilar` que lo ejecuta.
   *
   * El script del editor queda como estaba y `Compilar` se vuelve a habilitar, así
   * que volver a apretarlo es una línea de costumbre. En el caso de "Compilar" lo
   * que se cortó es la espera: el `DROP` puede igual haber salido.
   */
  cancelBusy(): void {
    if (!this.op.cancel()) return;
    this.busy.set(false);
  }
}
