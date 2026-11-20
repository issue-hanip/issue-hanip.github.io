"""HyperFrames(HTML+GSAP → 크롬 녹화 → mp4)로 숏츠를 만든다 (시범).

화면 구성 (1080×1920):
- 위 470px: 고정 제목 띠 (분류·채널명, 큰 제목, 강조어 빨강 튀기기)
- 가운데 1000px: 장면별 그림 — 인물 사진 / 자료 사진(Ken Burns) / 3D 이모지(떠다님·회전) / 큰 숫자(카운트업) / 체크리스트
  + 음성과 단어 단위로 맞춘 큰 자막(단어가 튀어나오고 강조어는 색)
- 아래 450px: 장면 헤드라인 상자, 안내 문구, 진행 막대
렌더: npx hyperframes render (무료, Apache 2.0). Node 22 + 크롬 + FFmpeg 필요 (GitHub Actions 기본 제공).
"""
import html
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import cards

HF_VERSION = "0.9.72"
ASSETS = Path(__file__).parent / "motion_assets"
W, H, MY0, MH = 1080, 1920, 470, 1000
LABEL = {"트렌드": "트렌드", "꿀팁": "꿀팁"}


def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def caption_chunks(words: list | None, narration: str, secs: float, size: int = 13) -> list[dict]:
    """자막 묶음 [{t0, t1, words:[(t, text)]}]. 단어 시각이 없으면 글자 수 비율로 추정."""
    if not words:
        toks = narration.split()
        tot = sum(len(t) + 1 for t in toks) or 1
        acc, words = 0.0, []
        for t in toks:
            words.append((acc / tot * (secs - 0.3), 0.2, t))
            acc += len(t) + 1
    # 문장부호는 앞 단어에 붙인다 (edge-tts 단어에는 부호가 없다)
    toks = narration.split()
    out, cur, n = [], [], 0
    for i, (t, _d, text) in enumerate(words):
        disp = toks[i] if len(toks) == len(words) else text
        if cur and (n + len(disp) > size or re.search(r"[.?!…]$", cur[-1][1])):
            out.append(cur)
            cur, n = [], 0
        cur.append((t, disp))
        n += len(disp) + 1
    if cur:
        out.append(cur)
    chunks = []
    for i, c in enumerate(out):
        t1 = out[i + 1][0][0] if i + 1 < len(out) else secs
        chunks.append({"t0": max(0.0, c[0][0] - 0.05), "t1": t1, "words": c})
    return chunks


def _parse_number(big: str):
    """'2만원' → (2, '', '만원'), '25%' → (25, '', '%'), '8.7억' → (8.7, '', '억'). 숫자가 없으면 None."""
    m = re.match(r"^(\D*?)(\d[\d,]*(?:\.\d+)?)(.*)$", big.strip())
    if not m:
        return None
    pre, num, suf = m.groups()
    val = float(num.replace(",", ""))
    dec = len(num.split(".")[1]) if "." in num else 0
    return pre, val, suf, dec, "," in num


def build(project: Path, story: dict, sh: dict, scenes: list[dict], parts: list[dict], theme: dict,
          site_title: str) -> float:
    """project 폴더에 index.html과 자료를 만든다. parts[i] = {audio, secs, words, headline, narration}."""
    if project.exists():
        shutil.rmtree(project)
    (project / "assets").mkdir(parents=True)
    for f in ASSETS.iterdir():
        shutil.copy(f, project / "assets" / f.name)
    band = sh.get("band") or {"text": sh["title"][:16], "hot": ""}
    hot = band.get("hot", "")
    th = theme
    bg = "rgb({},{},{})".format(*th["bg"])
    stripe = "rgb({},{},{})".format(*th["stripe"])
    cat = story["category"]
    label = f"{LABEL.get(cat, cat + ' 이슈')} · {site_title}"

    body, js, offset = [], [], 0.0
    for i, (sc, p) in enumerate(zip(scenes, parts)):
        secs = p["secs"]
        sid = f"s{i}"
        shutil.copy(p["audio"], project / "assets" / f"{i}.mp3")
        body.append(f'<audio id="a{i}" src="assets/{i}.mp3" data-start="{offset:.3f}" '
                    f'data-duration="{secs:.3f}" data-volume="1"></audio>')
        inner = []
        kind = sc["kind"]
        ef = sc.get("emoji_file")
        if ef:
            shutil.copy(ef, project / "assets" / Path(ef).name)
        emoji_tag = (f'<img class="emo" src="assets/{Path(ef).name}">' if ef
                     else f'<div class="emo txt">{_e(sc.get("emoji") or "✨")}</div>')
        if kind in ("photo", "person") and sc.get("file"):
            name = f"m{i}{Path(sc['file']).suffix or '.jpg'}"
            shutil.copy(sc["file"], project / "assets" / name)
            inner.append(f'<img class="photo" id="{sid}p" src="assets/{name}">')
            inner.append('<div class="shade"></div>')
            if sc.get("label"):
                inner.append(f'<div class="tag">{_e(sc["label"])}</div>')
            if sc.get("credit"):
                inner.append(f'<div class="credit">{_e(sc["credit"])}</div>')
            pan = 40 if i % 2 else -40
            js.append(f'tl.fromTo("#{sid}p",{{scale:1.04,x:{-pan}}},{{scale:1.16,x:{pan},duration:{secs:.2f},ease:"none"}},{offset:.3f});')
        elif kind == "number" and sc.get("big"):
            num = _parse_number(sc["big"])
            inner.append(f'<div class="orbs"><i></i><i></i><i></i></div>{emoji_tag.replace("emo", "emo side", 1)}')
            inner.append(f'<div class="big" id="{sid}n">{_e(sc["big"])}</div><div class="sub" id="{sid}s">{_e(sc.get("sub"))}</div>')
            js.append(f'tl.from("#{sid}n",{{scale:0.3,opacity:0,duration:0.5,ease:"back.out(2)"}},{offset + 0.15:.3f});')
            if num:
                pre, val, suf, dec, comma = num
                fmt = ('s=Number(s).toLocaleString("en-US",{minimumFractionDigits:%d});' % dec) if comma else ""
                js.append(
                    f'(function(){{const o={{v:0}},el=document.querySelector("#{sid}n");'
                    f'tl.to(o,{{v:{val},duration:{min(1.6, secs * 0.5):.2f},ease:"power2.out",onUpdate:function(){{'
                    f'let s=o.v.toFixed({dec});{fmt}'
                    f'el.textContent={json.dumps(pre)}+s+{json.dumps(suf)};}}}},{offset + 0.15:.3f});}})();')
            js.append(f'tl.from("#{sid}s",{{y:60,opacity:0,duration:0.4}},{offset + 0.6:.3f});')
            js.append(f'tl.to("#{sid}n",{{scale:1.06,duration:0.6,yoyo:true,repeat:{max(1, int(secs / 0.6) - 2)},ease:"sine.inOut"}},{offset + 1.0:.3f});')
        elif kind == "list" and sc.get("items"):
            inner.append(f'<div class="orbs"><i></i><i></i><i></i></div>{emoji_tag.replace("emo", "emo top", 1)}')
            items = "".join(f'<li id="{sid}i{k}"><b>✓</b><span>{_e(t)}</span></li>' for k, t in enumerate(sc["items"]))
            inner.append(f'<ul class="list">{items}</ul>')
            step = max(0.5, (secs - 1.0) / max(1, len(sc["items"])))
            for k in range(len(sc["items"])):
                js.append(f'tl.from("#{sid}i{k}",{{x:700,opacity:0,duration:0.45,ease:"power3.out"}},{offset + 0.3 + k * step:.3f});')
                js.append(f'tl.from("#{sid}i{k} b",{{scale:0,rotation:-90,duration:0.35,ease:"back.out(3)"}},{offset + 0.6 + k * step:.3f});')
        else:  # emoji (사진을 못 구한 장면 포함)
            inner.append(f'<div class="orbs"><i></i><i></i><i></i></div><div class="ring"></div>{emoji_tag.replace("emo", "emo hero", 1)}')
            js.append(f'tl.from("#{sid} .hero",{{scale:0,rotation:-35,duration:0.7,ease:"back.out(1.8)"}},{offset + 0.05:.3f});')
            js.append(f'tl.to("#{sid} .hero",{{y:-40,rotationY:18,duration:1.1,yoyo:true,repeat:{max(1, int(secs / 1.1))},ease:"sine.inOut"}},{offset + 0.75:.3f});')
            js.append(f'tl.fromTo("#{sid} .ring",{{scale:0.6,opacity:0.9}},{{scale:1.5,opacity:0,duration:1.4,repeat:{max(1, int(secs / 1.4))},ease:"power1.out"}},{offset:.3f});')
        js.append(f'tl.fromTo("#{sid} .orbs i",{{y:0}},{{y:-120,x:"random(-80,80)",duration:{secs:.2f},stagger:0.2,ease:"sine.inOut"}},{offset:.3f});')
        # 자막
        caps = []
        for k, ch in enumerate(caption_chunks(p.get("words"), p["narration"], secs)):
            cid = f"{sid}c{k}"
            ws = "".join(f'<span class="w{" hot" if hot and hot in t else ""}" id="{cid}w{j}">{_e(t)}</span> '
                         for j, (_t, t) in enumerate(ch["words"]))
            caps.append(f'<div class="cap" id="{cid}">{ws}</div>')
            js.append(f'tl.set("#{cid}",{{autoAlpha:1}},{offset + ch["t0"]:.3f});')
            js.append(f'tl.set("#{cid}",{{autoAlpha:0}},{offset + ch["t1"]:.3f});')
            for j, (t, _w) in enumerate(ch["words"]):
                js.append(f'tl.from("#{cid}w{j}",{{scale:0.4,y:30,opacity:0,duration:0.18,ease:"back.out(3)"}},{offset + max(t, ch["t0"]):.3f});')
        # 헤드라인·전환
        js.append(f'tl.fromTo("#h{i}",{{y:120,opacity:0}},{{y:0,opacity:1,duration:0.35,ease:"power3.out"}},{offset:.3f});')
        if i:
            js.append(f'tl.fromTo("#flash",{{opacity:0.85}},{{opacity:0,duration:0.3}},{offset:.3f});')
        js.append(f'tl.from("#{sid}",{{clipPath:"inset(0 0 100% 0)",duration:0.35,ease:"power2.out"}},{offset:.3f});')
        body.append(f'<section class="clip scene" id="{sid}" data-start="{offset:.3f}" data-duration="{secs:.3f}" '
                    f'data-track-index="1">{"".join(inner)}<div class="caps">{"".join(caps)}</div></section>')
        body.append(f'<div class="clip head" id="h{i}" data-start="{offset:.3f}" data-duration="{secs:.3f}" '
                    f'data-track-index="2"><span>{_e(p["headline"])}</span></div>')
        offset += secs
    total = offset

    # 위 띠: 강조어 빨강
    t = _e(band.get("text", ""))
    if hot and _e(hot) in t:
        t = t.replace(_e(hot), f'<em id="hotw">{_e(hot)}</em>', 1)
    js.insert(0, 'tl.from("#bandt",{y:-80,opacity:0,duration:0.5,ease:"power3.out"},0);')
    if hot:
        js.insert(1, 'tl.fromTo("#hotw",{scale:1},{scale:1.15,duration:0.5,yoyo:true,repeat:%d,ease:"sine.inOut"},0.5);'
                  % max(1, int(total / 2.0)))
    js.append(f'tl.fromTo("#prog",{{scaleX:0}},{{scaleX:1,duration:{total:.2f},ease:"none"}},0);')

    css = f"""
@font-face {{ font-family: "BHS"; src: url("assets/BlackHanSans-Regular.ttf"); }}
* {{ box-sizing: border-box; margin: 0; padding: 0 }}
body {{ background: #000; }}
#root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; background: {bg};
  font-family: "NanumGothic", "Noto Sans CJK KR", sans-serif; color: {th['text']} }}
.clip {{ position: absolute }}
#top {{ position: absolute; left: 0; top: 0; width: {W}px; height: {MY0}px; background: #000; z-index: 5;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 22px; padding: 40px 50px 30px }}
#top .lab {{ font-size: 36px; color: {th['muted']}; letter-spacing: 2px }}
#bandt {{ font-family: "BHS"; font-size: 112px; line-height: 1.12; text-align: center; color: #fff; word-break: keep-all }}
#bandt em {{ font-style: normal; color: {th['hot']}; display: inline-block }}
.scene {{ left: 0; top: {MY0}px; width: {W}px; height: {MH}px; overflow: hidden; z-index: 1;
  background: radial-gradient(circle at 50% 40%, {stripe}, {bg} 70%) }}
.photo {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; object-position: 50% 30% }}
.shade {{ position: absolute; inset: 0; background: linear-gradient(180deg, rgba(0,0,0,0) 45%, rgba(0,0,0,.75) 100%) }}
.tag {{ position: absolute; left: 30px; top: 28px; background: rgba(0,0,0,.65); color: #fff; font-size: 30px;
  padding: 6px 16px; border-radius: 8px; font-weight: 700 }}
.credit {{ position: absolute; right: 22px; top: 32px; max-width: 560px; color: rgba(255,255,255,.85); font-size: 22px;
  text-align: right; text-shadow: 0 0 6px #000 }}
.orbs i {{ position: absolute; display: block; border-radius: 50%; filter: blur(40px); opacity: .55 }}
.orbs i:nth-child(1) {{ width: 380px; height: 380px; left: -60px; top: 120px; background: {th['accent']} }}
.orbs i:nth-child(2) {{ width: 300px; height: 300px; right: -40px; top: 420px; background: {th['hot']} }}
.orbs i:nth-child(3) {{ width: 240px; height: 240px; left: 420px; top: 640px; background: {th['muted']} }}
.ring {{ position: absolute; left: 240px; top: 110px; width: 600px; height: 600px; border-radius: 50%;
  border: 10px solid {th['accent']} }}
.emo {{ position: absolute; filter: drop-shadow(0 30px 40px rgba(0,0,0,.45)) }}
.emo.txt {{ font-family: "Noto Color Emoji"; text-align: center }}
.emo.hero {{ left: 260px; top: 110px; width: 560px; height: 560px; font-size: 460px; line-height: 560px }}
.emo.side {{ right: 60px; top: 60px; width: 240px; height: 240px; font-size: 200px; line-height: 240px; transform: rotate(12deg) }}
.emo.top {{ left: 50px; top: 40px; width: 200px; height: 200px; font-size: 170px; line-height: 200px; transform: rotate(-10deg) }}
.big {{ position: absolute; left: 0; right: 0; top: 230px; text-align: center; font-family: "BHS"; font-size: 230px;
  color: {th['accent']}; text-shadow: 0 12px 0 rgba(0,0,0,.35) }}
.sub {{ position: absolute; left: 0; right: 0; top: 520px; text-align: center; font-size: 64px; font-weight: 800 }}
.list {{ position: absolute; left: 80px; right: 80px; top: 270px; list-style: none; display: grid; gap: 34px }}
.list li {{ display: flex; align-items: center; gap: 28px; background: rgba(255,255,255,.12); border-radius: 26px;
  padding: 26px 34px; font-size: 62px; font-weight: 800; border: 3px solid rgba(255,255,255,.18) }}
.list li b {{ display: inline-grid; place-items: center; width: 84px; height: 84px; border-radius: 50%;
  background: {th['accent']}; color: #111; font-size: 52px; flex: none }}
.caps {{ position: absolute; left: 40px; right: 40px; bottom: 60px; height: 260px }}
.cap {{ position: absolute; left: 0; right: 0; bottom: 0; text-align: center; visibility: hidden; opacity: 0;
  font-size: 84px; font-weight: 800; line-height: 1.22; word-break: keep-all; color: #fff;
  -webkit-text-stroke: 10px #000; paint-order: stroke fill }}
.cap .w {{ display: inline-block }}
.cap .w.hot {{ color: {th['accent']} }}
.head {{ left: 50px; right: 50px; top: {MY0 + MH + 50}px; z-index: 4; text-align: center }}
.head span {{ display: inline-block; background: {th['accent']}; color: #111; font-family: "BHS"; font-size: 80px;
  padding: 14px 34px; border-radius: 18px; word-break: keep-all }}
#foot {{ position: absolute; left: 0; right: 0; bottom: 70px; text-align: center; font-size: 30px; color: {th['muted']}; z-index: 4 }}
#progbar {{ position: absolute; left: 0; bottom: 0; width: {W}px; height: 14px; background: rgba(255,255,255,.12); z-index: 4 }}
#prog {{ width: 100%; height: 100%; background: {th['hot']}; transform-origin: 0 50% }}
#flash {{ position: absolute; inset: 0; background: #fff; opacity: 0; z-index: 9; pointer-events: none }}
"""
    doc = f"""<!doctype html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width={W}, height={H}">
<script src="assets/gsap.min.js"></script><style>{css}</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{total:.3f}" data-width="{W}" data-height="{H}" data-fps="30">
<div id="top"><div class="lab">{_e(label)}</div><div id="bandt">{t}</div></div>
{chr(10).join(body)}
<div id="foot">보도된 내용을 정리했습니다 · 의혹은 확정된 사실이 아닙니다</div>
<div id="progbar"><div id="prog"></div></div>
<div id="flash"></div>
</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
{chr(10).join(js)}
window.__timelines.main = tl;
</script>
</body></html>"""
    (project / "index.html").write_text(doc, encoding="utf-8")
    return total


def render(project: Path, out: Path, timeout: int = 1500) -> None:
    npx = shutil.which("npx") or "npx"
    lint = subprocess.run([npx, "--yes", f"hyperframes@{HF_VERSION}", "lint", str(project)],
                          capture_output=True, text=True, timeout=300)
    print("  hyperframes lint:", (lint.stdout + lint.stderr)[:6000])
    proc = subprocess.run([npx, "--yes", f"hyperframes@{HF_VERSION}", "render", str(project), "--output", str(out),
                           "--fps", "30", "--quality", "standard"], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError("hyperframes render 실패: " + (proc.stdout + proc.stderr)[-3000:])
    print("  hyperframes render:", (proc.stdout + proc.stderr)[-800:])
