"""파이프라인 진입점.

    python -m pipeline.run all              # 수집 → 이슈 선정·작성·검수 → 숏츠·YouTube → 사이트 빌드
    python -m pipeline.run instagram        # (배포 뒤) 릴스 게시
    python -m pipeline.run withdraw <slug>  # 정정·삭제 요청 대응: 글을 내리고 다음 빌드에서 뺀다
    python -m pipeline.run all --fake-writer --tts silent   # Claude 없이 전체 시험
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import db, news, site, stories
from .config import DATA_DIR, DB_PATH, load_config

KST = timezone(timedelta(hours=9))
SECRET_ENVS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "IG_ACCESS_TOKEN", "IG_STATE_KEY",
               "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "YT_CLIENT_ID")


def log(msg: str):
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}", flush=True)


def redact(summary: dict) -> dict:
    text = json.dumps(summary, ensure_ascii=False)
    for name in SECRET_ENVS:
        val = os.environ.get(name)
        if val and len(val) >= 8:
            text = text.replace(val, "***").replace(urllib.parse.quote(val, safe=""), "***")
    text = re.sub(r"(?i)(key|token|secret)=[^&\s\"']+", r"\1=***", text)
    return json.loads(text)


def _writer(cfg, fake: bool):
    from .write import ClaudeWriter, FakeWriter
    g = cfg["generate"]
    return FakeWriter() if fake else ClaudeWriter(g["model"], g["effort"], g.get("review_effort", "high"))


def do_collect(conn, cfg, summary, fake=False):
    if fake:
        items = [{"id": f"fake{i}", "feed": "entertainment", "title": f"배우 A씨 촬영 중 부상 {i}", "outlet": "가신문",
                  "link": f"https://example.com/{i}", "related": [{"title": "관련 기사", "outlet": "나일보"}],
                  "heat": 2, "rank": i, "published_at": db.now_iso()} for i in range(3)]
    else:
        items = news.fetch(cfg)
    new = news.store(conn, items)
    summary["collect"] = {"fetched": len(items), "new": new}
    log(f"뉴스 후보 {len(items)}건 (새 후보 {new}건)")


def do_generate(conn, cfg, summary, fake=False):
    if not cfg["safety"]["enabled"]:
        log("킬 스위치 → 생성 중단")
        return
    writer = _writer(cfg, fake)
    stories.run(conn, cfg, writer, summary)
    summary["usage"] = writer.usage
    log(f"발행 {len(summary.get('published', []))}편, 반려 {len(summary.get('rejected', []))}편")


def do_shorts(conn, cfg, summary, tts_override=None):
    from . import shorts, youtube
    if not (cfg["safety"]["enabled"] and cfg["shorts"]["enabled"]):
        return
    if tts_override:
        cfg["shorts"]["tts_provider"] = tts_override
    can_upload = cfg["youtube"]["upload"] and youtube.configured()
    if can_upload:   # Actions는 매번 새 환경: 파일이 사라진 미업로드 영상은 다시 만든다
        for v in conn.execute("SELECT slug FROM videos WHERE status != 'uploaded'").fetchall():
            if not (shorts.VIDEO_DIR / f"{v['slug']}.mp4").exists():
                conn.execute("DELETE FROM videos WHERE slug = ?", (v["slug"],))
        conn.commit()
    made, failed, uploaded = [], [], []
    for s in shorts.plan(conn, cfg):
        try:
            ok, problems = shorts.produce(conn, cfg, s)
        except Exception as e:
            ok, problems = False, [f"{type(e).__name__}: {e}"[:300]]
        (made if ok else failed).append(s["slug"] if ok else {"slug": s["slug"], "problems": problems[:3]})
        log(f"  {'영상 완성' if ok else '영상 실패'} {s['slug']} {'' if ok else problems[:2]}")
    if can_upload:
        for v in conn.execute("SELECT * FROM videos WHERE status IN ('rendered','upload_failed')").fetchall():
            path = shorts.VIDEO_DIR / f"{v['slug']}.mp4"
            if not path.exists():
                continue
            try:
                vid = youtube.upload(path, v["title"], v["description"], json.loads(v["tags"]),
                                     cfg["youtube"]["privacy"], cfg["youtube"]["category_id"],
                                     expected_channel=cfg["youtube"].get("expected_channel"))
                conn.execute("UPDATE videos SET status='uploaded', youtube_id=?, error=NULL, updated_at=? WHERE slug=?",
                             (vid, db.now_iso(), v["slug"]))
                uploaded.append(vid)
                log(f"  YouTube 업로드 {v['slug']} → {vid}")
            except Exception as e:
                conn.execute("UPDATE videos SET status='upload_failed', error=?, updated_at=? WHERE slug=?",
                             (str(e)[:500], db.now_iso(), v["slug"]))
                log(f"  YouTube 업로드 실패 {v['slug']}: {e}")
            conn.commit()
    summary["shorts"] = {"made": made, "failed": failed, "uploaded": uploaded}
    if failed and not made:
        raise RuntimeError(f"숏폼 {len(failed)}건 모두 실패")


def do_instagram(conn, cfg, summary):
    import requests

    from . import instagram
    ic = cfg.get("instagram", {})
    if not (cfg["safety"]["enabled"] and ic.get("enabled")) or not instagram.configured():
        log("Instagram 비활성화 또는 자격 증명 없음 → 건너뜀")
        return
    base = cfg["site"]["base_url"]
    rows = conn.execute(
        """SELECT v.* FROM videos v JOIN stories st ON st.slug = v.slug AND st.status = 'published'
           LEFT JOIN social_posts s ON s.slug = v.slug AND s.platform = 'instagram'
           WHERE (s.status IS NULL OR (s.status = 'failed' AND s.attempts < 3))
             AND v.created_at >= datetime('now', ?) ORDER BY v.created_at DESC""",
        (f"-{int(ic.get('days_back', 2))} days",)).fetchall()
    posted, failed = [], []
    for v in rows:
        if len(posted) + len(failed) >= int(ic.get("max_per_run", 2)):
            break
        url = f"{base}/media/{v['slug']}.mp4"
        if requests.head(url, timeout=30, allow_redirects=True).status_code != 200:
            continue
        try:
            mid = instagram.publish_reel(url, instagram.caption_for(dict(v), f"{base}/posts/{v['slug']}/"))
            status, err = "posted", None
            posted.append(mid)
        except Exception as e:
            mid, status, err = None, "failed", str(e)[:500]
            failed.append({"slug": v["slug"], "error": err})
        conn.execute(
            """INSERT INTO social_posts (slug, platform, status, remote_id, error, attempts, updated_at)
               VALUES (?, 'instagram', ?, ?, ?, 1, ?)
               ON CONFLICT(slug, platform) DO UPDATE SET status=excluded.status, remote_id=excluded.remote_id,
                   error=excluded.error, attempts=attempts+1, updated_at=excluded.updated_at""",
            (v["slug"], status, mid, err, db.now_iso()))
        conn.commit()
    summary["instagram"] = {"posted": posted, "failed": failed}
    if failed and not posted:
        raise RuntimeError(f"Instagram 게시 {len(failed)}건 모두 실패")


def withdraw(conn, slug: str) -> bool:
    cur = conn.execute("UPDATE stories SET status='withdrawn', updated_at=? WHERE slug=?", (db.now_iso(), slug))
    conn.commit()
    return cur.rowcount > 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["collect", "generate", "shorts", "build", "all", "instagram", "withdraw"])
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--fake-writer", action="store_true", help="Claude 대신 가짜 작성기 + 가짜 뉴스 (별도 DB)")
    ap.add_argument("--tts", choices=["auto", "edge", "gtts", "silent"])
    args = ap.parse_args(argv)

    cfg = load_config()
    conn = db.connect(DATA_DIR / "demo.db" if args.fake_writer else DB_PATH)
    if args.command == "withdraw":
        ok = withdraw(conn, args.slug or "")
        log("글을 내렸습니다 (다음 빌드에서 빠짐)" if ok else "해당 글이 없습니다")
        return 0 if ok else 1
    run_id = db.start_run(conn)
    summary = {"command": args.command, "errors": []}

    def stage(name, fn):
        try:
            fn()
        except Exception as e:
            summary["errors"].append(f"{name}: {type(e).__name__}: {e}"[:500])
            log(f"오류[{name}]: {e}")

    def build():
        from .shorts import VIDEO_DIR
        summary["site_posts"] = site.build(conn, cfg, media_dir=VIDEO_DIR)
        log(f"사이트 빌드: 글 {summary['site_posts']}편")

    if args.command in ("collect", "all"):
        stage("collect", lambda: do_collect(conn, cfg, summary, fake=args.fake_writer))
    if args.command in ("generate", "all"):
        stage("generate", lambda: do_generate(conn, cfg, summary, fake=args.fake_writer))
    if args.command in ("shorts", "all"):
        stage("shorts", lambda: do_shorts(conn, cfg, summary, tts_override=args.tts))
    if args.command in ("build", "all"):
        stage("build", build)
    if args.command == "instagram":
        stage("instagram", lambda: do_instagram(conn, cfg, summary))

    status = "error" if summary["errors"] else "ok"
    summary = redact(summary)
    db.finish_run(conn, run_id, status, summary)
    if not args.fake_writer and args.command != "instagram":
        (DATA_DIR / "last_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
