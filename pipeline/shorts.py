"""발행된 이슈 글 → 숏폼 세로 영상(mp4). 대본은 글을 쓸 때 함께 만들고 같은 검수를 거쳤다.

renderer = "motion": HyperFrames(HTML+GSAP) 방식. 장면별 그림(자료 사진·3D 이모지·숫자·체크리스트)과
단어 단위 자막. 실패하면 기존 Pillow 카드 방식("cards")으로 다시 만든다.
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from . import cards, media, render, tts
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


def assign_media(found: list) -> list:
    """장면 5개에 사진·영상 배정: 표지(hook)는 사진, 영상이 있으면 두 번째 장면(what)."""
    photos = [m for m in found if m.kind == "photo"]
    clips = [m for m in found if m.kind == "clip"]
    if not photos:
        return [None] * len(SCENES)
    out = [photos[i % len(photos)] for i in range(len(SCENES))]
    if clips:
        out = [photos[0], clips[0]] + [photos[(i + 1) % len(photos)] for i in range(len(SCENES) - 2)]
    return out


def produce(conn, cfg, story, out_dir: Path = VIDEO_DIR, weights: dict | None = None,
            vet=None, writer=None) -> tuple[bool, list[str]]:
    sh = json.loads(story["shorts"])
    s, site = cfg["shorts"], cfg["site"]
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / f"_{story['slug']}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        theme_name = pick_theme(story["slug"], weights)
        th = cards.THEMES[theme_name]
        use_motion = s.get("renderer", "cards") == "motion" and writer is not None
        parts, total = [], 0.0
        for i, k in enumerate(SCENES):
            sc = sh["scenes"][k]
            audio = work / f"{i}.mp3"
            if use_motion:
                secs, words = tts.speak_words(sc["narration"], audio, s["tts_provider"], s["voice"])
            else:
                secs, words = tts.speak(sc["narration"], audio, s["tts_provider"], s["voice"]), None
            secs += 0.3
            parts.append({"scene": k, "headline": sc["headline"], "narration": sc["narration"],
                          "audio": audio, "secs": secs, "words": words})
            total += secs
        if total > s["max_seconds"]:
            return False, [f"영상 길이 {total:.1f}초 > {s['max_seconds']}초"]
        out = out_dir / f"{story['slug']}.mp4"
        result = None
        if use_motion:
            try:
                result = _motion(story, sh, parts, th, site, work, out, writer, vet, s)
            except Exception as e:  # noqa: BLE001  새 방식이 안 되면 기존 방식
                print(f"  새 방식 렌더링 실패 → 기존 방식: {type(e).__name__}: {str(e)[-500:]}")
        if result is None:
            result = _cards(story, sh, parts, th, site, work, out, out_dir, vet, s, total)
        variant, credit_text = result
        variant["theme"] = theme_name

        sources = json.loads(story["sources"])
        tags = [clean_tag(t) for t in sh["hashtags"]][:6]
        post_url = f"{site['base_url']}/posts/{story['slug']}/"
        title = sh["title"][:90] + " #Shorts"
        desc = (story["description"]
                + f"\n\n전체 정리: {post_url}"
                + "\n출처: " + ", ".join(dict.fromkeys(x["outlet"] for x in sources))
                + "\n보도된 내용을 정리한 영상이며, 의혹·주장은 확정된 사실이 아닙니다."
                + ("\n\n" + credit_text if credit_text else "")
                + "\n\n" + " ".join("#" + t for t in tags if t))
        from .db import now_iso
        ts = now_iso()
        conn.execute(
            """INSERT OR REPLACE INTO videos (slug, title, description, tags, seconds, status, variant,
                   created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'rendered', ?, ?, ?)""",
            (story["slug"], title, desc, json.dumps(tags, ensure_ascii=False), round(total, 1),
             json.dumps(variant, ensure_ascii=False), ts, ts))
        conn.commit()
        return True, []
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _motion(story, sh, parts, th, site, work, out, writer, vet, s) -> tuple[dict, str]:
    from . import motion, visuals
    people = (sh.get("people") or []) if s.get("media", True) else []
    plan = writer.plan_visuals({"category": story["category"], "title": story["title"], "shorts": sh,
                                "body_md": story["body_md"][:2500]}, bool(people))
    by = {p["scene"]: p for p in plan.get("scenes", [])}
    plan = {"scenes": [by.get(k) or {"scene": k, "kind": "emoji", "query_en": "", "emoji": "✨", "big": "",
                                     "sub": "", "items": []} for k in SCENES]}
    if not s.get("media", True):        # 사진 검수를 못 하면 사진 없이 (이모지·숫자·목록)
        for x in plan["scenes"]:
            if x["kind"] in ("person", "photo"):
                x["kind"] = "emoji"
    scenes = visuals.resolve(plan, people, work / "vis", vet_people=vet,
                             vet_topic=getattr(writer, "vet_topic_photos", None))
    proj = work / "project"
    motion.build(proj, story, sh, scenes, parts, th, site["title"])
    motion.render(proj, out)
    thumb = out.with_suffix(".jpg")     # 표지: 첫 장면이 다 그려진 시점
    subprocess.run([tts.FFMPEG, "-y", "-loglevel", "error", "-ss", f"{max(0.5, parts[0]['secs'] - 0.4):.2f}",
                    "-i", str(out), "-frames:v", "1", "-q:v", "3", str(thumb)], check=False)
    kinds = [x["kind"] for x in scenes]
    return ({"layout": "motion", "kinds": kinds,
             "photos": sum(k in ("photo", "person") for k in kinds),
             "words_timed": sum(1 for p in parts if p["words"])}, visuals.credits(scenes))


def _cards(story, sh, parts, th, site, work, out, out_dir, vet, s, total) -> tuple[dict, str]:
    band = sh.get("band") or {"text": sh["title"][:16], "hot": ""}
    found = []
    if s.get("media", True) and sh.get("people"):
        try:
            found = media.gather(sh["people"], work / "media", s.get("max_photos", 4),
                                 s.get("max_clips", 1), vet=vet)
        except Exception as e:          # 사진을 못 구해도 글자 모션으로 만든다
            print(f"  사진 검색 실패: {e}")
    assigned = assign_media(found)
    segs, offset = [], 0.0
    for i, pt in enumerate(parts):
        m = assigned[i]
        arg = None
        if m is not None:
            arg = (m.kind, Image.open(m.path) if m.kind == "photo" else m.path, m.credit())
        scene = cards.Scene(pt["scene"], pt["headline"], pt["narration"], th, story["category"], site["title"],
                            pt["secs"], band, media=arg, first=(i == 0), pan=i)
        if i == 0:
            scene.frame(pt["secs"] - 0.1, 0.0).convert("RGB").save(out_dir / f"{story['slug']}.jpg", quality=90)
        seg = work / f"{i}.mp4"
        cards.render_scene(scene, pt["audio"], seg, offset, total)
        segs.append(seg)
        offset += pt["secs"]
    render.concat(segs, out)
    used = list(dict.fromkeys(x for x in assigned if x is not None))
    return ({"layout": "frame", "photos": sum(m.kind == "photo" for m in used),
             "clips": sum(m.kind == "clip" for m in used)},
            media.credits_text(used) if used else "")
