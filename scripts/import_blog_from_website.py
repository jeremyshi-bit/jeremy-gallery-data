import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from datetime import datetime, timezone


SITE_BASE_URL = "https://www.jeremy.gallery"
REPO_ROOT = Path(__file__).resolve().parents[1]
DISCOVERED_URLS_FILE = REPO_ROOT / "discovered_blog_urls.json"
OUTPUT_DIR = REPO_ROOT / "posts" / "imported"


class BlogPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title_text = ""
        self.meta = {}
        self.text_lines = []
        self.images = []
        self.tag_stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self.tag_stack.append(tag)
        attrs_dict = dict(attrs)

        if tag == "title":
            self.in_title = True

        if tag == "meta":
            key = attrs_dict.get("property") or attrs_dict.get("name")
            content = attrs_dict.get("content")

            if key and content:
                self.meta[key.lower()] = content.strip()

        if tag == "img":
            src = attrs_dict.get("src") or attrs_dict.get("data-src")

            if src:
                self.images.append(src)

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "title":
            self.in_title = False

        if self.tag_stack:
            self.tag_stack.pop()

    def handle_data(self, data):
        text = normalize_text(data)

        if not text:
            return

        if self.in_title:
            self.title_text += text + " "
            return

        if any(tag in self.tag_stack for tag in ["script", "style", "svg", "nav", "footer"]):
            return

        self.text_lines.append(text)


def fetch_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 JeremyGalleryBot/1.0"
        }
    )

    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1]
    slug = re.sub(r"[^a-zA-Z0-9\-]+", "-", slug).strip("-").lower()
    return slug or "untitled-post"


def clean_title(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"\s*\|\s*Jeremy Gallery\s*$", "", value, flags=re.I)
    value = re.sub(r"\s*-\s*Jeremy Gallery\s*$", "", value, flags=re.I)
    return value.strip()


def title_from_slug(slug: str) -> str:
    return " ".join(word.capitalize() for word in slug.split("-"))


def extract_title(parser: BlogPageParser, slug: str) -> str:
    candidates = [
        parser.meta.get("og:title"),
        parser.meta.get("twitter:title"),
        parser.title_text,
    ]

    for candidate in candidates:
        if candidate:
            title = clean_title(candidate)
            if title:
                return title

    return title_from_slug(slug)


def extract_excerpt(parser: BlogPageParser) -> str:
    candidates = [
        parser.meta.get("og:description"),
        parser.meta.get("description"),
        parser.meta.get("twitter:description"),
    ]

    for candidate in candidates:
        if candidate:
            return normalize_text(candidate)

    for line in parser.text_lines:
        line = normalize_text(line)
        if len(line) >= 40:
            return line[:240]

    return "Imported from Jeremy Gallery."


def extract_cover_image(parser: BlogPageParser, page_url: str) -> str:
    candidates = [
        parser.meta.get("og:image"),
        parser.meta.get("twitter:image"),
    ]

    candidates.extend(parser.images)

    for candidate in candidates:
        if candidate:
            return urljoin(page_url, candidate)

    return ""


def extract_date(parser: BlogPageParser) -> str:
    candidates = [
        parser.meta.get("article:published_time"),
        parser.meta.get("date"),
        parser.meta.get("publishdate"),
    ]

    for candidate in candidates:
        if not candidate:
            continue

        match = re.search(r"\d{4}-\d{2}-\d{2}", candidate)

        if match:
            return match.group(0)

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def clean_body_lines(lines, title):
    skip_lines = {
        "home",
        "blog",
        "portfolio",
        "contact",
        "jeremy gallery",
    }

    cleaned_lines = []
    previous = None
    title_lower = title.lower().strip()

    for line in lines:
        line = normalize_text(line)
        lower = line.lower()

        if not line:
            continue

        if lower in skip_lines:
            continue

        if lower == title_lower:
            continue

        if "powered by" in lower:
            continue

        if "©" in line:
            continue

        if lower == previous:
            continue

        cleaned_lines.append(line)
        previous = lower

    return cleaned_lines


def build_markdown_body(title, body_lines, cover_image_url):
    lines = [f"# {title}", ""]

    if cover_image_url:
        lines.append(f"![{title}]({cover_image_url})")
        lines.append("")

    if body_lines:
        for line in body_lines:
            lines.append(line)
            lines.append("")
    else:
        lines.append("Imported from Jeremy Gallery.")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def safe_front_matter_value(value):
    return str(value).replace("\n", " ").strip()


def create_markdown_file(url: str):
    slug = slug_from_url(url)
    html = fetch_html(url)

    parser = BlogPageParser()
    parser.feed(html)

    title = extract_title(parser, slug)
    excerpt = extract_excerpt(parser)
    cover_image_url = extract_cover_image(parser, url)
    date = extract_date(parser)
    body_lines = clean_body_lines(parser.text_lines, title)

    front_matter = [
        "---",
        f"id: {slug}",
        f"title: {safe_front_matter_value(title)}",
        f"date: {date}",
        f"updatedAt: {date}",
        "author: Jeremy",
        "language: en",
        f"excerpt: {safe_front_matter_value(excerpt)}",
    ]

    if cover_image_url:
        front_matter.append(f"coverImageUrl: {cover_image_url}")

    front_matter.extend(
        [
            f"sourcePageUrl: {url}",
            "tags: Imported, Website",
            "isPublished: false",
            "isFeatured: false",
            "readingMinutes: 2",
            "---",
            "",
        ]
    )

    markdown_body = build_markdown_body(
        title=title,
        body_lines=body_lines,
        cover_image_url=cover_image_url,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_path = OUTPUT_DIR / f"{slug}.md"
    output_path.write_text(
        "\n".join(front_matter) + markdown_body,
        encoding="utf-8"
    )

    print(f"Imported draft: {output_path.relative_to(REPO_ROOT)}")


def main():
    if not DISCOVERED_URLS_FILE.exists():
        raise FileNotFoundError(
            "discovered_blog_urls.json not found. Run discover_blog_urls.py first."
        )

    data = json.loads(DISCOVERED_URLS_FILE.read_text(encoding="utf-8"))
    urls = data.get("urls", [])

    if not urls:
        raise ValueError("No blog URLs found.")

    for url in urls:
        create_markdown_file(url)

    print(f"Imported drafts: {len(urls)}")


if __name__ == "__main__":
    main()
