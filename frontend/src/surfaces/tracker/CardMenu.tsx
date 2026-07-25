// Card ⋮ menu — anchored popover, not a modal (maintainer 2026-07-22 #5).
// (Extracted from Tracker.tsx 2026-07-25, F-M6 monolith split — pure move,
// zero behavior change.) Not memoized: it mounts only while a card's menu is
// open, and its callbacks are inline closures over the open menu at the root.

import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import type { Application } from "../../api/types";

export function CardMenu({
  app,
  anchor,
  onClose,
  onGenerate,
  onArchive,
  onReturn,
}: {
  app: Application;
  anchor: DOMRect;
  onClose: () => void;
  onGenerate: (label: string) => void;
  onArchive: () => void;
  onReturn: () => void;
}) {
  const { t } = useTranslation();
  const canGen = app.packet_state === "none" || app.packet_state === "failed";
  const canRegen = app.packet_state === "ready" || app.packet_state === "approved";
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  // Anchored popover, not a modal (maintainer 2026-07-22 #5): opens flush to
  // the ⋮'s right — over the neighbouring column, never its own card — and
  // clamps to the viewport. The dim layer below sits under the open card
  // (z-50) so only the card + menu read highlighted.
  const W = 232;
  const left = Math.min(anchor.right + 6, window.innerWidth - W - 8);
  const top = Math.min(anchor.top - 4, window.innerHeight - 260);
  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/20"
        onClick={onClose}
        data-testid="card-menu-backdrop"
      />
      <div
        role="menu"
        data-testid="card-menu"
        style={{ left, top, width: W }}
        className="fixed z-50 flex flex-col rounded-lg border border-border bg-surface p-1.5 text-[13px] shadow-xl"
      >
        {app.stage === "Saved" ? (
          <button onClick={onReturn} className="rounded px-3 py-2 text-left text-ink-2 hover:bg-surface-3">
            {t("tracker.moveToDiscover")}
          </button>
        ) : null}
        {canGen ? (
          <>
            <button
              onClick={() => onGenerate("tailored resume")}
              className="rounded px-3 py-2 text-left text-ink-2 hover:bg-surface-3"
            >
              {t("tracker.menu.generateResume")}
            </button>
            <button
              onClick={() => onGenerate("cover letter")}
              className="rounded px-3 py-2 text-left text-ink-2 hover:bg-surface-3"
            >
              {t("tracker.menu.generateCover")}
            </button>
          </>
        ) : null}
        {canRegen ? (
          <>
            <button
              onClick={() => onGenerate("tailored resume")}
              className="rounded px-3 py-2 text-left text-ink-2 hover:bg-surface-3"
            >
              {t("tracker.menu.regenResume")}
            </button>
            <button
              onClick={() => onGenerate("cover letter")}
              className="rounded px-3 py-2 text-left text-ink-2 hover:bg-surface-3"
            >
              {t("tracker.menu.regenCover")}
            </button>
          </>
        ) : null}
        <button
          onClick={onArchive}
          data-testid="card-menu-archive"
          className="rounded px-3 py-2 text-left text-bad hover:bg-bad-wash"
        >
          {t("tracker.archive")}
        </button>
      </div>
    </>
  );
}
