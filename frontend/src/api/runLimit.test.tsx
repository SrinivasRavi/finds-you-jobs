// A prompt test or a triage score never gives up while still spending (audit
// finding 4): it waits for as long as the run lasts, cancels the run itself
// at 240 s, and reports a Stop as a stop.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from ".";
import { RUN_LIMIT_MS, RunStoppedError, useTriageScore } from "./queries";
import type { Operation } from "./types";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const op = (state: string, extra: Partial<Operation> = {}) =>
  ({ id: "op1", state, ...extra }) as unknown as Operation;

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("long runs", () => {
  it("keeps waiting past the old 60 s and returns the result", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "scoreJobText").mockResolvedValue("op1");
    let polls = 0;
    vi.spyOn(api, "getOperation").mockImplementation(async () => {
      polls += 1;
      return polls < 200 ? op("running") : op("succeeded", { result_ref: { score: 72 } } as never);
    });
    const { result } = renderHook(useTriageScore, { wrapper });
    let out: unknown;
    act(() => {
      result.current.mutate({ description: "jd" }, { onSuccess: (r) => (out = r) });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200 * 500 + 1000);
    });
    expect(out).toMatchObject({ score: 72 });
  });

  it("cancels the run itself at the limit", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "scoreJobText").mockResolvedValue("op1");
    vi.spyOn(api, "getOperation").mockResolvedValue(op("running"));
    const cancel = vi.spyOn(api, "cancelOperation").mockResolvedValue(op("cancelled"));
    const { result } = renderHook(useTriageScore, { wrapper });
    let err: unknown;
    act(() => {
      result.current.mutate({ description: "jd" }, { onError: (e) => (err = e) });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUN_LIMIT_MS + 1000);
    });
    expect(cancel).toHaveBeenCalledWith("op1");
    expect(err).toBeInstanceOf(RunStoppedError);
    expect((err as RunStoppedError).reason).toBe("timeout");
  });

  it("reports a Stop as stopped, with the operation id handed over first", async () => {
    vi.spyOn(api, "scoreJobText").mockResolvedValue("op1");
    vi.spyOn(api, "getOperation").mockResolvedValue(op("cancelled"));
    const started = vi.fn();
    const { result } = renderHook(useTriageScore, { wrapper });
    let err: unknown;
    act(() => {
      result.current.mutate({ description: "jd", onStarted: started }, { onError: (e) => (err = e) });
    });
    await waitFor(() => expect(err).toBeInstanceOf(RunStoppedError));
    expect(started).toHaveBeenCalledWith("op1");
    expect((err as RunStoppedError).reason).toBe("stopped");
  });
});
