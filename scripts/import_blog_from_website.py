import base64
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
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
            image = {}

            for attr in [
                "src",
                "data-src",
                "data-original",
                "data-lazy-src",
                "srcset",
                "data-srcset",
                "alt",
                "title",
            ]:
                value = attrs_dict.get(attr)

                if value:
                    image[attr] = value.strip()

            if image:
                self.images.append(image)

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


def first_url_from_srcset(value: str) -> str:
    if not value:
        return ""

    parts = [part.strip() for part in value.split(",") if part.strip()]

    if not parts:
        return ""

    # Usually the last srcset item is the largest image.
    return parts[-1].split()[0].strip()


def image_url_from_item(item: dict, page_url: str) -> str:
    for attr in [
        "src",
        "data-src",
        "data-original",
        "data-lazy-src",
    ]:
        value = item.get(attr)

        if value:
            return urljoin(page_url, value)

    for attr in [
        "srcset",
        "data-srcset",
    ]:
        value = item.get(attr)
        candidate = first_url_from_srcset(value)

        if candidate:
            return urljoin(page_url, candidate)

    return ""


def decode_pixpa_original_key(image_url: str) -> str:
    """
    Pixpa image URLs usually end with a base64-like encoded original file path.
    Decoding it lets us identify the original filename, for example:
    amsterdam-airport-001.jpg
    This helps remove duplicates and exclude Latest Posts images.
    """
    try:
        path = urlparse(image_url).path.rstrip("/")
        encoded = unquote(path.split("/")[-1])

        if not encoded:
            return image_url.lower()

        padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("utf-8")).decode(
            "utf-8",
            errors="ignore",
        )

        if decoded:
            return decoded.lower()

    except Exception:
        pass

    return image_url.lower()


def extract_page_images(parser: BlogPageParser, page_url: str) -> list[str]:
    images = []
    seen_keys = set()

    for item in parser.images:
        image_url = image_url_from_item(item, page_url)

        if not image_url:
            continue

        key = decode_pixpa_original_key(image_url)

        if key in seen_keys:
            continue

        seen_keys.add(key)
        images.append(image_url)

    return images


def extract_cover_image(parser: BlogPageParser, page_url: str) -> str:
    candidates = [
        parser.meta.get("og:image"),
        parser.meta.get("twitter:image"),
    ]

    candidates.extend(extract_page_images(parser, page_url))

    for candidate in candidates:
        if candidate:
            return urljoin(page_url, candidate)

    return ""


def has_latest_posts_tail(image_urls: list[str]) -> bool:
    """
    Pixpa blog pages currently append the same Latest Posts image block
    at the end of each blog page:
    - amsterdam-airport-002
    - modena-001
    - 812-super-005

    These are navigation/recommendation images, not article body images.
    """
    if len(image_urls) < 4:
        return False

    tail_keys = [
        decode_pixpa_original_key(image_url)
        for image_url in image_urls[-3:]
    ]

    tail_text = "\n".join(tail_keys)

    return (
        "amsterdam-airport-002" in tail_text
        and "modena-001" in tail_text
        and "812-super-005" in tail_text
    )


def extract_article_images(parser: BlogPageParser, page_url: str) -> list[str]:
    all_images = extract_page_images(parser, page_url)

    if has_latest_posts_tail(all_images):
        return all_images[:-3]

    return all_images


def extract_body_images(
    parser: BlogPageParser,
    page_url: str,
    slug: str,
    title: str,
    cover_image_url: str,
) -> list[str]:
    article_images = extract_article_images(parser, page_url)
    body_images = []

    cover_key = decode_pixpa_original_key(cover_image_url) if cover_image_url else ""

    for image_url in article_images:
        image_key = decode_pixpa_original_key(image_url)

        if cover_key and image_key == cover_key:
            continue

        body_images.append(image_url)

    return body_images


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
        "item",
        "book a session",
        "copied",
    }

    stop_lines = {
    "tags:",
    "share",
    "next post",
    "latest posts",
    "follow me",
}

    cleaned_lines = []
    previous = None
    title_lower = title.lower().strip()

    for line in lines:
        line = normalize_text(line)
        lower = line.lower()

        if not line:
            continue

        if lower in stop_lines:
            break

        if lower.startswith("please enable javascript"):
            break

        if lower in skip_lines:
            continue

        if lower == title_lower:
            continue

        if lower.startswith("http://") or lower.startswith("https://"):
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


def build_markdown_body(title, body_lines, cover_image_url, body_image_urls):
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

    if body_image_urls:
        lines.append("## Photos")
        lines.append("")

        for index, image_url in enumerate(body_image_urls, start=1):
            lines.append(f"![{title} photo {index}]({image_url})")
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
    body_image_urls = extract_body_images(
        parser=parser,
        page_url=url,
        slug=slug,
        title=title,
        cover_image_url=cover_image_url,
    )

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
        body_image_urls=body_image_urls,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_path = OUTPUT_DIR / f"{slug}.md"
    output_path.write_text(
        "\n".join(front_matter) + markdown_body,
        encoding="utf-8"
    )

    print(
        f"Imported draft: {output_path.relative_to(REPO_ROOT)} "
        f"with {1 if cover_image_url else 0} cover image "
        f"and {len(body_image_urls)} body images"
    )


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
