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


SKIP_TEXT_LINES = {
    "home",
    "blog",
    "portfolio",
    "contact",
    "jeremy gallery",
}


class BlogPageParser(HTMLParser):
    def __init__(self):
        super().__init__()

        self.in_title = False
        self.current_tag_stack = []

        self.title_tag = ""
        self.meta = {}
        self.text_lines = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)
        self.current_tag_stack.append(tag)

        if tag == "title":
            self.in_title = True

        if tag == "meta":
            key = (
                attrs_dict.get("property")
                or attrs_dict.get("name")
                or attrs_dict.get("itemprop")
            )

            content = attrs_dict.get("content")

            if key and content:
                self.meta[key.strip().lower()] = content.strip()

        if tag == "img":
            image_url = (
                attrs_dict.get("src")
                or attrs_dict.get("data-src")
                or attrs_dict.get("data-original")
                or attrs_dict.get("data-lazy-src")
            )

            if not image_url:
                srcset = attrs_dict.get("srcset") or attrs_dict.get("data-srcset")
                image_url = self.first_url_from_srcset(srcset)

            if image_url:
                self.images.append(image_url)

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "title":
            self.in_title = False

        if self.current_tag_stack:
            self.current_tag_stack.pop()

    def handle_data(self, data):
        text = normalize_text(data)

        if not text:
            return

        if self.in_title:
            self.title_tag += text + " "
            return

        if self.should_skip_current_text():
            return

        self.text_lines.append(text)

    def should_skip_current_text(self):
        blocked_tags = {
            "script",
            "style",
            "svg",
            "nav",
            "footer",
            "button",
        }

        return any(tag in blocked_tags for tag in self.current_tag_stack)

    @staticmethod
    def first_url_from_srcset(srcset):
        if not srcset:
            return None

        first_part = srcset.split(",")[0].strip()

        if not first_part:
            return None

        return first_part.split(" ")[0].strip()


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
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1]
    slug = re.sub(r"[^a-zA-Z0-9\-]+", "-", slug).strip("-").lower()
    return slug or "untitled-post"


def clean_title(title: str) -> str:
    title = normalize_text(title)
    title = re.sub(r"\s*\|\s*Jeremy Gallery\s*$", "", title, flags=re.I)
    title = re.sub(r"\s*-\s*Jeremy Gallery\s*$", "", title, flags=re.I)
    return title.strip()


def title_from_slug(slug: str) -> str:
    return " ".join(word.capitalize() for word in slug.split("-"))


def extract_title(parser: BlogPageParser, slug: str) -> str:
    candidates = [
        parser.meta.get("og:title"),
        parser.meta.get("twitter:title"),
        parser.title_tag.strip(),
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
        cleaned = normalize_text(line)

        if len(cleaned) >= 40:
            return cleaned[:240]

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
        parser.meta.get("dc.date"),
    ]

    for candidate in candidates:
        if not candidate:
            continue

        match = re.search(r"\d{4}-\d{2}-\d{2}", candidate)

        if match:
            return match.group(0)

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def clean_body_lines(lines, title):
    cleaned_lines = []
    previous = None

    title_lower = title.lower().strip()

    for line in lines:
        cleaned = normalize_text(line)

        if not cleaned:
            continue

        lower = cleaned.lower()

        if lower in SKIP_TEXT_LINES:
            continue

        if "©" in cleaned:
            continue

        if "powered by" in lower:
            continue

        if lower == previous:
            continue

        if lower == title_lower:
            continue

        cleaned_lines.append(cleaned)
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


def front_matter_value(value):
    if value is None:
        return ""

    value = str(value).replace("\n", " ").strip()
    return value


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

    markdown_body = build_markdown_body(
        title=title,
        body_lines=body_lines,
        cover_image_url=cover_image_url,
    )

    front_matter = [
        "---",
        f"id: {slug}",
        f"title: {front_matter_value(title)}",
        f"date: {date}",
        f"updatedAt: {date}",
        "author: Jeremy",
        "language: en",
        f"excerpt: {front_matter_value(excerpt)}",
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

    markdown_content = "\n".join(front_matter) + markdown_body

    output_path = OUTPUT_DIR / f"{slug}.md"
    output_path.write_text(markdown_content, encoding="utf-8")

    print(f"Imported draft: {output_path.relative_to(REPO_ROOT)}")


def main():
    if not DISCOVERED_URLS_FILE.exists():
        raise FileNotFoundError(
            "discovered_blog_urls.json not found. "
            "Run scripts/discover_blog_urls.py first."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    data = json.loads(DISCOVERED_URLS_FILE.read_text(encoding="utf-8"))
    urls = data.get("urls", [])

    if not urls:
        raise ValueError("No blog URLs found in discovered_blog_urls.json.")

    for url in urls:
        create_markdown_file(url)

    print(f"Imported drafts: {len(urls)}")


if __name__ == "__main__":
    main()
