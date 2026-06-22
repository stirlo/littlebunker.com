#!/usr/bin/env python3
"""
lb_pipeline.py — LittleBunker.com Harold pipeline
Part of the harold_run.py ecosystem on Harold (Oracle Cloud ARM).

Replaces all GitHub Actions workflows:
  - 1-fetch-rss-enhanced.yml  (RSS fetch)
  - 2-process-rss-enhanced.yml (post creation)
  - bluesky-post.yml          (Bluesky publishing)
  - expire-old-posts.yml      (post expiry)

Usage (called from harold_run.py or standalone):
  python3 lb_pipeline.py [--fetch] [--process] [--bluesky] [--expire] [--build] [--deploy] [--all]

Requires (on Harold):
  pip install feedparser pyyaml beautifulsoup4 atproto python-dotenv

Environment (from .env in repo root):
  BLUESKY_HANDLE, BLUESKY_PASSWORD,
  CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, CF_PAGES_PROJECT_NAME,
  LB_REPO_PATH, JEKYLL_ENV, LOG_LEVEL
"""

import argparse
import feedparser
import glob
import hashlib
import html as _html
import json
import logging
import os
import re
import subprocess
import sys
import unicodedata
import yaml
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Load .env from repo root ───────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # dotenv optional; set env vars manually if not installed

# ── Logging ────────────────────────────────────────────────────────────────────
log_level = os.getenv("LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("lb_pipeline")

# ── Paths ──────────────────────────────────────────────────────────────────────
# REPO_ROOT resolves to the repo root (parent of scripts/)
REPO_ROOT = Path(os.getenv("LB_REPO_PATH", Path(__file__).parent.parent))
POSTS_DIR = REPO_ROOT / "_posts"
DATA_DIR = REPO_ROOT / "_data"
FEEDS_DIR = DATA_DIR / "feeds"
TRACKING_DIR = DATA_DIR / "rss_tracking"
RSS_SOURCES_DIR = REPO_ROOT / "rss-sources"
SITE_DIR = REPO_ROOT / os.getenv("JEKYLL_SITE_DIR", "_site")
POSTED_URLS_FILE = TRACKING_DIR / "posted_urls.json"
BLUESKY_TRACKING_FILE = DATA_DIR / "posted_to_bluesky.json"


# ══════════════════════════════════════════════════════════════════════════════
# SANITISATION HELPERS
# Ported from the GHA workflow 2 — battle-tested against YAML frontmatter bugs.
# ══════════════════════════════════════════════════════════════════════════════

def sanitise_for_yaml(text: str) -> str:
    """
    Clean arbitrary text for safe embedding in YAML double-quoted scalars.
    Rules:
      - Decode HTML entities
      - Normalise Unicode to NFC
      - Smart/curly quotes -> straight single quote
      - Straight double-quote -> single-quote (safe inside double-quoted YAML)
      - Strip all backslashes (avoid double-escape from prior runs)
      - Neutralise Jekyll/Liquid syntax {{ }} / {% %}
      - Strip control characters
      - Collapse whitespace
    """
    if not text:
        return ""
    text = _html.unescape(str(text))
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u201c", "'").replace("\u201d", "'")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace('"', "'")
    text = re.sub(r"\\+", "", text)
    text = text.replace("\\'", "'").replace('\\"', "'")
    text = text.replace("{{", "{ {").replace("}}", "} }")
    text = text.replace("{%", "{ %").replace("%}", "% }")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sanitise_slug(title: str) -> str:
    """
    Derive a clean URL slug from title.
    Strips apostrophes/quotes before hyphenating so "Alzheimer's" -> "alzheimers".
    """
    text = _html.unescape(str(title))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[\u2018\u2019\u201c\u201d'\"]", "", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def clean_content(content: str) -> str:
    """Strip HTML tags then sanitise for YAML."""
    content = re.sub(r"<[^>]+>", "", str(content))
    return sanitise_for_yaml(content)


# ══════════════════════════════════════════════════════════════════════════════
# SMART TAGGER
# ══════════════════════════════════════════════════════════════════════════════

class SmartTagger:
    """Keyword-to-tag mapping for climate content."""

    TAG_MAPPINGS = {
        "hurricane":             ["hurricane", "tropical-storms", "extreme-weather"],
        "typhoon":               ["typhoon", "tropical-storms", "extreme-weather"],
        "cyclone":               ["cyclone", "tropical-storms", "extreme-weather"],
        "drought":               ["drought", "water-crisis", "extreme-weather"],
        "flood":                 ["flooding", "extreme-weather", "disasters"],
        "flooding":              ["flooding", "extreme-weather", "disasters"],
        "wildfire":              ["wildfires", "extreme-weather", "forest-fires"],
        "heatwave":              ["heatwave", "extreme-weather", "temperature-records"],
        "heat wave":             ["heatwave", "extreme-weather", "temperature-records"],
        "arctic":                ["arctic", "polar-regions", "ice-melt"],
        "antarctic":             ["antarctica", "polar-regions", "ice-melt"],
        "glacier":               ["glaciers", "ice-loss", "climate-indicators"],
        "permafrost":            ["permafrost", "arctic", "tipping-points"],
        "sea level":             ["sea-level-rise", "coastal-impacts", "ocean-changes"],
        "ocean acidification":   ["ocean-acidification", "marine-impacts", "carbon-cycle"],
        "el nino":               ["el-nino", "weather-patterns", "pacific"],
        "la nina":               ["la-nina", "weather-patterns", "pacific"],
        "agriculture":           ["agriculture", "food-security", "farming"],
        "migration":             ["climate-migration", "displacement", "refugees"],
        "renewable":             ["renewable-energy", "clean-energy", "solutions"],
        "solar":                 ["solar-power", "renewable-energy", "clean-tech"],
        "fossil fuel":           ["fossil-fuels", "emissions", "oil-gas"],
        "paris agreement":       ["paris-agreement", "climate-policy", "international"],
        "cop29":                 ["cop29", "climate-summit", "negotiations"],
        "cop30":                 ["cop30", "climate-summit", "negotiations"],
        "ipcc":                  ["ipcc", "climate-science", "assessments"],
        "net zero":              ["net-zero", "climate-targets", "policy"],
    }

    URGENCY_KEYWORDS = [
        "breaking", "urgent", "emergency", "crisis", "unprecedented",
        "record", "extreme", "catastrophic", "devastating",
    ]

    CATEGORY_DEFAULTS = {
        "climate-science":     ["climate-science", "research"],
        "extreme-weather":     ["extreme-weather", "weather-events"],
        "environmental-news":  ["environment", "news"],
        "social-impact":       ["social-impact", "communities"],
        "research-papers":     ["research", "studies", "peer-reviewed"],
        "media":               ["media-coverage", "climate-communication"],
    }

    def extract_tags(self, title: str, content: str, category: str) -> list:
        tags = set(self.CATEGORY_DEFAULTS.get(category, []))
        full_text = f"{title} {content}".lower()
        for keyword, tag_list in self.TAG_MAPPINGS.items():
            if keyword in full_text:
                tags.update(tag_list[:2])
        for urgency_word in self.URGENCY_KEYWORDS:
            if urgency_word in full_text:
                tags.add("urgent")
                break
        year_match = re.search(r"202[3-9]", full_text)
        if year_match:
            tags.add(f"year-{year_match.group()}")
        return list(tags)[:10]

    def generate_seo_description(self, title: str, content: str, max_length: int = 160) -> str:
        clean = re.sub(r"<[^>]+>", "", content)
        clean = re.sub(r"\s+", " ", clean).strip()
        for sentence in re.split(r"[.!?]", clean)[:3]:
            if 50 < len(sentence) < max_length:
                return sentence.strip()
        return (clean[:max_length - 3] + "...") if len(clean) > max_length else (clean or title)

    def extract_keywords(self, title: str, content: str, tags: list, max_keywords: int = 10) -> list:
        keywords = set(tags[:5])
        title_words = re.findall(r"\b[a-z]{4,}\b", title.lower())
        stop = {"this", "that", "with", "from", "have", "been"}
        keywords.update(w for w in title_words[:3] if w not in stop)
        return list(keywords)[:max_keywords]


# ══════════════════════════════════════════════════════════════════════════════
# POSTED URL TRACKING
# ══════════════════════════════════════════════════════════════════════════════

def load_posted_urls() -> set:
    """Return set of already-posted external URLs."""
    try:
        if POSTED_URLS_FILE.exists():
            data = json.loads(POSTED_URLS_FILE.read_text())
            return set(data.get("posted_urls", []))
    except Exception as e:
        log.warning(f"Could not load posted URLs: {e}")
    return set()


def save_posted_urls(urls: set) -> None:
    """Persist the set of posted URLs (keep last 2000)."""
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    trimmed = list(urls)[-2000:]
    POSTED_URLS_FILE.write_text(json.dumps({"posted_urls": trimmed}, indent=2))


# ══════════════════════════════════════════════════════════════════════════════
# DATE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

# Max article age per category (hours)
CATEGORY_MAX_AGE = {
    "extreme-weather":    24,
    "environmental-news": 48,
    "research-papers":   168,
    "climate-science":    72,
    "social-impact":      96,
    "media":              72,
}

# Max posts created per feed per run per category
CATEGORY_POST_LIMITS = {
    "extreme-weather":    3,
    "environmental-news": 2,
    "research-papers":    1,
    "climate-science":    2,
    "social-impact":      2,
    "media":              1,
}


def is_recent(entry, category: str) -> bool:
    """Return True if the entry falls within the category freshness window."""
    max_hours = CATEGORY_MAX_AGE.get(category, 72)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hours)
    try:
        parsed = getattr(entry, "published_parsed", None)
        if parsed:
            article_date = datetime(*parsed[:6], tzinfo=timezone.utc)
            return article_date > cutoff
    except Exception:
        pass
    return True  # if we can't parse the date, allow it through


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — RSS FETCH
# ══════════════════════════════════════════════════════════════════════════════

def load_rss_sources() -> dict:
    """
    Read all rss-sources/*.txt files.
    Returns dict of {category: [url, ...]}
    """
    sources = {}
    for txt_file in sorted(RSS_SOURCES_DIR.glob("*.txt")):
        category = txt_file.stem
        urls = []
        for line in txt_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
        if urls:
            sources[category] = urls
            log.info(f"Loaded {len(urls)} feeds for category: {category}")
    return sources


def fetch_all_feeds(sources: dict) -> None:
    """
    Fetch every RSS feed and write JSON blobs to _data/feeds/.
    One JSON file per feed URL.
    """
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    total_ok = total_fail = 0

    for category, urls in sources.items():
        log.info(f"Fetching category: {category.upper()} ({len(urls)} feeds)")
        for url in urls:
            try:
                feed = feedparser.parse(url)
                if not feed or not hasattr(feed, "entries"):
                    raise ValueError("No entries")

                items = []
                for entry in feed.entries[:20]:
                    items.append({
                        "title":       sanitise_for_yaml(entry.get("title", "")),
                        "link":        entry.get("link", ""),
                        "pubDate":     entry.get("published", ""),
                        "description": sanitise_for_yaml(entry.get("summary", "")),
                        "category":    category,
                        "source_url":  url,
                    })

                feed_data = {
                    "title":       sanitise_for_yaml(feed.feed.get("title", "Unknown Feed")),
                    "link":        feed.feed.get("link", url),
                    "description": sanitise_for_yaml(feed.feed.get("description", "")),
                    "category":    category,
                    "source_url":  url,
                    "fetched_at":  datetime.now().isoformat(),
                    "items":       items,
                }

                # Deterministic filename derived from URL
                safe = re.sub(r"[^a-z0-9]+", "-", url.lower())[:60]
                out_path = FEEDS_DIR / f"{category}-{safe}.json"
                out_path.write_text(json.dumps(feed_data, indent=2, ensure_ascii=False))
                log.info(f"  Fetched {len(items)} items from {feed.feed.get('title', url)[:50]}")
                total_ok += 1

            except Exception as e:
                log.warning(f"  Failed {url}: {e}")
                total_fail += 1

    log.info(f"Fetch complete — {total_ok} ok, {total_fail} failed")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — PROCESS FEEDS -> _posts/*.md
# ══════════════════════════════════════════════════════════════════════════════

def process_feeds(sources: dict) -> int:
    """
    Read RSS feeds directly and create Jekyll _posts/*.md files.
    Returns count of new posts created.
    """
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    tagger = SmartTagger()
    posted_urls = load_posted_urls()
    total_new = 0

    for category, urls in sources.items():
        log.info(f"Processing category: {category.upper()}")
        for url in urls:
            try:
                feed = feedparser.parse(url)
                if not feed or not hasattr(feed, "entries"):
                    continue

                max_posts = CATEGORY_POST_LIMITS.get(category, 2)
                created_this_feed = 0

                for entry in feed.entries[:15]:
                    if created_this_feed >= max_posts:
                        break

                    link = entry.get("link", "")
                    if not link or link in posted_urls:
                        continue

                    if not is_recent(entry, category):
                        continue

                    title = sanitise_for_yaml(entry.get("title", "No Title"))

                    # Date handling
                    try:
                        p = entry.published_parsed
                        date_str = datetime(*p[:6]).strftime("%Y-%m-%d %H:%M:%S") + " +0000"
                        date_prefix = datetime(*p[:6]).strftime("%Y-%m-%d")
                    except Exception:
                        now = datetime.now()
                        date_str = now.strftime("%Y-%m-%d %H:%M:%S") + " +0000"
                        date_prefix = now.strftime("%Y-%m-%d")

                    # Content
                    if hasattr(entry, "content") and entry.content:
                        raw_content = entry.content[0].value
                    else:
                        raw_content = entry.get("summary", "")
                    content = clean_content(raw_content)

                    tags = tagger.extract_tags(title, content, category)
                    seo_desc = sanitise_for_yaml(
                        tagger.generate_seo_description(title, content)
                    )
                    keywords = tagger.extract_keywords(title, content, tags)
                    source_title = sanitise_for_yaml(feed.feed.get("title", "Unknown Source"))

                    url_hash = hashlib.md5(link.encode()).hexdigest()[:8]
                    slug = sanitise_slug(title)[:40]
                    filename = POSTS_DIR / f"{date_prefix}-{category}-{slug}-{url_hash}.md"

                    if filename.exists():
                        continue

                    post = f"""---
layout: feed_item
title: "{title}"
date: {date_str}
categories: [{category}]
tags: {json.dumps(tags)}
keywords: {json.dumps(keywords)}
description: "{seo_desc}"
external_url: {link}
is_feed: true
source_feed: "{source_title}"
feed_category: "{category}"
---

{content}

[Read original article]({link})
"""
                    filename.write_text(post, encoding="utf-8")
                    posted_urls.add(link)
                    created_this_feed += 1
                    total_new += 1
                    log.info(f"  Created: {title[:60]}")

            except Exception as e:
                log.warning(f"  Error processing {url}: {e}")

    save_posted_urls(posted_urls)
    log.info(f"Process complete — {total_new} new posts created")
    return total_new


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — EXPIRE OLD POSTS
# Weekly — matches expire-old-posts.yml logic
# ══════════════════════════════════════════════════════════════════════════════

EXPIRY_RULES = {
    "environmental-news": 180,
    "environmental_news": 180,
}


def expire_old_posts() -> int:
    """
    Delete posts older than their expiry threshold.
    Returns count of deleted files.
    """
    cutoff_map = {
        cat: (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        for cat, days in EXPIRY_RULES.items()
    }
    deleted = 0

    for post_path in sorted(POSTS_DIR.glob("*.md")):
        content = post_path.read_text(encoding="utf-8", errors="replace")
        parts = content.split("---", 2)
        if len(parts) < 3:
            continue
        try:
            fm = yaml.safe_load(parts[1]) or {}
        except Exception:
            continue

        cats = fm.get("categories", [])
        if isinstance(cats, str):
            cats = [cats]
        cat_set = {c.replace("-", "_") for c in cats}

        for rule_cat, cutoff in cutoff_map.items():
            rule_norm = rule_cat.replace("-", "_")
            if rule_norm in cat_set and post_path.name[:10] < cutoff:
                log.info(f"Expiring: {post_path.name}")
                post_path.unlink()
                deleted += 1
                break

    log.info(f"Expiry complete — {deleted} posts removed")
    return deleted


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — DEDUPLICATE POSTS
# Consolidated from dupes.yml + remove_duplicate_MD_posts.yml
# Keeps oldest file per external_url (most stable slug).
# ══════════════════════════════════════════════════════════════════════════════

def deduplicate_posts() -> int:
    """
    Scan _posts/ for duplicate external_url values.
    Keeps oldest, removes the rest.
    Returns count removed.
    """
    url_to_files = defaultdict(list)

    for post_path in POSTS_DIR.glob("*.md"):
        try:
            content = post_path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^external_url:\s*(.+)$", content, re.MULTILINE)
            if m:
                url = m.group(1).strip()
                url_to_files[url].append(post_path)
        except Exception as e:
            log.warning(f"Could not read {post_path.name}: {e}")

    removed = 0
    for url, files in url_to_files.items():
        if len(files) > 1:
            files.sort(key=lambda p: p.name)  # YYYY-MM-DD prefix = chronological
            for duplicate in files[1:]:
                log.info(f"Removing duplicate: {duplicate.name}")
                duplicate.unlink()
                removed += 1

    log.info(f"Dedup complete — {removed} duplicates removed")
    return removed


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — JEKYLL BUILD
# ══════════════════════════════════════════════════════════════════════════════

def build_site() -> bool:
    """
    Run `bundle exec jekyll build` in the repo root.
    Returns True on success.
    """
    env = os.environ.copy()
    env["JEKYLL_ENV"] = os.getenv("JEKYLL_ENV", "production")

    log.info("Building Jekyll site...")
    result = subprocess.run(
        ["bundle", "exec", "jekyll", "build", "--incremental"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        log.error(f"Jekyll build failed:\n{result.stderr}")
        return False

    log.info(f"Jekyll build complete — output in {SITE_DIR}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — DEPLOY TO CLOUDFLARE PAGES
# ══════════════════════════════════════════════════════════════════════════════

def deploy_to_cf_pages() -> bool:
    """
    Deploy _site/ to Cloudflare Pages via wrangler direct upload.
    Returns True on success.
    """
    token = os.getenv("CLOUDFLARE_API_TOKEN")
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    project = os.getenv("CF_PAGES_PROJECT_NAME", "littlebunker")

    if not token or not account_id:
        log.error("CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID must be set in .env")
        return False

    env = os.environ.copy()
    env["CLOUDFLARE_API_TOKEN"] = token

    log.info(f"Deploying to CF Pages project: {project}")
    result = subprocess.run(
        [
            "wrangler", "pages", "deploy", str(SITE_DIR),
            "--project-name", project,
            "--commit-dirty=true",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        log.error(f"CF Pages deploy failed:\n{result.stderr}")
        return False

    log.info(f"Deploy complete:\n{result.stdout.strip()}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — BLUESKY PUBLISHING
# Ported from bluesky-post.yml with URL facet fix (clickable links).
# ══════════════════════════════════════════════════════════════════════════════

def load_bluesky_tracking() -> dict:
    try:
        if BLUESKY_TRACKING_FILE.exists():
            return json.loads(BLUESKY_TRACKING_FILE.read_text())
    except Exception:
        pass
    return {"posted_entries": [], "last_climate_post": 0}


def save_bluesky_tracking(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data["posted_entries"] = data["posted_entries"][-50:]
    BLUESKY_TRACKING_FILE.write_text(json.dumps(data, indent=2))


def post_to_bluesky(text: str) -> bool:
    """
    Post text to Bluesky with proper URL facets so links are clickable.
    Requires: pip install atproto
    """
    handle = os.getenv("BLUESKY_HANDLE")
    password = os.getenv("BLUESKY_PASSWORD")

    if not handle or not password:
        log.error("BLUESKY_HANDLE and BLUESKY_PASSWORD must be set in .env")
        return False

    try:
        from atproto import Client
        client = Client()
        client.login(handle, password)

        # Build facets so URLs render as clickable links in Bluesky
        url_pattern = re.compile(r"https?://[^\s]+")
        facets = []
        for match in url_pattern.finditer(text):
            byte_start = len(text[:match.start()].encode("utf-8"))
            byte_end = len(text[:match.end()].encode("utf-8"))
            facets.append({
                "index": {"byteStart": byte_start, "byteEnd": byte_end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": match.group()}],
            })

        if facets:
            client.send_post(text=text, facets=facets)
        else:
            client.send_post(text=text)

        log.info(f"Bluesky posted: {text[:80]}...")
        return True

    except Exception as e:
        log.error(f"Bluesky post failed: {e}")
        return False


def _build_climate_post(metrics: dict, post_type: int) -> str | None:
    """Build one of 3 rotating climate metrics posts."""
    if post_type == 0:
        co2 = metrics.get("co2", {})
        return (
            f"🌍 CO2 Update\n\n"
            f"{co2.get('current', 'N/A')} ppm atmospheric CO2 "
            f"(+{co2.get('change', 'N/A')} ppm/year)\n\n"
            f"Carbon dioxide concentration continues to rise.\n\nhttps://littlebunker.com"
        )
    elif post_type == 1:
        ch4 = metrics.get("ch4", {})
        return (
            f"🔥 Methane Update\n\n"
            f"{ch4.get('current', 'N/A')} ppb atmospheric CH4 "
            f"(+{ch4.get('change', 'N/A')} ppb/year)\n\n"
            f"Methane concentration accelerating climate change.\n\nhttps://littlebunker.com"
        )
    elif post_type == 2:
        temp = metrics.get("temperature", {})
        return (
            f"🌡 Temperature Overshoot\n\n"
            f"+{temp.get('overshoot', 'N/A')}C above pre-industrial baseline\n\n"
            f"Global average temperature tracking above safe limits.\n\nhttps://littlebunker.com"
        )
    return None


def run_bluesky_pipeline() -> None:
    """
    Post one item to Bluesky — alternates between RSS content posts
    and climate metrics posts (every 4th post is a metrics post).
    """
    tracking = load_bluesky_tracking()
    posted_entries = tracking["posted_entries"]
    last_climate_post = tracking.get("last_climate_post", 0)

    total_posts = len(posted_entries)
    should_post_climate = (total_posts % 4 == 0)

    content = None
    entry_id = None
    success = False

    if should_post_climate:
        metrics_path = DATA_DIR / "metrics.yml"
        if metrics_path.exists():
            try:
                metrics = yaml.safe_load(metrics_path.read_text())
                climate_type = last_climate_post % 3
                content = _build_climate_post(metrics, climate_type)
                entry_id = f"climate_{climate_type}_{datetime.now().isoformat()}"
                if content:
                    success = post_to_bluesky(content)
                    if success:
                        tracking["last_climate_post"] = climate_type + 1
            except Exception as e:
                log.warning(f"Climate metrics post failed: {e}")
        else:
            log.info("No metrics.yml — falling through to RSS post")
            should_post_climate = False

    if not should_post_climate or not success:
        feed_url = "https://littlebunker.com/feed.xml"
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                eid = hashlib.md5(entry.get("link", "").encode()).hexdigest()
                if eid in posted_entries:
                    continue

                title = entry.get("title", "").strip()
                link = entry.get("link", "")
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", "") or "")
                summary = re.sub(r"\s+", " ", summary).strip()
                cats = entry.get("tags", [])
                cat = cats[0].get("term", "") if cats else ""

                cat_l = cat.lower().replace("_", "-")
                if any(w in cat_l for w in ["extreme", "weather", "hurricane", "flood", "fire", "drought"]):
                    lead = "⚠️ Extreme Weather"
                elif any(w in cat_l for w in ["research", "paper", "science"]):
                    lead = "🔬 Research"
                elif any(w in cat_l for w in ["social", "impact"]):
                    lead = "👥 Social Impact"
                elif any(w in cat_l for w in ["arctic", "polar", "ice"]):
                    lead = "🧊 Polar"
                else:
                    lead = "🌍 Climate"

                title_line = title[:120] + ("..." if len(title) > 120 else "")
                max_excerpt = 280 - len(lead) - len(title_line) - len(link) - 6
                excerpt = ""
                if summary and len(summary) > 20 and max_excerpt > 40:
                    excerpt = summary[:max_excerpt].rsplit(" ", 1)[0]
                    if len(excerpt) < len(summary[:max_excerpt]):
                        excerpt += "..."

                content = (
                    f"{lead}\n\n{title_line}\n\n{excerpt}\n\n{link}"
                    if excerpt else
                    f"{lead}\n\n{title_line}\n\n{link}"
                )
                if len(content) > 300:
                    content = f"{lead}\n\n{title_line}\n\n{link}"[:300]

                success = post_to_bluesky(content)
                if success:
                    entry_id = eid
                    break
        except Exception as e:
            log.error(f"Bluesky RSS pipeline failed: {e}")

    if success and entry_id:
        tracking["posted_entries"].append(entry_id)
        save_bluesky_tracking(tracking)
        log.info(f"Bluesky tracking updated — total posts: {len(tracking['posted_entries'])}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — CLIMATE DATA FETCHER
# Ported from fetch-climate-data.yml — runs every 2 hours via cron.
# Fetches CO2/CH4 from NOAA, temperature from Climate Reanalyzer,
# population from World Bank. Writes _data/metrics.yml.
# ══════════════════════════════════════════════════════════════════════════════

def fetch_climate_data() -> bool:
    """
    Fetch live climate metrics from NOAA, Climate Reanalyzer, World Bank.
    Writes _data/metrics.yml. Returns True on success.
    Requires: pip install requests
    """
    try:
        import requests
    except ImportError:
        log.error("requests not installed — run: pip install requests --break-system-packages")
        return False

    metrics_path = DATA_DIR / "metrics.yml"

    # Load previous values as fallback
    try:
        if metrics_path.exists():
            metrics = yaml.safe_load(metrics_path.read_text()) or {}
            log.info(f"Loaded previous metrics: CO2 {metrics.get('co2', {}).get('current')} ppm")
        else:
            metrics = {}
    except Exception as e:
        log.warning(f"Could not load previous metrics: {e}")
        metrics = {}

    # Ensure required structure exists with sensible fallbacks
    metrics.setdefault("co2", {"current": 421.5, "change": 2.5})
    metrics.setdefault("ch4", {"current": 1935.0, "change": 6.98})
    metrics.setdefault("temperature", {"current": 1.2, "recent_peak": 1.5})
    metrics.setdefault("population", {"total": 8.1, "growth": 67})

    def validate(new_val, old_val, min_val, max_val, name):
        """Reject values outside range or with >20% change from previous."""
        if not (min_val <= new_val <= max_val):
            log.warning(f"{name}: {new_val} outside range ({min_val}-{max_val}), keeping {old_val}")
            return old_val
        if old_val and old_val > 0:
            if abs((new_val - old_val) / old_val) > 0.20:
                log.warning(f"{name}: change too large ({new_val} vs {old_val}), keeping {old_val}")
                return old_val
        log.info(f"{name}: {old_val} -> {new_val}")
        return new_val

    # 1. CO2 from NOAA weekly Mauna Loa data
    try:
        r = requests.get(
            "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_weekly_mlo.txt",
            timeout=60
        )
        if r.status_code == 200:
            lines = [l for l in r.text.splitlines() if not l.startswith("#") and l.strip()]
            if lines:
                latest = lines[-1].split()
                if len(latest) >= 5:
                    new_co2 = round(float(latest[4]), 1)
                    metrics["co2"]["current"] = validate(
                        new_co2, metrics["co2"]["current"], 350, 500, "CO2"
                    )
                    if len(lines) >= 52:
                        year_ago = lines[-52].split()
                        if len(year_ago) >= 5:
                            metrics["co2"]["change"] = round(
                                metrics["co2"]["current"] - float(year_ago[4]), 1
                            )
    except Exception as e:
        log.warning(f"NOAA CO2 fetch failed: {e}")

    # 2. CH4 from NOAA global monthly mean
    try:
        r = requests.get(
            "https://gml.noaa.gov/webdata/ccgg/trends/ch4/ch4_mm_gl.txt",
            timeout=60
        )
        if r.status_code == 200:
            lines = [l for l in r.text.splitlines() if not l.startswith("#") and l.strip()]
            if lines:
                latest = lines[-1].split()
                if len(latest) >= 4:
                    new_ch4 = round(float(latest[3]), 2)
                    metrics["ch4"]["current"] = validate(
                        new_ch4, metrics["ch4"]["current"], 1800, 2100, "CH4"
                    )
                    if len(lines) >= 12:
                        year_ago = lines[-12].split()
                        if len(year_ago) >= 4:
                            metrics["ch4"]["change"] = round(
                                metrics["ch4"]["current"] - float(year_ago[3]), 2
                            )
    except Exception as e:
        log.warning(f"NOAA CH4 fetch failed: {e}")

    # 3. Temperature from Climate Reanalyzer daily anomaly
    try:
        r = requests.get(
            "https://climatereanalyzer.org/clim/t2_daily/json/cfsr_world_t2_day.json",
            timeout=60
        )
        if r.status_code == 200:
            data = r.json()
            # Adjust from 1979-2000 baseline to 1850-1900 baseline (+0.9C)
            current = round(data[-1]["value"] + 0.9, 2)
            peak = round(max(d["value"] for d in data[-365:]) + 0.9, 2)
            metrics["temperature"]["current"] = validate(
                current, metrics["temperature"]["current"], -2.0, 3.0, "Temp current"
            )
            metrics["temperature"]["recent_peak"] = validate(
                peak, metrics["temperature"]["recent_peak"], -2.0, 3.0, "Temp peak"
            )
    except Exception as e:
        log.warning(f"Climate Reanalyzer fetch failed: {e}")

    # 4. Population from World Bank API
    population_updated = False
    for year in [2024, 2023, 2022]:
        try:
            r = requests.get(
                f"https://api.worldbank.org/v2/country/WLD/indicator/SP.POP.TOTL"
                f"?format=json&date={year}",
                timeout=60
            )
            if r.status_code == 200:
                data = r.json()
                if len(data) > 1 and data[1] and data[1][0].get("value"):
                    new_pop = round(data[1][0]["value"] / 1_000_000_000, 1)
                    metrics["population"]["total"] = validate(
                        new_pop, metrics["population"]["total"], 7.0, 10.0, "Population"
                    )
                    population_updated = True
                    break
        except Exception as e:
            log.warning(f"World Bank population {year} failed: {e}")

    if not population_updated:
        log.info("Population data unchanged — keeping previous value")

    # Write updated metrics
    metrics["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(yaml.dump(metrics, default_flow_style=False, sort_keys=True))

    log.info(
        f"Climate data updated — "
        f"CO2: {metrics['co2']['current']} ppm, "
        f"CH4: {metrics['ch4']['current']} ppb, "
        f"Temp: +{metrics['temperature']['current']}C, "
        f"Peak: +{metrics['temperature']['recent_peak']}C"
    )
    return True


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="LittleBunker Harold pipeline")
    parser.add_argument("--fetch",         action="store_true", help="Step 1: fetch RSS feeds to JSON")
    parser.add_argument("--process",       action="store_true", help="Step 2: create _posts/*.md from feeds")
    parser.add_argument("--expire",        action="store_true", help="Step 3: delete old posts per expiry rules")
    parser.add_argument("--dedup",         action="store_true", help="Step 4: remove duplicate posts")
    parser.add_argument("--build",         action="store_true", help="Step 5: run jekyll build")
    parser.add_argument("--deploy",        action="store_true", help="Step 6: deploy to CF Pages via wrangler")
    parser.add_argument("--bluesky",       action="store_true", help="Step 7: post one item to Bluesky")
    parser.add_argument("--climate-data",  action="store_true", help="Step 8: fetch NOAA/climate metrics -> metrics.yml")
    parser.add_argument("--all",           action="store_true", help="Run all steps in order")
    args = parser.parse_args()

    run_all = args.all
    do_fetch         = run_all or args.fetch
    do_process       = run_all or args.process
    do_expire        = run_all or args.expire
    do_dedup         = run_all or args.dedup
    do_build         = run_all or args.build
    do_deploy        = run_all or args.deploy
    do_bluesky       = run_all or args.bluesky
    do_climate_data  = run_all or args.climate_data

    if not any([do_fetch, do_process, do_expire, do_dedup, do_build, do_deploy, do_bluesky, do_climate_data]):
        parser.print_help()
        sys.exit(0)

    sources = load_rss_sources()

    if do_climate_data:
        fetch_climate_data()
    if do_fetch:
        fetch_all_feeds(sources)
    if do_process:
        process_feeds(sources)
    if do_expire:
        expire_old_posts()
    if do_dedup:
        deduplicate_posts()
    if do_build:
        ok = build_site()
        if not ok and do_deploy:
            log.error("Build failed — aborting deploy")
            sys.exit(1)
    if do_deploy:
        deploy_to_cf_pages()
    if do_bluesky:
        run_bluesky_pipeline()


if __name__ == "__main__":
    main()
