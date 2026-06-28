# Remote Blog JSON Test

This article is loaded from an independent Markdown file.

The Blog list information still comes from `blog.json`, but the full article content now comes from:

`posts/remote-blog-json-test.md`

## Why this matters

Using a separate Markdown file makes long blog posts easier to maintain.
![Remote Markdown image test](https://picsum.photos/seed/markdown-image-test/1200/800)
Instead of putting a long article directly inside `blog.json`, the JSON file can stay clean and focused on metadata:

- Title
- Date
- Excerpt
- Cover image
- Tags
- Author
- Content URL

## Result

If you can see this article inside the app, it means:

- `blog.json` loaded successfully
- `contentUrl` was recognized
- The Markdown file was downloaded
- The article body was rendered from remote Markdown

This is a better structure for real Blog content in the future.
