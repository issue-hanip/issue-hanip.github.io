"""성과 평가 → 개선 → 다음 콘텐츠 반영 (학습 고리).

1) 수집: metrics.py가 매일 YouTube 조회·좋아요·시청 비율을 쌓는다.
2) 평가: 게시 후 2일 이상 지난 숏츠의 점수(조회수 × 시청 비율 가산)를 채널 중앙값으로 나눈 상대 점수.
3) 개선
   - 테마: 값별 상대 점수의 중앙값을 표본 수만큼만 믿어(수축) 0.5~2.0 가중치로. 표본 3개 미만은 1.0으로 계속 시험.
     (실거래가 채널에서 평균+상한 2.5 방식이 상한에 몰려 변별력을 잃은 것을 피함)
   - 분야·화면 구성(motion/frame, 사진 유무): 값별 성과를 요약해 선정·작성 지침으로.
   - 교훈: 주 1회, 채점된 영상이 8편 이상이면 Claude가 상·하위를 비교해 최대 5개.
4) 적용: data/playbook.json → 선정(pick)·작성(write) 지침, 숏츠 테마 선택.
   교훈은 사실·검수 규칙보다 우선하지 않는다.
"""
import json
import statistics
from datetime import datetime, timedelta, timezone

from . import metrics
from .config import DATA_DIR

PLAYBOOK = DATA_DIR / "playbook.json"
MIN_AGE_DAYS = 2
MIN_SAMPLES = 3
LESSON_MIN_SCORED = 8
W_MIN, W_MAX = 0.5, 2.0

LESSON_SCHEMA = {
    "type": "object",
    "properties": {"lessons": {"type": "array", "items": {"type": "string"}}},
    "required": ["lessons"], "additionalProperties": False,
}
LESSON_SYSTEM = (
    "당신은 한국 이슈·연예 숏폼 채널 '이슈 한입'의 성과 분석가입니다. 아래는 숏츠별 상대 성과(1.0이 채널 보통), "
    "분야, 제목, 화면 구성, 평균 시청 비율입니다. 상위와 하위를 비교해 다음 글·숏츠의 주제 선정, 제목, 첫 장면(hook)에 "
    "바로 적용할 수 있는 구체적인 교훈을 최대 5개 쓰세요.\n"
    "- 표본이 적어 확실하지 않은 것은 쓰지 마세요. 개별 연예인 이름이 아니라 유형(예: 수치가 분명한 소식, 생활 정보)으로 쓰세요.\n"
    "- 보도되지 않은 내용 추가, 과장·낚시 제목, 사생활·외모 소재, 의혹을 사실처럼 쓰는 것은 교훈이 될 수 없습니다.\n"
    "- 숫자를 직접 쓰지 말고 원칙만 쓰세요.")


def load() -> dict:
    if PLAYBOOK.exists():
        pb = json.loads(PLAYBOOK.read_text(encoding="utf-8"))
    else:
        pb = {}
    pb.setdefault("weights", {"theme": {}})
    pb.setdefault("stats", {})
    pb.setdefault("lessons", [])
    pb.setdefault("lessons_at", "")
    pb.setdefault("history", [])
    return pb


def save(pb: dict) -> None:
    PLAYBOOK.write_text(json.dumps(pb, ensure_ascii=False, indent=1), encoding="utf-8")


def theme_weights(pb: dict) -> dict:
    return dict(pb.get("weights", {}).get("theme") or {})


def score(conn) -> dict:
    """slug → 상대 점수. 게시 후 3일째 값이 있으면 그 값, 없으면 최신 값(숏츠 조회는 1~2일 안에 대부분 붙는다)."""
    metrics.ensure(conn)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=MIN_AGE_DAYS)).isoformat()
    vals = {}
    for v in conn.execute("SELECT slug, created_at FROM videos WHERE youtube_id IS NOT NULL AND created_at <= ?",
                          (cutoff,)):
        try:
            d3 = (datetime.fromisoformat(v["created_at"]) + timedelta(days=3)).strftime("%Y-%m-%d")
        except ValueError:
            d3 = "9999"
        row = conn.execute("""SELECT views, retention FROM metrics WHERE slug = ? AND platform = 'youtube'
                              AND day >= ? AND views IS NOT NULL ORDER BY day LIMIT 1""", (v["slug"], d3)).fetchone() \
            or conn.execute("""SELECT views, retention FROM metrics WHERE slug = ? AND platform = 'youtube'
                               AND views IS NOT NULL ORDER BY day DESC LIMIT 1""", (v["slug"],)).fetchone()
        if not row:
            continue
        val = row["views"]
        if row["retention"] is not None:
            val = val * (0.5 + min(row["retention"], 150) / 100)   # 끝까지 본 비율이 높을수록 가산
        vals[v["slug"]] = val
    if len(vals) < 2:
        return {}
    med = statistics.median(vals.values())
    return {s: round((x + 1) / (med + 1), 3) for s, x in vals.items()}


def _shrunk(xs: list[float]) -> float:
    n = len(xs)
    if n < MIN_SAMPLES:
        return 1.0
    m = statistics.median(xs)
    return round(min(W_MAX, max(W_MIN, 1 + (m - 1) * n / (n + 3))), 2)


def _features(conn) -> dict:
    out = {}
    for r in conn.execute("""SELECT v.slug, v.title, v.variant, s.category FROM videos v
                             JOIN stories s ON s.slug = v.slug"""):
        try:
            var = json.loads(r["variant"] or "{}")
        except ValueError:
            var = {}
        out[r["slug"]] = {"title": r["title"], "category": r["category"], "theme": var.get("theme"),
                          "layout": var.get("layout") or "frame",
                          "visual": "photo" if (var.get("photos") or 0) > 0 else "nophoto"}
    return out


def update(conn, writer=None) -> dict:
    """가중치·요약을 다시 계산하고, 주 1회 교훈을 갱신한다. 요약을 돌려준다."""
    pb = load()
    scores = score(conn)
    feats = _features(conn)
    groups: dict = {d: {} for d in ("theme", "category", "layout", "visual")}
    for slug, sc in scores.items():
        f = feats.get(slug)
        if not f:
            continue
        for d in groups:
            if f.get(d):
                groups[d].setdefault(f[d], []).append(sc)
    from .cards import THEMES
    pb["weights"]["theme"] = {t: _shrunk(groups["theme"].get(t, [])) for t in THEMES}
    pb["stats"] = {d: {k: {"n": len(xs), "median": round(statistics.median(xs), 2)} for k, xs in g.items()}
                   for d, g in groups.items()}
    summary = {"scored": len(scores), "theme_weights": pb["weights"]["theme"], "stats": pb["stats"]}

    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    due = not pb["lessons_at"] or pb["lessons_at"] < week_ago
    if writer is not None and due and len(scores) >= LESSON_MIN_SCORED:
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])

        def line(slug, sc):
            f = feats.get(slug)
            if not f:
                return None
            ret = conn.execute("""SELECT retention FROM metrics WHERE slug = ? AND platform='youtube'
                                  AND retention IS NOT NULL ORDER BY day DESC LIMIT 1""", (slug,)).fetchone()
            return (f"- 점수 {sc} | {f['category']} | {f['title']} | 화면 {f['layout']}·{f['visual']}"
                    + (f" | 시청 비율 {ret[0]:.0f}%" if ret else ""))
        k = min(6, len(ranked) // 2)
        table = "\n".join(filter(None, [line(s, sc) for s, sc in ranked[:k]] + ["…"]
                                 + [line(s, sc) for s, sc in ranked[-k:]]))
        try:
            res = writer._call(LESSON_SYSTEM, table, LESSON_SCHEMA, "medium")
            pb["lessons"] = [str(x)[:200] for x in res.get("lessons", [])][:5]
            pb["lessons_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            summary["lessons_updated"] = True
        except Exception as e:  # noqa: BLE001
            summary["lessons_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    pb["history"] = (pb["history"] + [{"at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                       "scored": len(scores), "theme": dict(pb["weights"]["theme"])}])[-30:]
    pb["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save(pb)
    summary["lessons"] = pb["lessons"]
    return summary


def _stat_line(name: str, st: dict) -> str:
    items = sorted(((k, v) for k, v in st.items() if v["n"] >= MIN_SAMPLES), key=lambda kv: -kv[1]["median"])
    if not items:
        return ""
    return f"- {name}별 숏츠 상대 성과(1.0=보통, 3편 이상만): " + ", ".join(
        f"{k} {v['median']}({v['n']}편)" for k, v in items)


def guidance(pb: dict, stage: str) -> str:
    """stage: "pick" | "write". 사실·검수 규칙이 항상 우선이라는 전제를 붙여 돌려준다."""
    lines = []
    if stage == "pick":
        s = _stat_line("분야", pb.get("stats", {}).get("category", {}))
        if s:
            lines += [s, "- 성과가 좋은 분야를 조금 더 고르되, 하루 구성의 다양성(국내 이슈·트렌드·꿀팁 섞기)은 유지하세요."]
    if pb.get("lessons"):
        lines.append("- 지금까지 성과 분석에서 얻은 교훈:")
        lines += [f"  - {x}" for x in pb["lessons"][:5]]
    if not lines:
        return ""
    return "\n".join(["(사실 확인·출처·명예훼손·사생활 규칙이 이 지침보다 항상 우선)"] + lines)
