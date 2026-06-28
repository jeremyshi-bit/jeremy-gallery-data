import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POSTS_DIR = REPO_ROOT / "posts" / "imported"
OUTPUT_FILE = REPO_ROOT / "blog.json"

SITE_BASE_URL = "https://jeremyshi-bit.github.io/jeremy-gallery-data"


def parse_front_matter(markdown: str) -> tuple[dict, str]:
    if not markdown.startswith("---"):
        return {}, markdown

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", markdown, re.S)

    if not match:
        return {}, markdown

    front_matter_text = match.group(1)
    body = match.group(2)

    data = {}

    for line in front_matter_text.splitlines():
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")

    return data, body


def extract_markdown_images(body: str) -> list[str]:
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", body)


def estimate_reading_minutes(body: str) -> int:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", body)
    text = re.sub(r"#+\s*", "", text)
    words = re.findall(r"\b\w+\b", text)

    return max(1, round(len(words) / 200))


def build_post_item(md_path: Path) -> dict:
    markdown = md_path.read_text(encoding="utf-8")
    front_matter, body = parse_front_matter(markdown)

    post_id = front_matter.get("id") or md_path.stem
    title = front_matter.get("title") or post_id.replace("-", " ").title()
    date = front_matter.get("date") or ""
    updated_at = front_matter.get("updatedAt") or date
    excerpt = front_matter.get("excerpt") or ""
    cover_image_url = front_matter.get("coverImageUrl") or ""
    source_page_url = front_matter.get("sourcePageUrl") or ""
    language = front_matter.get("language") or "en"
    author = front_matter.get("author") or "Jeremy"

    relative_path = md_path.relative_to(REPO_ROOT).as_posix()
    content_url = f"{SITE_BASE_URL}/{relative_path}"

    image_urls = extract_markdown_images(body)

    reading_minutes_raw = front_matter.get("readingMinutes")
    try:
        reading_minutes = int(reading_minutes_raw)
    except Exception:
        reading_minutes = estimate_reading_minutes(body)

    return {
        "id": post_id,
        "title": title,
        "date": date,
        "updatedAt": updated_at,
        "author": author,
        "language": language,
        "excerpt": excerpt,
        "coverImageUrl": cover_image_url,
        "sourcePageUrl": source_page_url,
        "contentPath": relative_path,
        "contentUrl": content_url,
        "imageCount": len(image_urls),
        "readingMinutes": reading_minutes,
        "isPublished": front_matter.get("isPublished", "false").lower() == "true",
        "isFeatured": front_matter.get("isFeatured", "false").lower() == "true",
        "tags": [
            tag.strip()
            for tag in front_matter.get("tags", "").split(",")
            if tag.strip()
        ],
    }


def main():
    if not POSTS_DIR.exists():
        raise FileNotFoundError(f"Posts directory not found: {POSTS_DIR}")

    posts = []

    for md_path in sorted(POSTS_DIR.glob("*.md")):
        posts.append(build_post_item(md_path))

    posts.sort(
        key=lambda item: item.get("date", ""),
        reverse=True,
    )

    data = {
        "version": 1,
        "generatedFrom": "posts/imported",
        "postCount": len(posts),
        "posts": posts,
    }

    OUTPUT_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Generated {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    print(f"Post count: {len(posts)}")

    for post in posts:
        print(
            f"- {post['date']} | {post['id']} | "
            f"{post['imageCount']} images | {post['title']}"
        )


if __name__ == "__main__":
    main()
