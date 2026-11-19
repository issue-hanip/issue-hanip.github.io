"""이슈 숏폼 렌더러 (1080x1920). 유사 채널 벤치마킹(2026-11-17) 구성:

  위쪽 검은 띠: 분류·채널 이름 + 영상 내내 고정되는 큰 제목(강조어 빨강)
  가운데: 당사자 사진(천천히 확대·이동) 또는 짧은 영상, 위에 큰 자막(흰 글자·검은 테두리)
          사진이 없으면 움직이는 줄무늬 + 큰 기호
  아래 검은 띠: 장면 헤드라인, 면책 문구 / 사진 오른쪽 아래 출처 / 맨 위 진행 막대

사진·영상은 media.py가 Wikimedia Commons에서 재사용 허락된 것만 가져온다.
테마(THEMES)는 강조색 실험 변수다.
"""
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .render import W, H, _wrap, font
from .tts import FFMPEG

FPS = 30
MY0, MH = 470, 1000          # 가운데 사진 영역
MY1 = MY0 + MH
ZOOM = 0.10

THEMES = {
    "tabloid": dict(bg=(18, 18, 22), stripe=(32, 32, 40), text="#ffffff", muted="#a1a1aa", accent="#facc15",
                    hot="#ef4444"),
    "neon": dict(bg=(10, 6, 30), stripe=(26, 16, 64), text="#ffffff", muted="#c4b5fd", accent="#22d3ee",
                 hot="#f472b6"),
    "paper": dict(bg=(24, 20, 16), stripe=(44, 38, 30), text="#ffffff", muted="#d6d3d1", accent="#fde68a",
                  hot="#f97316"),
    "mint": dict(bg=(8, 32, 34), stripe=(14, 52, 54), text="#f0fdfa", muted="#99f6e4", accent="#fde047",
                 hot="#fb7185"),
}

BADGE = {"hook": "?!", "what": "무슨 일?", "twist": "그런데", "reaction": "입장은", "outro": "정리"}
GLYPH = {"hook": "?", "what": "1", "twist": "!", "reaction": "“ ”", "outro": "→"}


def ease(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def pop(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1 + 0.18 * math.sin(p * math.pi) if p < 1 else 1.0


def _chunks(narration: str, size: int = 12) -> list[str]:
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


def _text_sprite(text: str, f, fill, stroke="#000000", sw=6, width: int = W - 100) -> Image.Image:
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = _wrap(probe, text, f, width)
    lh = int(f.size * 1.2)
    img = Image.new("RGBA", (W, lh * len(lines) + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        x = (W - d.textlength(line, font=f)) / 2
        d.text((x, i * lh + 4), line, font=f, fill=fill, stroke_width=sw, stroke_fill=stroke)
    return img


def _put(frame, sp, y, t, delay=0.0, dur=0.35, dy=40):
    p = ease((t - delay) / dur)
    if p <= 0:
        return
    if p >= 1:
        frame.paste(sp, (0, y), sp)
        return
    mask = sp.getchannel("A").point(lambda a: int(a * p))
    frame.paste(sp, (0, y + int((1 - p) * dy)), mask)


class Clip:
    """짧은 영상을 사진 영역 크기로 한 프레임씩 읽는다 (짧으면 처음부터 반복)."""

    def __init__(self, path: Path, seconds: float):
        self.proc = subprocess.Popen([
            FFMPEG, "-loglevel", "error", "-stream_loop", "-1", "-i", str(path), "-t", f"{seconds + 0.5:.2f}",
            "-vf", f"scale={W}:{MH}:force_original_aspect_ratio=increase,crop={W}:{MH},fps={FPS}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        self.last = Image.new("RGB", (W, MH))

    def next(self) -> Image.Image:
        buf = self.proc.stdout.read(W * MH * 3) if self.proc.stdout else b""
        if len(buf) == W * MH * 3:
            self.last = Image.frombytes("RGB", (W, MH), buf)
        return self.last

    def close(self):
        try:
            self.proc.kill()
        except OSError:
            pass


class Scene:
    """media: None | ("photo", PIL.Image, 출처) | ("clip", 경로, 출처)"""

    def __init__(self, kind, headline, narration, th, category, site_title, seconds, band, media=None,
                 first=False, pan=0):
        self.kind, self.th, self.seconds, self.first, self.pan = kind, th, seconds, first, pan
        self.top = self._top(category, site_title, band)
        self.bottom = self._bottom()
        self.head = _text_sprite(headline, font(84), th["accent"], sw=0, width=W - 120)
        chunks = _chunks(narration)
        weights = [max(4, len(c)) for c in chunks]
        tot = sum(weights)
        self.cap_at, acc = [], 0.0
        for wgt in weights:
            self.cap_at.append(acc / tot * seconds)
            acc += wgt
        self.caps = [_text_sprite(c, font(80), "#ffffff", sw=7) for c in chunks]
        self.photo = self.clip = self.credit = None
        self.glyph = self._glyph()
        if media:
            mk, src, credit = media
            self.credit = self._credit(credit)
            if mk == "photo":
                self.photo = ImageOps.fit(src.convert("RGB"), (int(W * (1 + ZOOM)), int(MH * (1 + ZOOM))),
                                          centering=(0.5, 0.3))
            else:
                self.clip = Clip(src, seconds)

    # ---- 고정 요소
    def _top(self, category, site_title, band):
        img = Image.new("RGBA", (W, MY0), (0, 0, 0, 255))
        d = ImageDraw.Draw(img)
        f = font(38, bold=False)
        label = f"{category if category in ('트렌드', '꿀팁') else category + ' 이슈'} · {site_title}"
        d.text(((W - d.textlength(label, font=f)) / 2, 70), label, font=f, fill=self.th["muted"])
        text, hot = band.get("text", ""), band.get("hot", "")
        bf = font(104)
        lines = _wrap(d, text, bf, W - 90)[:2]
        lh = int(bf.size * 1.22)
        y0 = 150 + (300 - lh * len(lines)) // 2
        for i, line in enumerate(lines):
            parts = [(line, self.th["text"])]
            if hot and hot in line:
                a, b = line.split(hot, 1)
                parts = [(a, self.th["text"]), (hot, self.th["hot"]), (b, self.th["text"])]
            x = (W - d.textlength(line, font=bf)) / 2
            for seg, col in parts:
                if seg:
                    d.text((x, y0 + i * lh), seg, font=bf, fill=col, stroke_width=4, stroke_fill="#000000")
                    x += d.textlength(seg, font=bf)
        return img

    def _bottom(self):
        img = Image.new("RGBA", (W, H - MY1), (0, 0, 0, 255))
        d = ImageDraw.Draw(img)
        foot = "보도 내용 정리 · 사실관계는 출처 기사 기준"
        ff = font(32, bold=False)
        d.text(((W - d.textlength(foot, font=ff)) / 2, H - MY1 - 110), foot, font=ff, fill=self.th["muted"])
        return img

    def _credit(self, text):
        f = font(26, bold=False)
        text = "사진: " + text + " · Wikimedia Commons"
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        while probe.textlength(text, font=f) > W - 80 and len(text) > 10:
            text = text[:-2]
        tw = probe.textlength(text, font=f)
        img = Image.new("RGBA", (int(tw) + 24, 42), (0, 0, 0, 150))
        ImageDraw.Draw(img).text((12, 6), text, font=f, fill="#e5e5e5")
        return img

    def _glyph(self):
        f = font(560)
        txt = GLYPH[self.kind]
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        tw = int(probe.textlength(txt, font=f))
        img = Image.new("RGBA", (tw + 40, 700), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        col = self.th["hot"] if self.kind in ("twist", "hook") else self.th["accent"]
        d.text((20, 0), txt, font=f, fill=col)
        img.putalpha(img.getchannel("A").point(lambda v: int(v * 0.3)))
        return img

    # ---- 움직이는 요소
    def _media(self, t) -> Image.Image:
        if self.clip:
            return self.clip.next()
        if self.photo is not None:
            p = max(0.0, min(1.0, t / max(self.seconds, 0.1)))
            bw, bh = self.photo.size
            z = 1.0 + ZOOM * p                       # 천천히 확대하면서 한쪽으로 이동
            cw, ch = bw / z, bh / z
            fx = 0.85 if self.pan % 2 == 0 else 0.15
            dx, dy = (bw - cw) * fx, (bh - ch) * 0.3
            return self.photo.resize((W, MH), Image.BILINEAR, box=(dx, dy, dx + cw, dy + ch))
        img = Image.new("RGB", (W, MH), self.th["bg"])
        d = ImageDraw.Draw(img)
        shift = int(t * 60) % 160
        for x in range(-MH, W + 160, 160):
            x0 = x + shift
            d.polygon([(x0, 0), (x0 + 60, 0), (x0 + 60 + MH, MH), (x0 + MH, MH)], fill=self.th["stripe"])
        sc = 0.9 + 0.04 * math.sin(t * 2.2)
        g = self.glyph.resize((max(1, int(self.glyph.width * sc)), max(1, int(self.glyph.height * sc))))
        img.paste(g, ((W - g.width) // 2, (MH - g.height) // 2 + 40), g)
        return img

    def _badge(self, fr, t):
        p = ease(t / 0.35)
        if p <= 0:
            return
        text = BADGE[self.kind]
        s = pop(t / 0.35)
        fs = font(max(10, int(58 * s)))
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        bw, bh = int(probe.textlength(text, font=fs) + 60), int(96 * s)
        badge = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        bd = ImageDraw.Draw(badge)
        bd.rounded_rectangle((0, 0, bw - 1, bh - 1), 24, fill=self.th["hot"])
        bd.text((30, (bh - fs.size) / 2 - 6), text, font=fs, fill="#ffffff")
        badge = badge.rotate(-4, expand=True, resample=Image.BICUBIC)
        fr.paste(badge, (40, MY0 + 36), badge)

    def frame(self, t: float, progress: float) -> Image.Image:
        tt = t + 5.0 if self.first else t      # 첫 장면은 처음부터 다 보이게 (표지)
        fr = Image.new("RGB", (W, H), (0, 0, 0))
        fr.paste(self._media(t), (0, MY0))
        fr.paste(self.top, (0, 0))
        fr.paste(self.bottom, (0, MY1))
        if self.credit:
            fr.paste(self.credit, (W - self.credit.width - 20, MY1 - self.credit.height - 14), self.credit)
        self._badge(fr, tt)
        idx = max(i for i, at in enumerate(self.cap_at) if at <= t)
        _put(fr, self.caps[idx], MY1 - 330, t + 5.0 if (self.first and idx == 0) else t, self.cap_at[idx], 0.12, 14)
        _put(fr, self.head, MY1 + 70, tt, 0.1, 0.35)
        ImageDraw.Draw(fr).rectangle((0, 0, int(W * progress), 12), fill=self.th["accent"])
        return fr

    def close(self):
        if self.clip:
            self.clip.close()


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
    finally:
        scene.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg 인코딩 실패")
