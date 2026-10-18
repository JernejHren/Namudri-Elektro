"""Render the SVG sources to the PNG files in the integration's brand/ folder.

Usage: python assets/brand/render.py   (needs playwright + chromium)
Sizes follow the Home Assistant brand rules: icon 256x256 (@2x 512x512),
logo with the shortest side 192 px (@2x 384 px). Backgrounds are transparent.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path(__file__).parent
OUT = SRC.parent.parent / "custom_components" / "namudri_elektro" / "brand"
SIZES = {"icon": (256, 256), "logo": (760, 192)}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for svg in sorted(SRC.glob("*.svg")):
            kind = "icon" if svg.stem.endswith("icon") else "logo"
            width, height = SIZES[kind]
            for scale, suffix in ((1, ""), (2, "@2x")):
                page = browser.new_page(
                    viewport={"width": width, "height": height}, device_scale_factor=scale
                )
                page.set_content(
                    f"<style>html,body{{margin:0;background:transparent}}</style>{svg.read_text()}"
                )
                page.screenshot(
                    path=str(OUT / f"{svg.stem}{suffix}.png"), omit_background=True
                )
                page.close()
        browser.close()


if __name__ == "__main__":
    main()
