# The end-to-end suite

These specs drive the real app in Chromium: the React frontend, the live Python sidecar, and real browser clicks and typing. They join the sidecar's pytest suite and the frontend's vitest suite. Run all 3 before you call a change done.

## Run it

```sh
cd frontend
FYJ_WEB_PORT=14211 npx playwright test          # the whole suite, about 3 minutes
FYJ_WEB_PORT=14211 npx playwright test journey  # only the click-and-fill journeys
```

- Playwright starts its own stack (`scripts/dev-web.mjs`) on the port you give. Use a port other than 1420 if `pnpm dev` is running, so the suite never attaches to your session.
- Every run starts from an empty data folder in `e2e/_results/appdata`. Your real data is never read.
- Screenshots land in `e2e/_screenshots/<spec>/`. Look at the ones for the screens you changed.
- Don't start a stack by hand with its data under `e2e/_results`: Playwright empties that folder when it starts, under the running sidecar.

## What stands in for the outside world

| Stand-in | What it replaces | Where |
|---|---|---|
| `FYJ_FAKE_LLM=1` | Every subscription CLI. It answers the tailor, cover letter and chat contracts at once, for 0 tokens, and `{}` to everything else | `sidecar/app/registry/engine_config.py`, `FakeInstantEngine` |
| `FYJ_APPLY_DEV=1` | A live employer form. The Applier fills a local fixture form | `applier*.spec.ts` |
| `VITE_LINKEDIN_ORIGIN` | linkedin.com. A loopback fixture serves the LinkedIn pages | `networking*.spec.ts`, `chat-browser.spec.ts` |
| `FYJ_SESSION_KEY` | The OS keychain. A fixed throwaway key seals saved API keys | `playwright.config.ts` |

Nothing in the suite sends, submits or spends from a real account.

## Which spec covers which feature

| Feature | Specs |
|---|---|
| Onboarding: resume upload, roles, provider choice, Skip, All set | `aa-onboarding` |
| Board: list, search, filters, sort, NEW badge, closed postings, virtualised list | `board`, `journey-workflow` |
| Board: save to the tracker, Remove, Deleted jobs and Undo | `journey-workflow`, `url-less-jobs` |
| Board: add a job with no link, duplicates, follow a company | `url-less-jobs`, `duplicate-prompt`, `watch-company`, `autofill-typing` |
| Scoring: score rings, reasons, rescore, weights | `score-ring`, `score-reasons-i18n`, `rescore` |
| Finder preferences and the place picker | `finder-prefs`, `place-picker` |
| Tracker: drag, the Applied confirm, priority, notes, Activity | `journey-workflow`, `tracker` |
| Tracker: archive, Deleted applications, Delete forever | `tracker` |
| Resume and cover letter: generate, approve, regenerate, copy, upload, edit | `journey-workflow`, `resume-editor`, `resume-upload`, `master-resume-alignment` |
| Export to PDF, Chromium download | `export-browser-download` |
| Applier: fill, review, Submit, autopilot, closed window, Applicant details | `applier`, `applier-browser` |
| Networking: kanban, contacts, referrals, Sync, sends, LinkedIn view | `networking`, `networking-browser`, `networking-aa-sync-copy` |
| Assistant: tools, confirmation cards, browser tools | `chat`, `chat-browser` |
| Settings: theme, language, lifecycle, observability, API keys, applicant details, generate-on-save | `journey-settings` |
| Settings: every pane renders, scoring, prompts, discovery sources, autopilot | `settings-analytics`, `prompt-tuning` |
| Analytics: tabs, filter chips, a row's timeline, paging, Stop | `journey-analytics`, `settings-analytics` |
| About and updates | `about` |
| Shell: boot, a crashed surface, sidecar restarts | `boot`, `zz-reconnect`, `zzz-reconnect-newport` |

### Not covered here

- An "Older listing" restore: a job reaches it only after days on the board, and no dev route ages one.
- Adding a job by its URL: the fetch needs a live careers page.
- The desktop shell (the Tauri window, its native Save dialog and the updater). macOS has no WebDriver for its webview, so these run by hand on a built app.
