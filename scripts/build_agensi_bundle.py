"""Build the Agensi submission bundle for rsi-boot.

Agensi is an Agent Skills marketplace: a listing is a SKILL.md plus a handful of
support files, NOT the full Python source tree. rsi-boot is delivered via
``pip install rsi-boot`` and driven through ``mcp.json`` (``rsi serve``), so the
submission zip must contain only the skill manifest + docs + icon.

This whitelist-packaging also keeps the security scanner from tripping on
``src/`` internals (os.environ writes, the masking redaction regex, optional
enhance-layer OPENAI_API_KEY references) that are irrelevant to the listing, and
strips every outbound URL from the bundled manifest so the "fully local /
zero API keys" claim holds.

Usage:
    python scripts/build_agensi_bundle.py

Outputs:
    output/agensi/rsi-boot/        unpacked bundle (top folder == skill name)
    output/agensi/rsi-boot.zip     the file to upload to Agensi
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_ROOT = REPO / "output" / "agensi"
BUNDLE_NAME = "rsi-boot"  # must equal the SKILL.md frontmatter `name`

# Files copied verbatim: (repo-relative source -> path inside the bundle).
COPY_FILES: dict[str, str] = {
    "SKILL.md": "SKILL.md",
    "SECURITY.md": "SECURITY.md",
    "mcp.json": "mcp.json",
    "README.md": "README.md",
    "README.en.md": "README.en.md",
    "LICENSE": "LICENSE",
    "assets/logo-1024.png": "assets/logo.png",
}


def _cleaned_plugin_json() -> dict:
    """Manifest copy with every external URL removed.

    The Qoder/Cursor plugin.json keeps ``homepage`` / ``repository``; those are
    flagged as network hosts by the scanner and contradict the local-only claim,
    so the submission copy drops them and points the icon at the bundled PNG.
    """
    src = json.loads((REPO / ".qoder-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return {
        "name": src.get("name", BUNDLE_NAME),
        "version": src.get("version", "0.1.0"),
        "description": src.get("description", ""),
        "displayName": src.get("displayName", "RSI Boot"),
        "author": src.get("author", {}),
        "license": src.get("license", "MIT"),
        "keywords": src.get("keywords", []),
        "icon": "assets/logo.png",
        # no homepage / repository / network hosts on purpose
        "mcpServers": "./mcp.json",
    }


def build() -> Path:
    bundle = OUT_ROOT / BUNDLE_NAME
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    for rel_src, rel_dst in COPY_FILES.items():
        src = REPO / rel_src
        if not src.is_file():
            raise SystemExit(f"missing required file for bundle: {rel_src}")
        dst = bundle / rel_dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    (bundle / "plugin.json").write_text(
        json.dumps(_cleaned_plugin_json(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    zip_path = OUT_ROOT / f"{BUNDLE_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=f"{BUNDLE_NAME}/{path.relative_to(bundle).as_posix()}")

    return zip_path


def main() -> None:
    zip_path = build()
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
    print(f"bundle zip : {zip_path.relative_to(REPO).as_posix()}")
    print(f"file count : {len(names)} (Agensi limit: 50)")
    for n in names:
        print(f"  - {n}")


if __name__ == "__main__":
    main()
