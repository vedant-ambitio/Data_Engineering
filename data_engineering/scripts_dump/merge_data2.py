"""
Merge new data from data_2 into existing masters_data and phd_data folders.
Only copies NEW universities and NEW files — never overwrites existing data.
"""

import shutil
from pathlib import Path

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")

CONFIGS = [
    {
        "name": "Masters",
        "source": BASE / "data_2" / "masters_data_2",
        "dest": BASE / "masters_data" / "masters",
    },
    {
        "name": "PhD",
        "source": BASE / "data_2" / "phd_data_2",
        "dest": BASE / "phd_data" / "phd",
    },
]


def merge(source: Path, dest: Path, name: str):
    new_unis = 0
    new_files = 0
    skipped_files = 0

    for uni_dir in sorted(source.iterdir()):
        if not uni_dir.is_dir() or "__MACOSX" in uni_dir.name or uni_dir.name.startswith("._"):
            continue

        dest_uni = dest / uni_dir.name

        if not dest_uni.exists():
            # Entirely new university — copy whole folder
            try:
                shutil.copytree(uni_dir, dest_uni, ignore_dangling_symlinks=True)
                file_count = len(list(dest_uni.glob("*.md")))
                new_unis += 1
                new_files += file_count
            except (FileNotFoundError, OSError) as e:
                # Some files have names too long for Windows — copy what we can
                dest_uni.mkdir(parents=True, exist_ok=True)
                copied = 0
                for md_file in uni_dir.glob("*.md"):
                    try:
                        if not md_file.name.startswith("._"):
                            shutil.copy2(md_file, dest_uni / md_file.name)
                            copied += 1
                    except (FileNotFoundError, OSError):
                        pass
                new_unis += 1
                new_files += copied
        else:
            # University exists — copy only new .md files
            for md_file in uni_dir.glob("*.md"):
                if md_file.name.startswith("._"):
                    continue
                try:
                    dest_file = dest_uni / md_file.name
                    if not dest_file.exists():
                        shutil.copy2(md_file, dest_file)
                        new_files += 1
                    else:
                        skipped_files += 1
                except (FileNotFoundError, OSError):
                    # Skip files with names too long for Windows
                    skipped_files += 1

    print(f"  {name}:")
    print(f"    New universities copied: {new_unis}")
    print(f"    New files copied: {new_files}")
    print(f"    Duplicate files skipped: {skipped_files}")


def main():
    print("Merging data_2 into existing folders...\n")

    for cfg in CONFIGS:
        if not cfg["source"].exists():
            print(f"  {cfg['name']}: source not found at {cfg['source']}")
            continue
        if not cfg["dest"].exists():
            print(f"  {cfg['name']}: dest not found at {cfg['dest']}")
            continue
        merge(cfg["source"], cfg["dest"], cfg["name"])

    # Print final counts
    print("\nFinal counts:")
    for cfg in CONFIGS:
        unis = len([d for d in cfg["dest"].iterdir() if d.is_dir() and "__MACOSX" not in d.name])
        files = len(list(cfg["dest"].rglob("*.md")))
        print(f"  {cfg['name']}: {unis} universities, {files} courses")


if __name__ == "__main__":
    main()
