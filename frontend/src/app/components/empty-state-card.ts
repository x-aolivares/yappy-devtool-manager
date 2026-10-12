import { Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-empty-state-card',
  imports: [RouterLink],
  templateUrl: './empty-state-card.html',
  styleUrl: './empty-state-card.scss',
})
export class EmptyStateCard {
  readonly message = input.required<string>();
  readonly buttonLabel = input<string | null>(null);
  readonly buttonLink = input<string>('/params-read');
  readonly buttonQueryParams = input<Record<string, unknown>>({});
}
