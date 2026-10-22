# Contributing to AutoShorts

Thanks for helping. Bug reports, ideas, docs fixes and code are all welcome.

## Ways to help

- **Try it and report what breaks**, especially on Windows, Linux and NVIDIA GPUs. Those paths are newer than the macOS one.
- **Share results.** A before/after clip, a caption style or a faceless format idea in Discussions helps others.
- **Pick an issue** labelled `good first issue` or `help wanted`.

## Development setup

```bash
make setup      # Python environment + dashboard
make dev        # engine on :3000, hot-reloading dashboard on :5173
make test       # backend tests
```

Read [tech-stack/](tech-stack/README.md) first: it explains the pipeline, the editing engine and why things are built the way they are.

## Guidelines

- Keep everything local: no features that require a paid or cloud AI API.
- New model backends go behind the existing `auto` selection (see `llm.py`, `voices.py`, `images.py`), so each platform keeps working.
- Add a test for logic you change. Tests must run offline and in under a few seconds.
- Match the surrounding code style. Run `ruff check backend` and `npx tsc --noEmit -p frontend` before opening a PR.
- One focused change per pull request, with a short description of what and why.

## Responsible use

AutoShorts is for content you own or have permission to use. Contributions that help bypass platform rules, scrape and repost other creators' videos, or fake engagement won't be accepted.
