"""Create a small hand-in ZIP without Git internals, build output, or the venv."""

from pathlib import Path
import subprocess
import zipfile

from xv6 import ROOT, XV6


def main():
    files = [ROOT / name for name in ["README.md", "PROJECT_PLAN.md", "requirements.txt"]]
    for directory in ["scripts", "docs", "results"]:
        files.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts)
    # Include both upstream tracked files and our newly added source files.
    names = subprocess.check_output([
        "git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
    ], cwd=XV6).decode().split("\0")
    files.extend(XV6 / name for name in names if name)
    destination = ROOT / "submission.zip"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, Path("mlfq-project") / path.relative_to(ROOT))
    print(f"Created {destination} ({destination.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
