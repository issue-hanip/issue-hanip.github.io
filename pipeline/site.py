"""정적 사이트 빌드 (GitHub Pages). 내려진 글(withdrawn)은 빌드에서 빠진다."""
import json
import shutil
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import PUBLIC_DIR, ROOT

KST = timezone(timedelta(hours=9))
CAT_SLUG = {"연예": "entertainment", "사회": "society", "화제": "trending", "스포츠": "sports", "경제": "economy",
            "기타": "etc"}


def _env():
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
    env.filters["date"] = lambda iso: datetime.fromisoformat(iso).astimezone(KST).strftime("%Y-%m-%d")
    return env


def _posts(conn, site):
    vids = {r["slug"]: r["youtube_id"] for r in conn.execute("SELECT slug, youtube_id FROM videos")}
    out = []
    for r in conn.execute("SELECT * FROM stories WHERE status = 'published' ORDER BY created_at DESC"):
        p = dict(r)
        p["tags"] = json.loads(p["tags"])
        p["sources"] = json.loads(p["sources"])
        p["category_slug"] = CAT_SLUG.get(p["category"], "etc")
        p["url"] = f"{site['base_path']}/posts/{p['slug']}/"
        p["abs_url"] = f"{site['base_url']}/posts/{p['slug']}/"
        p["youtube_id"] = vids.get(p["slug"])
        p["image"] = f"{site['base_url']}/media/{p['slug']}.jpg"
        p["html"] = markdown.markdown(p["body_md"], extensions=["extra"])
        out.append(p)
    return out


def build(conn, cfg, out=PUBLIC_DIR, media_dir=None) -> int:
    site = dict(cfg["site"])
    site["base_path"] = urlparse(site["base_url"]).path.rstrip("/")
    env = _env()
    build_date = datetime.now(KST).strftime("%Y-%m-%d")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(ROOT / "static", out / "static")
    posts = _posts(conn, site)

    def write(rel: str, html: str):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")

    ctx = {"site": site, "build_date": build_date}
    cats = sorted({p["category"] for p in posts}, key=list(CAT_SLUG).index)
    write("index.html", env.get_template("index.html").render(
        posts=posts[:60], categories=[{"name": c, "slug": CAT_SLUG[c]} for c in cats], **ctx))
    for c in cats:
        write(f"category/{CAT_SLUG[c]}/index.html", env.get_template("category.html").render(
            name=c, slug=CAT_SLUG[c], posts=[p for p in posts if p["category"] == c], **ctx))
    for i, p in enumerate(posts):
        related = [q for q in posts if q["slug"] != p["slug"]][:6]
        jsonld = json.dumps({
            "@context": "https://schema.org", "@type": "NewsArticle", "headline": p["title"],
            "description": p["description"], "datePublished": p["created_at"], "dateModified": p["updated_at"],
            "author": {"@type": "Organization", "name": site["author"]},
            "mainEntityOfPage": p["abs_url"]}, ensure_ascii=False)
        write(f"posts/{p['slug']}/index.html", env.get_template("post.html").render(
            post=p, related=related, jsonld=jsonld, **ctx))
    for name in ("about", "privacy"):
        write(f"{name}/index.html", env.get_template(f"{name}.html").render(**ctx))
    write("404.html", env.get_template("404.html").render(**ctx))
    write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {site['base_url']}/sitemap.xml\n")
    urls = [site["base_url"] + "/"] + [p["abs_url"] for p in posts]
    write("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + "".join(f"<url><loc>{escape(u)}</loc></url>\n" for u in urls) + "</urlset>\n")
    items = "".join(
        f"<item><title>{escape(p['title'])}</title><link>{escape(p['abs_url'])}</link>"
        f"<guid>{escape(p['abs_url'])}</guid><description>{escape(p['description'])}</description>"
        f"<pubDate>{format_datetime(datetime.fromisoformat(p['created_at']))}</pubDate></item>\n" for p in posts[:30])
    write("feed.xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>{escape(site["title"])}</title>'
          f"<link>{escape(site['base_url'])}/</link><description>{escape(site['tagline'])}</description>\n{items}</channel></rss>\n")
    # 오늘 만든 영상·표지 (Instagram 게시용 공개 주소)
    if media_dir and media_dir.exists():
        (out / "media").mkdir(exist_ok=True)
        for f in list(media_dir.glob("*.mp4")) + list(media_dir.glob("*.jpg")):
            shutil.copy2(f, out / "media" / f.name)
    return len(posts)
