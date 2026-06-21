# LittleBunker.com — Scripts

## lb_pipeline.py

Harold-side pipeline replacing all GitHub Actions workflows.

### Steps

| Flag | Replaces GHA workflow | Frequency on Harold |
|------|-----------------------|---------------------|
| `--fetch`   | `1-fetch-rss-enhanced.yml`   | Hourly |
| `--process` | `2-process-rss-enhanced.yml` | Hourly |
| `--expire`  | `expire-old-posts.yml`       | Weekly (Sunday) |
| `--dedup`   | `dupes.yml` + `remove_duplicate_MD_posts.yml` | Daily |
| `--build`   | Jekyll step in `site_build_and_update.yml` | Hourly (after process) |
| `--deploy`  | `jekyll.yml` -> CF Pages via wrangler | Hourly (after build) |
| `--bluesky` | `bluesky-post.yml`           | Every 3 hours |

### Usage on Harold

```bash
# Full hourly run (fetch + process + build + deploy)
python3 scripts/lb_pipeline.py --fetch --process --build --deploy

# Bluesky post (separate cron, every 3 hrs)
python3 scripts/lb_pipeline.py --bluesky

# Weekly maintenance (Sunday)
python3 scripts/lb_pipeline.py --expire --dedup

# Everything at once
python3 scripts/lb_pipeline.py --all
```

### Dependencies (install on Harold once)

```bash
pip install feedparser pyyaml beautifulsoup4 atproto python-dotenv
gem install bundler && bundle install
npm install -g wrangler  # for CF Pages deploy
```

### Environment

Copy `.env.example` to `.env` in the repo root and fill in all values.
The script loads `.env` automatically via python-dotenv.
Never commit `.env` — it is already in `.gitignore`.

## fix_frontmatter.py

One-off utility for repairing malformed YAML frontmatter in existing posts.
Run manually if posts were created with the old (pre-sanitiser) pipeline.
