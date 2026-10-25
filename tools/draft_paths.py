"""Where the drafts live, in one place (Kevin, 2026-10-24: "let's reorganize drafts").

    Drafts/
      Upcoming/<country>/        what is being worked on right now (the next launch)
      Full Articles/<country>/   full-article sets waiting their turn
      Field Notes/<country>/     field-notes drafts (field-notes.html)
      .Archive/<country>/        retired pages: never served, never checked, never pushed

Every draft page is therefore three folders below the repo root (Drafts/<folder>/<country>/<page>.html) and links
the site as ../../../styles.css. Before 2026-10-24 full articles sat in `Drafts/.Full Articles/` (a dot folder that
Python's glob could not see) and field-notes drafts in `Drafts/<country>/` (two folders down), so the depth told the
two apart. Now the depth is always 3 and the FOLDER says which kind of draft it is: tools ask this module instead of
spelling paths out.

    from draft_paths import draft_pages, live_rel, locate, country_of
"""
import glob as _glob
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFTS = ROOT / "Drafts"
UPCOMING = DRAFTS / "Upcoming"
FULL = DRAFTS / "Full Articles"
FIELD = DRAFTS / "Field Notes"
ARCHIVE = DRAFTS / ".Archive"

# the folders being worked on, in the order a page is looked up (a country being launched wins over a waiting one)
WORK = ("Upcoming", "Full Articles", "Field Notes")
ALL = WORK + (".Archive",)
PREFIXES = tuple("Drafts/%s/" % n for n in ALL)
# what the paths looked like before the reorganization; migrate() maps them
OLD_FULL = "Drafts/.Full Articles/"
OLD_ARCHIVED = "Drafts/Archived Articles/"


def work_dirs(folders=WORK):
    return [DRAFTS / n for n in folders]


def draft_pages(country="*", name="*.html", folders=WORK):
    """every draft page in the given folders, as Paths, sorted; a dot folder at the country level is scratch"""
    out = []
    for f in folders:
        for p in sorted((DRAFTS / f).glob("%s/%s" % (country, name))):
            if p.is_file() and not p.parent.name.startswith("."):
                out.append(p)
    return out


def draft_rels(country="*", name="*.html", folders=WORK):
    return [p.relative_to(ROOT).as_posix() for p in draft_pages(country, name, folders)]


def globs(country="*", name="*.html", folders=WORK):
    """repo-relative glob patterns, for tools that collect patterns ("Drafts/Upcoming/*/*.html", ...)"""
    return ["Drafts/%s/%s/%s" % (f, country, name) for f in folders]


def abs_globs(country="*", name="*.html", folders=WORK):
    return [os.path.join(str(ROOT), "Drafts", f, country, name) for f in folders]


def glob_rels(country="*", name="*.html", folders=WORK):
    out = []
    for pat in abs_globs(country, name, folders):
        out += [Path(p).relative_to(ROOT).as_posix() for p in sorted(_glob.glob(pat))]
    return out


def field_notes(country="*", folders=WORK):
    """the field-notes drafts (in Field Notes, or in Upcoming when one is next)"""
    return draft_pages(country, "field-notes.html", folders)


def articles(country="*", folders=("Upcoming", "Full Articles")):
    """full-article drafts (every page that is not a field-notes page)"""
    return [p for p in draft_pages(country, "*.html", folders) if p.name != "field-notes.html"]


def split(rel):
    """(folder | None, country, page) for 'Drafts/<folder>/<country>/<page>' or a live '<country>/<page>'"""
    r = str(rel).replace("\\", "/")
    while r.startswith("./"):
        r = r[2:]
    for old, new in ((OLD_FULL, "Drafts/Full Articles/"), (OLD_ARCHIVED, "Drafts/.Archive/")):
        if r.startswith(old):
            r = new + r[len(old):]
    parts = [x for x in r.split("/") if x]
    if len(parts) >= 4 and parts[0] == "Drafts" and parts[1] in ALL:
        return parts[1], parts[2], "/".join(parts[3:])
    if len(parts) == 3 and parts[0] == "Drafts":            # the old Drafts/<country>/field-notes.html
        return "Field Notes", parts[1], parts[2]
    if len(parts) >= 2 and parts[0] != "Drafts":
        return None, parts[0], "/".join(parts[1:])
    return None, "", parts[-1] if parts else ""


def is_draft(rel):
    return str(rel).replace("\\", "/").startswith("Drafts/")


def live_rel(rel):
    """where a page is (or will be) published: Drafts/<folder>/<c>/<p> -> <c>/<p>; a live path is returned as is"""
    folder, country, page = split(rel)
    return "%s/%s" % (country, page) if country else page


def country_of(rel):
    return split(rel)[1]


def folder_of(rel):
    return split(rel)[0]


def locate(country, page, folders=WORK):
    """the draft that holds <country>/<page>, as a repo-relative path, or None"""
    for f in folders:
        p = DRAFTS / f / country / page
        if p.is_file():
            return p.relative_to(ROOT).as_posix()
    return None


def country_folder(country, folders=("Upcoming", "Full Articles")):
    """the draft folder that holds a country's full articles (Upcoming before Full Articles), or None"""
    for f in folders:
        d = DRAFTS / f / country
        if d.is_dir() and any(d.glob("*.html")):
            return d
    return None


def depth_prefix(rel):
    """'../' x the folders a page sits below the repo root (live 1, every draft 3)"""
    return "../" * (len(str(rel).replace("\\", "/").split("/")) - 1)


def migrate(rel):
    """an old draft path -> where that page lives now (unchanged when it is not a draft path or has no new home)"""
    r = str(rel).replace("\\", "/")
    if r.startswith(OLD_FULL) or r.startswith(OLD_ARCHIVED) or (r.startswith("Drafts/") and r.count("/") == 2):
        folder, country, page = split(r)
        if folder == ".Archive":
            return "Drafts/.Archive/%s/%s" % (country, page)
        hit = locate(country, page, ALL)
        return hit or r
    return r
