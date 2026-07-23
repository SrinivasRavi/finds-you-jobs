// English — the onboarding wizard.
const onboarding = {
    stepDownload: "Download the app",
    stepResume: "Resume",
    stepPreferences: "Preferences",
    stepProvider: "LLM provider",
    stepAllSet: "All set",
    titleResume: "Add your master resume",
    titlePreferences: "What are you looking for?",
    titleProvider: "Choose your LLM provider",
    titleAllSet: "All set",
    back: "Back",
    continue: "Continue",
    finish: "Finish & go to Discover jobs",
    finishing: "Finishing…",
    pctComplete: "{{pct}}% complete",
    // Resume step.
    resumeIntro:
        "Upload a .md / .txt / .pdf, or paste it below. Review the extracted text — this is what the app scores and tailors against. You can refine it later from the Master Resume button in Discover jobs.",
    extracting: "Extracting…",
    resumeLoaded: "{{name}} — loaded, review below",
    resumeChoose: "Click to choose a .md / .txt / .pdf resume file",
    resumeTextLabel: "Resume (Markdown) — paste or edit the extracted text",
    resumePlaceholder: "# Your name\n\nPaste your resume here, or upload a file above.",
    // Preferences step.
    aliasLabel: "Role aliases (at least one)",
    aliasHint: "Type a role and press Enter or comma to add it. Add several — each becomes a chip below.",
    aliasPlaceholder: "e.g. Backend Engineer",
    locationLabel: "Locations (at least one; Remote is valid)",
    locationHint: "Type a location and press Enter or comma to add it. Remote is valid.",
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
    scoringLlm: "AI scoring — best quality, but costs LLM tokens and some time",
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
            verifyHint: "No key needed — we verify your Claude CLI is reachable.",
            loginLabel: "Log in to Claude",
            installName: "Claude Code",
        },
        "codex-cli": {
            label: "ChatGPT subscription (Codex CLI)",
            hint: "Uses your Codex CLI login — no key needed",
            name: "Codex CLI",
            verifyHint: "No key needed — we verify your Codex CLI is logged in.",
            loginLabel: "Log in to Codex",
            installName: "OpenAI Codex CLI",
        },
        "antigravity-cli": {
            label: "Google subscription (Antigravity CLI)",
            hint: "Experimental — uses your agy login, no key needed",
            name: "Antigravity CLI (agy)",
            verifyHint:
                "No key needed — we run a real test prompt through agy. Experimental: agy's non-interactive mode has known rough edges; Verify tells you honestly if it fails.",
            loginLabel: "Log in to Antigravity",
            installName: "Google Antigravity",
        },
    },
    noKeyNeeded: "No key needed.",
    pasteKey: "Paste API key",
    verify: "Verify",
    verifying: "Verifying…",
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
    allSetIntro: "On Finish we write your profile and fire the cold-start scrape across every role × location.",
    allSetAddUrl: "Add a job by URL anytime from the board",
    allSetResume: "Review your master resume from the board's Master Resume button",
    allSetSettings: "Tune providers + networking in Settings",
};

export default onboarding;
