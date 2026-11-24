/**
 * El panel del Kafka Agent, dentro de «Kafka local».
 *
 * La tarjeta del recurso dice *si el proceso está arriba*; este panel dice *qué
 * hace*. Son cosas distintas y por eso viven separadas: el start/stop lo decide
 * la página —que es la dueña del `busy` compartido— y las cinco operaciones y la
 * prueba local las resuelve acá, contra la API principal.
 *
 * El contrato de los endpoints es el de `kafka-agents`: los bodies se mandan
 * como vienen del repo original y la respuesta se muestra tal cual. No se
 * "mejora" ni se esconde un 404: si el mensaje no está, el panel dice que no
 * está, que es justo lo que el developer necesita saber.
 */
import { Component, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';

import type { WorkflowResourceInfo } from '../../api-gen/models';
import { toApiError } from '../../core/services/api-error';
import {
  KafkaAgentSmokeReport, KafkaAgentStatus, WorkflowService,
} from '../../core/services/workflow.service';
import { stateBadge, stateBadgeClass } from '../../core/resources';

type Tab = 'producer' | 'consumer' | 'consumer-v2' | 'listener' | 'clean';

@Component({
  selector: 'app-kafka-agent-panel',
  imports: [FormsModule],
  templateUrl: './kafka-agent-panel.html',
  styleUrl: './kafka-agent-panel.scss',
})
export class KafkaAgentPanel {
  private readonly workflow = inject(WorkflowService);

  /** La fila `kafka-agent` del tracker, o `null` si no levantó nunca. */
  readonly row = input<WorkflowResourceInfo | null>(null);
  /** El `busy` de la página: start/stop/restart son operaciones globales. */
  readonly busy = input(false);

  readonly start = output<void>();
  readonly stop = output<void>();
  readonly restart = output<void>();

  /** Lo que responde `GET /api/kafka-agent/status` (el `/health` del proceso). */
  readonly status = signal<KafkaAgentStatus | null>(null);
  readonly tab = signal<Tab>('producer');

  readonly opBusy = signal(false);
  readonly opError = signal('');
  readonly opResult = signal('');

  readonly testBusy = signal(false);
  readonly testError = signal('');
  readonly testReport = signal<KafkaAgentSmokeReport | null>(null);

  // --- campos de los formularios ------------------------------------------------

  readonly producerTopic = signal('yappy-agent-test');
  readonly producerId = signal('');
  readonly producerType = signal('com.yappy.evento');
  readonly producerHeaders = signal('{}');
  readonly producerPayload = signal('{"key": "value"}');

  readonly consumerTopic = signal('yappy-agent-test');
  readonly consumerId = signal('');
  readonly consumerMaxPolls = signal(3);

  readonly v2Topic = signal('yappy-agent-test');
  readonly v2HeaderKey = signal('ce_id');
  readonly v2HeaderValue = signal('');
  readonly v2MaxPolls = signal(3);

  readonly listenerTopic = signal('yappy-agent-test');
  readonly listenerMaxPolls = signal(3);

  readonly cleanTopics = signal('');

  readonly testTopic = signal('yappy-agent-smoke');
  readonly testMessages = signal(1);
  readonly testMaxPolls = signal(3);
  readonly testCleanup = signal(false);

  // --- estado ------------------------------------------------------------------

  /** La fuente de verdad para los botones es el tracker, igual que en las tarjetas. */
  readonly isUp = computed(() => this.row()?.state === 'up');
  readonly isStarting = computed(() => this.row()?.state === 'starting');
  readonly hasPid = computed(() => !!this.row()?.pid);
  readonly badge = stateBadge;
  readonly badgeClass = stateBadgeClass;

  constructor() {
    // El estado del proceso cambia cuando la página levanta o para el agente.
    // El tracker manda el frame, y acá lo único que se hace es volver a preguntar
    // el `/health`: dos fuentes distintas para dos preguntas distintas.
    effect(() => {
      this.row()?.state;
      this.row()?.pid;
      untracked(() => void this.loadStatus());
    });
  }

  private async loadStatus(): Promise<void> {
    try {
      this.status.set(await this.workflow.agentStatus());
    } catch {
      this.status.set({ ok: false, port: 9091, brokers: [] });
    }
  }

  /** Para el botón «Actualizar»: mismo camino, sin esperar a que cambie la fila. */
  protected refresh(): void {
    void this.loadStatus();
  }

  protected select(tab: Tab): void {
    this.tab.set(tab);
    this.opError.set('');
    this.opResult.set('');
  }

  // --- las cinco operaciones ---------------------------------------------------

  protected async runOperation(): Promise<void> {
    this.opBusy.set(true);
    this.opError.set('');
    this.opResult.set('');
    try {
      const result = await this.callActiveTab();
      this.opResult.set(JSON.stringify(result, null, 2));
    } catch (err) {
      this.opError.set(toApiError(err).message);
    } finally {
      this.opBusy.set(false);
    }
  }

  private callActiveTab(): Promise<unknown> {
    switch (this.tab()) {
      case 'producer': {
        let headers: Record<string, unknown>;
        let message: unknown;
        try {
          headers = JSON.parse(this.producerHeaders() || '{}') as Record<string, unknown>;
        } catch {
          throw new Error('Los headers no son un JSON válido.');
        }
        try {
          message = JSON.parse(this.producerPayload() || '{}') as unknown;
        } catch {
          throw new Error('El payload no es un JSON válido.');
        }
        return this.workflow.kafkaAgentProducer({
          topic: this.producerTopic(),
          cloud_event_id: this.producerId(),
          type: this.producerType(),
          headers,
          message,
        });
      }
      case 'consumer':
        return this.workflow.kafkaAgentConsumer({
          cloud_event_id: this.consumerId(),
          topic: this.consumerTopic(),
          max_polls: this.consumerMaxPolls(),
        });
      case 'consumer-v2':
        return this.workflow.kafkaAgentConsumerV2({
          header_key: this.v2HeaderKey(),
          header_value: this.v2HeaderValue(),
          topic: this.v2Topic(),
          max_polls: this.v2MaxPolls(),
        });
      case 'listener':
        return this.workflow.kafkaAgentListener({
          topic: this.listenerTopic(),
          max_polls: this.listenerMaxPolls(),
        });
      case 'clean':
        return this.workflow.kafkaAgentCleanTopics({
          body: {
            topics: this.cleanTopics()
              .split(',')
              .map((topic) => topic.trim())
              .filter(Boolean),
          },
        });
    }
  }

  // --- la prueba local ---------------------------------------------------------

  protected async runTests(): Promise<void> {
    this.testBusy.set(true);
    this.testError.set('');
    this.testReport.set(null);
    try {
      this.testReport.set(await this.workflow.runKafkaAgentTests({
        topic: this.testTopic(),
        messages: this.testMessages(),
        max_polls: this.testMaxPolls(),
        cleanup: this.testCleanup(),
      }));
    } catch (err) {
      this.testError.set(toApiError(err).message);
    } finally {
      this.testBusy.set(false);
    }
  }

  protected checkName(name: string): string {
    const labels: Record<string, string> = {
      producer: 'Publicar',
      consumer: 'Consumir (v1)',
      consumer_v2: 'Consumir (v2)',
      listener: 'Escuchar',
      cleanup: 'Limpiar',
    };
    return labels[name] ?? name.replace(/-/g, ' ');
  }
}
