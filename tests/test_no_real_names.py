"""No real artwork title or artist name in the repository — it is mirrored publicly.

Lessons from real works are kept as technical descriptions ("a 1994 Max installation whose entry
point is an empty data fork"), never as the work's or the artist's name. A name in a prompt or a
catalog line also leaks into reports and contaminates blind runs.

The denylist ``tests/data/real-name-denylist.sha256`` holds SHA-256 digests, so the guard itself
names no one (a deterrent against accidental listing, not secrecy: short names can be guessed).
Normalisation: NFKD, drop non-ASCII, lowercase, split on anything not ``[a-z0-9]``, join the tokens
with one space — and, for a multi-word name, also the tokens joined with nothing (``SomeTitle``,
``some-title.jar``). Every 1..5-token window of every tracked text file (and its path) is checked.
To add a name::

    python3 -c "import hashlib,re,sys,unicodedata; t=re.findall('[a-z0-9]+', unicodedata.normalize(
    'NFKD', sys.argv[1]).encode('ascii','ignore').decode().lower()); [print(hashlib.sha256(k.encode())
    .hexdigest()) for k in {' '.join(t), ''.join(t)}]" "Some Title"

Words that are also ordinary vocabulary (a title that is a common noun) cannot be listed; review
catches those.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import unicodedata
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DENYLIST = Path(__file__).with_name("data") / "real-name-denylist.sha256"
_MAX_TOKENS = 5
_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"}
_SKIP_DIRS |= {".pytest_cache", "dist", "build"}
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    )


def _digests() -> set[str]:
    lines = _DENYLIST.read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def _repo_files() -> list[Path]:
    """Tracked files when this is a git checkout, else every file under the root (a copied tree)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=_ROOT, capture_output=True, check=True, timeout=60
        ).stdout.decode("utf-8", "surrogateescape")
        return [_ROOT / p for p in out.split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        return [
            p
            for p in _ROOT.rglob("*")
            if p.is_file() and not _SKIP_DIRS.intersection(p.relative_to(_ROOT).parts)
        ]


def _hits(text: str, digests: set[str]) -> set[str]:
    """The n-grams of ``text`` whose digest is denylisted (returned as the matched text)."""
    toks = _tokens(text)
    found: set[str] = set()
    seen: set[str] = set()
    for i in range(len(toks)):
        for n in range(1, _MAX_TOKENS + 1):
            window = toks[i : i + n]
            if len(window) < n:
                break
            keys = {" ".join(window)} if n == 1 else {" ".join(window), "".join(window)}
            for key in keys - seen:
                seen.add(key)
                if hashlib.sha256(key.encode()).hexdigest() in digests:
                    found.add(key)
    return found


def test_denylist_is_hashed_and_nonempty() -> None:
    digests = _digests()
    assert len(digests) > 50
    assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in digests)


def test_the_guard_matches_spaced_joined_and_accented_forms() -> None:
    digests = {hashlib.sha256(k.encode()).hexdigest() for k in ("rene muller", "renemuller")}
    assert _hits("a work by René Müller (1994)", digests) == {"rene muller", "renemuller"}
    assert _hits("ReneMuller-1.2.jar", digests) == {"renemuller"}
    assert _hits("rene and muller", digests) == set()


def test_no_real_artwork_or_artist_name_in_the_repository() -> None:
    digests = _digests()
    offenders: list[str] = []
    for path in _repo_files():
        rel = path.relative_to(_ROOT).as_posix()
        if path.resolve() == _DENYLIST.resolve():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue  # binary
        text = rel + "\n" + data.decode("utf-8", "replace")
        offenders.extend(f"{rel}: {hit}" for hit in sorted(_hits(text, digests)))
    assert not offenders, "real artwork/artist names (anonymise them):\n" + "\n".join(offenders)
