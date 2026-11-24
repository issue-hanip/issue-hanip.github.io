"""이슈 고르기 → 원문 확인·작성 → 규칙 검사 → Claude 검수 → 저장."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from . import db, news, qa

KST = timezone(timedelta(hours=9))


def _slug(title: str, cid: str) -> str:
    day = datetime.now(KST).strftime("%Y%m%d")
    return f"{day}-{hashlib.sha1((cid + title).encode()).hexdigest()[:8]}"


def _cand(row) -> dict:
    return {"id": row["id"], "title": row["title"], "outlet": row["outlet"], "feed": row["feed"],
            "heat": row["heat"], "published_at": row["published_at"],
            "related": json.loads(row["related"])[:5]}


def run(conn, cfg, writer, summary: dict, guidance: str = "", write_guidance: str | None = None) -> None:
    """guidance: 선정 지침, write_guidance: 작성 지침 (없으면 guidance를 같이 쓴다)."""
    write_guidance = guidance if write_guidance is None else write_guidance
    g = cfg["generate"]
    rows = news.recent(conn, cfg)
    cands = [_cand(r) for r in rows]
    by_id = {c["id"]: c for c in cands}
    if not cands:
        summary["published"] = []
        return
    picked = writer.pick(cands, news.used_titles(conn), g["max_stories_per_run"], guidance)
    for s in picked.get("skipped", []):
        if s["id"] in by_id:
            conn.execute("UPDATE candidates SET status='skipped' WHERE id=?", (s["id"],))
    conn.commit()
    summary["skipped"] = [{"title": by_id[s["id"]]["title"], "reason": s["reason"]}
                          for s in picked.get("skipped", []) if s["id"] in by_id][:15]
    published, rejected, failures = [], [], 0
    for pk in picked.get("picks", [])[: g["max_stories_per_run"]]:
        cand = by_id.get(pk["id"])
        if not cand:
            continue
        conn.execute("UPDATE candidates SET status='picked' WHERE id=?", (cand["id"],))
        conn.commit()
        story, problems, feedback, review = None, [], None, {}
        for _ in range(g["max_attempts"]):
            story = writer.write(cand, pk["angle"], pk["category"], feedback, write_guidance)
            problems = qa.check(story, cfg)
            if not problems:
                review = writer.review(story)
                if review.get("approved") and review.get("risk") != "높음":
                    # 권장 사항이 있으면 반영본을 만들고, 규칙 검사를 통과할 때만 바꾼다 (출처·근거는 원본 유지)
                    if review.get("suggestions"):
                        try:
                            rev = writer.revise(story, review["suggestions"])
                            rev["sources"], rev["claims"] = story["sources"], story.get("claims", [])
                            if not qa.check(rev, cfg):
                                story, review["revised"] = rev, True
                        except Exception as e:
                            review["revise_error"] = str(e)[:200]
                    break
                problems = review.get("issues") or ["검수 반려(사유 없음)"]
            feedback = problems
        slug = _slug(story["title"] if story else cand["title"], cand["id"])
        if problems:
            db.log_rejection(conn, slug, problems)
            rejected.append({"title": cand["title"], "problems": problems[:5]})
            failures += 1
            if failures >= cfg["safety"]["max_consecutive_failures"]:
                break
            continue
        failures = 0
        ts = db.now_iso()
        conn.execute(
            """INSERT INTO stories (slug, candidate_id, category, title, description, body_md, tags, sources,
                   shorts, review, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slug, cand["id"], pk["category"], story["title"].strip(), story["description"].strip(),
             story["body_md"].strip(), json.dumps(story["tags"][:6], ensure_ascii=False),
             json.dumps(story["sources"], ensure_ascii=False), json.dumps(story["shorts"], ensure_ascii=False),
             json.dumps({"review": review, "claims": story.get("claims", [])}, ensure_ascii=False), ts, ts))
        conn.execute("UPDATE candidates SET status='used' WHERE id=?", (cand["id"],))
        conn.commit()
        published.append({"slug": slug, "title": story["title"]})
    summary["published"] = published
    summary["rejected"] = rejected


def clean_tag(t: str) -> str:
    return re.sub(r"[^\w가-힣]", "", t.lstrip("#"))
