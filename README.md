# AI Engineering labs

My private lab workspace for the AI Engineering course, created from
[Keel-academy/ai-engineering-labs-template](https://github.com/Keel-academy/ai-engineering-labs-template).
Unit work goes under `units/`; Lab 0's setup report and notes go in `setup/`.
The course project gets its own public repository in Unit 0.10.

## Everyday commands

Run these from this folder.

| Task | Command |
| --- | --- |
| Get new unit files | `git pull` |
| Install or update packages | `uv sync` |
| Run a script | `uv run python path/to/script.py` |
| Run a script that calls a model | `uv run --env-file .env python path/to/script.py` |
| Run tests | `uv run pytest path/to/tests` |
| Check my setup | `uv run --env-file .env python tools/doctor.py` |
| Add a package (only when a lab says so) | `uv add <package>` |

## Rules

- Never commit `.env`. It holds your API key.
- Never use `pip install`. Use `uv add`, so the grader gets the same packages.
- Work for each unit goes in its own folder under `units/`.
