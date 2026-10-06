// A minimal document for rendering tests: enough of the DOM for render.ts,
// plus helpers to find and click things.
import type { DocLike, El, InputEl } from '../src/stream/render.ts';

export class FakeEl implements InputEl {
  readonly tag: string;
  className = '';
  value = '';
  readonly attrs = new Map<string, string>();
  readonly listeners = new Map<string, Array<(ev: unknown) => void>>();
  children: FakeEl[] = [];
  #text: string | null = null;

  constructor(tag: string) {
    this.tag = tag;
  }

  get textContent(): string | null {
    return this.#text ?? this.children.map((c) => c.textContent ?? '').join('');
  }

  set textContent(v: string | null) {
    this.#text = v;
    this.children = [];
  }

  append(...nodes: El[]): void {
    this.children.push(...(nodes as FakeEl[]));
  }

  replaceChildren(...nodes: El[]): void {
    this.children = [...(nodes as FakeEl[])];
  }

  setAttribute(name: string, value: string): void {
    this.attrs.set(name, value);
  }

  addEventListener(type: string, fn: (ev: unknown) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }

  click(): void {
    for (const fn of this.listeners.get('click') ?? []) fn({});
  }

  *walk(): Generator<FakeEl> {
    yield this;
    for (const c of this.children) yield* c.walk();
  }

  find(pred: (e: FakeEl) => boolean): FakeEl | undefined {
    for (const e of this.walk()) if (pred(e)) return e;
    return undefined;
  }

  all(pred: (e: FakeEl) => boolean): FakeEl[] {
    return [...this.walk()].filter(pred);
  }

  byClass(cls: string): FakeEl[] {
    return this.all((e) => e.className.split(/\s+/).includes(cls));
  }
}

export const fakeDoc: DocLike = { createElement: (tag: string) => new FakeEl(tag) };
