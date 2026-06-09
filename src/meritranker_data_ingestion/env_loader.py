"""Optional .env loading for local CLI tooling."""

from __future__ import annotations

from pathlib import Path


def load_dotenv_if_present() -> Path | None:
    """Load `.env` from cwd or project root. Shell env vars take precedence."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return None

    cwd = Path.cwd().resolve()
    cwd_env = cwd / ".env"
    if cwd_env.is_file():
        load_dotenv(cwd_env, override=False)
        return cwd_env

    project_root = Path(__file__).resolve().parents[2]
    try:
        cwd.relative_to(project_root)
        under_project = True
    except ValueError:
        under_project = False

    if under_project:
        project_env = project_root / ".env"
        if project_env.is_file():
            load_dotenv(project_env, override=False)
            return project_env

    return None
