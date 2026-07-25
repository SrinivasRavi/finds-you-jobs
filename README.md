# finds-you-jobs

A free, open-source desktop app that takes the grunt work out of a job search — it
finds roles, scores them against your resume, tailors a resume and cover letter for
each, helps you ask for referrals, and fills application forms for you to review and
submit. Everything runs on **your** computer with **your** AI key; there is no
company server in the middle.

**Website: [findsyoujobs.com](https://findsyoujobs.com)**

## A quick tour

### Job Board — wake up to scored matches

![The Job Board: a feed of discovered jobs ranked by match score, with the score explained](.github/screenshots/job-board.png)

finds-you-jobs scans hundreds of company career boards and job sources for you —
Greenhouse, Lever, Ashby, Workday and other ATS boards, remote-job boards, Hacker
News "Who is hiring", LinkedIn, and more — then scores every posting against your
resume and explains the score, so the roles worth your time sit at the top. Open a
role to read the posting, generate a tailored resume and cover letter for it, or
watch a company you care about so its new postings always show up.

### Applications — your whole pipeline on one board

![The Applications tracker: a kanban board from Saved through Offer](.github/screenshots/applications.png)

Every role you save becomes a card that moves from Saved through Seeking Referral,
Applied, Interviewing, and Offer. A card carries its tailored resume, cover letter,
referral status, and a full activity history — so "where was I with this company?"
always has an answer. When you're ready to apply, the app fills the application form
and hands you the browser for the final review and Submit.

### Networking — referrals without the spreadsheet

![The Networking board: referral contacts tracked from Sent to Converted](.github/screenshots/networking.png)

A warm referral multiplies your odds of a callback, so finds-you-jobs treats it as a
first-class step: find the right people at a target company, draft a personalized
referral ask for each, and track every relationship from first contact to converted
referral. If you want, it can even send the connection requests and follow-ups for
you from your own LinkedIn account.★

★ Read the disclaimer in Settings before enabling LinkedIn automation.

## Install

**Download the installer for your OS**, run it, and the app walks you through
onboarding: paste your resume, set your job preferences, pick an AI provider,
and add your key. No terminal, no build steps.

### [⬇ Download finds-you-jobs v0.5.3-beta](https://github.com/SrinivasRavi/finds-you-jobs/releases/tag/v0.5.3-beta)

| Your computer | Download this file | On first launch |
|---|---|---|
| **Windows** 10/11 | `finds-you-jobs_0.5.3-beta_x64-setup.exe` | SmartScreen warns because the beta isn't code-signed yet: click **More info → Run anyway** |
| **Mac** (Apple Silicon — M1 and later) | `finds-you-jobs_0.5.3-beta_aarch64.dmg` | Gatekeeper blocks unsigned apps: open **System Settings → Privacy & Security**, scroll down, click **Open Anyway** |
| **Mac** (Intel) | `finds-you-jobs_0.5.3-beta_x64.dmg` | same as above |
| **Linux** (Debian/Ubuntu) | `finds-you-jobs_0.5.3-beta_amd64.deb` | `sudo apt install ./finds-you-jobs_*.deb` |
| **Linux** (any distro, portable) | `finds-you-jobs_0.5.3-beta_amd64.AppImage` | `chmod +x` the file, then run it |

The one-time warnings exist only because the beta installers aren't code-signed
yet (Apple's developer-identity review is pending; Windows signing follows
after). Every release is built in public by
[GitHub Actions](.github/workflows/release.yml) from the source in this repo.
All versions live on the
[releases page](https://github.com/SrinivasRavi/finds-you-jobs/releases).

**Uninstall** — quit the app, then remove it like any other program:
**Windows:** Settings → Apps → finds-you-jobs → Uninstall · **macOS:** drag
`finds-you-jobs.app` from Applications to the Trash · **Linux:**
`sudo apt remove finds-you-jobs` (or just delete the AppImage file). Your data
(database, tailored documents, settings) lives in one folder that updates never
touch — delete it too for a clean slate:
`~/Library/Application Support/finds-you-jobs` (Mac) ·
`%LOCALAPPDATA%\finds-you-jobs` (Windows) ·
`~/.local/share/finds-you-jobs` (Linux). The app's encryption key sits in your
OS keychain under `finds-you-jobs` if you want to remove that as well.

## What it does

- A scored daily feed of jobs from 20 source families and 300+ preconfigured company boards (all public by default — no key needed; optional bring-your-own-key sources like Apify actors and Brave Search add more).
- AI-tailored resumes and cover letters per posting, which you review before you use them.
- A pipeline tracker: Saved → Seeking Referral → Applied → Interviewing → Offer → Rejected.
- Referral outreach: find people at a target company and message them from **your own** LinkedIn account (experimental, off by default — the account risk is yours).
- An in-app cost dashboard so you always know what you're spending on AI calls.

## Principles

- **Local-first, bring-your-own-key.** Your data and your AI key stay on your machine; there's no hosted backend. (A cloud AI provider you choose will, of course, see the requests you send it.)
- **You stay in control.** The app never submits an application on its own — it fills the form and hands you the open browser to review and click Submit yourself.
- **No AI slop.** Tailored output is grounded in your real resume and shown to you before it's used.
- **Open source.** [AGPL-3.0-only](LICENSE) — inspect it, fork it, improve it, share it back.

## For developers & contributors

- Run from source: clone this repo, then `pnpm boot && pnpm dev` (toolchain: Node 20+ with pnpm 9, [uv](https://docs.astral.sh/uv/) for Python 3.13, and Rust/cargo — the first build compiles the desktop shell and takes a few minutes).
- `pnpm test` · `pnpm lint` · `pnpm typecheck` — the gates.
- `pnpm dev:web` — run the sidecar + UI in a browser (no desktop window) for quick iteration.
- Third-party provenance: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [UPSTREAMS.md](UPSTREAMS.md). Release process: [RELEASING.md](RELEASING.md). Contributing (DCO sign-off required): [CONTRIBUTING.md](CONTRIBUTING.md).

## Discord
Join the discord for job search discussions and beta testing - https://discord.gg/hQRjKw6QS

Licensed [AGPL-3.0-only](LICENSE); carried upstream portions keep their own notices.
