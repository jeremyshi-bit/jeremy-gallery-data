import json
from pathlib import Path
from datetime import datetime


REPO_ROOT = Path(__file__).resolve().parents[1]
POSTS_DIR = REPO_ROOT / "posts"
OUTPUT_FILE = REPO_ROOT / "blog.json"


REQUIRED_FIELDS = [
    "id",
    "title",
    "date",
    "excerpt",
]


OPTIONAL_FIELDS = [
    "updatedAt",
    "author",
    "language",
    "coverImageUrl",
    "sourcePageUrl",
    "tags",
    "isPublished",
    "isFeatured",
    "readingMinutes",
]


def parse_bool(value: str) -> bool:
    return value.strip().lower() in ["true", "yes", "1"]


def parse_int(value: str):
    try:
        return int(value.strip())
    except ValueError:
        return None


def parse_tags(value: str):
    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def parse_front_matter(content: str, file_path: Path):
    lines = content.splitlines()

    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{file_path} is missing front matter.")

    metadata = {}
    body_start_index = None

    for index in range(1, len(lines)):
        line = lines[index].strip()

        if line == "---":
            body_start_index = index + 1
            break

        if not line or line.startswith("#"):
            continue

        if ":" not in line:
            raise ValueError(
                f"Invalid metadata line in {file_path}: {line}"
            )

        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()

    if body_start_index is None:
        raise ValueError(f"{file_path} front matter is not closed.")

    for field in REQUIRED_FIELDS:
        if field not in metadata or not metadata[field]:
            raise ValueError(
                f"{file_path} is missing required field: {field}"
            )

    return metadata


def normalize_post(metadata: dict, markdown_path: Path):
    post = {}

    for field in REQUIRED_FIELDS:
        post[field] = metadata[field]

    for field in OPTIONAL_FIELDS:
        if field not in metadata or metadata[field] == "":
            continue

        value = metadata[field]

        if field in ["isPublished", "isFeatured"]:
            post[field] = parse_bool(value)

        elif field == "readingMinutes":
            parsed_value = parse_int(value)
            if parsed_value is not None:
                post[field] = parsed_value

        elif field == "tags":
            post[field] = parse_tags(value)

        else:
            post[field] = value

    relative_path = markdown_path.relative_to(REPO_ROOT).as_posix()
    post["contentUrl"] = relative_path

    return post


def sort_posts(posts):
    return sorted(
        posts,
        key=lambda post: (
            post.get("isFeatured") is True,
            post.get("date", "")
        ),
        reverse=True
    )


def validate_date(post):
    try:
        datetime.strptime(post["date"], "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"Invalid date format in post {post['id']}: {post['date']}. "
            "Expected YYYY-MM-DD."
        )


def main():
    if not POSTS_DIR.exists():
        raise FileNotFoundError(
            f"Posts folder not found: {POSTS_DIR}"
        )

    posts = []

    markdown_files = sorted(POSTS_DIR.rglob("*.md"))

    for markdown_path in markdown_files:
        content = markdown_path.read_text(encoding="utf-8")
        metadata = parse_front_matter(content, markdown_path)
        post = normalize_post(metadata, markdown_path)

        validate_date(post)

        if post.get("isPublished") is False:
            continue

        posts.append(post)

    posts = sort_posts(posts)

    output = {
        "version": 1,
        "posts": posts
    }

    OUTPUT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )

    print(f"Generated {OUTPUT_FILE}")
    print(f"Published posts: {len(posts)}")


if __name__ == "__main__":
    main()
