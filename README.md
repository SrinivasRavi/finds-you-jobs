# finds-you-jobs

A free, open-source desktop app that takes the grunt work out of a job search — it
finds roles, scores them against your resume, tailors a resume and cover letter for
each, helps you ask for referrals, and fills application forms for you to review and
submit. Everything runs on **your** computer with **your** AI key; there is no
company server in the middle.

## Install & run

You need a terminal for two commands. Here's how to open one:

- **macOS** — press `⌘ + Space`, type `Terminal`, press Enter.
- **Windows** — press the `Windows` key, type `PowerShell`, press Enter.
- **Linux** — press `Ctrl + Alt + T` (or open "Terminal" from your apps).

Then copy-paste **one** command for your system. It installs everything (git, the
runtimes, and all dependencies), downloading the project into a `finds-you-jobs`
folder in your current directory:

**macOS / Linux**
```bash
curl -fsSL https://raw.githubusercontent.com/SrinivasRavi/finds-you-jobs/main/scripts/setup.sh | bash
```

**Windows** (in PowerShell)
```powershell
irm https://raw.githubusercontent.com/SrinivasRavi/finds-you-jobs/main/scripts/setup.ps1 | iex
```

When it finishes, start the app:
```bash
cd finds-you-jobs
pnpm dev
```

The first launch builds the desktop shell and can take a few minutes; later launches
are fast. On first run the app walks you through onboarding (paste your resume, pick
an AI provider, add your key).

> Already have git and just want the code? `git clone https://github.com/SrinivasRavi/finds-you-jobs.git`, then `cd finds-you-jobs && bash scripts/setup.sh` (or `scripts\setup.ps1` on Windows).

### Everyday commands

| Command | What it does |
| --- | --- |
| `pnpm dev` | Start the app. |
| `FYJ_DATA_DIR=~/some-folder pnpm dev` | Start with a **fresh, separate profile** (its own board, settings, and data). Omit it to use your normal profile. |
| `git pull && pnpm run boot` | Update to the latest version and refresh dependencies. |

## What it does

- A scored daily feed of jobs from many sources (all public — no search-engine key, nothing of yours is shared).
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

- `pnpm test` · `pnpm lint` · `pnpm typecheck` — the gates.
- `pnpm dev:web` — run the sidecar + UI in a browser (no desktop window) for quick iteration.
- Third-party provenance: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [UPSTREAMS.md](UPSTREAMS.md). Release process: [RELEASING.md](RELEASING.md). Contributing (DCO sign-off required): [CONTRIBUTING.md](CONTRIBUTING.md).

Licensed [AGPL-3.0-only](LICENSE); carried upstream portions keep their own notices.
