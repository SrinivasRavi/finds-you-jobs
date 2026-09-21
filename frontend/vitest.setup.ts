// Node 22+ defines a global `localStorage` that reads undefined unless the
// process was started with --localstorage-file. vitest's jsdom environment
// leaves an already-present global alone, so jsdom's own Storage never lands
// and is unreachable afterwards (`document.defaultView === globalThis`).
// Install a spec-shaped Storage over the dead accessor instead.
class MemoryStorage implements Storage {
  private data = new Map<string, string>();

  get length(): number {
    return this.data.size;
  }
  key(index: number): string | null {
    return [...this.data.keys()][index] ?? null;
  }
  getItem(key: string): string | null {
    return this.data.get(String(key)) ?? null;
  }
  setItem(key: string, value: string): void {
    this.data.set(String(key), String(value));
  }
  removeItem(key: string): void {
    this.data.delete(String(key));
  }
  clear(): void {
    this.data.clear();
  }
  [name: string]: unknown;
}

for (const key of ["localStorage", "sessionStorage"] as const) {
  if (globalThis[key] === undefined) {
    Object.defineProperty(globalThis, key, {
      value: new MemoryStorage(),
      configurable: true,
      writable: true,
    });
  }
}
