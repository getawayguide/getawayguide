#!/usr/bin/env python3
"""
One-time migration script: fixes all links after moving El Salvador files
into el-salvador/ subfolder.

Run once from project root:
    python tools/migrate_folders.py
"""

import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
ES_DIR = PROJECT / "el-salvador"

# El Salvador article filenames (stay the same, now in el-salvador/)
ES_ARTICLES = [
    "el-salvador-itinerary.html",
    "top-10-el-salvador.html",
    "santa-ana.html",
    "ruta-de-las-flores.html",
    "el-tunco.html",
    "7-waterfalls-hike.html",
]

# Root-level pages that need ../ when referenced from inside el-salvador/
ROOT_PAGES = [
    "index.html",
    "destinations.html",
    "resources.html",
    "about.html",
    "contact.html",
    "privacy.html",
    "posts.html",
]


def fix_moved_file(path: Path):
    """Fix links inside files that were moved to el-salvador/."""
    text = path.read_text(encoding="utf-8")
    original = text

    # CSS stylesheet
    text = re.sub(r'href="styles\.css(\?[^"]*)?"', r'href="../styles.css\1"', text)

    # Nav logo (index.html = site home)
    text = text.replace('href="index.html" class="nav-logo"', 'href="../index.html" class="nav-logo"')

    # Root page hrefs (but NOT el-salvador article hrefs)
    for page in ROOT_PAGES:
        # href="page.html" and href="page.html#anchor"
        text = re.sub(rf'href="{re.escape(page)}(#[^"]*)?(")',
                      lambda m: f'href="../{page}{m.group(1) or ""}\"', text)

    # El Salvador country page → index.html (same folder)
    text = text.replace('href="el-salvador.html"', 'href="index.html"')
    text = text.replace("location.href='el-salvador.html'", "location.href='index.html'")
    text = text.replace('onclick="location.href=\'el-salvador.html\'"',
                        'onclick="location.href=\'index.html\'"')

    # onclick navigation to root pages
    for page in ROOT_PAGES:
        text = text.replace(f"location.href='{page}'", f"location.href='../{page}'")
        text = text.replace(f'location.href="{page}"', f'location.href="../{page}"')

    # Inline background image URLs (url('Images/...) → url('../Images/...)
    text = text.replace("url('Images/", "url('../Images/")
    text = text.replace('url("Images/', 'url("../Images/')

    # <img src="Images/..."> tags
    text = re.sub(r'(<img [^>]*src=")Images/', r'\1../Images/', text)

    # Mobile nav continent links (destinations.html#region)
    # Already handled by the ROOT_PAGES loop above for destinations.html

    # Fix nav active page detection — in el-salvador/ folder, index.html = country page not home
    # Replace the JS active-page map to correctly highlight Destinations
    text = text.replace(
        "var m={'index.html':'nav-home','':'nav-home','destinations.html':'nav-destinations',\n"
        "         'resources.html':'nav-resources','about.html':'nav-about'};",
        "var m={'destinations.html':'nav-destinations',\n"
        "         'resources.html':'nav-resources','about.html':'nav-about'};"
    )
    # Also handle single-line version
    text = re.sub(
        r"var m=\{'index\.html':'nav-home','':'nav-home','destinations\.html':'nav-destinations',"
        r"'resources\.html':'nav-resources','about\.html':'nav-about'\};",
        "var m={'destinations.html':'nav-destinations',"
        "'resources.html':'nav-resources','about.html':'nav-about'};",
        text
    )

    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"  [fixed] {path.name}")
    else:
        print(f"  [skip]  {path.name} (no changes)")


def fix_root_file(path: Path):
    """Fix links in root-level files that point to moved El Salvador pages."""
    text = path.read_text(encoding="utf-8")
    original = text

    # El Salvador country page
    text = text.replace('href="el-salvador.html"', 'href="el-salvador/"')
    text = text.replace("location.href='el-salvador.html'", "location.href='el-salvador/'")
    text = text.replace('onclick="location.href=\'el-salvador.html\'"',
                        'onclick="location.href=\'el-salvador/\'"')

    # El Salvador article pages
    for article in ES_ARTICLES:
        text = text.replace(f'href="{article}"', f'href="el-salvador/{article}"')
        text = text.replace(f"location.href='{article}'", f"location.href='el-salvador/{article}'")
        text = text.replace(f"onclick=\"location.href='{article}'\"",
                            f"onclick=\"location.href='el-salvador/{article}'\"")

    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"  [fixed] {path.name}")
    else:
        print(f"  [skip]  {path.name} (no changes)")


def main():
    print("\n=== Fixing links in moved El Salvador files ===")
    for filename in ["index.html"] + ES_ARTICLES:
        path = ES_DIR / filename
        if path.exists():
            fix_moved_file(path)
        else:
            print(f"  [MISSING] {filename}")

    print("\n=== Fixing links in root-level files ===")
    root_html = [p for p in PROJECT.glob("*.html")]
    for path in sorted(root_html):
        fix_root_file(path)

    print("\nMigration complete.")


if __name__ == "__main__":
    main()
