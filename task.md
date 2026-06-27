# Relay 초기 개발 Task

> 주간보고 정리 agent **Relay**의 초기 구현 작업 목록.
> 설계 근거는 `CLAUDE.md`의 "합의된 설계 결정 / 작성 워크플로우 / 검증 단계"를 따른다.
> 순서는 MVP-first — 위에서부터 의존 관계 순. 상태: `[ ]` 예정 / `[~]` 진행중 / `[x]` 완료.

---

## 마일스톤 1 — 코어 (LLM 없이 동작하는 골격)

이 단계가 끝나면 "LLM 없이도 이월·승격·렌더가 되는 주간보고 도구"가 된다.

### [x] T1. 프로젝트 스캐폴딩 정리
- **내용**: `src/relay/` 아래 레이어 디렉터리 골격을 만든다 — `models/`(Pydantic), `db/`(SQLite 접근), `services/`(순수 함수: collect/narrate/verify/render), `template/`(YAML 로더), `cli.py`(얇게 유지). 루트의 PyCharm 샘플 `main.py`는 제거하거나 진입점으로 대체.
- **완료 기준**: `relay version` 동작 유지, import 경로 정리, `uv run ruff check .` 통과.

### [x] T2. 주차(week) 변환 유틸
- **내용**: 날짜 → `YYYY-Www` 변환 유틸을 **한 곳**에 둔다(설계 #3). 월~금 업무주 기준 — 주말 작업은 직전 업무주로 귀속. 타임존은 KST 고정. 이전/다음 주 계산, 주차 시작·종료일(`{start} ~ {end}`) 헬퍼 포함.
- **완료 기준**: 경계 케이스(주말, 연말 ISO 주번호) 단위 테스트. 모든 이월·집계가 이 유틸만 쓰도록 강제.

### [x] T3. 템플릿 로더 + 검증
- **내용**: `templates/weekly_template.yaml`을 로드·검증(설계 #9). `label`/`role`/`key` 분리, 로드 시점에 깨진 YAML·중복 `key`·필수 role(`next_week_plan`) 누락을 명확한 에러로 차단(조용한 오동작 금지). 기본 템플릿을 사용자 위치(`~/.config/relay/`)로 첫 실행 시 복사.
- **완료 기준**: 정상/깨진 템플릿 각각에 대한 테스트. metric 0개 카테고리 허용(설계 #6).

### [x] T4. SQLite 스키마 + Task 모델
- **내용**: 합의된 Task 모델을 SQLite 테이블 + Pydantic 모델로 구현(설계 #2). 핵심 컬럼:
  - `id`(자동), `week`, `system`, `category_key`, `title`, `detail`
  - `status` ∈ {완료/진행중/미완료/보류/취소} — **작업 상태만**
  - `carried_from`, `carry_count` — 이월 출처(상태와 분리)
  - `thread_id` — 같은 작업을 주차 가로질러 잇는 키(자동 발급/승계, 사용자 미노출)
  - `related_ids`, `metrics`(JSON, 선택), 타임스탬프
  - 보고서(주차) 상태 `draft → in_progress → finalized`
  - 진행 메모 `note`는 별도 테이블(`task_id`, 날짜, 본문)로 누적.
- **완료 기준**: 마이그레이션/초기화 코드, 모델 ↔ 행 매핑 테스트. `status`에 이월 값이 절대 안 들어가는지 검증.

### [~] T5. `relay task add` / `list` / `update` / `note`
- **내용**: 결정적 파싱으로 카테고리·명령을 처리(설계 #7). `add <카테고리> "내용"`은 LLM 없이 그대로 저장. `list`는 **현재 주차 안에서 1,2,3… 작은 번호**로 표시(설계 #10) — 후속 명령은 이 번호로 가리킨다. `update <번호> <status>`, `note <번호> "..."`(진행 메모 누적).
- **완료 기준**: 활성 컨텍스트(현재 주/시스템) 기본값 적용, `--week`/`--system` 오버라이드. 번호→내부 id 해석 로직 테스트.
- **진행**: ✅ 최소 슬라이스 `add`+`list` 완료 — `config.py`(경로/기본값), `services/tasks.py`(카테고리 해석·번호 매김), `cli.py` task 서브커맨드, `Store.last_used_system()`. 테스트: `test_services_tasks.py`(9) + `test_cli.py`(6). ⬜ 남음: `update`(상태 변경 {완료/진행중/미완료/보류/취소}), `note`(진행 메모), 번호→id 해석을 두 명령에 연결.

### [ ] T6. `relay task history`
- **내용**: 사용자가 준 작은 번호 → `thread_id` 해석 → 그 작업의 전 주차 이력을 모아 표시(설계 #2). carry_count·주차별 note를 함께 보여 "몇 주째 끌고 있나 / 매주 전진했나"가 드러나게.
- **완료 기준**: 여러 주에 걸친 이월 체인 시나리오 테스트.

### [ ] T7. `relay draft` — 이월 + 승격 (핵심 가치)
- **내용**: 전주 finalized 보고에서 ① `status IN (진행중,미완료,보류)` 복제(이월, `thread_id` 승계·`carry_count`+1) + ② `next_week_plan` 항목을 금주 신규 task로 승격(새 thread)(설계 #4). 완료/취소는 안 끌어옴. 재실행 멱등: 사람이 추가/수정한 task는 건드리지 않고 이월·승격분만 upsert(`carried_from` 마커로 구분).
- **완료 기준**: 엣지 케이스 — 첫 주/주 건너뜀/전주 미확정 시 빈 초안 + 안내(설계 워크플로우). 멱등 재실행 테스트.

### [ ] T8. `relay finalize` + 보고서 렌더 (LLM 없이)
- **내용**: 보고 상태를 `finalized`로 확정. 템플릿 카테고리 순서대로 task를 Markdown 렌더(설계 #10, Markdown 우선). 카테고리(주제별) 그룹핑 + **"한 일 / 할 일" 시점별 뷰**(완료 vs 진행중·보류·미완료·승격) 함께. `carry_count ≥ 임계치`는 ⚠ 리스크로 표기.
- **완료 기준**: `examples/sample_report_2026-W26.md`(golden output)의 비-LLM 부분(표·추세·carry 경고·승격 표기)을 재현.

### [ ] T9. `relay status` — 사전 점검 / 오리엔테이션
- **내용**: 단발 CLI라 세션 기억이 없으므로 현재 주/시스템/보고상태, task 집계(완료/진행중/보류/미완료), 미입력 지표, `carry_count` 임계 도달 작업을 한눈에. finalize 전 사람이 확인하는 용도.
- **완료 기준**: 템플릿이 선언한 "이번 주 기대 지표" 대비 미입력 목록을 코드로 계산.

---

## 마일스톤 2 — LLM 연동 (어댑터 뒤)

### [ ] T10. LLM provider 어댑터
- **내용**: provider 인터페이스를 두고 외부 API(Claude API)와 로컬(Ollama 등)을 교체 가능하게(설계 #8). 호출 지점을 모두 어댑터 뒤로 숨긴다. 최신 Claude 모델(Opus 4.8 / Sonnet 4.6) 기본. `--dry-run`으로 "외부로 나가는 프롬프트"를 호출 없이 출력(데이터 경계 미확정 대비).
- **완료 기준**: 어댑터 인터페이스 + Claude 구현 + 가짜(fake) 구현(테스트용).

### [ ] T11. 신규 업무 요약 (`task add` 본문 정리)
- **내용**: 자연어 본문만 LLM에 넘겨 요약·정리(설계 #7). 카테고리·명령 파싱은 코드가 이미 처리. 결과는 task `detail`에 저장.
- **완료 기준**: 어댑터 통해 호출, 실패 시 원문 보존(graceful degrade).

### [ ] T12. 초안 서술 생성 + 자가검증 루프
- **내용**: `collect() → narrate() → render() → verify()` 순수 함수 분리(검증 단계). 숫자·데이터는 collect 확정값으로 루프 밖, **서술만 재생성**. ① 결정론 검증(코드: 숫자 정합성·카테고리/이월/승격 누락·빈 섹션 표기·Markdown 유효성) 먼저 → 통과분만 ② 판단 검증(LLM self-critique, Pydantic 구조화: 환각/3축 충족/목적 부합/리스크 반영). `MAX_ATTEMPTS=2~3`, 초과 시 "⚠ 검증 미통과" 표시 초안으로 남김(차단 아님). 데이터 문제는 재시도 말고 사람에게 안내.
- **완료 기준**: `for` 루프로 구현(LangGraph 미도입 — 의존성 정책). 검증 항목별 테스트.

---

## 마일스톤 3 — 집계 보고 + 내보내기

### [ ] T13. `relay summary month|year` (task 롤업 중심)
- **내용**: 숫자 합산이 아니라 **task 기반 롤업**(설계 #2 방향성) — 완료 N건/잔존 M건, 가장 오래 끈 작업(max carry_count), 반복 출현 패턴. metric이 입력된 경우만 숫자 집계를 보조로 첨부(숫자는 SQL, 서술은 LLM — 설계 #5).
- **완료 기준**: 월/연 기간 집계 쿼리, 시스템별 그룹핑.

### [ ] T14. `relay export [--system <name>]`
- **내용**: Markdown 파일 내보내기(그룹웨어/위키 붙여넣기용). 한 장에 여러 시스템 그룹핑 또는 특정 시스템만 추출(설계 #10).
- **완료 기준**: 파일 출력 경로 옵션, 멀티 시스템 그룹핑 확인.

### [ ] T15. `relay metric set` (선택 보조)
- **내용**: 주차별 지표 값 수동 입력, 전주 대비 증감·추세 자동 계산(설계 #6). 미입력 허용 — 검증이 finalize를 막지 않음.
- **완료 기준**: 추세(▲▼) 계산 테스트, 미입력 시 표 생략.

---

## 마일스톤 4 — RAG (선택, 단계적 도입)

### [ ] T16. `find_related_tasks(text, filters)` 인터페이스
- **내용**: 인터페이스를 먼저 두고(설계 #1), 초기 구현은 "최근 N주 finalized Task를 LLM 컨텍스트에 직접 투입". `relay task link <번호>`로 연관 확정(비대화형 — 출력만 하고 후속 확정).
- **완료 기준**: 인터페이스 정의 + 단순 구현 + link 명령.

### [ ] T17. sqlite-vec 하이브리드 검색으로 교체
- **내용**: 데이터가 쌓이면 메타필터(`category`/`system`/기간) + 벡터 유사도 top-k 하이브리드로 내부 구현 교체(설계 #1). SQLite가 원본, 벡터는 파생 — finalized Task만 인덱싱, 수정 시 re-index/삭제 시 제거.
- **완료 기준**: `[project.optional-dependencies] rag` extra로 분리, 동기화 로직 테스트.

---

## 교차 관심사 (해당 task 안에서 함께)

- **데이터 안전**: soft-delete(`status=취소`/삭제 플래그) 기본, finalize 시 SQLite 자동 백업.
- **테스트**: `CLAUDE.md`의 **"테스트 정책"** 을 따른다 — 모든 모듈은 대응 `tests/test_<mod>.py` 를 갖고, 신규·수정 시 테스트를 같은 작업에서 추가·갱신하며 `uv run pytest`+`ruff` 통과를 완료 게이트로 삼는다(테스트 없이 `[x]` 금지). 결정적 로직·실패/엣지 케이스·golden output 회귀 포함.
- **CLAUDE.md "명령어" 섹션**: 스캐폴딩 후 빌드/테스트/실행 명령을 갱신.
