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

Follow steps as per your OS. (Changed your mind later? Everything below can be
removed cleanly — see [Uninstall](#uninstall).)

---

### macOS

**Open a terminal:** press `⌘ + Space`, type `Terminal`, press Enter.

**Install** (one copy-paste — installs git and every dependency, and downloads the
app into a `finds-you-jobs` folder in your current directory):

```bash
curl -fsSL https://raw.githubusercontent.com/SrinivasRavi/finds-you-jobs/main/scripts/setup.sh | bash
```

If it asks you to install the "command line developer tools", click Install, wait,
then run the same command again.

**Start the app** (the script prints this exact line with your real path at the end;
if `pnpm` is "command not found", close Terminal, open a new one, and run it again):

```bash
cd finds-you-jobs && pnpm dev
```

**Everyday commands** (run inside the `finds-you-jobs` folder):

```bash
pnpm dev                                  # start the app
git pull && pnpm run boot                 # update to the latest version
FYJ_DATA_DIR="$HOME/fyj-test" pnpm dev    # start with a separate, fresh profile
```

---

### Windows

**Open a terminal:** press the `Windows` key, type `PowerShell`, press Enter.

**Install** (one copy-paste — installs git, the C++ build tools the desktop shell
needs, and every dependency, downloads the app into a `finds-you-jobs` folder in
your current directory (or your home folder if the current directory isn't
writable, e.g. an admin PowerShell that starts in `System32`), then **starts the
app automatically** — the build-tools download is large and the first launch
compiles the desktop shell, so let it run, this can take a while):

```powershell
irm https://raw.githubusercontent.com/SrinivasRavi/finds-you-jobs/main/scripts/setup.ps1 | iex
```

Keep that PowerShell window open while the app runs — closing it or pressing
Ctrl-C there quits the app. (The one exception: if an installer above asked you
to restart your PC first, the script tells you and stops there instead of
starting the app — restart, then run the two commands it prints.)

**Start the app again later** (after the first install, from inside the
`finds-you-jobs` folder; if a command is "not recognized", close PowerShell,
open a new one, and try again):

```powershell
cd finds-you-jobs
pnpm dev
```

**Everyday commands** (run inside the `finds-you-jobs` folder — note PowerShell
uses `;` between commands, not `&&`):

```powershell
pnpm dev                                    # start the app
git pull; pnpm run boot                     # update to the latest version
$env:FYJ_DATA_DIR="$HOME\fyj-test"; pnpm dev  # start with a separate, fresh profile
```

---

### Linux

**Open a terminal:** press `Ctrl + Alt + T`, or open "Terminal" from your apps.

**Install** (one copy-paste — installs git, the desktop-shell system libraries via
your package manager (`sudo` will prompt), and every dependency, and downloads the
app into a `finds-you-jobs` folder in your current directory):

```bash
curl -fsSL https://raw.githubusercontent.com/SrinivasRavi/finds-you-jobs/main/scripts/setup.sh | bash
```

**Start the app** (the script prints this exact line with your real path at the end;
if `pnpm` is "command not found", close the terminal, open a new one, and run it again):

```bash
cd finds-you-jobs && pnpm dev
```

**Everyday commands** (run inside the `finds-you-jobs` folder):

```bash
pnpm dev                                  # start the app
git pull && pnpm run boot                 # update to the latest version
FYJ_DATA_DIR="$HOME/fyj-test" pnpm dev    # start with a separate, fresh profile
```

---

## First launch

The first `pnpm dev` compiles the desktop shell and can take a few minutes; later
launches are fast. The app then walks you through onboarding: paste your resume,
set your job preferences, pick an AI provider, and add your key.

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

## Uninstall

Removing finds-you-jobs is as clean as installing it. Quit the app first, then
work through the steps for your OS: **step 1** removes the app, **step 2**
removes your data, **step 3** removes the developer tools the installer set up.
Steps 1 and 2 are always safe. In step 3, each line says what it removes —
**skip any tool you also use for something else**, because those are shared
tools, not part of the app.

One more rule for a machine that isn't yours (a friend's, a work laptop): the
installer only installs a tool when it's missing, so if the machine already had
git or Node, those belong to its owner — in step 3, remove only what the
install actually printed it was installing.

---

### macOS

**1. Delete the app.** Everything the app built lives inside the one folder the
installer created. In the folder where you ran the install command:

```bash
rm -rf finds-you-jobs
```

**2. Delete your data** — the database, tailored resumes, settings, window
caches, and the app's saved encryption key:

```bash
rm -rf "$HOME/Library/Application Support/finds-you-jobs"
rm -rf "$HOME/Library/WebKit/com.finds-you-jobs.app" "$HOME/Library/Caches/com.finds-you-jobs.app" "$HOME/Library/Saved Application State/com.finds-you-jobs.app.savedState"
security delete-generic-password -s finds-you-jobs
```

The last line removes the app's key from your macOS Keychain — it's fine if it
says the item can't be found. If you ever started the app with a custom
`FYJ_DATA_DIR` profile (like the `$HOME/fyj-test` example above), delete those
folders too.

**3. Remove the tools the installer set up** — skip any line for a tool you use
elsewhere:

```bash
rm -rf "$HOME/Library/Caches/ms-playwright"       # the app's private Chromium (~150 MB); shared with any other Playwright tools you have
rustup self uninstall -y                          # the Rust toolchain
rm -rf "$HOME/.cache/uv" "$HOME/.local/share/uv" "$HOME/.local/bin/uv" "$HOME/.local/bin/uvx"   # uv and the Python it installed
corepack disable pnpm 2>/dev/null; npm uninstall -g pnpm 2>/dev/null    # pnpm
rm -rf "$HOME/Library/pnpm" "$HOME/.npm" "$HOME/.cache/node/corepack"   # pnpm's store, npm + corepack caches
```

If the installer added Node via Homebrew (it only does this when Node was
missing and Homebrew was present): `brew uninstall node`. The Xcode command-line
tools (which provide git) are Apple's shared developer tools used by many other
things, so we don't suggest removing them.

Last trace: the Rust and uv installers each added a PATH line to your shell
profile (`~/.zshenv`, `~/.zshrc`, `~/.profile`, or `~/.bashrc`). They're
harmless after the step above, but for zero trace, open those files and delete
the lines mentioning `.cargo/env` or `.local/bin/env`.

---

### Windows

**1. Delete the app.** Everything the app built lives inside the one folder the
installer created. In the folder where you ran the install command (the
installer may have used your home folder instead — run `cd $HOME` first if the
folder isn't found):

```powershell
cmd /c "rmdir /s /q finds-you-jobs"
```

**2. Delete your data** — the database, tailored resumes, settings, window
cache, and the app's saved encryption key:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\finds-you-jobs" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\com.finds-you-jobs.app" -ErrorAction SilentlyContinue
cmdkey /delete:finds-you-jobs
```

The last line removes the app's key from Windows Credential Manager — it's fine
if it says the entry doesn't exist (you can also do it in Control Panel →
Credential Manager → Windows Credentials). If you ever started the app with a
custom `FYJ_DATA_DIR` profile (like the `$HOME\fyj-test` example above), delete
those folders too.

**3. Remove the tools the installer set up** — skip any line for a tool you use
elsewhere:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\ms-playwright" -ErrorAction SilentlyContinue   # the app's private Chromium (~150 MB)
rustup self uninstall -y                                    # the Rust toolchain
uv cache clean
Remove-Item -Recurse -Force "$env:APPDATA\uv","$env:LOCALAPPDATA\uv" -ErrorAction SilentlyContinue        # uv and the Python it installed
Remove-Item -Force "$HOME\.local\bin\uv.exe","$HOME\.local\bin\uvx.exe" -ErrorAction SilentlyContinue
corepack disable pnpm; npm uninstall -g pnpm                # pnpm
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\pnpm","$env:APPDATA\npm","$env:LOCALAPPDATA\npm-cache","$env:LOCALAPPDATA\node\corepack","$HOME\.cache\node\corepack" -ErrorAction SilentlyContinue  # pnpm's store, npm + corepack caches
winget uninstall OpenJS.NodeJS.LTS                          # Node — skip if you use Node elsewhere
winget uninstall Microsoft.VisualStudio.2022.BuildTools     # C++ Build Tools — skip if you build other software
winget uninstall Git.Git                                    # git — skip if you use git elsewhere
```

Two things we deliberately leave alone: the **WebView2 runtime** is a shared
Windows component other apps rely on (the installer only added it if Windows
didn't have it), and your **PowerShell execution policy** — the installer set it
to `RemoteSigned` for your user only if it was locked down, and putting it back
(`Set-ExecutionPolicy -ExecutionPolicy Undefined -Scope CurrentUser`) would
block other local scripts again. Restore it only if you know you want that.

---

### Linux

**1. Delete the app.** Everything the app built lives inside the one folder the
installer created. In the folder where you ran the install command:

```bash
rm -rf finds-you-jobs
```

**2. Delete your data** — the database, tailored resumes, settings, window
caches, and the app's saved encryption key:

```bash
rm -rf "$HOME/.local/share/finds-you-jobs"
rm -rf "$HOME/.local/share/com.finds-you-jobs.app" "$HOME/.cache/com.finds-you-jobs.app"
secret-tool clear service finds-you-jobs
```

(If you've set `$XDG_DATA_HOME`, the first folder lives under it instead.) The
last line removes the app's key from your keyring — if `secret-tool` isn't
installed, use your desktop's "Passwords and Keys" app, and if there's no
keyring at all the key lived in a file already deleted with your data folder.
If you ever started the app with a custom `FYJ_DATA_DIR` profile (like the
`$HOME/fyj-test` example above), delete those folders too.

**3. Remove the tools the installer set up** — skip any line for a tool you use
elsewhere:

```bash
rm -rf "$HOME/.cache/ms-playwright"               # the app's private Chromium (~150 MB); shared with any other Playwright tools you have
rustup self uninstall -y                          # the Rust toolchain
rm -rf "$HOME/.cache/uv" "$HOME/.local/share/uv" "$HOME/.local/bin/uv" "$HOME/.local/bin/uvx"   # uv and the Python it installed
corepack disable pnpm 2>/dev/null; sudo npm uninstall -g pnpm 2>/dev/null   # pnpm
rm -rf "$HOME/.local/share/pnpm" "$HOME/.npm" "$HOME/.cache/node/corepack"   # pnpm's store, npm + corepack caches
```

If the installer added Node (Debian/Ubuntu only, via the NodeSource repository)
and you don't use Node elsewhere:

```bash
sudo apt-get remove -y nodejs
sudo rm -f /etc/apt/sources.list.d/nodesource.list*
```

The system libraries the installer added via your package manager (webkit2gtk,
GTK, build tools, git) are shared with the rest of your desktop, so we don't suggest force-removing
them — many other programs use them. `sudo apt autoremove` will clean up
whatever nothing else needs.

Last trace: the Rust and uv installers each added a PATH line to your shell
profile (`~/.profile`, `~/.bashrc`, or `~/.zshrc`). They're harmless after the
step above, but for zero trace, open those files and delete the lines mentioning
`.cargo/env` or `.local/bin/env`.

## For developers & contributors

- `pnpm test` · `pnpm lint` · `pnpm typecheck` — the gates.
- `pnpm dev:web` — run the sidecar + UI in a browser (no desktop window) for quick iteration.
- Third-party provenance: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [UPSTREAMS.md](UPSTREAMS.md). Release process: [RELEASING.md](RELEASING.md). Contributing (DCO sign-off required): [CONTRIBUTING.md](CONTRIBUTING.md).

## Discord
Join the discord for job search discussions and beta testing - https://discord.gg/hQRjKw6QS. If there is something that bothers you in the app, there is a limited time offer till July 25, 2026 to submit your thoughts and wishlist and get a chance to have your very own custom finds-you-jobs branch. For free of course!

Licensed [AGPL-3.0-only](LICENSE); carried upstream portions keep their own notices.
