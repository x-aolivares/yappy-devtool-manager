import { Component, input, signal } from '@angular/core';
import { copyText } from '../core/copy';

@Component({
  selector: 'app-copy-button',
  templateUrl: './copy-button.html',
})
export class CopyButton {
  text = input('');
  label = input('Copiar script');

  readonly copied = signal(false);
  private timer: ReturnType<typeof setTimeout> | null = null;

  async copy() {
    const ok = await copyText(this.text());
    this.copied.set(ok);
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.copied.set(false), 1500);
  }
}