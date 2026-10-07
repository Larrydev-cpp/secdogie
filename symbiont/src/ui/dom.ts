/**
 * The few DOM operations the page uses, as an interface, so tests render into
 * a fake document. Text goes in only through `textContent`: whatever a node
 * sends is data, never markup.
 */

export interface El {
  textContent: string | null;
  className: string;
  append(...nodes: El[]): void;
  replaceChildren(...nodes: El[]): void;
  setAttribute(name: string, value: string): void;
  removeAttribute(name: string): void;
  addEventListener(type: string, fn: (ev: unknown) => void): void;
}

export interface DocLike {
  createElement(tag: string): El;
}

export function el(doc: DocLike, tag: string, cls: string, text?: string): El {
  const e = doc.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

export function button(doc: DocLike, label: string, cls: string, onClick: () => void): El {
  const b = el(doc, 'button', cls, label);
  b.setAttribute('type', 'button');
  b.addEventListener('click', () => onClick());
  return b;
}

export function setEnabled(b: El, enabled: boolean): void {
  if (enabled) {
    b.removeAttribute('disabled');
    b.removeAttribute('aria-disabled');
  } else {
    b.setAttribute('disabled', '');
    b.setAttribute('aria-disabled', 'true');
  }
}
