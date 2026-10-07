import { Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-empty-state-card',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="panel empty-state-card">
      <span class="muted empty-state-card__message">{{ message() }}</span>
      @if (buttonLabel(); as buttonLabel) {
        <a class="primary" [routerLink]="buttonLink()" [queryParams]="buttonQueryParams()" class="empty-state-card__action">
          {{ buttonLabel }}
        </a>
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
    }

    .empty-state-card {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-top: 0;
    }

    .empty-state-card__message {
      font-size: var(--text-md);
    }

    .empty-state-card__action {
      margin-left: auto;
      text-decoration: none;
    }
  `,
})
export class EmptyStateCardComponent {
  readonly message = input.required<string>();
  readonly buttonLabel = input<string | null>(null);
  readonly buttonLink = input<string>('/params-read');
  readonly buttonQueryParams = input<Record<string, unknown>>({});
}
