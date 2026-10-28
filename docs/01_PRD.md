# [PM봇] 프로젝트 요구사항 정의서 (PRD) — 독서 후 글쓰기 북리뷰 채널

- **문서 버전**: v1.0
- **작성 에이전트**: 🤖 PM봇 (Project Manager & Compliance Auditor)
- **대상 데이터베이스**: Notion `[독서 후 글쓰기]` (DB ID: `c24fd2e2-f56e-4969-aa1d-fc2039fbe008`)
- **최종 산출물**: GitHub Pages 호스팅 정적 웹 애플리케이션 (`index.html`, `data/notion_books.json`, `sync_readonly.py`, `.github/workflows/sync_notion_manual.yml`)

---

## 1. 프로젝트 개요 및 목적
기존의 양방향 Notion 연동 대시보드를 **'내 북리뷰와 독서 에세이를 안전하게 읽고 공유할 수 있는 공개용 북리뷰 채널'**로 고도화하고, **GitHub Pages**를 통해 비용 0원으로 배포·운영한다.

---

## 2. 🚨 최우선 절대 원칙 (Non-Negotiable Constraints)

### 원칙 1. 비용 0원 아키텍처 (Zero-Cost Architecture)
1. **무료 정적 호스팅**: GitHub Pages를 통해 정적 웹사이트(`index.html`, `data/notion_books.json`)를 100% 무료로 배포한다.
2. **무료 수동 데이터 갱신**: 유료 클라우드 서버(AWS, Heroku, 유료 DB 등)나 유료 스케줄러를 일절 사용하지 않고, **로컬 수동 브리지/스크립트 실행** 또는 **GitHub Actions 수동 트리거(`workflow_dispatch`)**만을 사용한다.
3. **API Secret 완벽 분리 (보안 필수)**:
   - `NOTION_TOKEN` 등 민감한 인증 키는 `index.html`이나 프론트엔드 JS 코드, 공개 Git 저장소에 **단 1글자도 하드코딩하거나 노출하지 않는다**.
   - 토큰은 로컬 환경변수(`.env`, `.gitignore`로 커밋 차단) 또는 GitHub Repository Secrets(`secrets.NOTION_TOKEN`)에만 안전하게 보관한다.

### 원칙 2. 단방향(Notion ➔ Web) 수동 동기화 & Notion 원본 DB 절대 보호
1. **실시간/자동 동기화 완전 제거**:
   - 기존 `index.html` 하단의 15초 주기 `setInterval` 자동 동기화 제거.
   - 브라우저 탭 `window.addEventListener('focus', ...)` 자동 동기화 제거.
   - 페이지 최초 로드(`DOMContentLoaded`) 시에도 Notion API를 직접 깨우지 않고, 정적 데이터 파일(`data/notion_books.json`), 브라우저 로컬 캐시(`localStorage`), 내장 스냅샷(`INITIAL_NOTION_BOOKS` 109권) 순서로 안전하게 렌더링한다.
2. **수동 버튼 클릭 시에만 동기화**:
   - 오직 사용자가 웹 화면의 **`[🔄 Notion 동기화]`** 버튼을 **직접 클릭(`syncFromNotion(true)`)**했을 때만 최신 데이터를 가져와 화면과 캐시를 갱신한다.
3. **Notion DB 쓰기(Write) 원천 차단 (Strict Read-Only)**:
   - Notion 원본 DB는 사용자가 Notion 앱에서 직접 작업하는 경우 외에는 웹에서 **절대 생성(`POST`), 수정(`PATCH`), 삭제(`DELETE`)될 수 없다**.
   - 프론트엔드에서 `saveBook()`, `deleteBook()`, `saveEssayBody()`, `markCompletedToday()` 및 등록/수정/삭제 모달(`#bookModal`, `#notionModal`)을 **전부 삭제**한다.
   - 동기화 스크립트(`sync_readonly.py`) 역시 Notion API 호출 시 **조회(Database Query `POST /v1/databases/{id}/query`, Database Retrieve `GET /v1/databases/{id}`, Block Children Retrieve `GET /v1/blocks/{id}/children`)**만 허용하며, 데이터를 변경하는 엔드포인트는 아예 구현하지 않는다.

---

## 3. 데이터 자산 및 스키마 보존 요구사항
- **Notion Database ID**: `c24fd2e2-f56e-4969-aa1d-fc2039fbe008`
- **핵심 7대 스키마 + 메타데이터**:
  1. `title` (책이름)
  2. `author` (저자)
  3. `completedDate` (완료일, `YYYY-MM-DD`)
  4. `year` (연도: `2023` ~ `2026`)
  5. `summary` (줄거리 요약)
  6. `bookType` (책유형: 소설, 교양, 자기계발, 과학교양, 인문교양 등)
  7. `externalUrl` (외부 서평 링크: 브런치 `@crimson0425`, 네이버 블로그, 구글독스, 뉴스 칼럼 등)
  - 부가 속성: `id`, `emoji`, `notionUrl`, `month`, `status`, `updatedAt`, `bodyContent`(독서 후 글쓰기 에세이 본문)
- **초기 스냅샷 보존**: 2023~2026년 109권 전체 데이터를 `INITIAL_NOTION_BOOKS` 및 `data/notion_books.json`에 보존하여 서버 없이 정적 호스팅만으로도 즉시 모든 차트와 목록이 동작하도록 보장한다.

---

## 4. 멀티에이전트 검수 체크리스트 (PM Gate)
| 검수 단계 | 담당 에이전트 | 핵심 통과 기준 (Gate Criteria) |
| :--- | :--- | :--- |
| **1단계** | **PM봇** | 비용 0원, 단방향 Read-Only, 수동 동기화 원칙 수립 및 PRD 문서화 |
| **2단계** | **기획봇** | 모든 수정/삭제 UI 제거, 리더(Reader) 모달 설계, 외부 서평 필터/배지 특화 UI 명세 |
| **3단계** | **프론트 개발봇** | `index.html` 무결성 구현, `sync_readonly.py` 및 GitHub Actions 수동 워크플로우 구현 |
| **4단계** | **QA봇** | 자동 동기화 0건, Notion Write 코드 0건, 토큰 노출 0건, 기능 전수 검사 통과 |
