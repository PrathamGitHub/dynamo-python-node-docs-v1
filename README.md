# Civil 3D Automation — Developer Training Docs

An onboarding module for developers writing **Civil 3D automation** (Dynamo Python
nodes and .NET scripts) at Dar. It teaches the core API concepts, walks through a
real Profile View generator chunk-by-chunk, extracts reusable patterns, and flags
common mistakes.

Built with [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) and
Mermaid diagrams, published via GitHub Pages.

## Contents

- **Getting Started** — the big picture + a Civil 3D API primer ("explain like I'm five").
- **Walkthrough (A–G)** — imports, inputs, helpers, styles, crossing detection, profile views, the main transaction.
- **Reusable Patterns Cookbook** — copy-paste building blocks.
- **Gotchas & Anti-patterns** — mistakes to avoid (with real examples).
- **Glossary** — every term, one-line definitions.

## Local preview

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt

mkdocs serve      # live preview at http://127.0.0.1:8000
mkdocs build      # static site into ./site
```

`mkdocs serve` auto-reloads as you edit files under `docs/`.

## Publishing (GitHub Pages)

Two options:

1. **Automatic** — push to `main`. The workflow at
   `.github/workflows/gh-pages.yml` builds the site and runs `mkdocs gh-deploy`,
   which pushes the built HTML to the `gh-pages` branch. Enable Pages in the repo
   settings (Source: `gh-pages` branch) once.
2. **Manual** — run `mkdocs gh-deploy --force` locally.

## Editing guidance

- **Teaching chapters are hand-written** for API accuracy. When editing anything
  that names a .NET type, method, or style collection, verify it against the
  Civil 3D SDK / a real drawing — don't let an AI assistant invent API names.
- Use the standard admonition boxes: `note` (concept), `tip` (do this),
  `warning` (careful), `danger` (will bite you), `bug` (a real mistake we learned
  from), `success` (why a fix works).
- Mermaid diagrams go in a fenced ` ```mermaid ` block.

## Repo layout

```text
mkdocs.yml
requirements.txt
README.md
.github/workflows/gh-pages.yml
docs/
  index.md
  assets/mermaid-init.js
  getting-started/
    big-picture.md
    civil3d-api-primer.md
  walkthrough/
    a-imports.md … g-main-loop.md
  cookbook.md
  gotchas.md
  glossary.md
  _cursor-prompts.md      # optional: Cursor prompts used to scaffold this site
```
