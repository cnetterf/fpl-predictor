import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SEASON = "2025-26"
ELO_SEASON = "2025-2026"
OUTPUT_DIR = ROOT / "archives" / SEASON

OFFICIAL_REPO = "https://github.com/vaastav/Fantasy-Premier-League.git"
OFFICIAL_BRANCH = "master"
OFFICIAL_PATH = f"data/{SEASON}"

ELO_REPO = "https://github.com/olbauday/FPL-Core-Insights.git"
ELO_BRANCH = "main"
ELO_PATH = f"data/{ELO_SEASON}"


def run(*args, cwd=None):
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def sparse_clone(repo, branch, sparse_path, destination):
    run(
        "git",
        "clone",
        "--depth",
        "1",
        "--filter=blob:none",
        "--sparse",
        "--branch",
        branch,
        repo,
        str(destination),
    )
    run("git", "sparse-checkout", "set", sparse_path, cwd=destination)
    return run("git", "rev-parse", "HEAD", cwd=destination)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree(source, destination):
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"),
    )


def official_gw_coverage(season_dir):
    merged = season_dir / "gws" / "merged_gw.csv"
    if not merged.exists():
        return []
    with merged.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        return sorted({int(row["GW"]) for row in rows if row.get("GW")})


def elo_gw_coverage(season_dir):
    by_gameweek = season_dir / "By Gameweek"
    if not by_gameweek.exists():
        return []
    return sorted(
        int(path.name.removeprefix("GW"))
        for path in by_gameweek.iterdir()
        if path.is_dir() and path.name.removeprefix("GW").isdigit()
    )


def make_archive(source_dir, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_base = output_path.with_suffix("")
    created = Path(
        shutil.make_archive(
            str(temporary_base),
            "zip",
            root_dir=source_dir.parent,
            base_dir=source_dir.name,
        )
    )
    if created != output_path:
        created.replace(output_path)


def build_archives():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model_commit = run("git", "rev-parse", "HEAD", cwd=ROOT)

    with tempfile.TemporaryDirectory(prefix="fpl-season-archive-") as temp:
        temp_dir = Path(temp)
        official_repo = temp_dir / "official-repo"
        elo_repo = temp_dir / "elo-repo"

        official_commit = sparse_clone(
            OFFICIAL_REPO,
            OFFICIAL_BRANCH,
            OFFICIAL_PATH,
            official_repo,
        )
        elo_commit = sparse_clone(
            ELO_REPO,
            ELO_BRANCH,
            ELO_PATH,
            elo_repo,
        )

        official_source = official_repo / OFFICIAL_PATH
        elo_source = elo_repo / ELO_PATH
        official_gws = official_gw_coverage(official_source)
        elo_gws = elo_gw_coverage(elo_source)
        expected_gws = list(range(1, 39))
        if official_gws != expected_gws:
            raise RuntimeError(f"Official archive GW coverage is {official_gws}")
        if elo_gws != expected_gws:
            raise RuntimeError(f"Elo archive GW coverage is {elo_gws}")

        raw_root = temp_dir / f"fpl-{SEASON}-raw-sources"
        copy_tree(official_source, raw_root / "official-fpl-historical")
        copy_tree(elo_source, raw_root / "elo-insights")

        model_root = temp_dir / f"fpl-{SEASON}-model-snapshot"
        code_dir = model_root / "code"
        code_dir.mkdir(parents=True)
        for name in (
            "server.py",
            "generate_static_data.py",
            "backtest_model.py",
            "app.js",
            "index.html",
            "README.md",
        ):
            shutil.copy2(ROOT / name, code_dir / name)

        snapshot_data = model_root / "data"
        snapshot_data.mkdir(parents=True)
        for name in (
            "cache.json",
            "static_predictions.json",
            "static_backtest.json",
        ):
            source = ROOT / "data" / name
            if source.exists():
                shutil.copy2(source, snapshot_data / name)
        copy_tree(ROOT / "data" / "backtest_windows", snapshot_data / "backtest_windows")

        metadata = {
            "season": SEASON,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "model_commit": model_commit,
            "official_source": {
                "repository": OFFICIAL_REPO,
                "commit": official_commit,
                "path": OFFICIAL_PATH,
                "gameweeks": official_gws,
            },
            "elo_source": {
                "repository": ELO_REPO,
                "commit": elo_commit,
                "path": ELO_PATH,
                "gameweeks": elo_gws,
            },
            "local_model_cache_note": "The local cache snapshot covers GW1-GW35; the public raw archives cover GW1-GW38.",
        }
        (raw_root / "ARCHIVE_MANIFEST.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )
        (model_root / "ARCHIVE_MANIFEST.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )

        raw_archive = OUTPUT_DIR / f"fpl-{SEASON}-raw-sources.zip"
        model_archive = OUTPUT_DIR / f"fpl-{SEASON}-model-snapshot.zip"
        make_archive(raw_root, raw_archive)
        make_archive(model_root, model_archive)

    assets = []
    for path in (raw_archive, model_archive):
        assets.append(
            {
                "file": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )

    release_manifest = {
        "season": SEASON,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_commit": model_commit,
        "assets": assets,
    }
    manifest_path = OUTPUT_DIR / "release-manifest.json"
    manifest_path.write_text(json.dumps(release_manifest, indent=2), encoding="utf-8")
    checksum_path = OUTPUT_DIR / "SHA256SUMS"
    checksum_path.write_text(
        "".join(f"{asset['sha256']}  {asset['file']}\n" for asset in assets),
        encoding="utf-8",
    )
    print(json.dumps(release_manifest, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.parse_args()
    build_archives()


if __name__ == "__main__":
    main()
