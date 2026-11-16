"""이슈 숏폼 모션 렌더러 (1080x1920). 인물 사진 없이 글자·도형 모션만 쓴다 (초상권·저작권).

장면 구성: hook(큰 질문) → what(무슨 일) → twist(그런데) → reaction(입장·반응) → outro(채널·블로그 안내)
표현: 위쪽 분류 띠, 장면 배지 도장, 헤드라인 튀어나오기, 구절 자막, 움직이는 사선 줄무늬 배경, 진행 막대.
테마(THEMES)는 성과 실험 변수다.
"""
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from .render import W, H, _wrap, font
from .tts import FFMPEG

FPS = 30

THEMES = {
    "tabloid": dict(bg=(18, 18, 22), stripe=(32, 32, 40), text="#ffffff", muted="#d4d4d8", accent="#facc15",
                    hot="#ef4444", band="#ef4444", band_text="#ffffff", cap=(0, 0, 0, 190)),
    "neon": dict(bg=(10, 6, 30), stripe=(26, 16, 64), text="#ffffff", muted="#c4b5fd", accent="#22d3ee",
                 hot="#f472b6", band="#7c3aed", band_text="#ffffff", cap=(0, 0, 0, 180)),
    "paper": dict(bg=(246, 242, 232), stripe=(232, 226, 210), text="#111111", muted="#44403c", accent="#b91c1c",
                  hot="#b91c1c", band="#111111", band_text="#fef08a", cap=(17, 17, 17, 225)),
    "mint": dict(bg=(8, 32, 34), stripe=(14, 52, 54), text="#f0fdfa", muted="#99f6e4", accent="#fde047",
                 hot="#fb7185", band="#0d9488", band_text="#ffffff", cap=(0, 0, 0, 180)),
}

BADGE = {"hook": "?!", "what": "무슨 일?", "twist": "그런데", "reaction": "입장은", "outro": "정리"}
GLYPH = {"hook": "?", "what": "1", "twist": "!", "reaction": "“ ”", "outro": "→"}


def ease(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def pop(p: float) -> float:
    """살짝 넘쳤다 돌아오는 크기 변화 (도장 찍는 느낌)."""
    p = max(0.0, min(1.0, p))
    return 1 + 0.18 * math.sin(p * math.pi) if p < 1 else 1.0


def _sprite(text: str, f, fill, width: int = W - 140, stroke=None) -> Image.Image:
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = _wrap(probe, text, f, width)
    lh = int(f.size * 1.22)
    img = Image.new("RGBA", (W, lh * len(lines) + 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        x = (W - d.textlength(line, font=f)) / 2
        if stroke:
            d.text((x, i * lh), line, font=f, fill=fill, stroke_width=6, stroke_fill=stroke)
        else:
            d.text((x, i * lh), line, font=f, fill=fill)
    return img


def _put(frame, sp, y, t, delay=0.0, dur=0.4, dy=50):
    p = ease((t - delay) / dur)
    if p <= 0:
        return
    if p >= 1:
        frame.paste(sp, (0, y), sp)
        return
    mask = sp.getchannel("A").point(lambda a: int(a * p))
    frame.paste(sp, (0, y + int((1 - p) * dy)), mask)


def _chunks(narration: str, size: int = 14) -> list[str]:
    out, cur = [], ""
    for word in narration.split():
        if cur and len(cur) + len(word) + 1 > size:
            out.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        out.append(cur)
    return out or [""]


class Scene:
    def __init__(self, kind, headline, narration, th, category, site_title, seconds, first=False):
        self.kind, self.th, self.seconds, self.first = kind, th, seconds, first
        self.head = _sprite(headline, font(118 if kind == "hook" else 96), th["accent"] if kind == "hook" else th["text"],
                            stroke=th["bg"] if kind == "hook" else None)
        self.badge_text = BADGE[kind]
        self.category, self.site_title = category, site_title
        chunks = _chunks(narration)
        weights = [max(4, len(c)) for c in chunks]
        tot = sum(weights)
        self.cap_at, acc = [], 0.0
        for wgt in weights:
            self.cap_at.append(acc / tot * seconds)
            acc += wgt
        self.caps = [self._caption(c) for c in chunks]
        self.base = self._static()
        self.glyph = self._glyph()

    def _caption(self, text):
        f = font(66)
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        tw = probe.textlength(text, font=f)
        img = Image.new("RGBA", (W, 120), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        x0 = (W - tw) / 2 - 30
        d.rounded_rectangle((x0, 6, x0 + tw + 60, 112), 22, fill=self.th["cap"])
        d.text(((W - tw) / 2, 20), text, font=f, fill="#ffffff")
        return img

    def _static(self):
        """움직이지 않는 위쪽 띠와 아래 채널 이름."""
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle((0, 120, W, 220), fill=self.th["band"])
        f = font(52)
        label = f"{self.category} 이슈"
        d.text((60, 140), label, font=f, fill=self.th["band_text"])
        name_f = font(40, bold=False)
        d.text((W - 60 - d.textlength(self.site_title, font=name_f), 150), self.site_title, font=name_f,
               fill=self.th["band_text"])
        foot = "보도 내용 정리 · 사실관계는 출처 기사 기준"
        ff = font(34, bold=False)
        d.text(((W - d.textlength(foot, font=ff)) / 2, H - 150), foot, font=ff, fill=self.th["muted"])
        return img

    def _glyph(self):
        """가운데 큰 상징 기호 (장면마다 다름): 반투명, 천천히 커졌다 작아진다."""
        f = font(560)
        txt = GLYPH[self.kind]
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        tw = int(probe.textlength(txt, font=f))
        img = Image.new("RGBA", (tw + 40, 700), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        col = self.th["hot"] if self.kind in ("twist", "hook") else self.th["accent"]
        d.text((20, 0), txt, font=f, fill=col)
        a = img.getchannel("A").point(lambda v: int(v * 0.28))
        img.putalpha(a)
        return img

    def _background(self, t):
        img = Image.new("RGB", (W, H), self.th["bg"])
        d = ImageDraw.Draw(img)
        shift = int(t * 60) % 160
        for x in range(-H, W + 160, 160):
            x0 = x + shift
            d.polygon([(x0, 0), (x0 + 60, 0), (x0 + 60 + H, H), (x0 + H, H)], fill=self.th["stripe"])
        return img

    def _badge(self, fr, t):
        f = font(64)
        d = ImageDraw.Draw(fr)
        tw = d.textlength(self.badge_text, font=f)
        p = ease(t / 0.35)
        if p <= 0:
            return
        s = pop(t / 0.35)
        bw, bh = int((tw + 80) * s), int(110 * s)
        x0, y0 = (W - bw) // 2, 330 - (bh - 110) // 2
        badge = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        bd = ImageDraw.Draw(badge)
        bd.rounded_rectangle((0, 0, bw - 1, bh - 1), 26, fill=self.th["hot"])
        fs = font(max(10, int(64 * s)))
        bd.text(((bw - bd.textlength(self.badge_text, font=fs)) / 2, (bh - fs.size) / 2 - 6), self.badge_text,
                font=fs, fill="#ffffff")
        badge = badge.rotate(-4, expand=True, resample=Image.BICUBIC)
        fr.paste(badge, (x0, y0), badge)

    def frame(self, t: float, progress: float) -> Image.Image:
        tt = t + 5.0 if self.first else t      # 첫 장면은 처음부터 다 보이게 (표지)
        fr = self._background(t)
        fr.paste(self.base, (0, 0), self.base)
        p = ease(tt / 0.6)
        if p > 0:
            sc = 0.85 + 0.15 * p + 0.03 * math.sin(t * 2.2)
            g = self.glyph.resize((max(1, int(self.glyph.width * sc)), max(1, int(self.glyph.height * sc))))
            ga = g.getchannel("A").point(lambda v: int(v * p))
            fr.paste(g, ((W - g.width) // 2, 1000 - g.height // 2 + 60), ga)
        self._badge(fr, tt)
        hy = 560 if self.kind != "hook" else 620
        _put(fr, self.head, hy, tt, 0.15, 0.4)
        idx = max(i for i, at in enumerate(self.cap_at) if at <= t)
        cap = self.caps[idx]
        _put(fr, cap, 1380, t + 5.0 if (self.first and idx == 0) else t, self.cap_at[idx], 0.15, 16)
        d = ImageDraw.Draw(fr)
        d.rectangle((0, 0, int(W * progress), 12), fill=self.th["accent"])
        return fr


def render_scene(scene: Scene, audio: Path, out: Path, t_offset: float, total: float) -> None:
    n = max(1, round(scene.seconds * FPS))
    proc = subprocess.Popen([
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(audio),
        "-filter_complex", "[0:v]format=yuv420p[v];[1:a]apad,aresample=44100[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{n / FPS:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ac", "2", str(out),
    ], stdin=subprocess.PIPE)
    try:
        for i in range(n):
            t = i / FPS
            proc.stdin.write(scene.frame(t, (t_offset + t) / total).convert("RGB").tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg 인코딩 실패")
