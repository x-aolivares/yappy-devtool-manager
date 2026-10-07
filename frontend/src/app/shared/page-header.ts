import { Component, input } from '@angular/core';

@Component({
  selector: 'app-page-header',
  standalone: true,
  template: `
    <header class="page-header">
      <h1>{{ title() }}</h1>
      @if (description()) {
        <p class="muted page-header__description">{{ description() }}</p>
      }
    </header>
  `,
  styles: `
    :host {
      display: block;
    }

    .page-header {
      display: grid;
      gap: 0.375rem;
      margin-bottom: 1.125rem;
    }

    h1 {
      margin: 0;
      font-size: clamp(1.75rem, 2.4vw, 2.375rem);
      line-height: 1.15;
      letter-spacing: -0.02em;
    }

    .page-header__description {
      margin: 0;
      max-width: 44rem;
      font-size: var(--text-base);
      line-height: 1.55;
    }
  `,
})
export class PageHeaderComponent {
  readonly title = input.required<string>();
  readonly description = input<string | null>(null);
}
