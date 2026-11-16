"""규칙 검사: Claude 검수 전에 기계적으로 잡을 수 있는 문제를 거른다."""
import re
from urllib.parse import urlparse

from .write import SCENES

URL = re.compile(r"https?://\S+")
# 단정 표현 바로 앞에 '의혹·혐의·주장' 같은 유보어가 없으면 위험 신호
ALLEGATION = re.compile(r"(폭행|갑질|사기|횡령|성추행|성폭행|마약|음주운전|학폭|학교폭력|불륜|외도|표절|탈세)(을|를)?\s?(했다|저질렀다|한 것으로 드러났다|사실이 드러났다|가 사실로)")
HEDGE = ("의혹", "혐의", "주장", "보도", "논란", "폭로", "고소", "고발", "부인", "인정")
QUOTES = (re.compile(r"“([^“”]*)”"), re.compile(r'"([^"\n]*)"'))   # 짝을 맞춘 인용부호 안쪽
MAX_QUOTE = 70                                                       # 이보다 긴 직접 인용은 베끼기 의심


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def check(story: dict, cfg: dict) -> list[str]:
    p = []
    g, safety, s = cfg["generate"], cfg["safety"], cfg["shorts"]
    title, body = story.get("title", ""), story.get("body_md", "")

    # 출처
    srcs = story.get("sources") or []
    good = [x for x in srcs if x.get("url", "").startswith("http") and "news.google.com" not in x["url"]]
    outlets = {x["outlet"].strip() for x in good} | set()
    domains = {_domain(x["url"]) for x in good}
    if min(len(outlets), len(domains)) < g["min_sources"]:
        p.append(f"출처 부족: 서로 다른 언론사 원문 기사 {g['min_sources']}개 이상 필요 (현재 {min(len(outlets), len(domains))}개)")

    # 길이·형식
    plain = re.sub(r"[#>*_\-\n]", "", body)
    if len(plain) < 700:
        p.append(f"본문이 너무 짧음 ({len(plain)}자, 700자 이상)")
    if len(plain) > 2600:
        p.append(f"본문이 너무 김 ({len(plain)}자)")
    if body.count("\n## ") < 2:
        p.append("## 소제목이 2개 미만")
    if len(title) > 50:
        p.append(f"제목 50자 초과 ({len(title)}자)")
    if URL.search(body):
        p.append("본문에 URL을 쓰지 말 것 (출처는 시스템이 붙임)")
    for line in body.split("\n"):
        for rx in QUOTES:
            for m in rx.finditer(line):
                if len(m.group(1)) > MAX_QUOTE:
                    p.append(f"직접 인용이 너무 김 (한 문장 이내로 요약): '{m.group(1)[:30]}…'")

    # 숏츠
    sh = story.get("shorts") or {}
    scenes = sh.get("scenes") or {}
    for k in SCENES:
        sc = scenes.get(k) or {}
        n = len(sc.get("narration", ""))
        if not n:
            p.append(f"숏츠 {k}: 내레이션 없음")
        elif n > s["max_narration_chars"]:
            p.append(f"숏츠 {k}: 내레이션 너무 김 ({n}자 > {s['max_narration_chars']})")
        if len(sc.get("headline", "")) > 16:
            p.append(f"숏츠 {k}: 화면 문구 16자 초과")
    if len(sh.get("title", "")) > 60:
        p.append("숏츠 제목 60자 초과")

    # 표현
    texts = [title, story.get("description", ""), body, sh.get("title", "")]
    texts += [scenes.get(k, {}).get(f, "") for k in SCENES for f in ("headline", "narration")]
    joined = "\n".join(texts)
    for phrase in safety["banned_phrases"]:
        if phrase in joined:
            p.append(f"금지 표현: '{phrase}'")
    for sent in re.split(r"(?<=[.!?。])\s+|\n", joined):
        m = ALLEGATION.search(sent)
        if m and not any(h in sent for h in HEDGE):
            p.append(f"의혹을 사실처럼 단정한 문장 (출처·'의혹/주장' 표기 필요): '{sent.strip()[:50]}'")
    return p
