"""발행된 이슈 글 → 숏폼 세로 영상(mp4). 대본은 글을 쓸 때 함께 만들고 같은 검수를 거쳤다."""
import hashlib
import json
import shutil
from pathlib import Path

from . import cards, render, tts
from .config import ROOT
from .stories import clean_tag
from .write import SCENES

VIDEO_DIR = ROOT / "videos"
THEME_NAMES = list(cards.THEMES)


def plan(conn, cfg) -> list:
    rows = conn.execute(
        """SELECT s.* FROM stories s LEFT JOIN videos v ON v.slug = s.slug
           WHERE v.slug IS NULL AND s.status = 'published' ORDER BY s.created_at DESC""").fetchall()
    return rows[: cfg["shorts"]["max_per_run"]]


def pick_theme(slug: str, weights: dict | None = None) -> str:
    """slug로 정해지는 실험 테마 (가중치가 있으면 반영)."""
    weights = weights or {k: 1.0 for k in THEME_NAMES}
    h = int(hashlib.sha1(slug.encode()).hexdigest(), 16) % 10_000 / 10_000
    tot = sum(weights.get(k, 1.0) for k in THEME_NAMES)
    acc = 0.0
    for k in THEME_NAMES:
        acc += weights.get(k, 1.0) / tot
        if h < acc:
            return k
    return THEME_NAMES[-1]


def produce(conn, cfg, story, out_dir: Path = VIDEO_DIR, weights: dict | None = None) -> tuple[bool, list[str]]:
    sh = json.loads(story["shorts"])
    s, site = cfg["shorts"], cfg["site"]
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / f"_{story['slug']}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        theme_name = pick_theme(story["slug"], weights)
        th = cards.THEMES[theme_name]
        parts, total = [], 0.0
        for i, k in enumerate(SCENES):
            sc = sh["scenes"][k]
            audio = work / f"{i}.mp3"
            secs = tts.speak(sc["narration"], audio, s["tts_provider"], s["voice"]) + 0.3
            parts.append((k, sc["headline"], sc["narration"], audio, secs))
            total += secs
        if total > s["max_seconds"]:
            return False, [f"영상 길이 {total:.1f}초 > {s['max_seconds']}초"]
        segs, offset = [], 0.0
        for i, (k, head, narr, audio, secs) in enumerate(parts):
            scene = cards.Scene(k, head, narr, th, story["category"], site["title"], secs, first=(i == 0))
            if i == 0:
                scene.frame(secs - 0.1, 0.0).convert("RGB").save(out_dir / f"{story['slug']}.jpg", quality=90)
            seg = work / f"{i}.mp4"
            cards.render_scene(scene, audio, seg, offset, total)
            segs.append(seg)
            offset += secs
        out = out_dir / f"{story['slug']}.mp4"
        render.concat(segs, out)

        sources = json.loads(story["sources"])
        tags = [clean_tag(t) for t in sh["hashtags"]][:6]
        post_url = f"{site['base_url']}/posts/{story['slug']}/"
        title = sh["title"][:90] + " #Shorts"
        desc = (story["description"]
                + f"\n\n전체 정리: {post_url}"
                + "\n출처: " + ", ".join(dict.fromkeys(x["outlet"] for x in sources))
                + "\n보도된 내용을 정리한 영상이며, 의혹·주장은 확정된 사실이 아닙니다."
                + "\n\n" + " ".join("#" + t for t in tags if t))
        from .db import now_iso
        ts = now_iso()
        conn.execute(
            """INSERT OR REPLACE INTO videos (slug, title, description, tags, seconds, status, variant,
                   created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'rendered', ?, ?, ?)""",
            (story["slug"], title, desc, json.dumps(tags, ensure_ascii=False), round(total, 1),
             json.dumps({"theme": theme_name}), ts, ts))
        conn.commit()
        return True, []
    finally:
        shutil.rmtree(work, ignore_errors=True)
