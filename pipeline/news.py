"""무료 뉴스 RSS(Google 뉴스)에서 화제 이슈 후보를 모은다.

Google 뉴스 RSS의 각 항목은 같은 이슈를 다룬 다른 언론사 기사 묶음(최대 5개)을 함께 준다.
묶음에 붙은 언론사 수를 화제성(heat)으로 본다. 기사 본문은 저장하지 않는다(저작권).
"""
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from . import db

UA = {"User-Agent": "Mozilla/5.0 (compatible; issue-digest/1.0)"}
LI = re.compile(r'<li><a href="([^"]+)"[^>]*>(.*?)</a>(?:&nbsp;|\s)*<font[^>]*>(.*?)</font></li>', re.S)


def _strip_outlet(title: str, outlet: str | None) -> str:
    title = html.unescape(title).strip()
    if outlet and title.endswith(" - " + outlet):
        title = title[: -len(outlet) - 3]
    return title.strip()


def parse(xml_bytes: bytes, feed: str) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for pos, it in enumerate(root.findall(".//item")):
        link = it.findtext("link") or ""
        outlet = it.findtext("source")
        title = _strip_outlet(it.findtext("title") or "", outlet)
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            pub = datetime.now(timezone.utc)
        related = []
        for _href, t, o in LI.findall(html.unescape(it.findtext("description") or "")):
            o = html.unescape(re.sub("<[^>]+>", "", o)).strip()
            t = html.unescape(re.sub("<[^>]+>", "", t)).strip()
            if t and t != title:
                related.append({"title": t, "outlet": o})
        outlets = {outlet} | {r["outlet"] for r in related}
        out.append({
            "id": hashlib.sha1(link.encode()).hexdigest()[:16],
            "feed": feed, "title": title, "outlet": outlet, "link": link,
            "related": related, "heat": len({o for o in outlets if o}), "rank": pos,
            "published_at": pub.isoformat(timespec="seconds"),
        })
    return out


def fetch(cfg) -> list[dict]:
    items = []
    for f in cfg["news"]["feeds"]:
        r = requests.get(f["url"], headers=UA, timeout=30)
        r.raise_for_status()
        items += parse(r.content, f["name"])
    return items


def store(conn, items: list[dict]) -> int:
    new = 0
    now = db.now_iso()
    for c in items:
        cur = conn.execute(
            """INSERT OR IGNORE INTO candidates (id, feed, title, outlet, link, related, heat, rank, published_at, seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (c["id"], c["feed"], c["title"], c["outlet"], c["link"],
             json.dumps(c["related"], ensure_ascii=False), c["heat"], c["rank"], c["published_at"], now))
        new += cur.rowcount
    conn.commit()
    return new


def recent(conn, cfg) -> list:
    """아직 쓰지 않은 최근 후보. 피드마다 Google 뉴스 노출 순서(rank)대로 나눠 뽑는다.
    연예 피드에 몫을 더 준다 (채널 성격)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=cfg["news"]["max_age_hours"])).isoformat(timespec="seconds")
    n = cfg["news"]["candidates_for_pick"]
    share = {"entertainment": n * 3 // 5}
    out = []
    for f in cfg["news"]["feeds"]:
        k = share.get(f["name"], n - share.get("entertainment", 0))
        out += conn.execute(
            """SELECT * FROM candidates WHERE status = 'new' AND feed = ? AND published_at >= ?
               ORDER BY seen_at DESC, rank ASC LIMIT ?""", (f["name"], since, k)).fetchall()
    return out


def used_titles(conn, days: int = 7) -> list[str]:
    """최근에 이미 다룬 이슈 제목 (같은 사건 반복 방지용으로 Claude에게 보여 줌)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return [r[0] for r in conn.execute("SELECT title FROM stories WHERE created_at >= ?", (since,))]
