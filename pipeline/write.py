"""Claude로 이슈를 고르고, 원문을 찾아 확인한 뒤 쓰고, 따로 검수한다.

원칙 (사용자 결정 2026-11-16): 형식은 궁금증을 자극하고 자극적으로, 내용은 보도된 사실만.
- 루머·추측은 '~라는 보도/주장'으로 출처와 함께만, 단정 금지
- 사생활(연애·가족·건강)의 추측, 범죄 혐의자 일반인 실명, 미성년자, 피해자 신상은 다루지 않음
- 기사 문장을 옮기지 않고 요약, 인용은 짧게(한 문장 이내)
"""
import json
import os
import shutil
import subprocess
import tempfile
import time

CATEGORIES = ["연예", "사회", "화제", "트렌드", "꿀팁", "스포츠", "경제", "기타"]
SCENES = ["hook", "what", "twist", "reaction", "outro"]

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "category": {"type": "string", "enum": CATEGORIES},
                           "angle": {"type": "string"}, "why": {"type": "string"}},
            "required": ["id", "category", "angle", "why"], "additionalProperties": False}},
        "skipped": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["id", "reason"], "additionalProperties": False}},
    },
    "required": ["picks", "skipped"], "additionalProperties": False,
}

_scene = {"type": "object",
          "properties": {"headline": {"type": "string"}, "narration": {"type": "string"}},
          "required": ["headline", "narration"], "additionalProperties": False}

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "body_md": {"type": "string"},
        "sources": {"type": "array", "items": {
            "type": "object",
            "properties": {"outlet": {"type": "string"}, "url": {"type": "string"}, "title": {"type": "string"}},
            "required": ["outlet", "url", "title"], "additionalProperties": False}},
        "claims": {"type": "array", "items": {
            "type": "object",
            "properties": {"claim": {"type": "string"}, "status": {"type": "string", "enum": ["확인된 사실", "보도·주장", "당사자 입장"]},
                           "source_outlet": {"type": "string"}},
            "required": ["claim", "status", "source_outlet"], "additionalProperties": False}},
        "shorts": {"type": "object",
                   "properties": {"title": {"type": "string"},
                                  "scenes": {"type": "object", "properties": {k: _scene for k in SCENES},
                                             "required": SCENES, "additionalProperties": False},
                                  "hashtags": {"type": "array", "items": {"type": "string"}},
                                  "band": {"type": "object",
                                           "properties": {"text": {"type": "string"}, "hot": {"type": "string"}},
                                           "required": ["text", "hot"], "additionalProperties": False},
                                  "people": {"type": "array", "items": {
                                      "type": "object",
                                      "properties": {"name_ko": {"type": "string"}, "name_en": {"type": "string"},
                                                     "aliases": {"type": "array", "items": {"type": "string"}}},
                                      "required": ["name_ko", "name_en", "aliases"], "additionalProperties": False}}},
                   "required": ["title", "scenes", "hashtags", "band", "people"], "additionalProperties": False},
    },
    "required": ["title", "description", "tags", "body_md", "sources", "claims", "shorts"],
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"approved": {"type": "boolean"},
                   "issues": {"type": "array", "items": {"type": "string"}},
                   "suggestions": {"type": "array", "items": {"type": "string"}},
                   "risk": {"type": "string", "enum": ["낮음", "중간", "높음"]}},
    "required": ["approved", "issues", "suggestions", "risk"], "additionalProperties": False,
}

RULES = """## 지켜야 할 선 (가장 중요 — 법적 책임이 채널 운영자에게 있다)
1. 보도된 사실만 쓴다. 기사에 없는 내용을 지어내거나 부풀리지 않는다. 숫자·날짜·이름·발언은 출처와 같아야 한다.
2. 의혹·루머·네티즌 주장은 반드시 "~라는 보도가 나왔다", "~라고 주장했다"처럼 누가 말했는지 밝히고, 사실로 단정하지 않는다.
   당사자(소속사 등) 입장이 보도됐으면 함께 쓴다. 수사·재판 중이면 "혐의", "재판 중"으로 쓰고 유죄처럼 쓰지 않는다.
3. 다루지 않는 것: 사생활 추측(연애·임신·이혼·건강·성적 지향), 미성년자 신상, 범죄·사고 피해자 신상, 일반인 실명·얼굴·주소,
   자살·자해 방법, 혐오(지역·성별·국적·장애), 특정인 조롱·모욕·외모 평가.
4. 저작권: 기사 문장을 그대로 옮기지 않고 내 말로 요약한다. 직접 인용은 한 문장 이내, 큰따옴표와 출처 표시.
   큰따옴표 안 발언은 실제로 읽은 기사 본문에 그 문구 그대로 있을 때만 쓴다 (관련기사 제목·다른 매체 기억으로 만들지 않음). 확실하지 않으면 따옴표 없이 "~라는 취지로 말했다"로 쓴다.
   어떤 내용을 "○○ 보도에 따르면"이라고 쓸 때는 그 매체 기사에 실제로 있는 내용이어야 한다. 세부 수치는 그 수치를 쓴 매체로만 돌린다.
   명단·목록을 일부만 쓰면 "등"을 붙인다.
5. 정치 이슈는 한쪽 편을 들지 않는다. 양쪽 입장을 같은 무게로 쓰거나 다루지 않는다.

## 형식 (여기서는 자극적으로)
- 제목·첫 문장은 궁금증을 부르는 질문·반전·긴장으로 연다. 예: "촬영 중 뼈가 부러졌는데, 왜 카메라를 끄지 않았을까?"
- 단, 제목이 본문 사실과 어긋나면 안 된다 (낚시 금지). 제목의 질문에 본문이 실제로 답해야 한다.
- 짧은 문장, 구어체, 사건 순서대로 '그런데', '여기서 반전' 같은 전개. 마지막은 앞으로 볼 점이나 독자에게 던지는 질문.
"""

PICK_SYSTEM = """당신은 한국 이슈·연예 소식 채널의 편집장입니다. 오늘 다룰 이슈를 고릅니다.
좋은 후보: 많은 사람이 궁금해하는 연예·화제·사회 이슈, 이야기 전개(갈등·반전·결말)가 있는 사건, 여러 언론이 다룬 사건.
또 2030이 저장·공유하고 싶은 소식도 고른다 (인스타 '인생치트키'·'20대 매거진'류):
- 트렌드: 줄 서는 팝업·품절 대란 신상·새로 뜨는 유행·시즌 한정(연말 케이크 등)처럼 여러 언론이 다룬 화제.
- 꿀팁: 이번 달 달라지는 제도, 신청 기간이 정해진 지원금·혜택, 요금·할인 변경처럼 독자가 바로 써먹을 정보.
피할 후보: 위 '다루지 않는 것'에 해당, 단순 동정·행사·보도자료성 기사, 학술·의료 홍보, 정치 공방이 중심인 기사,
비극(사망·참사)을 흥밋거리로 만들 수밖에 없는 사건, 최근에 이미 다룬 사건과 같은 이슈.
연예 이슈를 우선하되 섞어서 고르세요: 좋은 후보가 있으면 하루 발행분에 연예 외 1편(국내 이슈 '사회'·'화제'), 트렌드·꿀팁 1편을 넣는다.
같은 인물·같은 사건 중복 금지. 특정 업체 홍보성 보도자료(신제품 출시 알림 하나뿐인 기사)는 피하고, 여러 언론이 다룬 화제만 고른다.
angle에는 궁금증을 부를 관점을 한 줄로 적습니다. 트렌드·꿀팁은 "~ 총정리", "~ 이것만 알면 된다" 같은 정리형 관점이 좋다.
""" + RULES

WRITE_SYSTEM = """당신은 한국 이슈·연예 소식 채널의 작가입니다. 주어진 이슈를 웹에서 직접 찾아 확인한 뒤 블로그 글과 숏폼 대본을 씁니다.

## 작업 순서
1. WebSearch로 이 이슈를 다룬 기사를 찾고, WebFetch로 서로 다른 언론사 기사 2~4개를 읽는다.
   열 수 없는 기사는 건너뛴다. 읽지 않은 기사는 sources에 넣지 않는다.
2. 기사들이 공통으로 확인하는 사실과, 한쪽만 주장하는 내용을 나눈다 (claims에 기록).
3. 아래 규칙대로 쓴다.
""" + RULES + """
## 블로그 글 (body_md)
- 마크다운. 첫 문단은 3~4문장 핵심 요약(무슨 일이 왜 화제인지). ## 소제목 3~5개
  (예: 무슨 일이 있었나 / 논란의 핵심 / 당사자 입장 / 반응과 앞으로).
- 분량 900~1,600자. 출처 목록·면책 문구는 시스템이 붙이므로 쓰지 않는다. URL을 본문에 쓰지 않는다.
- title 45자 이내, description 80~140자, tags 3~6개(# 없이).
- sources: 실제로 읽은 기사만, 서로 다른 언론사로. url은 기사 원문 주소.

## 분류가 트렌드·꿀팁이면 (정리형)
- 사건 전개 대신 "핵심 3가지"처럼 정리한다: 무엇인지 / 언제·어디서·얼마·누가 대상인지 / 신청·이용 방법과 주의점.
- 날짜·가격·기간·조건·대상은 기사(또는 기사에 나온 공식 안내)와 정확히 같아야 하고, 기준일을 밝힌다("11월 19일 보도 기준").
- 특정 업체·상품을 사라고 권하지 않는다(광고 아님). 여러 곳을 다룬 기사면 "등"으로 균형 있게.
- 숏츠 장면은 같은 5개를 쓴다: hook(이것 모르면 손해 같은 궁금증), what(핵심 1), twist(핵심 2·의외의 점), reaction(핵심 3·방법), outro.
- people은 보통 빈 배열이다 (공식 활동 중인 공인이 당사자일 때만).

## 숏폼 대본 (shorts)
- 장면 5개: hook(질문·반전으로 3초 안에 붙잡기), what(무슨 일), twist(쟁점·반전), reaction(당사자 입장·반응), outro(앞으로 볼 점 + "자세한 정리는 블로그에서").
- headline: 화면에 크게 뜨는 문구 14자 이내. narration: 읽을 문장, 장면당 40~85자, 전체 45초 안팎.
- 규칙은 블로그와 같다 (의혹은 '보도에 따르면', 단정 금지).
- title: 50자 이내 궁금증 제목. hashtags: 4~6개(# 없이), 인물·사건 키워드 중심.
- band: 영상 내내 화면 위쪽에 고정되는 큰 제목. text는 16자 이내(예: "블랙핑크 YG 결별설 정리"),
  hot은 text 안에 그대로 들어 있는 강조 단어 하나(빨간색으로 표시, 예: "결별설"). 본문 사실과 어긋나면 안 된다.
- people: 영상에 사진을 보여줄 인물(최대 3명). 이 이슈의 당사자인 연예인·그룹·운동선수, 공식 활동 중인 공인만.
  범죄·사고의 혐의자나 피해자, 미성년자, 일반인, 사생활 보도(연애·이혼·건강)의 당사자는 넣지 않는다. 해당이 없으면 빈 배열.
  name_en은 위키미디어 공용(Wikimedia Commons) 파일 이름에 흔히 쓰는 영문 표기(예: "Cha Eun-woo", "BLACKPINK"),
  aliases에는 다른 영문 표기(예: "Cha Eunwoo")를 0~3개 적는다.
"""

REVIEW_SYSTEM = """당신은 언론사 법무·팩트체크 담당 편집장입니다. 발행 전 마지막 검수를 합니다.
필요하면 WebFetch로 sources 기사를 직접 열어 대조하세요. 아래 (1)~(9)에 해당하면 blocking 문제로 issues에 고칠 점을 구체적으로 적습니다.
표현을 더 정확하게 다듬을 수 있는 정도의 권장 사항은 suggestions에만 적습니다(발행을 막지 않음).
approved는 blocking 문제가 하나도 없을 때 true입니다.
(1) sources 기사와 다른 사실·숫자·날짜·이름·발언, 출처 없는 새로운 사실
(2) 의혹·루머·주장을 사실처럼 단정, 수사·재판 중 사안을 유죄처럼 서술 (명예훼손 위험)
(3) 사생활 추측, 미성년자·피해자·일반인 신상, 모욕·조롱·외모 평가, 혐오 표현
(4) 제목·hook이 본문 사실과 어긋남(낚시), 본문이 제목의 질문에 답하지 않음
(5) 기사 문장을 길게 베낌(한 문장 넘는 그대로 옮김)
(6) 정치적으로 한쪽 편을 듦, 자살·자해 방법 묘사
(7) 문장이 깨지거나 반복이 심함
(8) shorts.people에 혐의자·피해자·미성년자·일반인·사생활 보도 당사자가 들어 있거나, 이슈와 관계없는 인물이 들어 있음
(9) 트렌드·꿀팁 글의 가격·기간·조건·대상이 기사와 다름, 특정 업체·상품 구매를 권하는 광고성 서술
자극적인 문체·궁금증 유발 자체는 문제가 아닙니다. 사실과 선을 지키는지만 봅니다.
risk: 명예훼손·사생활 위험 수준. '높음'이면 approved=false.
""" + RULES


class WriterError(RuntimeError):
    pass


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise WriterError("응답에서 JSON을 찾지 못했습니다.")
    return json.loads(text[start:end + 1])


class ClaudeWriter:
    """Claude Code CLI(`claude -p`)를 구독 토큰으로 호출한다 (API 종량 과금 없음).
    글쓰기·검수 단계만 WebSearch/WebFetch를 허용한다 (원문 확인용)."""

    WEB = ["WebSearch", "WebFetch"]

    def __init__(self, model: str, effort: str = "medium", review_effort: str = "high", timeout: int = 1200):
        self.bin = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
        if not self.bin:
            raise WriterError("claude CLI를 찾을 수 없습니다 (npm i -g @anthropic-ai/claude-code 또는 CLAUDE_BIN).")
        self.model, self.effort, self.review_effort, self.timeout = model, effort, review_effort, timeout
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        self.env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        self.workdir = tempfile.mkdtemp(prefix="writer-")

    def _call(self, system: str, user: str, schema: dict, effort: str, web: bool = False,
              tools: list[str] | None = None, add_dir: str | None = None) -> dict:
        tools = tools if tools is not None else (self.WEB if web else [])
        cmd = [self.bin, "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
               "--system-prompt", system, "--tools", ",".join(tools), "--strict-mcp-config",
               "--model", self.model, "--effort", effort, "--no-session-persistence"]
        if tools:
            cmd += ["--allowedTools", *tools]
        if add_dir:
            cmd += ["--add-dir", add_dir]
        last = ""
        for attempt in range(3):
            try:
                proc = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8",
                                      timeout=self.timeout, env=self.env, cwd=self.workdir)
            except subprocess.TimeoutExpired:
                last = "시간 초과"
                continue
            try:
                out = json.loads(proc.stdout)
            except json.JSONDecodeError:
                last = (proc.stderr or proc.stdout)[-500:]
                time.sleep(20 * (attempt + 1))
                continue
            self.usage["calls"] += 1
            for k in ("input_tokens", "output_tokens"):
                self.usage[k] += (out.get("usage") or {}).get(k, 0) or 0
            if out.get("is_error"):
                last = str(out.get("result") or out.get("subtype"))[:500]
                low = last.lower()
                if "login" in low or "auth" in low or "token" in low:
                    raise WriterError(f"Claude 인증 실패(CLAUDE_CODE_OAUTH_TOKEN 확인): {last}")
                if "limit" in low:
                    raise WriterError(f"Claude 구독 사용 한도: {last}")
                time.sleep(20 * (attempt + 1))
                continue
            data = out.get("structured_output")
            if isinstance(data, dict):
                return data
            return _parse_json(out.get("result") or "")
        raise WriterError(f"claude CLI 호출 실패: {last}")

    def pick(self, candidates: list[dict], recent_titles: list[str], n: int, guidance: str = "") -> dict:
        user = (f"오늘 다룰 이슈를 최대 {n}개 고르세요. 적당한 후보가 없으면 적게 골라도 됩니다.\n\n후보:\n"
                + json.dumps(candidates, ensure_ascii=False, indent=1)
                + "\n\n최근 7일 동안 이미 다룬 이슈 (같은 사건 반복 금지):\n- " + "\n- ".join(recent_titles or ["(없음)"]))
        if guidance:
            user += "\n\n성과 기반 지침:\n" + guidance
        return self._call(PICK_SYSTEM, user, PICK_SCHEMA, self.effort)

    def write(self, candidate: dict, angle: str, category: str, feedback: list[str] | None = None,
              guidance: str = "") -> dict:
        user = ("이슈:\n" + json.dumps(candidate, ensure_ascii=False, indent=1)
                + f"\n\n분류: {category}\n편집장이 제안한 관점: {angle}\n"
                + "\n웹에서 원문을 찾아 확인한 뒤 규칙대로 작성하세요.")
        if guidance:
            user += "\n\n성과 기반 지침:\n" + guidance
        if feedback:
            user += "\n\n이전 원고가 검수에서 아래 이유로 반려되었습니다. 모두 고치세요:\n- " + "\n- ".join(feedback)
        return self._call(WRITE_SYSTEM, user, STORY_SCHEMA, self.effort, web=True)

    def review(self, story: dict) -> dict:
        user = "검수할 원고(JSON):\n" + json.dumps(story, ensure_ascii=False, indent=1)
        return self._call(REVIEW_SYSTEM, user, REVIEW_SCHEMA, self.review_effort, web=True)

    def revise(self, story: dict, suggestions: list[str]) -> dict:
        """검수를 통과한 원고에 권장 사항만 반영한다 (새 사실 추가 금지, 웹 도구 없음)."""
        system = (WRITE_SYSTEM + "\n\n## 지금 할 일\n검수를 통과한 원고에 편집장의 권장 사항을 반영해 다듬습니다. "
                  "권장 사항이 요구하는 부분만 고치고, 새 사실·새 인용·새 출처를 더하지 않습니다. sources와 claims는 그대로 둡니다.")
        user = ("원고(JSON):\n" + json.dumps(story, ensure_ascii=False, indent=1)
                + "\n\n반영할 권장 사항:\n- " + "\n- ".join(suggestions))
        return self._call(system, user, STORY_SCHEMA, self.effort)


    def vet_photos(self, paths: list, names: str, titles: list | None = None) -> set[int]:
        """숏츠에 넣을 사진 후보를 Claude가 직접 보고 고른다.
        얼굴로 신원을 판별하지 않고, 사진 속 글자·상황이 파일 정보와 맞는지만 대조한다."""
        from pathlib import Path
        paths = [Path(p).resolve() for p in paths]
        titles = titles or [""] * len(paths)
        listing = "\n".join(f"{i}: {p}  (Commons 파일 제목: {t})" for i, (p, t) in enumerate(zip(paths, titles)))
        user = (f"이슈 영상에 쓸 사진 후보입니다. 영상에 나올 인물/그룹: {names}\n"
                "Read 도구로 각 이미지를 열어 보고, 아래 조건을 모두 만족하는 번호만 ok에 넣으세요.\n"
                "(a) 무대·시상식·레드카펫·제작발표회·화보처럼 공식 활동 중인 연예인/공인이 사진의 주인공이다\n"
                "(b) 관계없어 보이는 일반인(행인·관객·직원)의 얼굴이 크게 나오지 않는다\n"
                "(c) 광고판·포스터·상품·부스·거리 풍경 위주의 사진이 아니다\n"
                "(d) 선정적이거나 사고·부상 장면이 아니고, 이미지가 깨지지 않았다\n"
                "(e) 사진 속 글자·상황이 파일 제목과 어긋나지 않는다: 배경판·이름표·자막에 적힌 이름이나 행사명이 "
                "다른 사람·다른 행사를 가리키면 제외. 그룹이면 인원수가 크게 어긋나면 제외. 단서가 없으면 이 조건은 통과로 본다.\n"
                "얼굴 생김새로 누구인지 판별하지 마세요. 위 조건만 봅니다.\n\n" + listing)
        schema = {"type": "object", "properties": {"ok": {"type": "array", "items": {"type": "integer"}},
                                                   "notes": {"type": "string"}},
                  "required": ["ok", "notes"], "additionalProperties": False}
        out = self._call("당신은 영상 편집 담당자입니다. 사진 사용 적합성만 판단합니다.", user, schema, "low",
                         tools=["Read"], add_dir=str(paths[0].parent) if paths else None)
        return {int(i) for i in out.get("ok", []) if 0 <= int(i) < len(paths)}

    def vet_topic_photos(self, paths: list, wants: list[str]) -> set[int]:
        """이해를 돕는 자료 사진(인물 아님) 후보를 Claude가 보고 고른다. wants[i] = i번 사진이 보여 줘야 할 것."""
        from pathlib import Path
        paths = [Path(p).resolve() for p in paths]
        listing = "\n".join(f"{i}: {p}  (보여 줄 것: {w})" for i, (p, w) in enumerate(zip(paths, wants)))
        user = ("이슈 숏폼 영상에 '자료 사진'으로 쓸 후보입니다. 실제 사건 사진이 아니라 이해를 돕는 사진입니다.\n"
                "Read 도구로 각 이미지를 열어 보고, 아래 조건을 모두 만족하는 번호만 ok에 넣으세요.\n"
                "(a) '보여 줄 것'이 사진의 주인공으로 한눈에 보인다\n"
                "(b) 일반인의 얼굴이 알아볼 수 있을 만큼 크게 나오지 않는다 (뒷모습·작은 군중은 괜찮음)\n"
                "(c) 선정적·폭력적·사고 장면이 아니고, 흐리거나 깨지지 않았다\n"
                "(d) 특정 사건 현장으로 오해될 장면(사고·시위·범죄 현장)이나, 이슈와 다른 특정 회사·상표가 크게 보이지 않는다\n"
                "(e) 워터마크·큰 글자가 화면을 가리지 않는다\n"
                "(f) 한국 이슈에 쓰이므로, 다른 나라임이 드러나는 장면(외국 국기·외국어 간판·외국 건물 양식이 주인공)은 제외. "
                "사물 클로즈업(서류·의사봉·표·돈·음식)처럼 나라가 드러나지 않는 사진은 괜찮다\n"
                "(g) 보여 줄 것과 관계없는 장면(예: '법원'인데 일반 주택)은 제외\n\n" + listing)
        schema = {"type": "object", "properties": {"ok": {"type": "array", "items": {"type": "integer"}},
                                                   "notes": {"type": "string"}},
                  "required": ["ok", "notes"], "additionalProperties": False}
        out = self._call("당신은 영상 편집 담당자입니다. 자료 사진 적합성만 판단합니다.", user, schema, "low",
                         tools=["Read"], add_dir=str(paths[0].parent) if paths else None)
        return {int(i) for i in out.get("ok", []) if 0 <= int(i) < len(paths)}

    def plan_visuals(self, story: dict, has_people: bool) -> dict:
        """숏츠 장면마다 화면에 무엇을 보여 줄지 정한다 (새 사실 없이 대본에 있는 내용만)."""
        user = ("숏츠 대본과 글(JSON):\n" + json.dumps(story, ensure_ascii=False, indent=1)
                + f"\n\n인물 사진 사용 가능: {'예' if has_people else '아니오 (person 쓰지 말 것)'}")
        return self._call(VISUAL_SYSTEM, user, VISUAL_SCHEMA, "low")


VISUAL_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "minItems": 5, "maxItems": 5, "items": {
        "type": "object",
        "properties": {
            "scene": {"type": "string", "enum": SCENES},
            "kind": {"type": "string", "enum": ["person", "photo", "emoji", "number", "list"]},
            "query_en": {"type": "string"}, "emoji": {"type": "string"},
            "big": {"type": "string"}, "sub": {"type": "string"},
            "items": {"type": "array", "maxItems": 3, "items": {"type": "string"}}},
        "required": ["scene", "kind", "query_en", "emoji", "big", "sub", "items"], "additionalProperties": False}}},
    "required": ["scenes"], "additionalProperties": False,
}

VISUAL_SYSTEM = """당신은 숏폼 영상의 미술 감독입니다. 장면 5개(hook, what, twist, reaction, outro) 각각에 화면 가운데에
보여 줄 그림을 정합니다. 목표: 글자만 나오는 장면 없이, 시청자가 내용을 한눈에 이해하게.
kind:
- person: 이 이슈 당사자(공인)의 사진. 인물 사진 사용 가능이 '예'일 때만, 최대 2장면.
- photo: 이해를 돕는 자료 사진. query_en에 사진 검색어(영어 2~4단어, 구체적인 사물·장소, 예: "roller coaster amusement park",
  "airplane window seat", "courtroom gavel", "exam answer sheet pencil"). 특정 인물·특정 회사 이름을 넣지 않는다.
- emoji: 3D 이모지 하나를 크게 움직여 보여 줌. 사물·감정을 상징 (예: 🎢 ✈️ ⚖️ 💸 📝 🎂 😭).
- number: 대본에 나온 숫자 하나를 크게 (big: "25%", "2만원", "98명", "8.7억" / sub: 그 숫자가 무엇인지 12자 이내).
- list: 대본의 핵심 2~3개를 체크리스트로 (items 각 12자 이내).
규칙:
- big·sub·items는 대본·글에 있는 사실만 쓴다. 새 숫자·새 사실을 만들지 않는다.
- 장면마다 다른 kind를 섞는다. photo는 2~3장면, number·list는 대본에 숫자·항목이 있을 때만.
- emoji 칸은 모든 장면에 채운다 (사진을 못 구했을 때 대신 보여 줌). 쓰지 않는 칸은 빈 문자열·빈 배열.
- 범죄·사고·법원 이슈에서 자극적인 자료 사진(피·흉기·사고 현장)은 고르지 않는다.
- 건물·거리 풍경은 외국 사진이 나오기 쉬우므로, 나라가 드러나지 않는 사물 클로즈업을 우선한다
  (법원 → "judge gavel", 돈 → "korean won banknotes", 시험 → "exam answer sheet pencil", 항공 → "airplane wing sky").
  한국 장소가 꼭 필요하면 "Seoul"·"Korea"를 넣는다 (예: "Seoul subway platform").
"""


class FakeWriter:
    """테스트용: Claude를 부르지 않고 규칙을 지키는 고정 원고를 돌려준다."""

    def __init__(self, approve: bool = True):
        self.approve = approve
        self.usage = {"calls": 0}

    def revise(self, story, suggestions):
        return dict(story, title=story["title"].replace("?", "?!"))

    def pick(self, candidates, recent_titles, n, guidance=""):
        return {"picks": [{"id": c["id"], "category": "연예", "angle": "왜 화제인가", "why": "테스트"}
                          for c in candidates[:n]], "skipped": []}

    def write(self, candidate, angle, category, feedback=None, guidance=""):
        body = ("배우 A씨가 촬영 중 다쳤다는 보도가 나왔습니다. 소속사는 회복 중이라고 밝혔습니다. "
                "그런데 촬영은 계속됐습니다. 왜일까요?\n\n## 무슨 일이 있었나\n" + "보도에 따르면 촬영 도중 사고가 있었습니다. " * 12
                + "\n\n## 당사자 입장\n" + "소속사는 큰 문제는 없다고 설명했습니다. " * 10
                + "\n\n## 앞으로\n" + "남은 촬영 일정이 관심을 모읍니다. " * 8)
        scenes = {k: {"headline": f"{k} 장면", "narration": "보도에 따르면 촬영 중 사고가 있었고, 소속사는 회복 중이라고 밝혔습니다."}
                  for k in SCENES}
        return {"title": "촬영 중 다쳤는데 왜 멈추지 않았을까?", "description": "배우 A씨 촬영 중 부상 보도와 소속사 입장을 정리했습니다. 무슨 일이 있었는지, 왜 촬영이 이어졌는지 한입에 봅니다.",
                "tags": ["연예", "촬영"], "body_md": body,
                "sources": [{"outlet": "가신문", "url": "https://example.com/a", "title": "기사 1"},
                            {"outlet": "나일보", "url": "https://example.org/b", "title": "기사 2"}],
                "claims": [{"claim": "촬영 중 부상", "status": "확인된 사실", "source_outlet": "가신문"}],
                "shorts": {"title": "다쳤는데 촬영은 계속? 무슨 일", "scenes": scenes, "hashtags": ["연예", "이슈"],
                           "band": {"text": "촬영 중 부상, 왜 계속?", "hot": "부상"}, "people": []}}

    def review(self, story):
        return {"approved": self.approve, "issues": [] if self.approve else ["테스트 반려"], "suggestions": [],
                "risk": "낮음"}

    def plan_visuals(self, story, has_people):
        kinds = ["emoji", "number", "photo", "list", "emoji"]
        return {"scenes": [{"scene": k, "kind": kinds[i], "query_en": "film camera" if kinds[i] == "photo" else "",
                            "emoji": "🎬", "big": "3일" if kinds[i] == "number" else "", "sub": "촬영 중단",
                            "items": ["부상", "촬영 계속"] if kinds[i] == "list" else []}
                           for i, k in enumerate(SCENES)]}

    def vet_topic_photos(self, paths, wants):
        return set(range(len(paths)))
