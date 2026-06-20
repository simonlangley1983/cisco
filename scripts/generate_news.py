import json
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf


SYMBOL = "CSCO"
COMPANY = "Cisco"
OUTPUT_FILE = Path("data/cisco_news.json")
MAX_ARTICLES = 10

RELEVANCE_TERMS = [
    "cisco",
    "csco",
    "splunk",
    "networking",
    "cybersecurity",
    "security",
]


def get_nested(data, *keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def is_relevant(title, summary):
    text = f"{title or ''} {summary or ''}".lower()
    return any(term in text for term in RELEVANCE_TERMS)


def extract_thumbnail(content):
    thumbnail = content.get("thumbnail") or {}

    resolutions = thumbnail.get("resolutions") or []
    if resolutions:
        return resolutions[0].get("url")

    return thumbnail.get("originalUrl")


def extract_article(raw_article):
    content = raw_article.get("content", raw_article)

    title = content.get("title")
    summary = content.get("summary") or content.get("description") or ""
    publisher = get_nested(content, "provider", "displayName") or "Yahoo Finance"
    published_utc = content.get("pubDate") or content.get("displayTime")

    canonical_url = get_nested(content, "canonicalUrl", "url")
    yahoo_url = get_nested(content, "clickThroughUrl", "url")
    url = canonical_url or yahoo_url

    thumbnail = extract_thumbnail(content)

    if not title or not url:
        return None

    if not is_relevant(title, summary):
        return None

    return {
        "title": title,
        "summary": summary,
        "publisher": publisher,
        "published_utc": published_utc,
        "url": url,
        "yahoo_url": yahoo_url,
        "thumbnail": thumbnail,
    }


def main():
    ticker = yf.Ticker(SYMBOL)
    raw_news = ticker.news or []

    articles = []
    seen_urls = set()
    seen_titles = set()

    for raw_article in raw_news:
        article = extract_article(raw_article)

        if not article:
            continue

        title_key = article["title"].strip().lower()
        url_key = article["url"].strip().lower()

        if title_key in seen_titles or url_key in seen_urls:
            continue

        seen_titles.add(title_key)
        seen_urls.add(url_key)

        articles.append(article)

        if len(articles) >= MAX_ARTICLES:
            break

    output = {
        "generated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "symbol": SYMBOL,
        "company": COMPANY,
        "article_count": len(articles),
        "articles": articles,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(articles)} articles to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
