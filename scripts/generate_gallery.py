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

ALBUM_DATES = {
    # Format: "YYYY-MM-DD"
    # Fill these keys according to the album ids in gallery.json.
    # Empty or missing date means the App will not show a date chip.

    "milano": "2023-07-08",
    "blog-pictures": "2017-08-19",

    # Add the remaining albums here, for example:
    # "modena-walk": "2019-09-13",
    # "amsterdam-airport": "2019-11-18",
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

def date_from_album_id(album_id: str) -> str:
    return ALBUM_DATES.get(album_id, "")

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


def extract_candidate_urls_with_browser(page_url: str) -> list[str]:
    print(f"[INFO] Opening page with browser: {page_url}")

    candidates = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 2200},
            user_agent=HEADERS["User-Agent"],
        )

        page.goto(page_url, wait_until="networkidle", timeout=60000)

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

                        // The image may be above the current viewport after scrolling,
                        // but it still belongs to the page layout if it has real size.
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

    return candidates


def extract_images_from_page(page_url: str) -> list[str]:
    print(f"[INFO] Reading portfolio page: {page_url}")

    # 先用真实浏览器滚动页面，抓取当前相册真正可见的图片
    browser_candidates = extract_candidate_urls_with_browser(page_url)

    print(f"[INFO] Browser visible matched images: {len(browser_candidates)}")

    # 如果浏览器没有抓到，再 fallback 到静态 HTML
    if not browser_candidates:
        print("[WARN] Browser found no images. Falling back to static HTML.")
        html = fetch_text(page_url)
        browser_candidates = extract_candidate_urls_from_html(page_url, html) if html else []

    deduped = {}

    for image_url in browser_candidates:
        key = canonical_image_key(image_url)

        if key not in deduped:
            deduped[key] = image_url

    images = list(deduped.values())

    print(f"[INFO] Final unique visible images after dedupe: {len(images)} from {page_url}")
    
    return images

def build_gallery_json() -> dict:
    pages = get_portfolio_pages_from_sitemap()

    if not pages:
        raise RuntimeError("No portfolio pages found.")

    albums = []

    for page_url in pages:
        album_id = slug_from_url(page_url)
        album_title = title_from_slug(album_id)
        image_urls = extract_images_from_page(page_url)

        if not image_urls:
            print(f"[WARN] No images found for {page_url}")
            continue

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
                "date": date_from_album_id(album_id),
                "pageUrl": page_url,
                "coverUrl": image_urls[0],
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
