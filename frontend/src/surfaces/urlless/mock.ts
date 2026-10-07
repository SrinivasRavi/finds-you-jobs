// Mock data and proposed copy for the url-less-jobs UI preview
// (docs/internal/url-less-jobs.md section 9). Nothing here touches the sidecar:
// every "fetch" resolves from this file after a fake delay, so the 16 drawings
// can be clicked through before D8 and D9 are answered. Copy lives in COPY
// below rather than in i18n/locales/en, because the strings themselves are what
// the maintainer is reviewing and 13 locales shouldn't translate a proposal.

/** The fields both Add-a-job doors collect (the shipped `JobDraft`, plus the
 *  salary input section 9.1 item 4 adds). */
export interface PreviewDraft {
  link: string;
  title: string;
  company: string;
  location: string;
  salary: string;
  description: string;
}

export const EMPTY_DRAFT: PreviewDraft = {
  link: "",
  title: "",
  company: "",
  location: "",
  salary: "",
  description: "",
};

/** A board/tracker row, cut down to what the drawings render. */
export interface PreviewJob {
  id: string;
  title: string;
  company: string;
  location: string;
  workStyle: string;
  salary: string;
  postedAgo: string;
  applicants: number | null;
  tags: string[];
  sourceAdapter: string;
  /** null is the whole feature: a job with no posting link. */
  link: string | null;
  score: number;
  scorer: "llm" | "keyword";
  reasons: string[];
  description: string;
}

export const PASTED_JOB: PreviewJob = {
  id: "pasted-1",
  title: "Senior Software Engineer",
  company: "Acme",
  location: "Pune, India",
  workStyle: "Remote",
  salary: "₹45L – ₹60L",
  postedAgo: "3d ago",
  applicants: null,
  tags: ["python", "fastapi", "aws"],
  sourceAdapter: "paste-text",
  link: null,
  score: 72,
  scorer: "keyword",
  reasons: [
    "7 of 9 required skills present",
    "Seniority matches your target band",
    "Location is onsite, you want remote",
  ],
  description: "## About the role\n\nWe're hiring a Senior Software Engineer to build and run the "
    + "services behind our ingestion pipeline.\n\n**You'll work on**\n\n- Python 3.13 and "
    + "FastAPI services\n- Event-driven ingestion on AWS\n- Query performance on Postgres\n\n"
    + "**We're looking for**\n\n- 5+ years building backend services\n- Strong Python, "
    + "comfortable in SQL\n- Experience owning a service in production",
};

export const SCANNED_JOB: PreviewJob = {
  ...PASTED_JOB,
  id: "scanned-1",
  sourceAdapter: "greenhouse",
  link: "https://boards.greenhouse.io/acme/jobs/9",
  applicants: 48,
  score: 72,
  scorer: "llm",
};

export const LINKED_JOB: PreviewJob = {
  ...SCANNED_JOB,
  id: "linked-1",
  title: "Platform Engineer",
  company: "Rubrik",
  location: "Bengaluru, India",
  salary: "₹38L – ₹52L",
  postedAgo: "1d ago",
  tags: ["go", "kubernetes"],
  score: 88,
  description:
    "## About the role\n\nPlatform Engineer on the cluster team.\n\n- Go services on Kubernetes\n"
    + "- Multi-tenant control plane\n- On-call for the platform you build",
};

/** Rows for the board list drawing: 1 with a link, 1 without, so the chips and
 *  the Link filter group have something to separate. */
export const BOARD_ROWS: PreviewJob[] = [PASTED_JOB, LINKED_JOB, SCANNED_JOB];

// ─── fake async ──────────────────────────────────────────────────────────────

const LATENCY_MS = 700;

function later<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), LATENCY_MS));
}

export type ProbeResult =
  | { kind: "filled"; adapter: string; fields: Partial<PreviewDraft> }
  | { kind: "failed"; reason: string }
  | { kind: "tombstoned" };

/** Stands in for `probe_url` (section 5.8). The typed link picks the outcome, so
 *  states 3, 4 and 5 of drawing 1 are all reachable by typing. */
export function probePreview(url: string): Promise<ProbeResult> {
  const u = url.toLowerCase();
  if (u.includes("deleted")) return later<ProbeResult>({ kind: "tombstoned" });
  if (u.includes("greenhouse")) {
    return later<ProbeResult>({
      kind: "filled",
      adapter: "greenhouse",
      fields: {
        title: "Senior Software Engineer",
        company: "Acme",
        location: "Pune, India",
        salary: "₹45L – ₹60L",
        description:
          "## About the role\n\nWe're hiring a Senior Software Engineer to build and run the "
          + "services behind our ingestion pipeline.\n\n**You'll work on**\n\n- Python 3.13 and "
          + "FastAPI services\n- Event-driven ingestion on AWS\n- Query performance on Postgres\n\n"
          + "**We're looking for**\n\n- 5+ years building backend services\n- Strong Python, "
          + "comfortable in SQL\n- Experience owning a service in production",
      },
    });
  }
  return later<ProbeResult>({
    kind: "failed",
    reason:
      "fetched the URL but extracted only 94 chars, likely a JS-only page; pass the JD text or a file instead",
  });
}

export interface TriageScore {
  score: number;
  band: string;
  method: string;
  reasons: string[];
}

/** Stands in for a score operation with no job id (section 3.2). The typed JD
 *  length picks the method, so the silent keyword downgrade (section 8.7) shows. */
export function scorePreview(description: string): Promise<TriageScore> {
  if (description.trim().length < 200) {
    return later({
      score: 44,
      band: "Possible match",
      method: "Keywords (the description was too short to score with AI)",
      reasons: [
        "4 of 9 required skills present",
        "No seniority signal in the text",
        "Scored on keywords only, so treat it as a floor",
      ],
    });
  }
  return later({
    score: 72,
    band: "Good match",
    method: "AI (claude-sonnet-5)",
    reasons: PASTED_JOB.reasons,
  });
}

export type MatchResult =
  | { kind: "match" }
  | { kind: "mismatch"; pasted: { title: string; company: string; location: string; similarity: string }; note: string }
  | { kind: "unreadable" };

/** Stands in for the compound promotion gate (section 5.4, measured in 6.4):
 *  hashes equal, or company and title match at 0.90 similarity, or neither. */
export function matchCheck(url: string, job: PreviewJob): Promise<MatchResult> {
  const u = url.toLowerCase();
  if (u.includes("greenhouse")) return later<MatchResult>({ kind: "match" });
  if (u.includes("principal")) {
    return later<MatchResult>({
      kind: "mismatch",
      pasted: {
        title: "Principal Software Engineer",
        company: job.company,
        location: job.location,
        similarity: "97% same description",
      },
      note: "Same company, different title. Attaching this link means Apply will open the Principal role.",
    });
  }
  return later<MatchResult>({ kind: "unreadable" });
}

// ─── proposed copy, every string in section 9 ────────────────────────────────

export const COPY = {
  addJob: {
    title: "Add a job",
    linkLabel: "Job posting link",
    linkTag: "recommended",
    fetch: "Fetch",
    hint: "Paste a link and we'll fill in the rest. No link? Paste the description below.",
    reading: "Reading the posting…",
    filled: (n: number, adapter: string) => `Filled ${n} fields from ${adapter}.`,
    undo: "Undo",
    failed: (reason: string) =>
      `Couldn't read that page: ${reason}. Fill the fields in by hand, or paste the description.`,
    tryAgain: "Try again",
    tombstoned:
      "This job was permanently deleted and can't be re-added. If you still want to track it, keep a record of it outside the app.",
    looksLikeJd: "That looks like a job description. Move it into Description?",
    moveIt: "Move it",
    title_: "Title",
    company: "Company",
    location: "Location",
    salary: "Salary",
    description: "Description",
    scoreFirst: "Score it first",
    scoring: "Scoring…",
    scoreAgain: "Score again",
    nothingSaved: "Nothing saved yet.",
    scoredWith: (method: string) => `Scored with: ${method}`,
    cancel: "Cancel",
    submit: "Add job",
    needTitle: "Add a title and a description to save this job.",
  },
  logApp: {
    title: "Add a job application",
    intro: "Already applied somewhere else? Log it here.",
    hint: "No link? Fill in what you remember.",
    stage: "Stage",
    documents: "Documents you used",
    optional: "optional",
    resume: "Resume",
    cover: "Cover letter",
    chooseFile: "Choose file",
    noneChosen: "none chosen",
    notes: "Notes",
    submit: "Log it",
  },
  card: {
    noLink: "no link",
    trustLabel: "Unverified source",
    trustTip:
      "You typed this job in, so the title, company and location haven't been checked against a job board. Add the posting link to verify it.",
    linkFilter: "Link",
    linkAll: "All",
    linkHas: "Has link",
    linkNone: "No link",
  },
  detail: {
    addLink: "Add link",
    openPosting: "Open posting ↗",
    watchCompany: "Watch this company",
    showMore: "Show more jobs from this company",
  },
  addLink: {
    title: "Add the posting link",
    hint: "We'll check it against this job before we attach it.",
    checking: "Checking that link…",
    attach: "Attach",
    attached: "Link attached. You can apply to this job now.",
    mismatchTitle: "That link looks like a different job",
    youPasted: "You pasted",
    cardSays: "This card says",
    useDifferent: "Use a different link",
    attachAnyway: "Attach anyway",
    unreadable:
      "We couldn't read that page, so we can't check it against this job. Attach it anyway?",
    watchTitle: "Which careers page should I watch?",
    watchHint: "Paste the company's careers page and every new posting on it joins your scans.",
    watchAttach: "Watch it",
    applyTitle: "Where should I apply?",
    applyBody: "This job has no posting link, so there's no form to fill in. Paste the link and I'll start.",
    applyAttach: "Attach and apply",
    applyStarted: "Link attached. Starting the apply run.",
  },
  duplicate: {
    prompt: "This looks like the job you added on Monday.",
    compare: "Compare",
    different: "They're different",
    mergeTitle: "Merge these 2 jobs?",
    keeping: "Keeping",
    keepingFrom: "Your Monday card",
    taking: "Taking",
    takingFrom: "from the scanned job",
    stage: "stage",
    stageValue: "Seeking Referral",
    documents: "documents",
    documentsValue: "1 tailored resume, 1 cover",
    notes: "notes",
    notesValue: "\"ask Priya for a referral\"",
    description: "description",
    descriptionValue: "the board's full text",
    link: "link",
    linkValue: "boards.greenhouse.io/acme/…",
    source: "source",
    sourceValue: "greenhouse",
    footnote: "The scanned card is deleted. Nothing you wrote is lost.",
    keepBoth: "Keep both",
    merge: "Merge",
    merged: "Merged. Your card kept its stage, notes and documents.",
    dismissed: "Kept both. We won't ask about this pair again.",
    refusal:
      "Both of these are in your tracker, 1 in Seeking Referral and 1 in Applied. Merging would throw 1 of them away, so pick the one to keep and delete the other yourself.",
    openMonday: "Open Monday's card",
    openOther: "Open the other",
  },
  ledger: {
    viewResume: "View resume",
  },
  lifecycle: {
    label: "Remove old listings after",
    hint:
      "Jobs found by scanning leave the board this long after they first appeared, then are erased about a month later. Jobs you added by hand are never removed automatically.",
    days: "days",
  },
  trash: {
    deleteForever: "Delete forever",
    cancel: "Cancel",
    textWarning:
      "This also stops the description coming back if you paste it again.",
    retention:
      "Deleted jobs are erased permanently after 7 days. For a job you pasted, that also blocks pasting the same description again.",
  },
  empty: {
    noMatches: "No jobs matched your filters",
    widen: "Try widening your job finder preferences.",
    lastRefresh: "Last refresh 2 hours ago",
    addJob: "Add a job",
    gotOne: "Got a description from an email? Paste it.",
  },
} as const;
