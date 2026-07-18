#!/usr/bin/env python3
"""
gen_sitemap.py — generate sitemap.xml for getawayguide.io and register it in robots.txt.

Walks live HTML pages (excludes Drafts/, .tmp/, .git/, and robots-disallowed pages),
emits clean URLs, and uses each file's last git-commit date as <lastmod>.

Usage: python tools/gen_sitemap.py
Re-run any time you add/rename pages (or wire it into the publish flow).
"""
import os, glob, subprocess, datetime, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOMAIN = "https://getawayguide.io"
EXCLUDE = {"editor.html", "404.html"}   # private/tool pages, and the noindex 404
TODAY = datetime.date.today().isoformat()

def rel(p): return os.path.relpath(p, ROOT).replace("\\", "/")

def url_for(r):
    if r == "index.html": return DOMAIN + "/"
    if r.endswith("/index.html"): return "%s/%s/" % (DOMAIN, r[:-len("/index.html")])
    return "%s/%s" % (DOMAIN, r)

def priority(r):
    if r == "index.html": return "1.0"
    if r in ("destinations.html", "posts.html", "resources.html"): return "0.8"
    if r.endswith("field-notes.html") or r.endswith("/index.html"): return "0.7"
    if r.endswith(".html") and "/" in r: return "0.6"   # article sub-pages
    if r in ("contact.html", "privacy.html"): return "0.3"
    return "0.6"

def changefreq(r):
    if r in ("index.html", "destinations.html", "posts.html"): return "weekly"
    if r in ("contact.html", "privacy.html", "about.html"): return "yearly"
    return "monthly"

def git_lastmod(path):
    try:
        out = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%cs", "--", rel(path)],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        return out or TODAY
    except Exception:
        return TODAY

pages = []
for p in glob.glob(os.path.join(ROOT, "**", "*.html"), recursive=True):
    r = rel(p)
    if r.startswith(("Drafts/", ".tmp/", ".git/")): continue
    if os.path.basename(r) in EXCLUDE: continue
    pages.append(r)
pages.sort(key=lambda r: (r != "index.html", r))   # homepage first

lines = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
for r in pages:
    lines += ["  <url>",
              "    <loc>%s</loc>" % url_for(r),
              "    <lastmod>%s</lastmod>" % git_lastmod(os.path.join(ROOT, r)),
              "    <changefreq>%s</changefreq>" % changefreq(r),
              "    <priority>%s</priority>" % priority(r),
              "  </url>"]
lines.append("</urlset>")
open(os.path.join(ROOT, "sitemap.xml"), "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
print("Wrote sitemap.xml with %d URLs" % len(pages))

# register sitemap in robots.txt (idempotent)
rp = os.path.join(ROOT, "robots.txt")
robots = open(rp, encoding="utf-8").read() if os.path.exists(rp) else "User-agent: *\n"
if "Sitemap:" not in robots:
    if not robots.endswith("\n"): robots += "\n"
    robots += "\nSitemap: %s/sitemap.xml\n" % DOMAIN
    open(rp, "w", encoding="utf-8", newline="\n").write(robots)
    print("Added Sitemap line to robots.txt")
else:
    print("robots.txt already references a sitemap")
