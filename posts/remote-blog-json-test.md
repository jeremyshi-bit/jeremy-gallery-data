---
id: remote-blog-json-test
title: Remote Blog JSON Test
date: 2026-06-28
updatedAt: 2026-06-28
author: Jeremy
language: en
excerpt: This post confirms that the app is loading Blog content from GitHub Pages.
coverImageUrl: https://picsum.photos/seed/jeremy-blog-json-test/1200/800
sourcePageUrl: https://www.jeremy.gallery
tags: Test, Blog, JSON
isPublished: true
isFeatured: true
readingMinutes: 3
---

# Remote Blog JSON Test

This article is loaded from an independent Markdown file.

![Remote Markdown image test](https://picsum.photos/seed/markdown-image-test/1200/800)

The Blog list information comes from `blog.json`, while the full article content comes from this Markdown file:

`posts/remote-blog-json-test.md`

## Why this matters

Using a separate Markdown file makes long blog posts easier to maintain.

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
