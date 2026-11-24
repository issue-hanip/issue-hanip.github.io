"""성과 수집: YouTube 숏츠의 조회수·좋아요·댓글·시청 지속률을 날짜별로 쌓는다 (learn.py가 이 표로 학습).

  1) Data API 통계 (조회·좋아요·댓글, 실시간에 가까움)
  2) Analytics API (평균 시청 비율·공유, 1~2일 늦게 잡힘) — 있으면 덧붙임
  3) 둘 다 안 되면 공개 시청 페이지에서 조회수만
"""
import re
from datetime import datetime, timedelta, timezone

import requests

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    slug TEXT NOT NULL,
    platform TEXT NOT NULL,          -- youtube | instagram | tiktok
    day TEXT NOT NULL,               -- YYYY-MM-DD (UTC)
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    retention REAL,                  -- 평균 시청 비율(%)
    shares INTEGER,
    PRIMARY KEY (slug, platform, day)
);
"""


def ensure(conn):
    conn.executescript(SCHEMA)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def save(conn, slug, platform, views=None, likes=None, comments=None, retention=None, shares=None, day=None):
    ensure(conn)
    conn.execute("""INSERT OR REPLACE INTO metrics (slug, platform, day, views, likes, comments, retention, shares)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                 (slug, platform, day or _today(), views, likes, comments, retention, shares))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def collect_youtube(conn) -> dict:
    """반환: {"videos": 수집한 영상 수, "retention": 시청 비율까지 잡힌 수, "source": ...}"""
    ensure(conn)
    rows = conn.execute("SELECT slug, youtube_id FROM videos WHERE youtube_id IS NOT NULL").fetchall()
    by_id = {r["youtube_id"]: r["slug"] for r in rows}
    if not by_id:
        return {"videos": 0, "retention": 0, "source": "-"}
    from . import youtube
    stats, extra, source = {}, {}, []
    if youtube.configured():
        try:
            tok = youtube.access_token()
            ids = list(by_id)
            for i in range(0, len(ids), 50):
                r = requests.get("https://www.googleapis.com/youtube/v3/videos", timeout=30,
                                 params={"part": "statistics", "id": ",".join(ids[i:i + 50])},
                                 headers={"Authorization": f"Bearer {tok}"})
                r.raise_for_status()
                for it in r.json().get("items", []):
                    s = it.get("statistics", {})
                    stats[it["id"]] = (_int(s.get("viewCount")), _int(s.get("likeCount")),
                                       _int(s.get("commentCount")))
            source.append("data-api")
        except Exception as e:  # noqa: BLE001
            print(f"  YouTube 통계 실패: {type(e).__name__}: {str(e)[:200]}")
        try:
            end = datetime.now(timezone.utc).date()
            extra = youtube.analytics(list(by_id), (end - timedelta(days=90)).isoformat(), end.isoformat())
            source.append("analytics")
        except Exception as e:  # noqa: BLE001
            print(f"  YouTube 분석 실패: {type(e).__name__}: {str(e)[:200]}")
    for vid in by_id:
        if vid in stats:
            continue
        try:   # 공개 페이지 (조회수만)
            r = requests.get(f"https://www.youtube.com/watch?v={vid}", timeout=30,
                             headers={"Accept-Language": "en-US,en;q=0.8", "User-Agent": "Mozilla/5.0"})
            m = re.search(r'"viewCount":"(\d+)"', r.text)
            if m:
                stats[vid] = (int(m.group(1)), None, None)
                if "page" not in source:
                    source.append("page")
        except requests.RequestException:
            continue
    n_ret = 0
    for vid, slug in by_id.items():
        v, l, c = stats.get(vid, (None, None, None))
        a = extra.get(vid) or {}
        if v is None and not a:
            continue
        ret = a.get("avg_view_pct")
        n_ret += ret is not None
        save(conn, slug, "youtube", v if v is not None else a.get("views"),
             l if l is not None else a.get("likes"), c, ret, a.get("shares"))
    conn.commit()
    return {"videos": len(set(stats) | set(extra)), "retention": n_ret, "source": "+".join(source) or "-"}
