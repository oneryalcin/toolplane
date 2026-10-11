"""Generate the throwaway release repository under build/release-repo.

Deterministic: fixed data seed (data.py), fixed authors and dates via env.
History: 15 commits, tag v1.4, then exactly 100 commits, one ticket id each.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from data import BEFORE_TAG_IDS, RELEASE_IDS, SUBJECTS

REPO = Path(__file__).resolve().parent / "build" / "release-repo"
AUTHORS = [
    ("Ines Okafor", "ines@lumenstack.example"),
    ("Tobias Reyes", "tobias@lumenstack.example"),
    ("Mei Lindqvist", "mei@lumenstack.example"),
    ("Arjun Baptiste", "arjun@lumenstack.example"),
]
START = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def git(*args: str, when: datetime = START, author: tuple[str, str] = AUTHORS[0]) -> str:
    # GIT_DIR/GIT_WORK_TREE pin every command to the demo repo: it lives inside
    # another repository and must never fall through to the parent.
    env = {
        "PATH": os.environ["PATH"],
        "GIT_DIR": str(REPO / ".git"),
        "GIT_WORK_TREE": str(REPO),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": author[0],
        "GIT_AUTHOR_EMAIL": author[1],
        "GIT_COMMITTER_NAME": author[0],
        "GIT_COMMITTER_EMAIL": author[1],
        "GIT_AUTHOR_DATE": when.isoformat(),
        "GIT_COMMITTER_DATE": when.isoformat(),
    }
    done = subprocess.run(
        ["git", *args], cwd=REPO, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def main() -> None:
    shutil.rmtree(REPO, ignore_errors=True)
    REPO.mkdir(parents=True)
    git("init", "--quiet", "--initial-branch=main")
    changelog = REPO / "CHANGELOG.md"
    changelog.write_text("# Lumenstack changelog\n")
    for n, ticket_id in enumerate(BEFORE_TAG_IDS + RELEASE_IDS):
        if n == len(BEFORE_TAG_IDS):
            git("tag", "v1.4")
        subject = SUBJECTS[ticket_id]
        with changelog.open("a") as fh:
            fh.write(f"- {subject}\n")
        git("add", "CHANGELOG.md")
        git(
            "commit", "--quiet", "-m", subject,
            when=START + timedelta(hours=7 * n, minutes=13 * (n % 5)),
            author=AUTHORS[n % len(AUTHORS)],
        )
    print("repo:", REPO)
    print("HEAD:", git("rev-parse", "HEAD"))
    print("commits total:", git("rev-list", "--count", "HEAD"))
    print("commits since v1.4:", git("rev-list", "--count", "v1.4..HEAD"))


if __name__ == "__main__":
    main()
