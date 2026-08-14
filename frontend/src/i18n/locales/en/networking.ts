// English — the networking namespace. Filled by the string-extraction pass.
const networking = {
  linkedinPill: {
    connected: "LinkedIn connected",
    connecting: "Connecting…",
    backingOff: "Backing off",
    expired: "Session expired",
    connect: "Connect LinkedIn",
    title: "Read-only — connect/enable LinkedIn from Settings",
  },
  addByUrl: "Add a contact by URL",
  // The Contacts / Browser tab bar — shown only when Referral Outreach is on.
  // The Browser tab mounts the core browser surface pointed at LinkedIn.
  tabs: {
    contacts: "Contacts",
    browser: "Browser",
  },
  // Paste-a-URL reach-out: add the person to the kanban, then confirm the note
  // before it's sent (per-action confirm). Distinct from "Add a contact by URL",
  // which only files the contact.
  reachByUrl: {
    open: "Reach out by URL",
    title: "Reach out by URL",
    blurb:
      "Paste a LinkedIn profile URL to add the person to your kanban, then review and confirm the note before anything is sent.",
    urlLabel: "LinkedIn profile URL",
    nameLabel: "Name",
    messageLabel: "Your note",
    messagePlaceholder: "Hi, I came across your profile and would love to connect about roles on your team.",
    cancel: "Cancel",
    submit: "Continue",
  },
  sync: {
    label: "Sync",
    busy: "Syncing…",
    title: "Check LinkedIn for status changes on your contacts. Runs only when you ask — nothing syncs in the background.",
  },
  connectionCount_one: "{{count}} connection",
  connectionCount_other: "{{count}} connections",
  degreeSummary: "{{first}} 1st · {{second}} 2nd",
  filters: {
    company: "Company",
    all: "All",
    audience: "Audience",
    search: "Search",
  },
  audience: {
    peer: "Peer",
    hm: "Hiring Team",
    recruiter: "Recruiter",
    leadership: "Top Management",
    other: "Other",
  },
  columns: {
    sent: "Sent",
    accepted: "Accepted",
    engagement: "Engagement",
    ghosted: "Ghosted",
    converted: "Converted",
  },
  columnEmpty: {
    sent: "Awaiting accepts — keep sending.",
    accepted: "Accepted, awaiting first reply.",
    engagement: "Active conversation — nudge as needed.",
    ghosted: "No activity for 7+ days.",
    converted: "They referred you or intro'd.",
  },
  moveError: "Could not move contact.",
  dismiss: "dismiss",
  card: {
    today: "today",
    days: "{{n}}d",
    inStatus: "{{duration}} in {{status}}",
    you: "You:",
  },
  deleted: {
    title: "Deleted Contacts",
    blurb:
      "Deleted contacts are hidden from the kanban but keep their outreach history. Restore one to bring it back, or re-add it by URL — either way it returns to where it was.",
    empty: "No deleted contacts.",
    restore: "Restore",
  },
  detail: {
    linkedin: "LinkedIn",
    lastMessage: "Last message",
    archive: "Delete",
  },
  add: {
    title: "Add a contact",
    blurb:
      "Add anyone by their LinkedIn URL — always available regardless of LinkedIn state (rank, don't gate).",
    urlLabel: "LinkedIn profile URL",
    nameLabel: "Name",
    companyLabel: "Company",
    roleLabel: "Role",
    initialColumn: "Initial column",
    optionSent: "Sent — invite is out",
    optionAccepted: "Accepted — already connected",
    optionEngagement: "Engagement — actively chatting",
    optionConverted: "Converted — referring me",
    cancel: "Cancel",
    submit: "Add contact",
  },
};

export default networking;
