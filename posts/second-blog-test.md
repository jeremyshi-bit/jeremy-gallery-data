---
id: second-blog-test
title: Second Blog Test
date: 2026-06-29
updatedAt: 2026-06-29
author: Jeremy
language: en
excerpt: This is the second Markdown blog post used to verify the automatic blog.json generation workflow.
coverImageUrl: https://picsum.photos/seed/second-blog-test/1200/800
sourcePageUrl: https://www.jeremy.gallery
tags: Test, Workflow, Markdown
isPublished: true
isFeatured: false
readingMinutes: 2
---

# Second Blog Test

This is the second Markdown blog post.

Its purpose is to verify that GitHub Actions can automatically regenerate `blog.json` when a new article is added to the `posts` folder.

## Expected result

After this file is committed:

- GitHub Actions should run automatically.
- `blog.json` should be regenerated.
- The Blog section in the app should show two posts after refreshing.

![Second blog test image](https://picsum.photos/seed/second-blog-test-image/1200/800)
