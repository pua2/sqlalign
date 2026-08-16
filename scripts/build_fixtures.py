"""Split a samples file into per-sample fixture files by '-- #N' headers."""
import re
import sys
from pathlib import Path

HEADER = re.compile(r"^-- #(\d+)[:\s]", re.M)


def split(src: str) -> dict[str, str]:
    marks = list(HEADER.finditer(src))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(src)
        out[f"{int(m.group(1)):02d}"] = src[m.start():end].rstrip() + "\n"
    return out


if __name__ == "__main__":
    src_file, dest = Path(sys.argv[1]), Path(sys.argv[2])
    dest.mkdir(parents=True, exist_ok=True)
    for num, body in split(src_file.read_text()).items():
        (dest / f"{num}.sql").write_text(body)
        print(num)
