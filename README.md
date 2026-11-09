# LungDaily

Automated lung-health news site. Every 3 hours (06:00–21:00 CET) a GitHub Action:

1. **Collects** posts from tracked voices and keyword searches on **X**, **LinkedIn** (via Apify), new **PubMed** papers and **RSS** feeds (WHO, Google News).
2. **Triages** everything with Claude Haiku (score 0–10, category, duplicate check against recent headlines).
3. **Writes** the best items as short sourced articles with Claude Sonnet ("Name: what they said" for Voices).
4. **Fact-checks** each article with a second, independent Claude call plus code checks (verbatim quote, length). Anything that fails is **held**, never published.
5. **Publishes** to a static site on GitHub Pages (article pages with embedded original post, RSS, sitemap, Google News sitemap, NewsArticle structured data).
6. **Shares** each article from the site's X account (quoting the original X post, which notifies the author) and writes a run report (published + held) on the GitHub Actions run page. Telegram posting is built in but switched off (`amplify.telegram_post` in settings).

No server and no database: state lives in `data/` and articles in `content/posts/`, committed back to the repo by the Action.

## Setup (about 30 minutes)

1. **Create a GitHub repo** `lungdaily` (public, unless you have a paid GitHub plan — free Pages needs a public repo) and push this folder.
2. **Turn on Pages:** Settings → Pages → Source: **GitHub Actions**.
3. **Edit config:**
   - `config/settings.yaml`: `base_url`, `custom_domain`, `x_handle`, `contact_email`.
   - `config/voices.yaml`: the people and organisations to follow (X handle and/or LinkedIn URL). This list is the heart of the site, so aim for 200–500.
   - `config/topics.yaml`: keywords, PubMed query, RSS feeds.
4. **Add secrets** (Settings → Secrets and variables → Actions). Only `ANTHROPIC_API_KEY` is required; each source turns itself off when its key is missing.

| Secret | What it's for |
|---|---|
| `ANTHROPIC_API_KEY` | triage, writing, fact-check |
| `X_BEARER_TOKEN` | reading X (recent search; pay-per-use) |
| `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | posting from the site's X account |
| `APIFY_TOKEN` | LinkedIn posts |
| `LINKEDIN_LI_AT` | optional, LinkedIn keyword search only (cookie of a **separate** LinkedIn account) |
| `LINKEDIN_ORG_ID` | numeric id of the LungDaily LinkedIn page (posting) |
| `LINKEDIN_REFRESH_TOKEN`, `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | posting to the LinkedIn page (preferred: token renews itself) |
| `LINKEDIN_ACCESS_TOKEN` | alternative to the three above; expires after 60 days |
| `MAKE_LINKEDIN_WEBHOOK` | Make.com webhook URL that posts to the LungDaily LinkedIn page |
| `NCBI_API_KEY` | optional, faster PubMed |
| `TELEGRAM_BOT_TOKEN` | bot that posts to the channel and reports to you |
| `TELEGRAM_CHANNEL` | e.g. `@lungdaily` (bot must be channel admin) |
| `TELEGRAM_ADMIN_CHAT_ID` | your Telegram user id, for run reports |

5. **First run:** Actions → Pipeline → Run workflow → `all`.
6. **Custom domain** (optional): set `custom_domain`, then add a CNAME record at Namecheap pointing to `<your-github-user>.github.io`.

## LinkedIn

LinkedIn's official API cannot read or search other people's posts, so the only automated route is a third-party scraper. This project uses Apify:

- **Profile tracking (on by default):** `scrapemint/linkedin-profile-posts-scraper` reads public posts from the LinkedIn URLs in `voices.yaml`. No LinkedIn login. You pay only Apify platform usage.
- **Keyword search (off by default):** `crawlerbros/linkedin-post-search-scraper` searches posts by keyword, from about $3 per 1,000 results. It needs a LinkedIn session cookie (`li_at`). Use a separate LinkedIn account, never your own: LinkedIn's terms forbid scraping, and the account can be restricted. Cookies expire, so expect to refresh this one from time to time.

Both actors can be swapped with `APIFY_PROFILE_ACTOR` / `APIFY_SEARCH_ACTOR`. The normaliser accepts the field names most LinkedIn actors use.

You can also add posts by hand: if someone sends you a post, add their LinkedIn URL to `voices.yaml`.

### Posting to the LungDaily LinkedIn page via Make.com (in use)

After each deploy, the pipeline sends new articles (published in the last 36 h, max 8 a day) to a Make.com webhook. Make downloads the share card and creates a Company Image Post on the LungDaily page. Make already has LinkedIn's approval for page posting, so no LinkedIn developer app review is needed.

Payload fields: `text` (headline, summary, source, "Read more" link, hashtags), `text_escaped` (same, LinkedIn-escaped), `image_url`, `url`, `title`, `summary`, `slug`, `category`.

Setup: Make scenario = **Webhooks › Custom webhook** → **HTTP › Download a file** (`image_url`) → **LinkedIn › Create a Company Image Post** (LungDaily page, text = `text`, image = the downloaded file). Put the webhook URL in the GitHub secret `MAKE_LINKEDIN_WEBHOOK`. To let Make learn the data structure, run the workflow with `linkedin-test` (sends the newest article once, marks nothing).

**Tagging organisations on LinkedIn:** add `linkedin_id` (the page's numeric id) to a voice in `config/voices.yaml`, and optionally `linkedin_name` (the exact page name). The post then tags the source organisation ("Source: @…") and up to 2 more organisations named in the article ("Featuring: @…"). LinkedIn's API cannot tag individual people. To find a page's id: open the page, click "See all … employees", and copy the number in the address after `currentCompany=%5B%22`.

### Posting via our own LinkedIn app (alternative; needs LinkedIn approval of a registered business)

Each new article is shared on the LungDaily company page (headline, summary, source credit, up to 3 hashtags, and a link card with the branded image). It stays switched off until the secrets exist. Setup:

1. Create the **LungDaily** company page on LinkedIn (you must be its admin).
2. At https://www.linkedin.com/developers/apps create an app, associate it with the LungDaily page, and verify the page.
3. In the app's **Products** tab request **Community Management API** (LinkedIn reviews this; it can take days or weeks).
4. When approved, generate a token with the `w_organization_social` scope (Auth tab → OAuth 2.0 tools), signed in as the page admin. Copy the access token and, if offered, the refresh token.
5. Add GitHub secrets: `LINKEDIN_ORG_ID` (the number in the page admin URL `linkedin.com/company/<number>/admin`), and either `LINKEDIN_REFRESH_TOKEN` + `LINKEDIN_CLIENT_ID` + `LINKEDIN_CLIENT_SECRET` (preferred) or just `LINKEDIN_ACCESS_TOKEN` (renew every 60 days).

Settings: `amplify.linkedin_post`, `linkedin_hashtags`, `linkedin_api_version` in `config/settings.yaml`. Failed posts retry on the next 3 runs; the reason is in the run log.

## Pictures

- **Articles from an X post** show the original post (author photo, name, text, any image) via X's official embed: at the top of the article page (lists keep the branded thumbnail). On X, LungDaily quotes the original post, so X shows it too.
- **Articles from a LinkedIn post** are reposted on the LungDaily LinkedIn page (`amplify.linkedin_reshare_source`), so LinkedIn shows the original author and visual.
- **Your own picture for an article:** put it in `content/images/` named after the article slug, e.g. `content/images/2026-11-08-some-article.jpg` (jpg, png or webp, ideally at least 1200x630). The next build uses it as the article picture, the list thumbnail and the share card (with the LungDaily branding and headline on top). Use only pictures you have the right to use. Already-sent X posts keep their old card.
- **Picture from a URL:** Actions → Pipeline → Run workflow → `add-image`, with the article slug, `image_url` (direct link to the file) and optionally `image_credit` (shown under the picture, e.g. `NASA Worldview`). It downloads the picture into `content/images/` and rebuilds. Optional credit lines live in `content/images/<slug>.credit.txt`.

## Publishing schedule

Articles go out one at a time at fixed slots (`publishing.slots` in `config/settings.yaml`, Europe/Berlin time):
07:00, 09:00, 10:00, 10:30, 11:00, 11:30, 11:45, 14:00, 15:00, 15:30, 16:00, 16:30, 16:45, 17:30, 18:00, 21:00.

- **Write runs** (06:07, 09:07, 12:07, 15:07, 18:07, 21:07) collect, triage, write and fact-check, and put passing articles in a ready queue (`status: ready` in `content/posts/`, not on the site). They top the queue up to `ready_buffer` (6).
- **Publish runs** (one per slot) publish ONE ready article — best score first, avoiding the same author twice in a row — then rebuild, post on X and send to LinkedIn. No Claude cost.
- Catch-up: if a GitHub run is late or skipped, the next run publishes the missed slot (still one per run). Ready articles older than 36 h expire.
- GitHub cron is UTC: the slot crons in `pipeline.yml` are written for CET (UTC+1). When summer time starts (last Sunday of March) shift them one hour earlier, or the slots run an hour late.
- To go back to publishing straight away, delete `slots` from settings.yaml.

## Day-to-day

- **Nothing is required day to day:** the pipeline runs, publishes and reports to you on Telegram.
- **Release a held article:** Actions → Pipeline → Run workflow → `release` + the slug from the report. Use `discard` to drop it.
- **Someone opts out:** add their handle, URL or name to `config/optout.yaml`. Their posts are no longer collected.
- **Take an article down:** add its slug to `takedown` in `config/optout.yaml`, then run `build`.
- **Volume and cost:** tune `config/settings.yaml` → `limits`. The default is up to 4 articles per run and 12 per day.
- **Stricter mode:** set `publishing.auto_publish: false` and everything waits for your release.

## Cost guide (monthly, at default settings)

- **Claude:** roughly $20–60. Haiku triages everything; Sonnet writes and checks about 12 articles a day.
- **X reads:** this is the cost to watch. You are billed per post read, so keep `x_max_queries_per_run` and `x_max_results_per_query` modest. If the account runs out of credits you get a 402 error, logged as "credits exhausted".
- **Apify:** a few dollars for profile tracking of about 100 voices.
- **GitHub Pages and Actions:** free.

## Local commands

```bash
pip install -r requirements.txt
python tests/test_offline.py          # full offline test, fake LLM, no keys needed
python -m lungdaily.run all                 # real run (needs env vars)
python -m lungdaily.run build               # rebuild site into ./site
python -m lungdaily.run release <slug>
```

## Layout

```
config/        settings, voices, topics, opt-outs
lungdaily/sources/   x.py, linkedin.py, pubmed.py, rss.py
lungdaily/           collect.py, process.py (triage/write/check), build.py, amplify.py, llm.py, run.py
templates/     site HTML (Jinja2)   static/  CSS
data/          pipeline state (inbox, seen ids, held queue)
content/posts/ published articles (JSON)
```

## Rules built in

- Every article links to its source, and social posts are embedded rather than copied.
- Quotes must be verbatim, and the code verifies this.
- No @mentions in automated X posts, because X's automation rules restrict them. Authors are notified through a quote-post instead.
- No AI-generated images of real people.
- Opt-out and takedown are honoured on every run.
