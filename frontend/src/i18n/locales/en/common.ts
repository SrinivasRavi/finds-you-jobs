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
    discovery: "Discover jobs",
    discoveryBlurb: "Where your jobs come from",
    scoring: "Scoring",
    scoringBlurb: "How jobs are rated, and the rubric",
    documents: "Resume & cover letter",
    documentsBlurb: "What gets written, and how",
    applying: "Applying",
    applyingBlurb: "Form answers & the Applier's model",
    networking: "Networking",
    networkingBlurb: "Referral outreach & LinkedIn",
    autopilot: "Autopilot",
    autopilotBlurb: "What runs without asking",
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
    llm: "LLM scoring",
    jev: "Jev scoring",
    local: "Laya scoring",
    keyword: "Keyword scoring",
    // The ring's hover line. `scoredBy` reads as a sentence on its own because
    // a title attribute has no surrounding copy to lean on.
    scoredBy: "Scored by {{method}}",
  },
  // The 7 scoring axes, named for a reader rather than for the code. Shared,
  // because the board's bars and Settings' weight sliders label the same axes.
  scoreFactor: {
    hard_requirements: "Hard requirements",
    core_stack: "Core stack",
    years: "Years of experience",
    level: "Seniority fit",
    location_preference: "Location",
    logistics: "Logistics",
    nice_to_haves: "Nice to haves",
    // A confidence is not a graded position, so it reads differently on purpose.
    confidence: "{{percent}}% confident",
    weight: "weight {{percent}}%",
    none: "This method recorded no per-axis detail for this job.",
  },
  scoreReason: {
    unstated: "unstated",
    kw: {
      no_description: "No usable job description: {{chars}} chars, under the {{min}}-char minimum. Scoring a title and company name measures neither.",
      skills: "Skill-keyword overlap: {{found}}/{{total}} JD terms found in resume ({{score}}/100, weight {{weight}}%).",
      years: "Experience: {{offered}} yrs on resume vs {{required}} yrs required ({{score}}/100, weight {{weight}}%).",
      level: "Seniority: posting reads {{posting}}, resume reads {{candidate}} ({{score}}/100, weight {{weight}}%).",
      location: "Location: {{why}}",
      red_flag: "Cap applied: red-flag term '{{term}}' detected — capped at {{cap}}.",
      penalty: "Location modifier: -{{penalty}} for a location you did not ask for.",
    },
    ev: {
      skills: "{{found}} of {{total}} posting terms appear in the resume",
      years: "{{offered}} on the resume against {{required}} required",
      level: "posting reads {{posting}}, resume reads {{candidate}}",
    },
    loc: {
      no_prefs: "No location preference set, so nothing to compare.",
      city: "{{city}} is a location you asked for.",
      region: "{{where}} is inside a region you asked for.",
      country: "{{where}} is in a country you asked for.",
      remote_any: "Remote with no region restriction.",
      remote_open: "Remote and open to {{countries}}.",
      remote_only: "Remote, but only within {{scope}}.",
      remote_unscoped: "Says remote without naming a region, so eligibility can't be read from the field.",
      elsewhere: "Located in {{named}}, which you did not ask for.",
      unreadable: "No location could be read from the posting.",
    },
  },
  // Relative-time phrases, as keys rather than formatted strings, because the
  // board interpolates them into translated sentences (see jobFormat.ts).
  relativeTime: {
    never: "never",
    justNow: "just now",
    today: "today",
    minutes: "{{n}}m ago",
    hours: "{{n}}h ago",
    days: "{{n}}d ago",
    months: "{{n}}mo ago",
  },
  run: {
    stop: "Stop",
    stopped: "Stopped. Nothing more is spent on it.",
    timedOut: "Stopped after 4 minutes, so it couldn't keep spending on your key.",
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
