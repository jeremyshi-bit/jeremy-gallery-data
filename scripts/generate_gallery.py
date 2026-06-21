import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


SITE_URL = "https://www.jeremy.gallery"
OUTPUT_JSON = Path("gallery.json")

SITEMAP_URLS = [
    "https://www.jeremy.gallery/sitemap.xml",
    "https://www.jeremy.gallery/sitemap-website.xml",
]

# 测试阶段：先只抓 Milano，确认成功后再改成 False 抓全部相册
TEST_ONLY_MILANO = True

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
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


def extract_images_from_page(page_url: str) -> list[str]:
    print(f"[INFO] Reading portfolio page: {page_url}")

    html = fetch_text(page_url)

    if not html:
        return []

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

    # 去重并保持顺序
    images = list(dict.fromkeys(candidates))

    print(f"[INFO] Found {len(images)} images from {page_url}")
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
