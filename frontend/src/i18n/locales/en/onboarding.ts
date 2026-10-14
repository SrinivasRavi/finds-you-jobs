// English — the onboarding wizard.
const onboarding = {
    stepDownload: "Download the app",
    stepResume: "Master resume",
    stepPreferences: "Preferences",
    stepProvider: "LLM provider",
    stepAllSet: "All set",
    titleResume: "Add your master resume",
    titlePreferences: "What are you looking for?",
    titleProvider: "Choose your LLM provider",
    titleAllSet: "You're all set",
    back: "Back",
    continue: "Continue",
    finish: "Find jobs",
    finishing: "Finishing…",
    pctComplete: "{{pct}}% complete",
    // Resume step.
    resumeIntro: "Your master resume is your resume with everything in it: every role, project and result worth mentioning. The app scores each job against it and builds every tailored resume from it, so more detail means better matches. Upload it or paste it below, then check the text. You can edit it later from Master Resume in Discover jobs.",
    extracting: "Extracting…",
    resumeLoaded: "{{name}} loaded. Check the text below.",
    resumeChoose: "Upload your master resume: PDF, Word, Pages, ODT, RTF, Markdown or text",
    resumeTextLabel: "Master resume text: paste it, or edit what came from your file",
    resumePlaceholder: "# Your name\n\nPaste your resume here, or upload a file above.",
    resumeCheck: {
      not_a_resume: "This doesn't look like a resume: it has hardly any dates and no Experience, Education or Skills section. Check you picked the right file. You can still continue.",
      instructions: "This text contains instructions addressed to an AI. Every score and tailored resume reads it, so remove them before you continue.",
    },
    // Preferences step.
    aliasLabel: "Role aliases (at least one)",
    aliasHint: "Type a role and press Enter or comma to add it. Add several — each becomes a chip below.",
    aliasPlaceholder: "e.g. Backend Engineer",
    locationLabel: "Locations (at least one; Remote is valid)",
    locationHint: "Type a place and pick it from the list, so we know which London you mean. Remote is valid.",
    locationPlaceholder: "e.g. Mumbai",
    removeChip: "Remove {{value}}",
    freshnessLabel: "Freshness window",
    // Freshness / cadence pill labels — keys are the draft/API value strings
    // verbatim (mirroring tracker.stage); the values on the wire are never
    // translated.
    freshness: {
        "24h": "24h",
        "7d": "7d",
        "30d": "30d",
        Any: "Any",
    },
    cadenceLabel: "Background scrape cadence",
    cadence: {
        "Every 6h": "Every 6h",
        "Every 12h": "Every 12h",
        "Every 24h": "Every 24h",
        "Every 48h": "Every 48h",
        "Every 72h": "Every 72h",
    },
    scoringLabel: "How jobs are scored",
    scoringLlm: "LLM scoring — best quality, but costs LLM tokens and some time",
    scoringKeyword: "Keyword scoring — lower quality, but free and instant",
    scoringHint: "You can change this any time in Settings → Scoring.",
    // LLM-provider step.
    providerIntro:
        "You bring your own key (or a local model) — the app scores and tailors with it, and your data goes only to the provider you choose. We verify the key before you finish, so your first board shows real scores right away.",
    verifyWhyLabel: "Why we verify now",
    verifyWhyBody:
        "We make one small test request to your provider to confirm the key works — so your first board shows real scores instead of stuck “Pending” chips. Nothing from the test is stored.",
    // Keyed by provider id (API value, never translated). The subscription-CLI
    // providers additionally carry the verify-guidance strings.
    providers: {
        openrouter: { label: "OpenRouter BYOK", hint: "One key, most models (recommended)" },
        local: { label: "Local LLM", hint: "Ollama / LM Studio / vLLM base URL" },
        anthropic: { label: "Direct Anthropic", hint: "Anthropic API key" },
        openai: { label: "Direct OpenAI", hint: "OpenAI API key" },
        "claude-cli": {
            label: "Claude subscription (CLI)",
            hint: "Uses your Claude CLI — no key needed",
            name: "Claude CLI",
            verifyHint: "No key needed. Verify asks the Claude CLI which account is logged in, and shows you its email and plan.",
            loginLabel: "Log in to Claude",
            installName: "Claude Code",
        },
        "codex-cli": {
            label: "ChatGPT subscription (Codex CLI)",
            hint: "Uses your Codex CLI login — no key needed",
            name: "Codex CLI",
            verifyHint: "No key needed. Verify asks the Codex CLI whether you're logged in, and shows you what it says.",
            loginLabel: "Log in to Codex",
            installName: "OpenAI Codex CLI",
        },
        "antigravity-cli": {
            label: "Google subscription (Antigravity CLI)",
            hint: "Experimental — uses your agy login, no key needed",
            name: "Antigravity CLI (agy)",
            verifyHint: "No key needed. Verify sends 1 short test prompt through agy, on your subscription. Experimental: agy's non-interactive mode has known rough edges, and Verify tells you plainly if it fails.",
            loginLabel: "Log in to Antigravity",
            installName: "Google Antigravity",
        },
    },
    noKeyNeeded: "No key needed.",
    pasteKey: "Paste API key",
    verify: "Verify",
    verifying: "Verifying…",
    skipProvider: "Skip for now",
    skipProviderHint: "AI features stay off until you add a provider in Settings. Jobs are scored by keyword in the meantime.",
    verified: "Verified ✓",
    verifiedDetail: "{{detail}} — you can finish onboarding.",
    verifiedOk: "Provider verified — you can finish onboarding.",
    cliNotLoggedIn: "Your {{cli}} is installed but not logged in. Log in to your subscription, then Verify.",
    cliFallback: "CLI",
    loginFallback: "Log in",
    // <lnk> wraps the install link (react-i18next Trans).
    cliNotFound: "{{cli}} not found. Install <lnk>{{name}}</lnk>, then Verify.",
    installFallback: "the CLI",
    retry: "Retry",
    // All-set step.
    allSetIntro: "Press Find jobs and the app starts looking for every role and place you picked. New matches land on the board as they're found.",
    allSetAddUrl: "Found a posting yourself? Add it from the board anytime.",
    allSetResume: "Your master resume is 1 click away, under Master Resume on the board.",
    allSetSettings: "Change providers or turn on networking in Settings.",
    allSetAiOff: "AI is off for now: jobs are scored by keyword, and tailoring waits until you add a provider in Settings.",
};

export default onboarding;
