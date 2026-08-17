"""Measure how long sqlalign takes over a repository-sized tree.

    uv run python tools/benchmark.py [--files N]

The question this exists to answer is whether formatting is slow enough to be
worth parallelising. Publishing the numbers is the point: "fast enough" is a
claim, and a claim about performance with no measurement behind it is the kind
of thing that quietly stops being true.

A synthetic tree is built from the vendored corpus plus the samples, so the
input is SQL of realistic shape rather than one statement repeated. Timing is
end-to-end through the CLI -- interpreter start, imports, walking the tree and
writing results -- because that is what a user waits for, and a figure that
excludes startup flatters the tool.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _sources() -> list[str]:
    files = sorted((ROOT / "tests" / "corpus").rglob("*.sql"))
    files += [ROOT / "samples" / "queries.sql"]
    texts = [f.read_text(errors="replace") for f in files]
    if not texts:
        sys.exit("no corpus found; run tools/fetch_corpus.py")
    return texts


def _build_tree(root: pathlib.Path, count: int, texts: list[str]) -> int:
    """A tree of `count` files, spread over directories like a real project."""
    size = 0
    for i in range(count):
        directory = root / f"models/{i // 50:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        body = texts[i % len(texts)]
        (directory / f"model_{i:04d}.sql").write_text(body)
        size += len(body)
    return size


def _time(command: list[str], cwd: pathlib.Path) -> float:
    start = time.perf_counter()
    subprocess.run(command, cwd=cwd, capture_output=True)
    return time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=500,
                        help="how many SQL files to generate (default 500)")
    args = parser.parse_args()

    texts = _sources()
    executable = shutil.which("sqlalign") or str(pathlib.Path(sys.executable).parent / "sqlalign")

    with tempfile.TemporaryDirectory() as tmp:
        tree = pathlib.Path(tmp)
        size = _build_tree(tree, args.files, texts)

        version = subprocess.run([executable, "--version"], capture_output=True,
                                 text=True).stdout.strip()
        startup = min(_time([executable, "--version"], tree) for _ in range(5))
        check = _time([executable, "--check", "."], tree)
        write = _time([executable, "."], tree)

    print(f"{version} · Python {sys.version.split()[0]} · {sys.platform}")
    print(f"{args.files} files, {size / 1024 / 1024:.1f} MB\n")
    print(f"  --check      {check:7.2f} s   {args.files / check:6.0f} files/s")
    print(f"  rewrite      {write:7.2f} s   {args.files / write:6.0f} files/s")
    print(f"  startup      {startup:7.3f} s   (interpreter, imports, argument parsing)\n")

    per_file = (check - startup) / args.files
    for scale in (1_000, 10_000):
        print(f"  projected {scale:>6,} files: {startup + per_file * scale:6.1f} s")


if __name__ == "__main__":
    main()
