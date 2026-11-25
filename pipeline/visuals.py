"""숏츠 장면별 그림 자료: 이해를 돕는 자료 사진·3D 이모지 (모두 무료·재사용 허락분).

- 자료 사진: Wikimedia Commons 주제 검색 → Openverse(Flickr 등 CC 사진) 순. CC0·PD·CC BY·CC BY-SA만.
  실제 사건 사진이 아니므로 화면에 "자료 사진"으로 표시하고 출처를 적는다. Claude가 보고 거른다.
- 3D 이모지: Microsoft Fluent Emoji 3D (MIT 라이선스, 출처 표시 의무 없음).
- 인물 사진은 media.py (공인, 이름이 제목·분류에 있는 Commons 파일).
"""
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

import requests

from . import media

UA = media.UA
OPENVERSE = "https://api.openverse.org/v1/images/"
FLUENT = "https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/assets/{d}/3D/{f}_3d.png"
SKIP = ("logo", "map", "flag", "diagram", "chart", "screenshot", "signature", "poster", "nude", "accident")


def commons_topic(query: str, limit: int = 20) -> list[dict]:
    try:
        r = requests.get(media.API, headers=UA, timeout=30, params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": limit,
            "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1400,
            "iiextmetadatafilter": "LicenseShortName|Artist"})
        pages = (r.json().get("query") or {}).get("pages") or {}
    except (requests.RequestException, ValueError):
        return []
    out = []
    for p in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        ii = (p.get("imageinfo") or [{}])[0]
        m = ii.get("extmetadata") or {}
        lic = (m.get("LicenseShortName") or {}).get("value", "")
        title = p.get("title", "")
        if media.license_rank(lic) is None or any(w in title.lower() for w in SKIP):
            continue
        if ii.get("width", 0) < 800 or ii.get("height", 0) < 600:
            continue
        out.append({"title": title, "license": lic, "artist": media._clean((m.get("Artist") or {}).get("value", "")),
                    "thumb": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl", ""),
                    "source": "Wikimedia Commons"})
    return out


def openverse(query: str, limit: int = 20) -> list[dict]:
    time.sleep(2)                       # 익명 사용 한도가 낮다
    try:
        r = requests.get(OPENVERSE, headers=UA, timeout=30, params={
            "q": query, "license": "by,by-sa,cc0,pdm", "page_size": limit, "mature": "false"})
        res = r.json().get("results") or []
    except (requests.RequestException, ValueError):
        return []
    out = []
    for x in res:
        lic = f"CC {x.get('license', '').upper()} {x.get('license_version', '')}".strip()
        if x.get("license") in ("cc0", "pdm"):
            lic = "CC0" if x["license"] == "cc0" else "Public domain"
        if media.license_rank(lic) is None or (x.get("width") or 0) < 800:
            continue
        if any(w in (x.get("title") or "").lower() for w in SKIP):
            continue
        out.append({"title": x.get("title") or query, "license": lic, "artist": (x.get("creator") or "")[:60],
                    "thumb": x.get("url"), "page": x.get("foreign_landing_url") or x.get("url"),
                    "source": x.get("source") or "Openverse"})
    return out


def topic_candidates(query: str, n: int = 5) -> list[dict]:
    c = commons_topic(query)[:n]
    if len(c) < n:
        c += openverse(query)[: n - len(c)]
    return c


def emoji_png(ch: str, work: Path) -> Path | None:
    """Fluent 3D 이모지 PNG. 폴더 이름은 유니코드 이름의 첫 글자만 대문자 (예: BIRTHDAY CAKE → Birthday cake)."""
    base = "".join(c for c in ch if unicodedata.category(c) != "Mn" and ord(c) not in (0xFE0F, 0x200D))
    if not base:
        return None
    try:
        name = unicodedata.name(base[0])
    except ValueError:
        return None
    out = work / f"emoji_{ord(base[0]):x}.png"
    names = [name.capitalize()]
    words = {w for w in name.lower().replace("-", " ").split() if len(w) > 2}
    folders = _fluent_folders()
    # 유니코드 이름과 폴더 이름이 다른 경우 (SCALES → Balance scale): 단어가 겹치는 폴더
    names += [f for f in folders if words and words & {w.rstrip("s") for w in f.lower().split()}
              | {w.rstrip("s") + "s" for w in f.lower().split()}][:3]
    for d in dict.fromkeys(names):
        f = d.lower().replace(" ", "_").replace("-", "_")
        try:
            r = requests.get(FLUENT.format(d=quote(d), f=quote(f)), headers=UA, timeout=30)
            if r.status_code == 200 and len(r.content) > 2000:
                out.write_bytes(r.content)
                return out
        except requests.RequestException:
            pass
    return None


_FOLDERS: list[str] | None = None


def _fluent_folders() -> list[str]:
    global _FOLDERS
    if _FOLDERS is None:
        try:
            r = requests.get("https://api.github.com/repos/microsoft/fluentui-emoji/contents/assets",
                             headers=UA, timeout=30)
            _FOLDERS = [x["name"] for x in r.json()] if r.status_code == 200 else []
        except (requests.RequestException, ValueError):
            _FOLDERS = []
    return _FOLDERS


def match_person(who: str, people: list[dict]) -> dict | None:
    """장면의 who → people 항목. 비어 있으면 인물이 한 명일 때만 그 인물. 못 맞추면 None (사진 안 넣음)."""
    who = (who or "").replace(" ", "").lower()
    if not who:
        return people[0] if len(people) == 1 else None
    for pp in people:
        names = [pp.get("name_ko", ""), pp.get("name_en", "")] + list(pp.get("aliases") or [])
        if any(n and n.replace(" ", "").lower() == who for n in names):
            return pp
    return None


def resolve(plan: dict, people: list[dict], work: Path, vet_people=None, vet_topic=None,
            max_people_photos: int = 2) -> list[dict]:
    """장면 계획 → 실제 자료. 장면마다 {kind, file, credit, label, emoji_file, big, sub, items}.
    사진을 못 구한 장면은 emoji로 바꾼다. emoji 그림도 없으면 글자(이모지 문자)로 보여 준다."""
    work.mkdir(parents=True, exist_ok=True)
    scenes = [dict(s) for s in plan["scenes"]]
    # 인물 사진
    # 장면마다 그 장면 대본이 말하는 인물(who)의 사진만 넣는다. 다른 인물 사진으로 채우지 않는다.
    person_idx = [i for i, s in enumerate(scenes) if s["kind"] == "person"]
    wants = {i: match_person(scenes[i].get("who", ""), people) for i in person_idx}
    need = [pp for pp in people if any(w is pp for w in wants.values())]
    found = []
    if need:
        try:
            found = [m for m in media.gather(need, work / "people", max_people_photos, 0, vet=vet_people)
                     if m.kind == "photo"]
        except Exception as e:
            print(f"  인물 사진 실패: {e}")
    used = set()
    for i in person_idx:
        pp = wants[i]
        key = (pp or {}).get("name_ko") or (pp or {}).get("name_en", "")
        m = next((m for j, m in enumerate(found) if pp is not None and m.person == key and j not in used), None)
        if m is None:
            scenes[i]["kind"] = "emoji"
            continue
        used.add(found.index(m))
        scenes[i].update(file=m.path, credit=m.credit(), label="", title=m.title, page=m.page)
    # 자료 사진: 장면마다 후보 3장 → 한 번에 검수 → 장면별 첫 통과분
    cands, owner = [], []
    for i, s in enumerate(scenes):
        if s["kind"] != "photo" or not s.get("query_en"):
            if s["kind"] == "photo":
                s["kind"] = "emoji"
            continue
        for k, it in enumerate(topic_candidates(s["query_en"])):
            path = work / f"t{i}_{k}.jpg"
            if media._download(it["thumb"], path):
                cands.append((path, it))
                owner.append(i)
    ok = set(range(len(cands)))
    if cands and vet_topic:
        try:
            ok = vet_topic([p for p, _ in cands], [scenes[o]["query_en"] for o in owner])
        except Exception as e:
            print(f"  자료 사진 검수 실패 → 사진 없이: {e}")
            ok = set()
    for i, s in enumerate(scenes):
        if s["kind"] != "photo":
            continue
        pick = next(((p, it) for j, ((p, it), o) in enumerate(zip(cands, owner)) if o == i and j in ok), None)
        if pick:
            p, it = pick
            s.update(file=p, credit=f"{it['artist']} · {it['license']} · {it['source']}".strip(" ·"),
                     label="자료 사진", title=it["title"], page=it["page"])
        else:
            s["kind"] = "emoji"
    # 이모지 그림 (모든 장면에 받아 둔다: 숫자·목록 장면에도 작게 쓴다)
    for s in scenes:
        if s.get("emoji"):
            s["emoji_file"] = emoji_png(s["emoji"], work)
    return scenes


def credits(scenes: list[dict]) -> str:
    lines = [f"- {s.get('title', '').removeprefix('File:')} — {s['credit']} — {s.get('page', '')}"
             for s in scenes if s["kind"] in ("photo", "person") and s.get("credit")]
    if not lines:
        return ""
    return "사진 출처 (재사용 허락된 자료, 자료 사진은 실제 사건 사진이 아님):\n" + "\n".join(dict.fromkeys(lines))
