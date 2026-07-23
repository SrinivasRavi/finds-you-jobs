// English — the settingsPage namespace (the Settings surface). Grouped by
// pane/section; values must stay byte-identical to the shipped UI copy
// (Playwright asserts several of them exactly).
const settingsPage = {
  experimental: "Experimental",
  linkedinHazardTip:
    "Uses your logged-in LinkedIn session. This breaks LinkedIn's Terms of Service and can get your account restricted or permanently banned — your account, your risk.",
  riskLine: "This breaks LinkedIn's Terms of Service — your account is your risk.",
  riskDetailLabel: "Full LinkedIn risk detail",
  acknowledgedOn: "Acknowledged on {{date}}",
  navAriaLabel: "Settings categories",
  sources: {
    title: "Select the sources for the next scan",
    ats: {
      heading: "Company boards (ATS)",
      blurb: "Direct company careers boards from your source registry.",
    },
    board: {
      heading: "Job boards",
      blurb: "Public keyless boards, scanned whole and filtered locally.",
    },
    search: {
      heading: "Search sources",
      blurb: "Queried with your role aliases × locations each scan.",
    },
    apify: {
      heading: "Apify",
      blurb: "Actor-run boards on your own Apify key (Naukri, Indeed, Seek…).",
    },
    fallback: {
      heading: "Feeds",
      blurb: "Any RSS/Atom feed URL you add as a source.",
    },
    apifyEmpty: "Save your Apify key below to add its actor sources.",
    sectionToggleTitle: "Enable or disable every source in this section",
    boardCount_one: "{{count}} board",
    boardCount_other: "{{count}} boards",
    keys: {
      title: "Provide your API Keys",
      intro:
        "Optional. These cover boards we can't scrape cleanly first-party (Indeed, Naukri, Seek). Keys are encrypted at rest and only ever sent to that provider.",
      apifyLabel: "Apify",
      apifyHint:
        "Runs job-scraper actors on your Apify account — a free account (~$5/mo credit, no card) covers roughly 5,000 jobs/month.",
      braveLabel: "Brave Search",
      braveHint:
        "Finds fresh postings on ATS boards outside your registry via Brave's Search API — free tier is ~2,000 queries/month (we stop at the cap).",
      keyPlaceholder: "API key",
      save: "Save",
      remove: "Remove",
    },
  },
  scoring: {
    title: "Scoring",
    howTitle: "How jobs are scored",
    fallbackLabel: "Scoring fallback",
    fallbackInfo:
      "If an AI score fails, a grey keyword score fills in — retry it from Analytics → Logs.",
    howHint: "Every new job gets a fit score against your master resume.",
    modeLlm: "AI scoring — best quality, but costs LLM tokens and some time",
    modeKeyword: "Keyword scoring — lower quality, but free and instant",
    batchCap: "Scoring batch cap",
    batchCapInfo:
      "Uncapped scores everything found in a scan; a cap spreads the LLM cost across more scans.",
    batchCapHint: "How many new jobs to score per scan.",
    uncapped: "Uncapped",
    parallel: "Parallel AI calls",
    parallelInfo:
      "Higher = a faster board, but the spend arrives just as fast and your provider may rate-limit bursts (429s). Unlimited removes the app's cap entirely — you own that tradeoff.",
    parallelHint: "How many AI calls run at once.",
    atOnce: "{{n}} at once",
    unlimited: "Unlimited",
  },
  automation: {
    title: "Automation on Save",
    intro: "Defaults applied to every job you save.",
    perJobLabel: "Per-job overrides",
    perJobInfo:
      "Need something different for one job? Flip its per-job toggles in Discover jobs before you save — that doesn't change these defaults.",
    resumeLabel: "Tailor my resume when I save a job",
    resumeInfoLabel: "Resume tailoring on save",
    resumeInfo: "Review it before you copy or export — nothing is ever auto-submitted.",
    resumeHint: "Tailors your resume in the background, ready on the tracker card.",
    coverLabel: "Draft a cover letter when I save a job",
    coverHint: "Drafts a cover letter in the background — a separate step from the resume.",
    referralsLabel: "Find referrals when I save a job",
    referralsInfoLabel: "Referrals on save",
    referralsInfo:
      "You still confirm the company and approve every message before anything sends.",
    referralsHint: "Starts Referral Outreach in the background.",
  },
  providers: {
    title: "Select the LLM Providers",
    unconfiguredWarning:
      "An operation is routed to a provider that isn't set up yet — verify and save it below, or pick a different engine under Prompts & Models.",
    configured: "Configured",
    notSet: "Not set",
    inUse: "In use",
    openrouterDesc: "One key, most models — the simplest bring-your-own-key path.",
    anthropicDesc: "Direct Anthropic API key (x-api-key).",
    openaiDesc: "Direct OpenAI API key (Bearer).",
    localDesc:
      "Point at a running Ollama / LM Studio / vLLM server — nothing leaves your machine.",
    keySavedPlaceholder: "Key saved ({{hint}}) — paste to replace",
    keyPlaceholder: "Paste your API key",
    verifying: "Verifying…",
    verify: "Verify",
    save: "Save",
    remove: "Remove",
    verified: "✓ Verified",
    verifiedCheck: "Verified ✓",
    clisTitle: "Subscription CLIs",
    clisIntro:
      "Use a coding CLI you're already logged into — no API key, your subscription pays. Verify checks the login; pick one per operation under Prompts & Models.",
    cli: {
      claudeLabel: "Claude subscription (CLI)",
      claudeDesc: "Your logged-in Claude Code CLI.",
      codexLabel: "ChatGPT subscription (Codex CLI)",
      codexDesc: "Your logged-in OpenAI Codex CLI.",
      antigravityLabel: "Google subscription (Antigravity CLI)",
      antigravityDesc: "Uses your agy login. Verify runs a real test prompt.",
    },
  },
  prompts: {
    title: "Pick the model engines and edit the prompts",
    modelEngine: "Model engine",
    cliDefaultModel: "CLI default model",
    providerDefault: "provider default",
    modelTitle:
      "Model this operation uses on {{engineLabel}}. Blank = {{effectiveModel}} (the provider/CLI default).",
    noModel: "This prompt has no model — it's drafted when a referral message is sent.",
    resetConfirm: "Reset this prompt to the shipped default? Your edits will be lost.",
    charCount: "{{n}} chars",
    overrideActive: " · override active",
    shippedDefault: " · shipped default",
    resetToDefault: "Reset to default",
    save: "Save",
    customized: "Customized",
  },
  referral: {
    title: "Referral Outreach",
    intro:
      "Automatically message people at a company you're applying to — from your own LinkedIn account — to ask for a referral. You confirm every batch before it sends.",
    howLabel: "How Referral Outreach works",
    howInfo:
      "It finds <em>current</em> employees at the company and drafts a short message for each from a fixed per-role template (peer / hiring-manager / recruiter / leadership) that you can edit — or hit Regenerate for an AI version grounded in your profile. Sending goes through your own LinkedIn session as connection requests or DMs, paced slowly with conservative daily/weekly caps to reduce detection risk. Off by default; you can also use it drafts-only and send yourself. Tracking contacts by hand (the Networking tab) is always on and needs none of this.",
    warning:
      "Automation on LinkedIn of any kind violates LinkedIn's terms of service. finds-you-jobs does not misuse the automation to farm data, sell it, or profit from it, and it keeps the automation 1-to-1 identical to what a human would do — sending messages at human typing speed, respecting daily caps, and randomising timing. But LinkedIn's Terms of Service is violated whatever way we slice it, so we insist you use your own judgement and take full responsibility for the consequences from LinkedIn. Your account may face restrictions, and finds-you-jobs is not responsible for any consequences to your LinkedIn account. Please use this feature responsibly, monitor your sent messages, and turn it off if you notice unusual account behaviour. Not using this feature does not impact your LinkedIn account or any other account in any way.",
    ack: "I want to automate LinkedIn outreach seeking referrals, at the cost of BREAKING LinkedIn's Terms of Service — which can lead to account restrictions, up to a permanent ban. I accept full responsibility.",
    enable: "Enable Referral Outreach",
    lockedHint:
      "Turn the toggle on to unlock the next step: connecting your LinkedIn session (required for auto-discovery and sending).",
  },
  linkedinSearch: {
    title: "Scan LinkedIn jobs (LinkedIn login needed)",
    intro:
      "Search LinkedIn jobs — from your own LinkedIn account — using your saved roles and locations. Richer results than the guest search, deduped into your Discover jobs feed.",
    howLabel: "How LinkedIn job search works",
    howInfo:
      "A one-off search through your own logged-in LinkedIn session, run only when you click Search — scheduled scans never touch it. Read-only against LinkedIn; results dedupe against everything already found. Shares the same session as Referral Outreach.",
    warning:
      "Searching LinkedIn while logged in means reading its job listings through automation, which violates LinkedIn's terms of service. finds-you-jobs never resells or misuses what it reads, and it keeps the footprint minimal: the search runs ONLY when you click it (never on a schedule) and pulls a modest batch — 25 jobs by default — at human pace, so it reads as ordinary browsing rather than bulk scraping. But LinkedIn's Terms of Service is violated whatever way we slice it, so use your own judgement and take full responsibility. Your account may face restrictions, and finds-you-jobs is not responsible for any consequences to your LinkedIn account. Turn it off if you notice unusual account behaviour. Not using this feature does not impact your LinkedIn account in any way.",
    ack: "I want to search LinkedIn while logged in, at the cost of BREAKING LinkedIn's Terms of Service — which can lead to account restrictions, up to a permanent ban. I accept full responsibility.",
    enable: "Enable “Scan LinkedIn jobs (LinkedIn login needed)”",
    connectHint: "Connect the LinkedIn session above to run a search.",
    runNow:
      "Run a search now with your saved roles & locations — results land in your Discover jobs feed.",
    searching: "Searching…",
    searchBtn: "Search LinkedIn jobs",
    resultsPerSearch: "Results per search",
    resultsPerSearchInfo:
      "How many jobs to pull per role × location, in pages of 25. Higher means more results — but more requests fired on <strong>your own</strong> LinkedIn account in one burst, which raises rate-limit / account risk. Keep it modest.",
    jobsOption: "{{n}} jobs",
    started: "Search started — new matches will appear in Discover jobs shortly.",
    failed: "Search failed.",
  },
  session: {
    title: "LinkedIn session",
    statusConnected: "Connected",
    statusConnecting: "Connecting…",
    statusBackingOff: "Backing off",
    statusExpired: "Session expired",
    statusDisconnected: "Disconnected",
    intro:
      "Connect your own LinkedIn login — <strong>your session stays on your device</strong> and is shared by both LinkedIn features.",
    howLabel: "How connecting + your session work",
    howInfo:
      "Connect opens a real browser window on LinkedIn's own login page; you log in there (2FA included), so finds-you-jobs never sees your password. It keeps only the session cookie — encrypted at rest (Fernet) in the app's local data folder, never uploaded. Disconnect deletes the saved session from this device (it does not log you out of LinkedIn itself) and applies to both Referral Outreach and LinkedIn job search.",
    connectedAs: "Connected as",
    expires: "Session expires",
    lastValidated: "Last validated",
    backoffNotice:
      "Outreach is paused after a LinkedIn rate-limit signal. Fix the underlying issue, then Resume to send again.",
    backoffNoticeReason:
      'Outreach is paused after a LinkedIn rate-limit signal: "{{reason}}" Fix the underlying issue, then Resume to send again.',
    connect: "Connect LinkedIn",
    connectingHint: "A browser window opened — finish logging in there…",
    validating: "Validating…",
    validate: "Validate",
    validateOk:
      'Session checked ✓ — status + "Last validated" updated (local check, no LinkedIn call)',
    validateFailed: "Validate failed: {{message}}",
    errorFallback: "error",
    resume: "Resume outreach",
    disconnect: "Disconnect",
    tier: "Account tier",
    tierCapsLabel: "Account-tier caps",
    tierInfo:
      "The LinkedIn worker enforces these caps. New = 15/day · 100/wk. Seasoned = 30/day · 200/wk. Pick the one that honestly matches your account.",
    tierHint: "Pick the tier that matches your account.",
    tierNew: "New account (safe default)",
    tierSeasoned: "Seasoned account",
  },
  observability: {
    title: "Observability",
    contentLogging: "Content logging",
    contentLoggingInfo:
      "Off by default — only sizes + fingerprints are logged otherwise. Either way, nothing leaves your machine.",
    contentLoggingHint: "Log prompt/output text locally for debugging.",
    otlpExport: "OTLP export",
    otlpExportInfo:
      "Off by default — spans stay local and nothing leaves your machine. Turn on to also send them to Honeycomb, Grafana, Logfire Cloud, and similar.",
    otlpExportHint: "Send traces to an external OTLP endpoint.",
    otlpEndpoint: "OTLP endpoint",
    otlpHeaders: "OTLP headers",
    retention: "Local log retention (days)",
    retentionHint: "Spans older than this are pruned from the local store.",
  },
  lifecycle: {
    title: "Contact & data lifecycle",
    intro:
      "When finds-you-jobs auto-marks quiet contacts and permanently deletes old items. Contacts you've marked <em>converted</em> are never changed automatically.",
    days: "days",
    hours: "hours",
    engagementGhostedLabel: "Mark a quiet thread “ghosted” after",
    engagementGhostedHint:
      "Someone replied, then went silent this many days — treat it as no reply coming.",
    sentGhostedLabel: "Mark an unanswered request “ghosted” after",
    sentGhostedHint:
      "A connection request never accepted (or accepted but never replied to) this many days.",
    contactPurgeLabel: "Delete removed contacts for good after",
    contactPurgeHint: "Contacts you deleted are erased permanently this many days later.",
    trashedJobsLabel: "Delete trashed jobs for good after",
    trashedJobsHint:
      "Jobs in Trash are erased permanently (and won't be re-scraped) this many days later.",
    archivedAppsLabel: "Delete archived applications for good after",
    archivedAppsHint:
      "Archived tracker cards and their documents are erased permanently this many days later.",
    syncCadenceLabel: "Check LinkedIn for contact updates every",
    syncCadenceHint:
      "How often to refresh your contacts' status from LinkedIn (only while Referral Outreach is on and connected).",
  },
};

export default settingsPage;
