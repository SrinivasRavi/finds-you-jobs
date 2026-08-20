// English — the networking namespace. Filled by the string-extraction pass.
const networking = {
  // The header's LinkedIn status BUTTON (2026-08-16, was a read-only pill):
  // it opens the browser modal — or Settings when there is nothing to watch
  // (expired / never connected). `inProgress` is the live face while an op
  // drives the surface. The old `title` key is retired (renamed, not reworded,
  // so stale locale translations can't apply to the new behavior).
  linkedinPill: {
    connected: "LinkedIn connected",
    connecting: "Connecting…",
    backingOff: "Backing off",
    expired: "Session expired",
    connect: "Connect LinkedIn",
    inProgress: "LinkedIn in progress",
    titleOpen: "Open the LinkedIn browser.",
    titleSettings: "Connect LinkedIn in Settings.",
  },
  addByUrl: "Add a contact by URL",
  // The LinkedIn browser modal (2026-08-16; a left-rail destination before).
  linkedinModal: {
    title: "LinkedIn",
  },
  // The modal body when Referral Outreach is off (the toggle can flip while
  // the dialog is open; entry points are hidden otherwise).
  linkedinView: {
    disabled: "Turn on Referral Outreach in Settings to use the LinkedIn view.",
  },
  // Manual-only sync copy (maintainer decision, 2026-08-15): the Sync button
  // is the one trigger — the on-open refresh is removed, so the copy states
  // plainly that nothing syncs unless the user presses Sync. Each press is
  // real LinkedIn read traffic from the user's own account, and the copy says
  // so. The status (running / stopped reason / stamp) lives INSIDE the
  // fixed-size button since 2026-08-16; `running` is the busy note beside the
  // spinning icon.
  sync: {
    label: "Sync",
    running: "Checking contacts…",
    title: "Checks your contacts on LinkedIn. Runs only when you press Sync.",
    // `{{n}}` (not `{{count}}`) on purpose — count triggers i18next's plural
    // key machinery; these are compact unit stamps like the card's "{{n}}d".
    lastSynced: "Synced {{when}}",
    justNow: "just now",
    minutesAgo: "{{n}}m ago",
    hoursAgo: "{{n}}h ago",
    daysAgo: "{{n}}d ago",
    // The honest outcome of the newest Sync press (2026-08-16): a sweep the
    // read budget/backoff/session cut short must say so instead of letting
    // the stamp read as a clean sync. `notChecked` sizes the untouched tail.
    stoppedCap: "Sync stopped: today's LinkedIn read budget is used up",
    stoppedRate: "Sync stopped: LinkedIn throttled the read — backing off",
    stoppedAuth: "Sync stopped: LinkedIn session expired — reconnect in Settings",
    stoppedOther: "Sync stopped before finishing",
    notChecked: "{{n}} not checked",
  },
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
  sendError: "Could not start the send.",
  dismiss: "dismiss",
  card: {
    today: "today",
    days: "{{n}}d",
    inStatus: "{{duration}} in {{status}}",
    // Last-message attribution (maintainer, 2026-08-15): the snippet is the
    // thread's REAL last message, so the prefix names whose it is — "Me:" for
    // our own, the contact's first name for theirs. The name is data; only
    // the label and the composition are localized.
    me: "Me:",
    from: "{{name}}:",
  },
  deleted: {
    title: "Deleted Contacts",
    blurb: "Deleted contacts keep their history. Restore one, or re-add it by URL.",
    empty: "No deleted contacts.",
    restore: "Restore",
  },
  detail: {
    linkedin: "LinkedIn",
    archive: "Delete",
  },
  // Stage-dependent composer in the contact detail modal. The dropdown offers a
  // FEW template starting points per kanban stage; the box stays fully editable
  // and the single Send IS the per-action confirmation (the modal shows the
  // recipient, the message, and the channel + irreversibility line).
  compose: {
    title: "Send a message",
    templateLabel: "Suggested message",
    custom: "Write my own",
    messageLabel: "Your message",
    send: "Send",
    // Honest channel + irreversibility statement, beside the Send button so
    // the one click stays informed.
    channelDm:
      "Sends a real DM from your LinkedIn account. finds-you-jobs can't take it back.",
    channelInvite:
      "Sends a real connection request with your note. finds-you-jobs can't take it back.",
    // Not messageable: the invite is still pending, so a DM would fail and a
    // second invite is not a thing we send.
    blockedInvitePending:
      "Their connection request is still pending. Message them once they accept.",
    emptyError: "Write a message first.",
    // Greeting halves — joined with the option bodies below.
    greeting: "Hi {{firstName}},",
    greetingNoName: "Hi,",
    templates: {
      accepted: {
        gentle: "Gentle reminder",
        gentleBody:
          "Thanks for connecting. I reached out earlier and would still love to hear your thoughts when you get a moment.",
        direct: "Direct follow-up",
        directBody:
          "Following up on my earlier note. Do you have 10 minutes this week for a quick chat?",
        context: "Add context",
        contextBody:
          "Glad we're connected. I'm exploring new roles right now and would really value any pointers you could share about your team.",
      },
      engagement: {
        direct: "Referral ask — direct",
        directBody:
          "Can you please refer me for a role at {{company}}? I'm a strong fit and happy to send over my resume and the specific opening.",
        directBodyNoCompany:
          "Can you please refer me for a role at your company? I'm happy to send over my resume and the specific opening.",
        soft: "Referral ask — softer",
        softBody:
          "I've really enjoyed our conversation. If you feel comfortable, would you be open to referring me for a role at {{company}}? No pressure at all.",
        softBodyNoCompany:
          "I've really enjoyed our conversation. If you feel comfortable, would you be open to referring me for a role at your company? No pressure at all.",
        advice: "Ask for advice first",
        adviceBody:
          "I'm applying for a role at {{company}} and would value your advice on standing out. If you think I'd be a good fit, a referral would mean a lot.",
        adviceBodyNoCompany:
          "I'm applying for a role at your company and would value your advice on standing out. If you think I'd be a good fit, a referral would mean a lot.",
      },
      ghosted: {
        gentle: "Gentle nudge",
        gentleBody:
          "I know things get busy. Just floating my earlier note back to the top of your inbox in case it slipped through.",
        direct: "Direct last nudge",
        directBody:
          "I haven't heard back, so this is my last nudge. If now isn't a good time, no worries at all.",
        reconnect: "Pick it back up",
        reconnectBody:
          "It's been a while since we last spoke. I'd still love to pick the conversation back up if you're open to it.",
      },
    },
  },
  // The browser modal's queue panel: who is being worked on, live step
  // progress, and an explicit idle state. No panel heading and no "Done"
  // chip (maintainer, 2026-08-16): the check mark is the whole done signal.
  opPlan: {
    idleTitle: "Nothing is running",
    // Each row states its ACTION, not just its subject (maintainer,
    // 2026-08-16: a bare contact name can be a view, an invite, or a DM).
    // `reachOut` covers a send whose channel the server hasn't routed yet.
    row: {
      view: "View: {{name}}",
      connect: "Connect: {{name}}",
      message: "Message: {{name}}",
      reachOut: "Reach out: {{name}}",
      discover: "Find employees in: {{name}}",
      search: "Search jobs: {{name}}",
      login: "Sign in: {{name}}",
    },
    notSent: "Not sent",
    failed: "Failed",
    cancelled: "Cancelled",
    dryRun: "Dry run — nothing is sent.",
    foundSoFar_one: "{{count}} person found so far",
    foundSoFar_other: "{{count}} people found so far",
    waitingCompanyConfirm:
      "Paused for you: pick the right company in the referrals popup, then discovery continues.",
    kinds: {
      send: "Referral outreach send",
      discover: "Find people at the company",
      contact_sync: "Contact status sync",
      linkedin_search: "LinkedIn job search",
      linkedin_login: "LinkedIn sign-in",
      // Last-resort only: the ledger subject names every view (contact or
      // URL path), so a row should never actually read this bare.
      view_page: "View a page",
    },
    // Flat numbered keys (not arrays) — the shape every locale file mirrors.
    steps: {
      dm1: "Wait out the self-imposed pacing gap",
      dm2: "Open the contact's profile",
      dm3: "Open the message thread",
      dm4: "Deliver your approved message",
      dm5: "Record the outcome in your log",
      invite1: "Wait out the self-imposed pacing gap",
      invite2: "Open the contact's profile",
      invite3: "Click Connect",
      invite4: "Attach your note",
      invite5: "Send the invitation and verify it went out",
      discover1: "Resolve the company to its LinkedIn entity",
      discover2: "Search its current employees",
      discover3: "Save candidates to your contacts",
    },
  },
  add: {
    title: "Add a contact",
    blurb: "Add anyone by their LinkedIn URL.",
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
