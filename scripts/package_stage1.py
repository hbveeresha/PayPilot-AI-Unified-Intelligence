"""Build and verify a clean unified intelligence delivery archive."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "PayPilot.zip"
PACKAGE_ROOT = "PayPilot"
ROOT_FILES = (
    ".env.example",
    ".gitignore",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "pytest.ini",
    "requirements.txt",
    "run.bat",
    "setup.bat",
)
ROOT_DIRS = ("backend", "frontend", "scripts", "tests", ".vscode")
EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def files_to_package() -> list[Path]:
    files = [ROOT / name for name in ROOT_FILES if (ROOT / name).is_file()]
    for directory in ROOT_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDED_DIRS for part in relative.parts):
                continue
            if path.name == ".env" or path.suffix.lower() in {".pyc", ".db", ".zip", ".log"}:
                continue
            if path.name.endswith((".db-wal", ".db-shm")):
                continue
            files.append(path)
    keep_marker = ROOT / "data" / ".gitkeep"
    if keep_marker.is_file():
        files.append(keep_marker)
    return sorted(set(files), key=lambda item: item.relative_to(ROOT).as_posix().lower())


def main() -> None:
    files = files_to_package()
    if not files:
        raise SystemExit("No project files found to package")
    with ZipFile(ARCHIVE, "w", compression=ZIP_DEFLATED, compresslevel=8) as bundle:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            bundle.write(path, f"{PACKAGE_ROOT}/{relative}")

    required = {
        f"{PACKAGE_ROOT}/backend/app/main.py",
        f"{PACKAGE_ROOT}/backend/app/api/marketplace.py",
        f"{PACKAGE_ROOT}/backend/app/services/workspace_tools.py",
        f"{PACKAGE_ROOT}/backend/app/services/permissions.py",
        f"{PACKAGE_ROOT}/frontend/index.html",
        f"{PACKAGE_ROOT}/frontend/js/app.js",
        f"{PACKAGE_ROOT}/frontend/css/marketplace.css",
        f"{PACKAGE_ROOT}/frontend/images/product-fallback.svg",
        f"{PACKAGE_ROOT}/frontend/images/ASSET_CREDITS.md",
        f"{PACKAGE_ROOT}/tests/test_api.py",
        f"{PACKAGE_ROOT}/README.md",
        f"{PACKAGE_ROOT}/.vscode/tasks.json",
        f"{PACKAGE_ROOT}/.vscode/launch.json",
        f"{PACKAGE_ROOT}/.vscode/settings.json",
    }
    with ZipFile(ARCHIVE) as bundle:
        names = set(bundle.namelist())
        missing = required - names
        if missing:
            raise SystemExit(f"Archive is missing required files: {sorted(missing)}")
        forbidden = [
            name for name in names
            if "/.git/" in name
            or "/.venv/" in name
            or name.endswith((".db", ".db-wal", ".db-shm", ".pyc"))
            or name.endswith("/.env")
        ]
        if forbidden:
            raise SystemExit(f"Archive includes excluded local data: {forbidden}")
        product_photos = [name for name in names if name.startswith(f"{PACKAGE_ROOT}/frontend/images/products/") and name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
        if len(product_photos) != 24:
            raise SystemExit(f"Archive must include all 24 seeded product photos, found {len(product_photos)}")
        if any("product_uploads" in name or "/data/backups/" in name for name in names):
            raise SystemExit("Archive includes private merchant uploads or local database backups")
        print(f"Created {ARCHIVE.name}: {len(names)} files, {ARCHIVE.stat().st_size:,} bytes")
        print(f"Verified all {len(product_photos)} seeded product photographs are bundled.")
        print("Required project files are present; databases, credentials, environments, and nested archives are excluded.")


if __name__ == "__main__":
    main()
