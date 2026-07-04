import base64
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright


SITE_URL = "https://www.jeremy.gallery"
OUTPUT_JSON = Path("gallery.json")

SITEMAP_URLS = [
    "https://www.jeremy.gallery/sitemap.xml",
    "https://www.jeremy.gallery/sitemap-website.xml",
]

# 测试阶段：先只抓 Milano，确认成功后再改成 False 抓全部相册
TEST_ONLY_MILANO = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

ALBUM_DATE_OVERRIDES = {
    # Optional manual overrides.
    # Use this only if the automatically detected date is wrong.
    # Format: "YYYY-MM-DD"

    # "milano": "2023-07-08",
    # "blog-pictures": "2017-08-19",
}

TAG_EXCLUDE_WORDS = {
    "",
    "all",
    "back",
    "blog",
    "close",
    "contact",
    "gallery",
    "galleries",
    "home",
    "image",
    "images",
    "menu",
    "next",
    "photo",
    "photos",
    "portfolio",
    "previous",
    "share",
    "tags",
    "tag",
    "view",
    "view all",
    "loading more images",
    "loading more images . . .",
    "previous next",
    "previous",
    "next",
    "copied",
    "book a session",
    "get in touch",
    "support the site",
}


def fetch_text(url: str) -> str | None:
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        return response.text
    except Exception as error:
        print(f"[WARN] Cannot fetch {url}: {error}")
        return None


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1]
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).lower()


def title_from_slug(slug: str) -> str:
    mapping = {
        "milano": "Milano",
        "venezia": "Venezia",
        "roma": "Roma",
        "egypt": "Egypt",
        "thailand": "Thailand",
        "hong-kong": "Hong Kong",
    }
    return mapping.get(slug, slug.replace("-", " ").title())


def normalize_text(text: str) -> str:
    return " ".join((text or "").replace("\xa0", " ").split()).strip()


def strip_site_suffix(title: str) -> str:
    """
    Remove common website suffixes from page title.
    Examples:
    Milano (2018) | Jeremy Gallery -> Milano (2018)
    Milano (2018) - Jeremy Gallery -> Milano (2018)
    """
    title = normalize_text(title)

    suffix_patterns = [
        r"\s*\|\s*Jeremy\s*Gallery\s*$",
        r"\s*-\s*Jeremy\s*Gallery\s*$",
        r"\s*—\s*Jeremy\s*Gallery\s*$",
    ]

    for pattern in suffix_patterns:
        title = re.sub(pattern, "", title, flags=re.IGNORECASE)

    return normalize_text(title)


def parse_album_title_and_tags(raw_title: str, fallback_title: str) -> tuple[str, list[str]]:
    """
    Parse album title and tags from a title like:
    Milano (2018)
    Cat Coffee Shanghai (2023, Cat, Shanghai)

    Returns:
    ("Milano", ["2018"])
    ("Cat Coffee Shanghai", ["2023", "Cat", "Shanghai"])
    """
    title = strip_site_suffix(raw_title)

    if not title:
        return fallback_title, []

    match = re.match(r"^(?P<title>.*?)\s*\((?P<tags>[^()]+)\)\s*$", title)

    if not match:
        return title, []

    clean_title = normalize_text(match.group("title"))
    raw_tags = match.group("tags")

    tags = []

    for item in raw_tags.split(","):
        tag = normalize_text(item)

        if tag:
            tags.append(tag)

    return clean_title or fallback_title, tags


def extract_album_title_and_tags_from_html(
    page_url: str,
    fallback_title: str,
) -> tuple[str, list[str]]:
    """
    Read the public Pixpa page title and extract tags from parentheses.
    """
    html = fetch_text(page_url)

    if not html:
        return fallback_title, []

    soup = BeautifulSoup(html, "lxml")

    candidates = []

    if soup.title and soup.title.string:
        candidates.append(soup.title.string)

    for selector in [
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
    ]:
        element = soup.select_one(selector)

        if element and element.get("content"):
            candidates.append(element.get("content"))

    for selector in ["h1", "h2"]:
        element = soup.select_one(selector)

        if element:
            text = element.get_text(" ", strip=True)

            if text:
                candidates.append(text)

    # Prefer title candidates that contain parentheses, because that is where tags are stored.
    for candidate in candidates:
        album_title, tags = parse_album_title_and_tags(candidate, fallback_title)

        if tags:
            return album_title, tags

    # Fallback: use the first clean title even if it has no tags.
    for candidate in candidates:
        album_title, tags = parse_album_title_and_tags(candidate, fallback_title)

        if album_title:
            return album_title, tags

    return fallback_title, []


def merge_album_tags(*tag_lists: list[str]) -> list[str]:
    """
    Merge and de-duplicate tags while preserving order.
    """
    merged = []
    seen = set()

    for tag_list in tag_lists:
        for tag in tag_list:
            clean_tag = normalize_text(tag)

            if not clean_tag:
                continue

            key = clean_tag.casefold()

            if key in seen:
                continue

            merged.append(clean_tag)
            seen.add(key)

    return merged


def is_portfolio_page(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.netloc.endswith("jeremy.gallery")
        and "/portfolio/" in parsed.path
        and not parsed.path.rstrip("/").endswith("/portfolio")
    )


def is_real_gallery_image(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    lower_url = url.lower()

    # 只保留 Pixpa 作品图片 CDN
    if "px-web-images-v2.pixpa.com" not in host:
        return False

    # 排除非作品图片
    blocked_keywords = [
        "logo",
        "icon",
        "loader",
        "spinner",
        "placeholder",
        "avatar",
    ]

    if any(word in lower_url for word in blocked_keywords):
        return False

    if path.endswith(".svg"):
        return False

    return True


def extract_urls_from_srcset(srcset: str) -> list[str]:
    urls = []
    for part in srcset.split(","):
        candidate = part.strip().split(" ")[0]
        if candidate:
            urls.append(candidate)
    return urls


def get_portfolio_pages_from_sitemap() -> list[str]:
    pages = []

    for sitemap_url in SITEMAP_URLS:
        print(f"[INFO] Reading sitemap: {sitemap_url}")
        xml_text = fetch_text(sitemap_url)

        if not xml_text:
            continue

        try:
            root = ET.fromstring(xml_text.encode("utf-8"))
        except Exception as error:
            print(f"[WARN] Cannot parse sitemap {sitemap_url}: {error}")
            continue

        namespace = {
            "sm": "http://www.sitemaps.org/schemas/sitemap/0.9"
        }

        for loc in root.findall(".//sm:loc", namespace):
            if not loc.text:
                continue

            url = loc.text.strip()

            if is_portfolio_page(url):
                pages.append(url)

    # 去重并保持顺序
    pages = list(dict.fromkeys(pages))

    if TEST_ONLY_MILANO:
        pages = [page for page in pages if "/portfolio/milano" in page]

        # sitemap 没找到 Milano 时，手动补充
        if not pages:
            pages = ["https://www.jeremy.gallery/portfolio/milano"]

    return pages


def decode_pixpa_asset_path(url: str) -> str:
    """
    Try to recover the original source asset path from a Pixpa CDN URL.

    Example decoded result may look like:
    s3://pixpa-test/com/large/690527/1688802759-790490-010-d-sony-milano021.jpg
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    segments = path.split("/")

    for segment in reversed(segments):
        candidate = unquote(segment)

        # skip obvious CDN operation segments, such as rs:fit:1500:0 or q:80
        if ":" in candidate and not candidate.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue

        try:
            padded = candidate + "=" * (-len(candidate) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
            if decoded.startswith("s3://") or decoded.startswith("http://") or decoded.startswith("https://"):
                return decoded
        except Exception:
            pass

    return url


def canonical_image_key(url: str) -> str:
    """
    Produce a stable dedupe key for the original image.
    Prefer the original decoded filename/path from Pixpa.
    """
    decoded_path = decode_pixpa_asset_path(url)

    parsed = urlparse(decoded_path)
    filename = parsed.path.split("/")[-1] if parsed.path else decoded_path.split("/")[-1]
    filename = unquote(filename).lower()

    if filename and "." in filename:
        return filename

    return decoded_path.lower()


def extract_date_from_image_url(image_url: str) -> str:
    """
    Extract date from Pixpa original image filename.

    Pixpa decoded filenames often include a Unix timestamp, for example:
    1688802759-790490-010-d-sony-milano021.jpg

    1688802759 -> 2023-07-08
    """
    decoded_path = decode_pixpa_asset_path(image_url)

    parsed = urlparse(decoded_path)
    filename = parsed.path.split("/")[-1] if parsed.path else decoded_path.split("/")[-1]
    filename = unquote(filename).lower()

    timestamp_candidates = re.findall(r"(?<!\d)(\d{10})(?!\d)", filename)

    for candidate in timestamp_candidates:
        try:
            timestamp = int(candidate)
            parsed_date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except Exception:
            continue

        # Keep only realistic dates.
        current_year = datetime.now(timezone.utc).year
        if 2000 <= parsed_date.year <= current_year + 1:
            return parsed_date.date().isoformat()

    return ""


def date_from_album_images(album_id: str, image_urls: list[str]) -> str:
    """
    Determine album date from its images.

    Priority:
    1. Manual override, if defined.
    2. Earliest valid Pixpa image timestamp in this album.
    3. Empty string if no date can be detected.
    """
    if album_id in ALBUM_DATE_OVERRIDES:
        return ALBUM_DATE_OVERRIDES[album_id]

    detected_dates = []

    for image_url in image_urls:
        image_date = extract_date_from_image_url(image_url)

        if image_date:
            detected_dates.append(image_date)

    if detected_dates:
        return min(detected_dates)

    return ""


def album_specific_image_key(album_id: str, url: str) -> str | None:
    """
    Keep only images that appear to belong to the current album.

    For Milano, original Pixpa filenames look like:
    1688802759-790490-010-d-sony-milano021.jpg

    This function converts different URL variants of the same image into:
    milano021
    """
    decoded_path = decode_pixpa_asset_path(url)
    filename = decoded_path.split("/")[-1].lower()

    normalized_filename = re.sub(r"[^a-z0-9]+", "", filename)
    normalized_album_id = re.sub(r"[^a-z0-9]+", "", album_id.lower())

    if normalized_album_id not in normalized_filename:
        return None

    # Prefer album + number as the dedupe key, e.g. milano021
    match = re.search(rf"{re.escape(normalized_album_id)}0*(\d+)", normalized_filename)

    if match:
        number = int(match.group(1))
        return f"{normalized_album_id}{number:03d}"

    return filename


def split_raw_tag_text(raw_text: str) -> list[str]:
    """
    Split raw tag text into possible individual tags.

    Examples:
    "2018 · Italy · Travel" -> ["2018", "Italy", "Travel"]
    "2018\nItaly\nTravel" -> ["2018", "Italy", "Travel"]
    """
    if not raw_text:
        return []

    text = raw_text.replace("\xa0", " ").strip()

    # Common separators used in tag areas
    parts = re.split(r"[\n\r\t,|•·]+", text)

    cleaned_parts = []

    for part in parts:
        tag = " ".join(part.split()).strip()
        tag = tag.strip("#").strip()

        if tag:
            cleaned_parts.append(tag)

    return cleaned_parts


def is_valid_album_tag(tag: str) -> bool:
    """
    Basic filtering to avoid collecting menu labels, long text blocks,
    image captions, and unrelated UI text.
    """
    if not tag:
        return False

    normalized = " ".join(tag.split()).strip()
    lower = normalized.lower()

    if lower in TAG_EXCLUDE_WORDS:
        return False

    if len(normalized) > 40:
        return False

    # Avoid collecting large chunks or technical strings
    blocked_fragments = [
        "http://",
        "https://",
        "www.",
        "{",
        "}",
        "[",
        "]",
        "px-web-images",
        "pixpa.com",
        "javascript:",
    ]

    if any(fragment in lower for fragment in blocked_fragments):
        return False

    # Avoid image filenames
    if re.search(r"\.(jpg|jpeg|png|webp|gif|svg)$", lower):
        return False

    # Avoid pure numbers except 4-digit years
    if lower.isdigit() and not re.fullmatch(r"(19|20)\d{2}", lower):
        return False

    # Avoid very long sentence-like strings
    if len(normalized.split()) > 6:
        return False

    return True


def clean_album_tags(raw_tags: list[str]) -> list[str]:
    """
    Clean, split, filter, and de-duplicate tags while keeping original order.
    """
    cleaned = []
    seen = set()

    for raw_tag in raw_tags:
        for tag in split_raw_tag_text(raw_tag):
            if not is_valid_album_tag(tag):
                continue

            key = tag.casefold()

            if key in seen:
                continue

            cleaned.append(tag)
            seen.add(key)

    return cleaned


def extract_tags_from_static_html(page_url: str, html: str) -> list[str]:
    """
    Fallback tag extraction from static HTML.
    This is used only if browser-based extraction returns no tags.
    """
    soup = BeautifulSoup(html, "lxml")
    raw_tags = []

    selectors = [
        '[class*="tag"]',
        '[class*="Tag"]',
        '[class*="category"]',
        '[class*="Category"]',
        'a[href*="tag"]',
        'a[href*="tags"]',
        'a[href*="category"]',
        'a[href*="categories"]',
    ]

    for selector in selectors:
        for element in soup.select(selector):
            text = element.get_text(" ", strip=True)
            if text:
                raw_tags.append(text)

            for attr in [
                "title",
                "aria-label",
                "data-tag",
                "data-tags",
                "data-category",
                "data-categories",
            ]:
                value = element.get(attr)
                if value:
                    raw_tags.append(value)

    # Some Pixpa pages may expose keyword-like metadata.
    # Keep this as a weak fallback. The cleaning function will remove obvious noise.
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        content = meta.get("content") or ""

        if name in {"keywords", "article:tag"} and content:
            raw_tags.append(content)

    return clean_album_tags(raw_tags)


def extract_candidate_urls_from_html(page_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    candidates = []

    for tag in soup.find_all(["img", "source", "a"]):
        for attr in [
            "src",
            "href",
            "data-src",
            "data-original",
            "data-large",
            "srcset",
            "data-srcset",
        ]:
            value = tag.get(attr)

            if not value:
                continue

            if "srcset" in attr:
                raw_urls = extract_urls_from_srcset(value)
            else:
                raw_urls = [value]

            for raw_url in raw_urls:
                full_url = urljoin(page_url, raw_url).replace("&amp;", "&")

                if is_real_gallery_image(full_url):
                    candidates.append(full_url)

    return candidates


def extract_candidate_urls_and_tags_with_browser(page_url: str) -> tuple[list[str], list[str]]:
    print(f"[INFO] Opening page with browser: {page_url}")

    candidates = []
    browser_tags = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 2200},
            user_agent=HEADERS["User-Agent"],
        )

        page.goto(page_url, wait_until="networkidle", timeout=60000)

        # Extract visible or DOM-based tags once the page is loaded.
        raw_tags = page.evaluate(
            """
            () => {
                const values = [];

                const selectors = [
                    '[class*="tag" i]',
                    '[class*="category" i]',
                    'a[href*="tag" i]',
                    'a[href*="tags" i]',
                    'a[href*="category" i]',
                    'a[href*="categories" i]',
                    '[data-tag]',
                    '[data-tags]',
                    '[data-category]',
                    '[data-categories]'
                ];

                const attrNames = [
                    'title',
                    'aria-label',
                    'data-tag',
                    'data-tags',
                    'data-category',
                    'data-categories'
                ];

                function pushValue(value) {
                    if (!value) return;

                    const text = String(value).trim();
                    if (text) {
                        values.push(text);
                    }
                }

                for (const selector of selectors) {
                    document.querySelectorAll(selector).forEach(el => {
                        pushValue(el.innerText || el.textContent || '');

                        for (const attr of attrNames) {
                            pushValue(el.getAttribute(attr));
                        }
                    });
                }

                document.querySelectorAll('meta[name="keywords"], meta[property="article:tag"]').forEach(el => {
                    pushValue(el.getAttribute('content'));
                });

                return values;
            }
            """
        )

        browser_tags = clean_album_tags(raw_tags)

        previous_unique_count = 0
        stable_rounds = 0

        for round_index in range(1, 31):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1800)

            current_urls = page.evaluate(
                """
                () => {
                    const urls = [];
                    const attrs = [
                        "src",
                        "href",
                        "data-src",
                        "data-original",
                        "data-large",
                        "srcset",
                        "data-srcset"
                    ];

                    function isVisibleElement(el) {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);

                        if (!style) return false;
                        if (style.display === "none") return false;
                        if (style.visibility === "hidden") return false;
                        if (parseFloat(style.opacity || "1") === 0) return false;

                        if (rect.width < 20 || rect.height < 20) return false;

                        return true;
                    }

                    document.querySelectorAll("img, source, a, picture").forEach(el => {
                        if (!isVisibleElement(el)) return;

                        for (const attr of attrs) {
                            const value = el.getAttribute(attr);
                            if (value) {
                                urls.push(value);
                            }
                        }
                    });

                    return urls;
                }
                """
            )

            for value in current_urls:
                possible_values = extract_urls_from_srcset(value) if "," in value else [value]

                for possible_url in possible_values:
                    full_url = urljoin(page_url, possible_url).replace("&amp;", "&")

                    if is_real_gallery_image(full_url):
                        candidates.append(full_url)

            unique_keys = {canonical_image_key(url) for url in candidates}
            current_unique_count = len(unique_keys)

            print(
                f"[INFO] Scroll round {round_index}: "
                f"visible unique images so far = {current_unique_count}"
            )

            if current_unique_count == previous_unique_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                previous_unique_count = current_unique_count

            if stable_rounds >= 4:
                break

        browser.close()

    return candidates, browser_tags


def extract_page_data(page_url: str) -> tuple[list[str], list[str]]:
    """
    Extract gallery image URLs and album tags from one Pixpa portfolio page.
    """
    print(f"[INFO] Reading portfolio page: {page_url}")

    # 先用真实浏览器滚动页面，抓取当前相册真正可见的图片和 tags
    browser_candidates, album_tags = extract_candidate_urls_and_tags_with_browser(page_url)

    print(f"[INFO] Browser visible matched images: {len(browser_candidates)}")
    print(f"[INFO] Browser matched tags: {album_tags}")

    html = None

    # 如果浏览器没有抓到图片，再 fallback 到静态 HTML
    if not browser_candidates:
        print("[WARN] Browser found no images. Falling back to static HTML.")
        html = fetch_text(page_url)
        browser_candidates = extract_candidate_urls_from_html(page_url, html) if html else []

    # 如果浏览器没有抓到 tags，也 fallback 到静态 HTML
    if not album_tags:
        print("[WARN] Browser found no tags. Falling back to static HTML for tags.")
        if html is None:
            html = fetch_text(page_url)

        album_tags = extract_tags_from_static_html(page_url, html) if html else []

    deduped = {}

    for image_url in browser_candidates:
        key = canonical_image_key(image_url)

        if key not in deduped:
            deduped[key] = image_url

    images = list(deduped.values())

    print(f"[INFO] Final unique visible images after dedupe: {len(images)} from {page_url}")
    print(f"[INFO] Final album tags: {album_tags}")

    return images, album_tags


def extract_images_from_page(page_url: str) -> list[str]:
    """
    Backward-compatible wrapper.
    If other scripts still call extract_images_from_page(), it will keep working.
    """
    image_urls, _ = extract_page_data(page_url)
    return image_urls


def build_gallery_json() -> dict:
    pages = get_portfolio_pages_from_sitemap()

    if not pages:
        raise RuntimeError("No portfolio pages found.")

    albums = []

    for page_url in pages:
        album_id = slug_from_url(page_url)
        fallback_title = title_from_slug(album_id)
        
        album_title, title_tags = extract_album_title_and_tags_from_html(
            page_url,
            fallback_title,
        )
        
        image_urls, _ignored_dom_tags = extract_page_data(page_url)
        # 当前 Pixpa Portfolio 页面没有稳定暴露真正的独立 Tag DOM。
        # 因此先只使用标题括号里的 tags，避免误抓 Loading / Previous / Next。
        album_tags = merge_album_tags(title_tags)

        if not image_urls:
            print(f"[WARN] No images found for {page_url}")
            continue

        album_date = date_from_album_images(album_id, image_urls)

        if album_date:
            print(f"[INFO] Album date for {album_id}: {album_date}")
        else:
            print(f"[WARN] No album date detected for {album_id}")

        if album_tags:
            print(f"[INFO] Tags for {album_id}: {album_tags}")
        else:
            print(f"[WARN] No tags detected for {album_id}")

        photos = []

        for index, image_url in enumerate(image_urls, start=1):
            photos.append(
                {
                    "id": f"{album_id}-{index:03d}",
                    "title": f"{album_title} {index:03d}",
                    "imageUrl": image_url,
                    "thumbUrl": image_url,
                    "sourcePageUrl": page_url,
                }
            )

        albums.append(
            {
                "id": album_id,
                "title": album_title,
                "date": album_date,
                "pageUrl": page_url,
                "coverUrl": image_urls[0],
                "photoCount": len(photos),
                "tags": album_tags,
                "photos": photos,
            }
        )

    total_photos = sum(len(album["photos"]) for album in albums)

    if total_photos == 0:
        raise RuntimeError("No photos found. Stop generating broken JSON.")

    return {
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceSite": SITE_URL,
        "albums": albums,
    }


def main() -> None:
    gallery = build_gallery_json()

    OUTPUT_JSON.write_text(
        json.dumps(gallery, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    album_count = len(gallery["albums"])
    photo_count = sum(len(album["photos"]) for album in gallery["albums"])

    print(f"[OK] Written to {OUTPUT_JSON}")
    print(f"[OK] Albums: {album_count}")
    print(f"[OK] Photos: {photo_count}")


if __name__ == "__main__":
    main()
