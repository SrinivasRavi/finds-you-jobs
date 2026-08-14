// Unit tests for the screencast surface parameterization (Phase 8): the client
// attaches to the `?surface=` slug it is handed, url-encoded, and omits the
// param entirely when none is given — the dev `/browser` route's behavior, which
// lets the sidecar fall back to its generic `default` surface.
//
// Mocked seams: `getSidecarInfo` (./client) so the handshake resolves without a
// real sidecar, and the global WebSocket (jsdom has none) with an inspectable
// fake that records the URL it was opened against.
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("./client", () => ({ getSidecarInfo: vi.fn() }));

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;
  url: string;
  binaryType = "";
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: unknown) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  send(): void {}
  close(): void {
    this.readyState = 3;
  }
}

async function importFresh() {
  const client = await import("./client");
  const getSidecarInfo = client.getSidecarInfo as Mock;
  getSidecarInfo.mockReset();
  getSidecarInfo.mockResolvedValue({ port: 5000, token: "tok" });
  const mod = await import("./screencast");
  return { ScreencastClient: mod.ScreencastClient, getSidecarInfo };
}

describe("ScreencastClient surface parameterization", () => {
  beforeEach(() => {
    vi.resetModules();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("appends the slug it is handed as ?surface= (the add-on-supplied LinkedIn surface)", async () => {
    const { ScreencastClient } = await importFresh();
    const client = new ScreencastClient({ onFrame: () => {} }, "linkedin");
    client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const { url } = FakeWebSocket.instances[0];
    expect(url).toContain("/api/browser/screencast?token=tok");
    expect(url).toContain("&surface=linkedin");
    client.close();
  });

  it("omits the surface param when no slug is given (the dev /browser default)", async () => {
    const { ScreencastClient } = await importFresh();
    const client = new ScreencastClient({ onFrame: () => {} });
    client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(FakeWebSocket.instances[0].url).not.toContain("surface=");
    client.close();
  });

  it("url-encodes a slug so it can never break out of the query string", async () => {
    const { ScreencastClient } = await importFresh();
    const client = new ScreencastClient({ onFrame: () => {} }, "a b/c");
    client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(FakeWebSocket.instances[0].url).toContain("surface=a%20b%2Fc");
    client.close();
  });
});
