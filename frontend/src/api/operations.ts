// Typed reads over the operations ledger. The wire DTO comes from the
// generated OpenAPI types (schema.d.ts) — so Pydantic↔TS drift is a build
// error (architecture §4.3).

import type { components } from "./schema";
import { apiFetch, getSidecarInfo } from "./client";

export type OperationDTO = components["schemas"]["OperationDTO"];

export async function fetchOperations(limit = 100): Promise<OperationDTO[]> {
  const info = await getSidecarInfo();
  const res = await apiFetch(info, `/api/operations?limit=${limit}`);
  if (!res.ok) {
    throw new Error(`/api/operations returned ${res.status}`);
  }
  return (await res.json()) as OperationDTO[];
}
