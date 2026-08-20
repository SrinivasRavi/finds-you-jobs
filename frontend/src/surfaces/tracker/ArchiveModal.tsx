// Deleted-applications modal with per-row restore / two-step Delete forever.
// (Extracted from Tracker.tsx 2026-07-25, F-M6 monolith split — pure move,
// zero behavior change.) Not memoized: it mounts only while open. Row chrome
// and the confirm flow now come from the shared RecoveryListModal (duplication
// audit D-F2) — the same one Job Trash and deleted Contacts render.

import { useTranslation } from "react-i18next";

import { useDeleteApplicationForever, useUnarchiveApplication } from "../../api/queries";
import type { Application } from "../../api/types";
import { RecoveryListModal } from "../../shell/RecoveryListModal";

export function ArchiveModal({ archived, onClose }: { archived: Application[]; onClose: () => void }) {
  const { t } = useTranslation();
  const unarchive = useUnarchiveApplication();
  const deleteForever = useDeleteApplicationForever();
  return (
    <RecoveryListModal
      title={t("tracker.deletedApplications")}
      onClose={onClose}
      bodyTestid="deleted-applications-modal"
      empty={t("tracker.archiveModal.empty")}
      rows={archived.map((a) => ({
        id: a.id,
        avatarName: a.job.company,
        title: a.job.title,
        subtitle: `${a.job.company} · ${t("tracker.archiveModal.deletedRecently")}`,
      }))}
      restore={{
        label: t("tracker.archiveModal.restore"),
        testid: "deleted-app-restore-btn",
        onRun: (id) => unarchive.mutate(id),
      }}
      deleteForever={{
        label: t("tracker.archiveModal.deleteForever"),
        cancelLabel: t("tracker.cancel"),
        testid: "deleted-app-delete-forever-btn",
        confirmTestid: "deleted-app-delete-forever-confirm-btn",
        onRun: (id) => deleteForever.mutate(id),
      }}
    />
  );
}
