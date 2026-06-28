from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from pathlib import Path
import json


SITE_BASE_URL = "https://www.jeremy.gallery"
BLOG_INDEX_URL = "https://www.jeremy.gallery/blog"

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = REPO_ROOT / "discovered_blog_urls.json"


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return

        attrs_dict = dict(attrs)
        href = attrs_dict.get("href")

        if href:
            self.links.append(href)


def fetch_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 JeremyGalleryBot/1.0"
        }
    )

    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_url(href: str) -> str:
    absolute_url = urljoin(SITE_BASE_URL, href)
    parsed = urlparse(absolute_url)

    clean_url = parsed._replace(
        query="",
        fragment=""
    ).geturl()

    if clean_url.endswith("/"):
        clean_url = clean_url[:-1]

    return clean_url


def is_blog_post_url(url: str) -> bool:
    parsed = urlparse(url)

    if parsed.netloc not in ["www.jeremy.gallery", "jeremy.gallery"]:
        return False

    path = parsed.path.rstrip("/")

    if path == "/blog":
        return False

    return path.startswith("/blog/")


def discover_blog_urls() -> list[str]:
    html = fetch_html(BLOG_INDEX_URL)

    parser = LinkParser()
    parser.feed(html)

    urls = []

    for href in parser.links:
        url = normalize_url(href)

        if is_blog_post_url(url):
            urls.append(url)

    return sorted(set(urls))


def main():
    urls = discover_blog_urls()

    output = {
        "source": BLOG_INDEX_URL,
        "count": len(urls),
        "urls": urls
    }

    OUTPUT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )

    print(f"Discovered blog URLs: {len(urls)}")
    for url in urls:
        print(url)

    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
