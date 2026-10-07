// Jobs-without-a-URL UI preview (docs/internal/url-less-jobs.md section 9).
// All 16 drawings as clickable screens, so reviewing the design is a yes or a
// no instead of reading ASCII. Nothing here calls the sidecar: every fetch,
// score and match check resolves from urlless/mock.ts after a fake delay, which
// is why it can ship while D8 and D9 are still open. Hidden from the left rail
// like /dev; reachable at /url-less (and from the dev-only rail tile in a vite
// dev build). Strings are inline on purpose: the copy itself is the proposal,
// so 13 locales shouldn't translate it yet.

import { useEffect, useState } from "react";

import { AddJobModal } from "./urlless/AddJobModal";
import { AddLinkDialog } from "./urlless/AddLinkDialog";
import { BoardPreview } from "./urlless/BoardPreview";
import { LogApplicationModal } from "./urlless/LogApplicationModal";
import { LedgerPreview, LifecyclePreview, TrashPreview } from "./urlless/SmallScreens";
import { TrackerPreview } from "./urlless/TrackerPreview";
import { PASTED_JOB } from "./urlless/mock";

type Screen =
  | "addJob"
  | "logApp"
  | "board"
  | "boardDuplicate"
  | "boardEmpty"
  | "addLink"
  | "tracker"
  | "ledger"
  | "lifecycle"
  | "trash";

interface Drawing {
  n: number;
  name: string;
  surface: string;
  kind: "new" | "changed";
  settles: string[];
  section: string;
  screen: Screen;
  /** What to do on the screen to reach this drawing's state. */
  tryThis: string;
}

const DRAWINGS: Drawing[] = [
  {
    n: 1,
    name: "Add a job, the 5 states",
    surface: "board modal",
    kind: "changed",
    settles: ["D10", "D11"],
    section: "9.1",
    screen: "addJob",
    tryThis:
      "Leave the link empty and fill the fields by hand. Then paste a greenhouse link and tab out of the box: autofill runs on blur, fills only the empty fields, and offers Undo. Any other link fails with the sidecar's own reason; a link containing \"deleted\" hits the tombstone stop.",
  },
  {
    n: 2,
    name: "Triage: score before saving",
    surface: "board modal",
    kind: "new",
    settles: ["D3", "D14"],
    section: "9.1.1",
    screen: "addJob",
    tryThis:
      "Paste a description and press \"Score it first\". Over 200 characters scores with AI, under 200 names the keyword downgrade instead of hiding it. Closing without Add job leaves nothing behind.",
  },
  {
    n: 3,
    name: "Log an application",
    surface: "tracker modal",
    kind: "changed",
    settles: [],
    section: "9.2",
    screen: "logApp",
    tryThis:
      "Same link box, same autofill, same description requirement, plus the stage, the documents actually sent and notes. The person logging a 3-week-old application is the one least likely to still have the URL.",
  },
  {
    n: 4,
    name: "The card and the filter row",
    surface: "board list",
    kind: "changed",
    settles: [],
    section: "9.3",
    screen: "board",
    tryThis:
      "The top row has no link, so it carries a `no link` chip beside its `paste-text` source chip. The Link filter group splits the list on a real column, which is what makes it a migration.",
  },
  {
    n: 5,
    name: "The trust chip and its tooltip",
    surface: "board list",
    kind: "new",
    settles: [],
    section: "9.3",
    screen: "board",
    tryThis:
      "Hover the \"?\" on the first card. This chip is the first reader of `trust_score` and `trust_flags`, which are computed at ingest and rendered nowhere today.",
  },
  {
    n: 6,
    name: "The detail pane action row",
    surface: "board detail",
    kind: "changed",
    settles: ["D12"],
    section: "9.4",
    screen: "board",
    tryThis:
      "Select the first card: \"Open posting\" is replaced by \"Add link\" in the same slot, and \"Show more jobs from this company\" becomes \"Watch this company\", which opens the same dialog asking for a careers page.",
  },
  {
    n: 7,
    name: "Add link, the promotion dialog",
    surface: "shared dialog",
    kind: "new",
    settles: [],
    section: "9.5",
    screen: "addLink",
    tryThis:
      "Paste a link containing \"greenhouse\" and press Attach: the compound check passes and the dialog closes with 1 toast. A link we can't read says so and takes your word.",
  },
  {
    n: 8,
    name: "The mismatch confirm",
    surface: "shared dialog",
    kind: "new",
    settles: [],
    section: "9.5",
    screen: "addLink",
    tryThis:
      "Paste a link containing \"principal\" and press Attach. Same company, different title, so you get both side by side. Attach anyway is always available, it just isn't the default button.",
  },
  {
    n: 9,
    name: "The duplicate prompt",
    surface: "board + tracker",
    kind: "new",
    settles: [],
    section: "9.6",
    screen: "boardDuplicate",
    tryThis:
      "The prompt rides on the scanned card, because that's the one that appeared without you asking. Compare opens the merge ledger; \"They're different\" dismisses it (where that dismissal is stored is D15).",
  },
  {
    n: 10,
    name: "The merge refusal",
    surface: "board + tracker",
    kind: "new",
    settles: [],
    section: "9.6",
    screen: "boardDuplicate",
    tryThis:
      "Use the link under the list to open the refusal. 1 application per job is a unique index, so when both rows have one the merge stops and asks instead of guessing with your pipeline.",
  },
  {
    n: 11,
    name: "The tracker detail Overview tab",
    surface: "tracker modal",
    kind: "changed",
    settles: [],
    section: "9.7",
    screen: "tracker",
    tryThis:
      "Click the card. \"Open job posting\" becomes \"+ Add link\", and the source and unverified chips appear here too, because this is where you look at a job you're already pursuing.",
  },
  {
    n: 12,
    name: "Apply asks for the URL",
    surface: "tracker card",
    kind: "new",
    settles: [],
    section: "9.8",
    screen: "tracker",
    tryThis:
      "Click the Apply slot. The prompt lands before the run row is written, so the Applier panel never shows a run that had no chance of working.",
  },
  {
    n: 13,
    name: "The cost ledger row",
    surface: "analytics",
    kind: "changed",
    settles: [],
    section: "9.10",
    screen: "ledger",
    tryThis:
      "Both rows resolve their subject, and the tailor row opens the document it paid for. On main a job-less operation reads \"(none)\" and its output is dropped.",
  },
  {
    n: 14,
    name: "Lifecycle copy",
    surface: "settings",
    kind: "changed",
    settles: [],
    section: "9.11",
    screen: "lifecycle",
    tryThis:
      "The hint now says hand-added jobs are never removed automatically. Someone who sets this to 3 days and finds their pasted JDs still there deserves to have been told.",
  },
  {
    n: 15,
    name: "Delete forever, for text",
    surface: "trash modal",
    kind: "changed",
    settles: [],
    section: "9.12",
    screen: "trash",
    tryThis:
      "Arm \"Delete forever\" on each row. The job with a link is unchanged; the pasted one warns that the description itself stops coming back. The retention line in the footer gains the same clause.",
  },
  {
    n: 16,
    name: "The empty board",
    surface: "board",
    kind: "changed",
    settles: ["D13"],
    section: "9.13",
    screen: "boardEmpty",
    tryThis:
      "1 button, opening the same modal as drawing 1. A dedicated paste surface was Design 3 in a smaller hat, and section 6.6 killed that.",
  },
];

const MODAL_SCREENS: Screen[] = ["addJob", "logApp", "addLink"];

function Toast({ note, onDone }: { note: string; onDone: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDone, 4000);
    return () => clearTimeout(timer);
  }, [note, onDone]);
  return (
    <div
      role="status"
      data-testid="urlless-toast"
      className="pointer-events-none fixed bottom-5 left-1/2 z-[70] -translate-x-1/2 rounded-lg border border-good/40 bg-good-wash px-4 py-2 text-[12.5px] text-good shadow-lg"
    >
      ✓ {note}
    </div>
  );
}

function IndexEntry({
  drawing,
  active,
  onPick,
}: {
  drawing: Drawing;
  active: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onPick}
      data-testid={`urlless-index-${drawing.n}`}
      className={
        "flex w-full items-start gap-2.5 border-b border-border px-4 py-2.5 text-left " +
        (active ? "bg-accent-wash/50" : "hover:bg-surface-2")
      }
    >
      <span
        className={
          "mt-0.5 inline-grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full text-[10px] font-semibold " +
          (active ? "bg-accent text-white" : "bg-surface-3 text-ink-3")
        }
      >
        {drawing.n}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px] font-medium text-ink">{drawing.name}</span>
        <span className="mt-0.5 flex flex-wrap items-center gap-1 text-[10px]">
          <span className="text-ink-4">{drawing.surface}</span>
          <span
            className={
              "rounded-full border px-1.5 " +
              (drawing.kind === "new"
                ? "border-accent/40 bg-accent-wash text-accent-ink"
                : "border-border-2 bg-surface-2 text-ink-3")
            }
          >
            {drawing.kind}
          </span>
          {drawing.settles.map((d) => (
            <span
              key={d}
              className="rounded-full border border-warn/40 bg-warn/10 px-1.5 text-warn"
            >
              {d}
            </span>
          ))}
        </span>
      </span>
    </button>
  );
}

export function UrlLessPreview() {
  const [current, setCurrent] = useState(DRAWINGS[0]!);
  const [toast, setToast] = useState<string | null>(null);
  // Modal drawings open on a click, not on being picked: the shared Modal's
  // backdrop covers the index, so auto-opening would trap the reviewer on
  // drawing 1 until they dismissed it.
  const [modalOpen, setModalOpen] = useState(false);

  function pick(drawing: Drawing) {
    setCurrent(drawing);
    setModalOpen(false);
  }

  const isModal = MODAL_SCREENS.includes(current.screen);
  const toastFn = (note: string) => setToast(note);

  return (
    <>
      <header className="flex min-h-[48px] flex-wrap items-center gap-3 border-b border-border bg-surface px-5">
        <h1 className="text-[14px] font-semibold text-ink">Jobs without a URL · UI preview</h1>
        <span className="text-[12px] text-ink-3">
          16 drawings from docs/internal/url-less-jobs.md section 9. Mock data only, no sidecar
          calls, nothing is saved.
        </span>
      </header>
      <div className="flex min-h-0 flex-1">
        <nav
          className="w-[268px] shrink-0 overflow-y-auto border-r border-border bg-surface"
          aria-label="Drawings"
        >
          {DRAWINGS.map((drawing) => (
            <IndexEntry
              key={drawing.n}
              drawing={drawing}
              active={drawing.n === current.n}
              onPick={() => pick(drawing)}
            />
          ))}
        </nav>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="border-b border-border bg-surface-2 px-5 py-3">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-[13px] font-semibold text-ink">
                {current.n}. {current.name}
              </span>
              <span className="text-[11.5px] text-ink-4">section {current.section}</span>
              {current.settles.length > 0 ? (
                <span className="text-[11.5px] text-warn">
                  takes a position on {current.settles.join(", ")}
                </span>
              ) : null}
              {isModal ? (
                <button
                  type="button"
                  data-testid="urlless-reopen"
                  onClick={() => setModalOpen(true)}
                  className="ml-auto rounded-md border border-accent bg-accent px-2.5 py-1 text-[11.5px] font-medium text-white hover:bg-accent-ink"
                >
                  {modalOpen ? "Open it again" : "Open the dialog"}
                </button>
              ) : null}
            </div>
            <p
              className="mt-1 max-w-[760px] text-[11.5px] leading-snug text-ink-2"
              data-testid="urlless-try-this"
            >
              {current.tryThis}
            </p>
          </div>

          {current.screen === "board" || current.screen === "boardDuplicate" ? (
            <BoardPreview
              key={current.screen}
              showDuplicate={current.screen === "boardDuplicate"}
              onToast={toastFn}
            />
          ) : current.screen === "boardEmpty" ? (
            <BoardPreview key="empty" empty onToast={toastFn} />
          ) : current.screen === "tracker" ? (
            <TrackerPreview onToast={toastFn} />
          ) : current.screen === "ledger" ? (
            <LedgerPreview onToast={toastFn} />
          ) : current.screen === "lifecycle" ? (
            <LifecyclePreview />
          ) : current.screen === "trash" ? (
            <TrashPreview onToast={toastFn} />
          ) : (
            <div className="grid flex-1 place-items-center">
              <button
                type="button"
                data-testid="urlless-open-dialog"
                onClick={() => setModalOpen(true)}
                className="rounded-md border border-accent bg-accent px-4 py-2 text-[12.5px] font-medium text-white hover:bg-accent-ink"
              >
                Open {current.name.toLowerCase()}
              </button>
            </div>
          )}
        </main>
      </div>

      {isModal && modalOpen && current.screen === "addJob" ? (
        <AddJobModal
          onClose={() => setModalOpen(false)}
          onSaved={(draft) => {
            setModalOpen(false);
            setToast(`Added "${draft.title}" to the board and saved a tracker card.`);
          }}
        />
      ) : null}
      {isModal && modalOpen && current.screen === "logApp" ? (
        <LogApplicationModal
          onClose={() => setModalOpen(false)}
          onSaved={(draft, stage) => {
            setModalOpen(false);
            setToast(`Logged "${draft.title}" in ${stage}.`);
          }}
        />
      ) : null}
      {isModal && modalOpen && current.screen === "addLink" ? (
        <AddLinkDialog
          job={PASTED_JOB}
          onClose={() => setModalOpen(false)}
          onAttached={(note) => {
            setModalOpen(false);
            setToast(note);
          }}
        />
      ) : null}
      {toast ? <Toast note={toast} onDone={() => setToast(null)} /> : null}
    </>
  );
}
