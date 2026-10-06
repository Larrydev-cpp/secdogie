/**
 * Focus signals -- counts, never contents.
 *
 * A {@link FocusSample} says how much input happened in an interval, how long
 * since the last input, where focus is, and a coarse class for the context.
 * It never carries which keys, what text, which window title or which page.
 *
 * Where the signals come from is deliberately narrow:
 *  - {@link BrowserFocusProvider} sees only this app's own document: its
 *    visibility, whether it has focus, and how many key / pointer presses
 *    landed in it. Nothing outside the page.
 *  - System-wide typing rate and window context would need a global input
 *    hook, which is a keylogger by another name and is on the forbidden list.
 *    So there is no such provider here. A host that has an *authorized*
 *    source -- the node's accessibility layer reporting a coarse app class,
 *    an OS idle-time API -- feeds aggregate samples through
 *    {@link ManualFocusProvider}.
 */

export type ContextClass = 'work' | 'communication' | 'media' | 'sensitive' | 'presenting' | 'unknown';
export type Surface = 'secdogie' | 'other' | 'unknown';

export interface FocusSample {
  /** Milliseconds, monotonic. */
  readonly at: number;
  readonly surface: Surface;
  readonly context: ContextClass;
  /** Key and pointer presses in the interval -- a count only. */
  readonly inputEvents: number;
  /** The interval `inputEvents` covers. */
  readonly sampleMs: number;
  /** Time since the last input event, as of `at`. */
  readonly idleMs: number;
}

export interface FocusProvider {
  /** Starts sampling; returns a function that stops it. */
  start(onSample: (s: FocusSample) => void): () => void;
}

/** Samples pushed by the host (tests, or an authorized system-side source). */
export class ManualFocusProvider implements FocusProvider {
  #listener: ((s: FocusSample) => void) | null = null;

  start(onSample: (s: FocusSample) => void): () => void {
    this.#listener = onSample;
    return () => {
      this.#listener = null;
    };
  }

  push(s: FocusSample): void {
    this.#listener?.(s);
  }
}

/** The slice of `Document` the browser provider reads. */
export interface DocumentLike {
  readonly visibilityState: string;
  hasFocus(): boolean;
  addEventListener(type: string, fn: () => void, opts?: { capture?: boolean; passive?: boolean }): void;
  removeEventListener(type: string, fn: () => void, opts?: { capture?: boolean }): void;
}

/**
 * Counts presses inside this document only. The listener takes no event
 * argument on purpose: it cannot read which key or what was clicked.
 */
export class BrowserFocusProvider implements FocusProvider {
  readonly #doc: DocumentLike;
  readonly #intervalMs: number;
  readonly #now: () => number;
  readonly #context: () => ContextClass;

  constructor(
    doc: DocumentLike,
    opts: { intervalMs?: number; now?: () => number; context?: () => ContextClass } = {},
  ) {
    this.#doc = doc;
    this.#intervalMs = opts.intervalMs ?? 1000;
    this.#now = opts.now ?? (() => performance.now());
    this.#context = opts.context ?? (() => 'unknown');
  }

  start(onSample: (s: FocusSample) => void): () => void {
    let count = 0;
    let last = this.#now();
    let lastInput = -Infinity;
    const press = () => {
      count++;
      lastInput = this.#now();
    };
    const opts = { capture: true, passive: true };
    this.#doc.addEventListener('keydown', press, opts);
    this.#doc.addEventListener('pointerdown', press, opts);
    const timer = setInterval(() => {
      const at = this.#now();
      const visible = this.#doc.visibilityState === 'visible';
      onSample({
        at,
        surface: visible && this.#doc.hasFocus() ? 'secdogie' : 'other',
        context: this.#context(),
        inputEvents: count,
        sampleMs: at - last,
        idleMs: Number.isFinite(lastInput) ? at - lastInput : at,
      });
      count = 0;
      last = at;
    }, this.#intervalMs);
    return () => {
      clearInterval(timer);
      this.#doc.removeEventListener('keydown', press, { capture: true });
      this.#doc.removeEventListener('pointerdown', press, { capture: true });
    };
  }
}
