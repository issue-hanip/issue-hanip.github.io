"""새 숏츠 방식(HyperFrames + 장면별 그림) 시범 렌더링. 업로드·DB 기록 없이 videos/pilot/ 에 mp4만 만든다.

python -m pipeline.pilot <slug> [<slug> ...] [--fake-writer]
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

from . import cards, db, motion, tts, visuals
from .config import load_config
from .run import _writer
from .shorts import VIDEO_DIR, pick_theme
from .write import SCENES

OUT = VIDEO_DIR / "pilot"


def sheet(mp4: Path, out: Path, n: int = 10) -> None:
    """장면 확인용 프레임 모음(5×2)."""
    dur = tts.duration(mp4)
    fps = n / max(1.0, dur)
    subprocess.run([tts.FFMPEG, "-y", "-loglevel", "error", "-i", str(mp4), "-vf",
                    f"fps={fps:.4f},scale=270:-1,tile=5x2", "-frames:v", "1", str(out)], check=False)


def make(conn, cfg, slug: str, writer, report: dict) -> None:
    story = dict(conn.execute("SELECT * FROM stories WHERE slug = ?", (slug,)).fetchone())
    sh = json.loads(story["shorts"])
    s = cfg["shorts"]
    work = OUT / f"_{slug}"
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    parts = []
    for i, k in enumerate(SCENES):
        sc = sh["scenes"][k]
        audio = work / f"{i}.mp3"
        secs, words = tts.speak_words(sc["narration"], audio, s["tts_provider"], s["voice"])
        parts.append({"audio": audio, "secs": secs + 0.3, "words": words, "headline": sc["headline"],
                      "narration": sc["narration"]})
    people = sh.get("people") or []
    plan = writer.plan_visuals({"category": story["category"], "title": story["title"], "shorts": sh,
                                "body_md": story["body_md"][:2500]}, bool(people))
    by = {p["scene"]: p for p in plan["scenes"]}
    plan = {"scenes": [by.get(k) or {"scene": k, "kind": "emoji", "query_en": "", "emoji": "✨", "big": "",
                                     "sub": "", "items": []} for k in SCENES]}
    vet_people = getattr(writer, "vet_photos", None)
    scenes = visuals.resolve(plan, people, work / "vis", vet_people=vet_people,
                             vet_topic=getattr(writer, "vet_topic_photos", None))
    t1 = time.time()
    theme = cards.THEMES[pick_theme(slug)]
    proj = work / "project"
    total = motion.build(proj, story, sh, scenes, parts, theme, cfg["site"]["title"])
    out = OUT / f"{slug}.mp4"
    motion.render(proj, out)
    t2 = time.time()
    sheet(out, OUT / f"{slug}_sheet.jpg")
    report[slug] = {"category": story["category"], "seconds": round(total, 1),
                    "prepare_sec": round(t1 - t0), "render_sec": round(t2 - t1),
                    "words_timed": sum(1 for p in parts if p["words"]),
                    "plan": plan["scenes"],
                    "resolved": [{"kind": x["kind"], "credit": x.get("credit", ""), "label": x.get("label", ""),
                                  "emoji_png": bool(x.get("emoji_file"))} for x in scenes],
                    "credits": visuals.credits(scenes)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="+")
    ap.add_argument("--fake-writer", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    conn = db.connect()
    writer = _writer(cfg, a.fake_writer)
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}
    for slug in a.slugs:
        try:
            make(conn, cfg, slug, writer, report)
        except Exception as e:  # noqa: BLE001
            report[slug] = {"error": str(e)[-3000:]}
        print(json.dumps(report[slug], ensure_ascii=False, indent=1, default=str)[:4000])
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
