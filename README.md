# Ireland GMP Board — direct-ATS scraper

A self-updating job board of QA, manufacturing, aseptic, and validation roles at
Ireland's pharma and GMP sites — scraped **directly from each employer's own
careers API** (Workday, SmartRecruiters, Greenhouse), not from a job board's
aggregation. It refreshes itself every morning on GitHub Actions and serves the
board on GitHub Pages, for free, with no server to maintain.

## One-time setup (about 10 minutes)

1. **Create the repo.** Sign in at github.com → click **+** (top right) →
   **New repository** → name it `ireland-gmp-board` → keep it **Public**
   (Pages and unlimited Actions minutes are free on public repos) → Create.

2. **Upload these files.** On the new repo page: **uploading an existing
   file** link → drag the *contents* of this folder in (the `scraper/`,
   `docs/`, and `.github/` folders plus this README) → Commit.
   *If the drag-and-drop won't take the `.github` folder, create the file
   manually: **Add file → Create new file**, type
   `.github/workflows/refresh.yml` as the name, and paste that file's
   contents.*

3. **Run it once.** Repo → **Actions** tab → enable workflows if asked →
   choose **Daily refresh** → **Run workflow**. Wait ~2–4 minutes. The run's
   summary shows how many boards validated and how many Ireland roles it found.

4. **Turn on the web page.** Repo → **Settings → Pages** → under *Build and
   deployment* choose **Deploy from a branch** → branch `main`, folder
   `/docs` → Save. After a minute your board is live at
   `https://<your-username>.github.io/ireland-gmp-board/`.

5. Done — the **Daily refresh** workflow now runs every morning at 05:45 UTC
   on its own. Tell Claude the Pages URL so the daily artifact refresh can
   switch from Indeed to this direct-ATS feed.

## How it works

- `scraper/companies.json` — the registry: each employer's ATS portal, tenant,
  and board token, plus unproven candidates. Adding company #30 is a data
  edit, not a code change.
- `scraper/validate.py` — runs first on every refresh: every board token is
  probed live against its real API and only validated boards are scraped.
- `scraper/scrape.py` — Workday CXS (resolves each tenant's Ireland country
  facet dynamically), SmartRecruiters (`country=ie`), and Greenhouse adapters;
  Ireland-only location filter; multi-label tagging into 5 categories
  (QA/Compliance/Investigation, CAPA/RCA/MSAT, Aseptic Fill-Finish, Drug
  Substance & Drug Product, Validation) plus a strong-CV-fit flag; merge with
  yesterday's data — a closed posting is stamped inactive, never deleted.
  Malformed 200s are retried, never trusted; an all-sources failure keeps
  yesterday's data rather than publishing zeros.
- `docs/index.html` + `docs/data/jobs.json` — the board and its data, served
  by GitHub Pages.
- `.github/workflows/refresh.yml` — the daily schedule.

## Tuning

- **Add or fix a company:** edit `scraper/companies.json` (find a company's
  Workday tenant by opening any of its job ads and reading the
  `xxx.wdN.myworkdayjobs.com/SiteName` URL), commit, and run the workflow.
- **Tags or fit rules:** edit the regex lists in `scraper/common.py`.
- **Schedule:** edit the `cron:` line in `.github/workflows/refresh.yml`.
