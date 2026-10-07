// English — the reference locale (shell + settings-nav + appearance slice).
// Every other locale mirrors these keys; missing keys fall back to English.
const common = {
  nav: {
    jobBoard: "Discover jobs",
    applications: "Applications",
    networking: "Networking",
    analytics: "Analytics",
    settings: "Settings",
  },
  settingsNav: {
    providers: "LLM Providers",
    providersBlurb: "Keys & connected models",
    prompts: "Prompts & Models",
    promptsBlurb: "Customize the AI's instructions",
    discovery: "Discover jobs",
    discoveryBlurb: "Sources, scoring & automation",
    networking: "Networking",
    networkingBlurb: "Referral outreach & LinkedIn",
    data: "Privacy & Data",
    dataBlurb: "Logging & data cleanup",
    appearance: "Appearance",
    appearanceBlurb: "Theme & language",
    about: "About & Updates",
    aboutBlurb: "Version, updates & support",
  },
  // The 4 scoring methods. They live in `common` because 2 surfaces name them:
  // Settings' method menu and the board's score ring. One home, so the menu and
  // the ring can never disagree about what a method is called.
  scoreMethod: {
    llm: "AI scoring",
    jev: "Fast scoring",
    local: "On-device scoring",
    keyword: "Keyword scoring",
    // The ring's hover line. `scoredBy` reads as a sentence on its own because
    // a title attribute has no surrounding copy to lean on.
    scoredBy: "Scored by {{method}}",
  },
  appearance: {
    theme: "Theme",
    themeHint: "\u201cSystem\u201d will match your OS light/dark settings.",
    light: "Light",
    dark: "Dark",
    system: "System",
    language: "Language",
    languageHint:
      "Applies right away. English is complete; other languages are still being translated — untranslated text stays in English.",
  },
};

export default common;
