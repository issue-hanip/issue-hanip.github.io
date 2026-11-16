# 이슈 한입 — 마스터 문서

> 이 문서는 프로젝트의 **변하지 않는 기준**(목표·원칙·구조·계정·운영 방법)을 담는다.
> 진행 상황과 할 일은 [STATUS.md](STATUS.md)에 적는다. 구조나 계정·원칙이 바뀌면 이 문서를 같은 커밋에서 고친다.
> 형제 프로젝트: `..\claude_ara` (실거래가 리포트). 코드는 그쪽에서 복사해 고쳐 썼고, 서로의 폴더·저장소·예약 작업은 건드리지 않는다.

## 1. 목표
- 오늘 화제가 된 뉴스·연예·사회 이슈를 골라 **궁금증을 자극하는 형식**으로 한입에 정리한다.
- 블로그·YouTube 숏츠(필요하면 긴 영상)·Instagram 릴스·TikTok에 **사람 개입 없이** 매일 발행하고, 성과를 보고 개선한다.
- 수익: 블로그 광고(애드센스), 채널 성장.

## 2. 운영 원칙
| 원칙 | 내용 |
|---|---|
| 각색의 선 (2026-11-16 사용자 결정) | **형식만 자극적으로, 내용은 보도된 사실만.** 궁금증 유발 제목·반전 구성·짧은 구어체는 허용. 사실·숫자·발언은 출처 기사와 같아야 하고, 의혹·루머는 "~라는 보도/주장"으로만, 수사·재판 중은 "혐의"로 |
| 다루지 않는 것 | 사생활 추측(연애·임신·이혼·건강·성적 지향), 미성년자·피해자·일반인 신상, 자살·자해 방법, 혐오·모욕·외모 평가, 정치적 편들기 |
| 저작권·초상권 | 기사 문장을 옮기지 않고 요약(직접 인용은 한 문장 이내), 기사 본문은 저장하지 않음. 인물 사진·방송 화면은 쓰지 않고 글자·도형 모션만 |
| 정정 | 모든 글에 출처 기사 목록과 정정 요청 안내. 요청이 오면 `python -m pipeline.run withdraw <slug>` 로 즉시 내림 |
| 무료 | 유료 결제 수단은 쓰지 않는다. Claude는 구독 토큰(`claude -p`)으로 호출 |
| 모델 | Opus (`claude-opus-5-5`). 검수는 effort high |
| 개인정보 | 개인 연락처·본명·구글 이메일이 들어간 아이디는 공개되면 안 된다. 계정 아이디는 `private/ACCOUNTS.md`(git 제외)에만 |
| 품질 | 완전 자동화. 규칙 검사 + Claude 독립 검수(원문 대조) + 정기 Claude 직접 검토 |
| 언어·문서 | 사용자 보고는 한국어. 변경마다 MASTER/STATUS/현황 아티팩트 갱신 |
| 계정 (사용자 결정) | 기존 계정을 쓰되 위험이 번지지 않게 나눈다 (5절) |

## 3. 전체 구조
```
매일 02:10 KST  GitHub Actions (daily-pipeline) — 실거래가 채널 01:00 실행과 겹치지 않게
  run       : 테스트 → 뉴스 RSS 수집(Google 뉴스 종합·연예) → Claude가 이슈 선정(위험 이슈 제외)
              → 이슈마다 Claude가 WebSearch/WebFetch로 원문 2~4개 확인 후 글+숏츠 대본 작성
              → 규칙 검사(qa.py) → Claude 독립 검수(원문 대조, 명예훼손·사생활 위험) → 반려 시 1회 재작성
              → 숏츠 렌더(cards.py) → YouTube 업로드 → 사이트 빌드 → DB 커밋
  deploy    : GitHub Pages 배포 (public/, media/*.mp4)
  instagram : 배포된 mp4 URL로 릴스 게시
  report    : 오류가 있으면 실패 표시 → GitHub 이메일 알림
```
- 하루 발행 상한: 글 3편, 숏츠 2편 (계정 위험을 줄이려고 적게 시작, 성과를 보고 조정)

## 4. 코드 지도
| 파일 | 역할 |
|---|---|
| `config.toml` | 사이트·뉴스 피드·발행 상한·금지 표현·숏츠·YouTube·Instagram 설정 |
| `pipeline/news.py` | RSS 수집·파싱, 화제성(같은 이슈 언론사 수)·노출 순서, 후보 저장 |
| `pipeline/write.py` | Claude 호출: 이슈 선정(`pick`), 원문 확인·작성(`write`, 웹 도구 허용), 검수(`review`). 각색 규칙(RULES) |
| `pipeline/qa.py` | 규칙 검사: 서로 다른 언론사 출처 2개 이상, 길이, URL·긴 인용 금지, 금지 표현, 유보어 없는 의혹 단정 |
| `pipeline/stories.py` | 선정 → 작성 → 검사 → 검수 → 저장 흐름 |
| `pipeline/cards.py` | 숏츠 모션 렌더러: 분류 띠, 장면 배지, 큰 상징 기호, 헤드라인, 구절 자막, 줄무늬 배경. 테마 4종(tabloid·neon·paper·mint) |
| `pipeline/shorts.py` | 대본 → 음성(edge-tts) → 장면 렌더 → mp4, 설명란에 출처·면책 |
| `pipeline/site.py` + `templates/` | 정적 사이트: 글마다 출처 목록·정정 요청 안내, 분류 페이지, sitemap, RSS |
| `pipeline/youtube.py`, `instagram.py`, `tts.py`, `render.py` | 실거래가 프로젝트에서 복사 |
| `pipeline/run.py` | 진입점: `all`, `instagram`, `withdraw <slug>`, `--fake-writer` 시험 |
| `tests/test_pipeline.py` | 단위·통합 테스트 (실패하면 발행 안 함) |

## 5. 계정·서비스 (기존 계정 활용, 위험 분리)
| 서비스 | 방식 | 위험을 줄이는 점 |
|---|---|---|
| GitHub | 같은 개인 계정 아래 **새 조직** + 공개 저장소 | 블로그 주소·저장소에 실거래가 조직 이름이 드러나지 않음 |
| YouTube | 같은 Google 계정에 **브랜드 채널 추가**, 채널별 업로드 토큰 | 채널 단위 경고는 그 채널에만. 단 심각한 위반 시 연결 채널까지 해지될 수 있어 내용 규칙을 엄격히 |
| Instagram | 같은 로그인(계정 센터)에 **새 계정 추가**, **새 비즈니스 포트폴리오**의 시스템 사용자 토큰 | 포트폴리오 제한이 실거래가 쪽으로 번지지 않음 |
| TikTok | 같은 기기·로그인에서 **계정 추가** (계정은 채널마다 하나) | 계정별 제재 |
| Claude | 같은 구독, 새 저장소 Secret `CLAUDE_CODE_OAUTH_TOKEN` | — |

## 6. 운영 방법
- 수동 실행: 새 저장소 Actions → daily-pipeline → Run workflow
- 긴급 중단: 저장소 변수 `PIPELINE_ENABLED=false` 또는 `config.toml`의 `[safety] enabled=false`
- 글 내리기: `python -m pipeline.run withdraw <slug>` → 커밋·푸시 → 다음 배포에서 빠짐 (YouTube·Instagram 게시물은 각 플랫폼에서 비공개 처리)
- 로컬 시험: `python -m pipeline.run all --fake-writer --tts silent` (Claude·뉴스 없이, data/demo.db)
- 로컬 테스트: `python -m unittest discover -s tests`

## 7. 하지 않기로 한 것
- 인물 사진·방송 캡처·기사 본문 저장 (초상권·저작권)
- 루머만 있는 이슈, 비극을 흥밋거리로 만드는 이슈
- 유료 도구·API 종량 과금
