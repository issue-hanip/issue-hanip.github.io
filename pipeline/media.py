"""숏츠에 쓸 인물 사진·짧은 영상을 Wikimedia Commons에서 찾는다 (재사용 허락된 것만).

- 라이선스: CC0·퍼블릭 도메인·CC BY 우선, CC BY-SA는 대안이 없을 때만. NC·ND·기타는 쓰지 않는다.
- 파일 제목에 인물 이름(영문 또는 한글)이 들어 있는 것만 쓴다 (다른 사람 사진이 섞이지 않게).
- 출처(작가·라이선스·주소)는 영상 화면과 설명에 표시한다.
- 누구 사진을 쓸지는 작가 단계에서 정하고 검수를 거친다 (혐의자·피해자·미성년자·일반인 제외).
"""
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

from .tts import FFMPEG

API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "IssueHanipBot/1.0 (https://issue-hanip.github.io; shorts illustration)"}
SKIP_WORDS = ("signature", "logo", "autograph", "서명", "로고", "map", "flag")


@dataclass(frozen=True)
class Media:
    kind: str          # photo | clip
    path: Path
    title: str
    artist: str
    license: str
    page: str
    person: str

    def credit(self) -> str:
        return f"{self.artist} · {self.license}" if self.artist else self.license


def license_rank(lic: str) -> int | None:
    """작을수록 우선. None 이면 쓰지 않는다."""
    s = (lic or "").lower().strip()
    if "nc" in s.split("-") or "-nc" in s or "-nd" in s or "nd " in s:
        return None
    if s.startswith("cc0") or "public domain" in s or s.startswith("pd"):
        return 1
    if re.match(r"cc[ -]by \d", s):
        return 1
    if re.match(r"cc[ -]by-sa \d", s):
        return 2
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


def name_in_title(title: str, names: list[str]) -> bool:
    t = _norm(title)
    return any(len(_norm(n)) >= 2 and _norm(n) in t for n in names if n)


def _clean(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()[:60]


def search(names: list[str], kind: str = "bitmap", limit: int = 30) -> list[dict]:
    """이름이 제목에 들어간 파일 후보 (라이선스·크기 거름, 최신·라이선스 순)."""
    seen, out = set(), []
    for q in [n for n in names if n]:
        try:
            r = requests.get(API, headers=UA, timeout=30, params={
                "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                "gsrsearch": f'intitle:"{q}" filetype:{kind}', "gsrlimit": limit,
                "prop": "imageinfo|categories", "iiprop": "url|size|extmetadata|mediatype", "iiurlwidth": 1400,
                "iiextmetadatafilter": "LicenseShortName|Artist|DateTimeOriginal",
                "clshow": "!hidden", "cllimit": "max"})
            pages = (r.json().get("query") or {}).get("pages") or {}
        except (requests.RequestException, ValueError):
            continue
        for p in pages.values():
            title = p.get("title", "")
            if title in seen or not p.get("imageinfo"):
                continue
            seen.add(title)
            ii = p["imageinfo"][0]
            m = ii.get("extmetadata") or {}
            lic = (m.get("LicenseShortName") or {}).get("value", "")
            rank = license_rank(lic)
            if rank is None or not name_in_title(title, names):
                continue
            if any(w in title.lower() for w in SKIP_WORDS):
                continue
            if kind == "bitmap" and (ii.get("height", 0) < 420 or ii.get("width", 0) < 320):
                continue
            dur = float(ii.get("duration") or 0)
            if kind == "video" and not (8 <= dur <= 900):
                continue
            cats = [c.get("title", "") for c in p.get("categories") or []]
            out.append({"title": title, "license": lic, "rank": rank,
                        "categorized": any(name_in_title(c, names) for c in cats),
                        "artist": _clean((m.get("Artist") or {}).get("value", "")),
                        "date": (m.get("DateTimeOriginal") or {}).get("value", "")[:10],
                        "url": ii.get("url"), "thumb": ii.get("thumburl") or ii.get("url"),
                        "page": ii.get("descriptionurl") or "", "duration": dur,
                        "portrait": ii.get("height", 0) >= ii.get("width", 1) * 0.95})
    # 우선순위: 사람이 붙인 인물 분류까지 맞는 파일 → 제목만 맞는 파일
    #          → 세로 인물 사진(가로는 행사장·거리 풍경이 많다) → 라이선스 → 최신
    out.sort(key=lambda x: (not x["categorized"], kind == "bitmap" and not x["portrait"], x["rank"],
                            x["date"] == "",
                            "".join(chr(255 - ord(c)) for c in x["date"])))
    return out


def _download(url: str, path: Path) -> bool:
    try:
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code != 200 or len(r.content) < 5000:
            return False
        path.write_bytes(r.content)
        return True
    except requests.RequestException:
        return False


def _clip(item: dict, path: Path, seconds: float = 7.0) -> bool:
    """영상 중간에서 몇 초만 잘라 소리 없이 저장 (720p 변환본 우선)."""
    start = max(0.0, min(item["duration"] * 0.3, item["duration"] - seconds - 1))
    urls = []
    if "/wikipedia/commons/" in item["url"]:
        name = item["url"].rsplit("/", 1)[1]
        urls.append(item["url"].replace("/wikipedia/commons/", "/wikipedia/commons/transcoded/")
                    + f"/{name}.720p.vp9.webm")
    urls.append(item["url"])
    for u in urls:
        try:
            proc = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-user_agent", UA["User-Agent"],
                                   "-ss", f"{start:.1f}", "-t", f"{seconds:.1f}", "-i", u, "-an",
                                   "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                                   str(path)], capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            continue
        if proc.returncode == 0 and path.exists() and path.stat().st_size > 20_000:
            return True
    return False


def names_of(pp: dict) -> list[str]:
    return [n for n in [pp.get("name_en", ""), pp.get("name_ko", ""), *pp.get("aliases", [])] if n]


def _still(clip: Path, out: Path) -> bool:
    proc = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", "2", "-i", str(clip), "-frames:v", "1",
                           str(out)], capture_output=True, timeout=60)
    return proc.returncode == 0 and out.exists()


def gather(people: list[dict], work: Path, max_photos: int = 4, max_clips: int = 1, vet=None) -> list[Media]:
    """인물별로 사진을 고르게 모으고, 짧은 영상은 있으면 하나.

    vet(경로 목록, 인물 이름) -> 쓸 수 있는 번호 집합. Claude가 사진을 직접 보고
    일반인이 크게 나오거나 인물이 주인공이 아닌 사진(거리·부스·광고판)을 뺀다. 실패하면 사진을 쓰지 않는다.
    """
    work.mkdir(parents=True, exist_ok=True)
    per = [(pp, search(names_of(pp))) for pp in people[:3]]
    want = max_photos * 2 if vet else max_photos
    photos: list[Media] = []
    i = 0
    while len(photos) < want and any(c for _, c in per):
        pp, cands = per[i % len(per)]
        i += 1
        if not cands:
            continue
        it = cands.pop(0)
        ext = Path(it["thumb"].split("?")[0]).suffix.lower() or ".jpg"
        path = work / f"p{len(photos)}{ext}"
        if _download(it["thumb"], path):
            photos.append(Media("photo", path, it["title"], it["artist"], it["license"], it["page"],
                                pp.get("name_ko") or pp.get("name_en", "")))
    clips: list[Media] = []
    if photos and max_clips:
        for pp in people[:3]:
            for it in search(names_of(pp), kind="video", limit=15)[:3]:
                path = work / f"c{len(clips)}.mp4"
                if _clip(it, path):
                    clips.append(Media("clip", path, it["title"], it["artist"], it["license"], it["page"],
                                       pp.get("name_ko") or pp.get("name_en", "")))
                    break
            if len(clips) >= max_clips:
                break
    if vet:
        stills = [m.path for m in photos]
        for j, c in enumerate(clips):
            st = work / f"c{j}.jpg"
            stills.append(st if _still(c.path, st) else c.path)
        names = ", ".join(dict.fromkeys(n for pp in people[:3] for n in names_of(pp)))
        titles = [m.title for m in photos] + [m.title for m in clips]
        try:
            ok = vet(stills, names, titles)
        except Exception as e:
            print(f"  사진 검수 실패 → 사진 없이 진행: {e}")
            return []
        photos = [m for j, m in enumerate(photos) if j in ok][:max_photos]
        clips = [m for j, m in enumerate(clips) if j + len(stills) - len(clips) in ok] if photos else []
    return photos[:max_photos] + clips


def credits_text(media: list[Media]) -> str:
    if not media:
        return ""
    lines = ["사진·영상 출처 (Wikimedia Commons):"]
    for m in media:
        lines.append(f"- {m.title.removeprefix('File:')} — {m.credit()} — {m.page}")
    return "\n".join(lines)
