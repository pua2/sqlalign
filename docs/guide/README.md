# Guide sources

These markdown files are the **source** for the published documentation site,
not the site itself. `tools/build_docs.py` renders them into `docs/v1/*.html`
with the shared navigation, search and version selector.

Read the built site locally with `python3 -m http.server -d docs 8000`. Edit
these files, then rebuild:

```sh
uv run python tools/build_docs.py
```

| Source | Published as |
|---|---|
| `getting-started.md` | [Getting started](../v1/getting-started.html) |
| `configuration.md` | [Configuration](../v1/configuration.html) |
| `style.md` | [The house style](../v1/style.html) |
| `cli.md` | [CLI reference](../v1/cli.html) |
| `dialects.md` | [Dialects](../v1/dialects.html) |
| `architecture.md` | [Architecture](../v1/architecture.html) |
| `faq.md` | [FAQ](../v1/faq.html) |

Two pages have no markdown source because they are generated entirely:
the **Overview** and the **Settings reference**, whose examples come from
running the formatter at build time. Their content lives in
`tools/build_docs.py` and `tools/docs_settings.py`.
