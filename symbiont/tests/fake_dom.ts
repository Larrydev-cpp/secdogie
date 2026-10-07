// A minimal document for rendering tests: enough of the DOM for ui/*, plus
// helpers to read everything a person (or a screen reader) could see.
import type { DocLike, El } from '../src/ui/dom.ts';

export class FakeEl implements El {
  readonly tag: string;
  className = '';
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

  removeAttribute(name: string): void {
    this.attrs.delete(name);
  }

  addEventListener(type: string, fn: (ev: unknown) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }

  click(): void {
    if (this.attrs.has('disabled')) return;
    for (const fn of this.listeners.get('click') ?? []) fn({});
  }

  *walk(): Generator<FakeEl> {
    yield this;
    for (const c of this.children) yield* c.walk();
  }

  byClass(cls: string): FakeEl[] {
    return [...this.walk()].filter((e) => e.className.split(/\s+/).includes(cls));
  }

  /** Every string on the page: text and attribute values. */
  everything(): string {
    return [...this.walk()].map((e) => [e.textContent ?? '', ...e.attrs.values(), e.className].join('\n')).join('\n');
  }
}

export const fakeDoc: DocLike = { createElement: (tag: string) => new FakeEl(tag) };
