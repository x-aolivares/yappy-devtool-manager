/**
 * Explorador de bases de datos + migración de DDL a MySQL local.
 *
 * El flujo es deliberadamente en dos pasos: primero se elige qué objetos
 * migrar y se previsualiza el DDL exacto, después se aplica. Aurora y un MySQL
 * local no son iguales (DEFINER, AUTO_INCREMENT, sql_mode), y ver el DDL
 * sanitizado antes de aplicarlo evita surprises a mitad de una migración de 20
 * tablas.
 *
 * `MigrateRequest` ya trae `date_column` / `date_from` / `date_to` en el
 * backend, aunque la UI todavía no los expone: la migración de datos por rango
 * de fechas va a entrar sin cambiar este contrato.
 */
import { Component, computed, effect, inject, signal } from '@angular/core';
import { ApiService } from '../../core/api';
import { EnvContext } from '../../core/env-context';
import {
  DbObject,
  MigrateObject,
  MigrateRequest,
  MigrateResult,
  ObjectKind,
  Schema,
} from '../../core/models';
import { Alert, describe } from '../../shared/alert';

type ErrorInfo = { code: string; message: string; detail: string | null };

const KINDS: ObjectKind[] = [
  'table',
  'view',
  'procedure',
  'function',
  'trigger',
];

const KIND_LABELS: Record<ObjectKind, string> = {
  table: 'Tablas',
  view: 'Vistas',
  procedure: 'Procedimientos',
  function: 'Funciones',
  trigger: 'Triggers',
};

@Component({
  selector: 'app-databases',
  imports: [Alert],
  templateUrl: './databases.html',
  styleUrl: './databases.css',
})
export class Databases {
  private readonly api = inject(ApiService);
  private readonly ctx = inject(EnvContext);

  protected readonly env = this.ctx.env;
  protected readonly hasEnv = this.ctx.hasEnv;
  protected readonly kinds = KINDS;
  protected readonly kindLabels = KIND_LABELS;

  // --- schemas -------------------------------------------------------------
  protected readonly schemas = signal<Schema[]>([]);
  protected readonly schema = signal<string>('');
  protected readonly loadingSchemas = signal(false);
  protected readonly schemasError = signal<ErrorInfo | null>(null);

  // --- objects -------------------------------------------------------------
  protected readonly objects = signal<DbObject[]>([]);
  protected readonly loadingObjects = signal(false);
  protected readonly objectsError = signal<ErrorInfo | null>(null);
  protected readonly kindFilter = signal<string>('');

  /** Claves `${kind}:${name}` de los objetos marcados. */
  private readonly selected = signal<ReadonlySet<string>>(new Set());

  // --- target --------------------------------------------------------------
  protected readonly targetSchema = signal('');

  // --- migration -----------------------------------------------------------
  protected readonly preview = signal<MigrateObject[]>([]);
  protected readonly previewing = signal(false);
  protected readonly previewError = signal<ErrorInfo | null>(null);

  protected readonly result = signal<MigrateResult | null>(null);
  protected readonly migrating = signal(false);
  protected readonly migrateError = signal<ErrorInfo | null>(null);

  /**
   * Objetos agrupados por tipo y en el orden de `KINDS`, que es el que espera
   * la persona mirando la pantalla (tablas primero, después el resto). Se
   * devuelve como lista de objetos y no de tuplas porque `@for` no destructura.
   */
  protected readonly grouped = computed<{ kind: ObjectKind; items: DbObject[] }[]>(
    () => {
      const filter = this.kindFilter();
      const source = filter
        ? this.objects().filter((o) => o.kind === filter)
        : this.objects();

      const groups: { kind: ObjectKind; items: DbObject[] }[] = [];
      for (const kind of KINDS) {
        const items = source.filter((o) => o.kind === kind);
        if (items.length > 0) groups.push({ kind, items });
      }
      return groups;
    },
  );

  protected readonly counts = computed(() => {
    const map = new Map<ObjectKind, number>();
    for (const o of this.objects()) {
      map.set(o.kind, (map.get(o.kind) ?? 0) + 1);
    }
    return map;
  });

  protected readonly selectionCount = computed(() => this.selected().size);

  protected readonly effectiveTarget = computed(() => {
    const explicit = this.targetSchema().trim();
    if (explicit !== '') return explicit;
    const schema = this.schema();
    return schema ? `${this.env()}_${schema}` : '';
  });

  constructor() {
    effect(() => {
      const env = this.env();
      this.reset();
      if (env) this.loadSchemas();
    });
  }

  private reset(): void {
    this.schemas.set([]);
    this.schema.set('');
    this.objects.set([]);
    this.selected.set(new Set());
    this.preview.set([]);
    this.result.set(null);
    this.previewError.set(null);
    this.migrateError.set(null);
    this.kindFilter.set('');
  }

  // --- data loading --------------------------------------------------------

  protected loadSchemas(): void {
    const env = this.env();
    if (!env) return;

    this.loadingSchemas.set(true);
    this.schemasError.set(null);

    this.api.safe(this.api.listSchemas(env)).subscribe({
      next: (list) => {
        this.schemas.set(list);
        this.loadingSchemas.set(false);
        // Preselecciona el primero: casi siempre se migra del schema principal.
        if (list.length > 0 && this.schema() === '') {
          this.pickSchema(list[0].name);
        }
      },
      error: (err: unknown) => {
        this.schemas.set([]);
        this.schemasError.set(describe(err));
        this.loadingSchemas.set(false);
      },
    });
  }

  protected onSchemaChange(event: Event): void {
    this.pickSchema((event.target as HTMLSelectElement).value);
  }

  private pickSchema(schema: string): void {
    this.schema.set(schema);
    this.objects.set([]);
    this.selected.set(new Set());
    this.preview.set([]);
    this.result.set(null);
    if (schema) this.loadObjects();
  }

  private loadObjects(): void {
    const env = this.env();
    const schema = this.schema();
    if (!env || !schema) return;

    this.loadingObjects.set(true);
    this.objectsError.set(null);

    this.api.safe(this.api.listObjects(env, schema)).subscribe({
      next: (list) => {
        this.objects.set(list);
        this.loadingObjects.set(false);
      },
      error: (err: unknown) => {
        this.objects.set([]);
        this.objectsError.set(describe(err));
        this.loadingObjects.set(false);
      },
    });
  }

  // --- selection -----------------------------------------------------------

  protected toggle(obj: DbObject): void {
    const key = `${obj.kind}:${obj.name}`;
    const next = new Set(this.selected());
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    this.selected.set(next);
    this.result.set(null);
  }

  protected isSelected(obj: DbObject): boolean {
    return this.selected().has(`${obj.kind}:${obj.name}`);
  }

  protected selectAllVisible(): void {
    const next = new Set(this.selected());
    for (const group of this.grouped()) {
      for (const o of group.items) next.add(`${o.kind}:${o.name}`);
    }
    this.selected.set(next);
  }

  protected clearSelection(): void {
    this.selected.set(new Set());
    this.preview.set([]);
    this.result.set(null);
  }

  protected onTargetChange(event: Event): void {
    this.targetSchema.set((event.target as HTMLInputElement).value);
  }

  // --- migration -----------------------------------------------------------

  private buildRequest(): MigrateRequest {
    const objects = [...this.selected()].map(
      (key) => key.split(':') as [string, string],
    );
    const target = this.targetSchema().trim();
    return {
      schema: this.schema(),
      mode: 'ddl',
      objects,
      tables: [],
      target_schema: target === '' ? null : target,
    };
  }

  protected runPreview(): void {
    const env = this.env();
    if (!env || this.selected().size === 0) return;

    this.previewing.set(true);
    this.previewError.set(null);
    this.result.set(null);

    this.api.safe(this.api.previewMigration(env, this.buildRequest())).subscribe({
      next: (list) => {
        this.preview.set(list);
        this.previewing.set(false);
      },
      error: (err: unknown) => {
        this.preview.set([]);
        this.previewError.set(describe(err));
        this.previewing.set(false);
      },
    });
  }

  protected runMigrate(): void {
    const env = this.env();
    if (!env || this.selected().size === 0) return;

    this.migrating.set(true);
    this.migrateError.set(null);

    this.api.safe(this.api.migrate(env, this.buildRequest())).subscribe({
      next: (result) => {
        this.result.set(result);
        this.migrating.set(false);
      },
      error: (err: unknown) => {
        this.result.set(null);
        this.migrateError.set(describe(err));
        this.migrating.set(false);
      },
    });
  }

  protected copyDdl(ddl: string): void {
    void navigator.clipboard?.writeText(ddl);
  }

  protected setKindFilter(event: Event): void {
    this.kindFilter.set((event.target as HTMLSelectElement).value);
  }
}
