#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[Strict Read-Only] Notion ➔ Web 단방향 수동 동기화 스크립트 (sync_readonly.py)
- 대상 데이터베이스: Notion [독서 후 글쓰기] (ID: c24fd2e2-f56e-4969-aa1d-fc2039fbe008)
- 보안 원칙: NOTION_TOKEN은 오직 환경변수(os.environ)에서만 읽으며 코드나 JSON에 절대 저장하지 않습니다.
- 원본 보호 원칙 (Strict Read-Only):
  Notion DB를 수정/생성/삭제(POST page, PATCH page/block, DELETE block)하는 코드는 단 1줄도 존재하지 않습니다.
  오직 데이터베이스 조회(query) 및 페이지 본문 블록 읽기(children GET)만 수행합니다.

사용법:
1) 단발성 수동 동기화 (로컬 또는 GitHub Actions workflow_dispatch):
   python sync_readonly.py --once

2) 로컬 웹 화면의 [🔄 Notion 동기화] 버튼과 연동되는 읽기 전용 로컬 서버 실행:
   python sync_readonly.py --serve --port 8000
"""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path

DEFAULT_DB_ID = "c24fd2e2-f56e-4969-aa1d-fc2039fbe008"
NOTION_VERSION = "2022-06-28"
ROOT_DIR = Path(__file__).resolve().parent
DATA_FILE = ROOT_DIR / "data" / "notion_books.json"


def load_env_file():
    """로컬 .env 파일이 있으면 환경변수로 로드 (.gitignore에 의해 Git에는 절대 커밋되지 않음)"""
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def notion_get(url: str, token: str) -> dict:
    """Strict Read-Only: Notion API GET 요청만 수행"""
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def notion_query_database(database_id: str, token: str) -> list:
    """
    Strict Read-Only: Notion API의 데이터베이스 목록 조회(/v1/databases/{id}/query)만 호출합니다.
    (Notion API 명세상 DB 필터/정렬 조회는 query 엔드포인트를 사용하며 원본 데이터를 일절 변경하지 않습니다.)
    """
    results = []
    has_more = True
    next_cursor = None

    while has_more:
        payload = {"page_size": 100}
        if next_cursor:
            payload["start_cursor"] = next_cursor

        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            next_cursor = data.get("next_cursor")

    return results


def extract_plain_text(rich_text_list: list) -> str:
    if not rich_text_list:
        return ""
    return "".join(item.get("plain_text", "") for item in rich_text_list).strip()


def fetch_page_body_readonly(page_id: str, token: str, depth: int = 0) -> str:
    """Strict Read-Only: 페이지 내부의 독서록 본문 블록을 페이지네이션 및 하위 블록까지 읽기 전용(GET)으로 가져옵니다."""
    if depth > 2:
        return ""
    blocks = []
    has_more = True
    next_cursor = None
    while has_more:
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
        if next_cursor:
            url += f"&start_cursor={next_cursor}"
        try:
            data = notion_get(url, token)
        except Exception:
            break
        blocks.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    paragraphs = []
    for block in blocks:
        btype = block.get("type", "")
        bval = block.get(btype, {})
        if isinstance(bval, dict) and "rich_text" in bval:
            text = extract_plain_text(bval.get("rich_text", []))
            if btype.startswith("heading_"):
                if text:
                    paragraphs.append(f"### {text}")
            elif btype in ("quote", "callout"):
                if text:
                    paragraphs.append(f"❝ {text}")
            elif btype == "bulleted_list_item":
                if text:
                    paragraphs.append(f"• {text}")
            elif btype == "numbered_list_item":
                if text:
                    paragraphs.append(f"- {text}")
            elif btype == "to_do":
                checked = "☑️" if bval.get("checked") else "⬜"
                if text:
                    paragraphs.append(f"{checked} {text}")
            else:
                if text:
                    paragraphs.append(text)
        elif btype == "divider":
            paragraphs.append("---")

        # 토글/콜아웃 등 하위 블록(has_children)에 작성된 본문도 누락 없이 추출
        if block.get("has_children") and block.get("id") and btype != "child_page":
            child_text = fetch_page_body_readonly(block["id"], token, depth + 1)
            if child_text:
                paragraphs.append(child_text)

    return "\n\n".join(p for p in paragraphs if p).strip()


def parse_notion_page(page: dict, existing_map: dict, token: str, fetch_body: bool = True) -> dict:
    page_id = page.get("id", "")
    props = page.get("properties", {})

    # 아이콘(emoji)
    icon_obj = page.get("icon") or {}
    emoji = icon_obj.get("emoji") if icon_obj.get("type") == "emoji" else "📚"

    def get_prop_text(names):
        # 공백 유무에 관계없이 속성명을 유연하게 매칭
        norm_props = {k.replace(" ", ""): v for k, v in props.items()}
        for name in names:
            p = props.get(name) or norm_props.get(name.replace(" ", ""))
            if p:
                ptype = p.get("type")
                if ptype == "title":
                    return extract_plain_text(p.get("title", []))
                if ptype == "rich_text":
                    return extract_plain_text(p.get("rich_text", []))
                if ptype == "select" and p.get("select"):
                    return p["select"].get("name", "")
                if ptype == "multi_select":
                    return ", ".join(x.get("name", "") for x in p.get("multi_select", []))
                if ptype == "url":
                    return p.get("url") or ""
                if ptype == "date" and p.get("date"):
                    return p["date"].get("start") or ""
                if ptype == "number" and p.get("number") is not None:
                    return str(p.get("number"))
        return ""

    title = get_prop_text(["책 이름", "책이름", "이름", "제목", "Title", "Name"])
    if not title:
        # fallback: type == 'title'인 속성을 자동 탐색
        for p in props.values():
            if p.get("type") == "title":
                title = extract_plain_text(p.get("title", []))
                if title:
                    break
    title = title or "제목 없음"
    author = get_prop_text(["저자", "작가", "Author"]) or "미상"
    completed_date = get_prop_text(["완료일", "완독일", "날짜", "Date"])
    year = get_prop_text(["연도", "년도", "Year"])
    if not year and completed_date and len(completed_date) >= 4:
        year = completed_date[:4]
    year = str(year or "2026").replace("년", "").strip()

    month = 1
    if completed_date and len(completed_date) >= 7:
        try:
            month = int(completed_date[5:7])
        except ValueError:
            month = 1

    summary = get_prop_text(["줄거리", "요약", "한줄평", "Summary"])
    book_type = get_prop_text(["책 유형", "책유형", "분류", "장르", "카테고리", "Category", "Type"]) or "교양"
    external_url = get_prop_text(["URL", "링크", "브런치", "서평링크", "Link"])

    last_edited_str = page.get("last_edited_time", "")
    updated_at = 0
    if last_edited_str:
        try:
            dt = datetime.datetime.fromisoformat(last_edited_str.replace("Z", "+00:00"))
            updated_at = int(dt.timestamp() * 1000)
        except ValueError:
            updated_at = 0

    notion_url = page.get("url") or f"https://app.notion.com/p/{page_id.replace('-', '')}"

    # 변경되지 않은 페이지이고 이미 본문(bodyContent)이 채워져 있는 경우에만 캐시 재사용
    prev = existing_map.get(page_id)
    body_content = ""
    if prev and prev.get("updatedAt") == updated_at and prev.get("bodyContent"):
        body_content = prev.get("bodyContent", "")
    elif fetch_body and token:
        body_content = fetch_page_body_readonly(page_id, token)

    return {
        "id": page_id,
        "notionUrl": notion_url,
        "emoji": emoji or "📚",
        "title": title,
        "author": author,
        "bookType": book_type,
        "summary": summary,
        "completedDate": completed_date,
        "year": year,
        "month": month,
        "status": "completed" if completed_date else "reading",
        "externalUrl": external_url,
        "updatedAt": updated_at,
        "bodyContent": body_content,
    }


def sync_from_notion_readonly() -> dict:
    """Notion DB에서 최신 데이터를 읽기 전용으로 가져와 data/notion_books.json을 갱신합니다."""
    load_env_file()
    token = os.environ.get("NOTION_TOKEN", "").strip()
    database_id = os.environ.get("NOTION_DATABASE_ID", DEFAULT_DB_ID).strip() or DEFAULT_DB_ID

    existing_data = {}
    existing_map = {}
    if DATA_FILE.exists():
        try:
            existing_data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            for b in existing_data.get("books", []):
                if b.get("id"):
                    existing_map[b["id"]] = b
        except Exception:
            pass

    if not token:
        raise RuntimeError(
            "NOTION_TOKEN 환경변수가 설정되지 않았습니다. (.env 파일 또는 GitHub Actions Secrets에 NOTION_TOKEN을 설정해주세요.)"
        )

    db_meta = notion_get(f"https://api.notion.com/v1/databases/{database_id}", token)
    db_title = extract_plain_text(db_meta.get("title", [])) or "독서 후 글쓰기"

    pages = notion_query_database(database_id, token)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as executor:
        raw_books = list(executor.map(lambda p: parse_notion_page(p, existing_map, token, fetch_body=True), pages))
    # 빈 행(제목 없음 & 저자 미상 & 완료일 없음) 제외
    books = [
        b for b in raw_books
        if not (b.get("title") in ("", "제목 없음") and b.get("author") in ("", "미상") and not b.get("completedDate"))
    ]
    books.sort(key=lambda x: (x.get("completedDate") or "", x.get("updatedAt") or 0), reverse=True)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        "connected": True,
        "readOnly": True,
        "databaseId": database_id,
        "databaseTitle": db_title,
        "syncedAt": now_str,
        "books": books,
    }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # index.html 내부의 INITIAL_NOTION_BOOKS 스냅샷도 함께 최신화하여 정적 배포 시에도 항상 일치하도록 유지
    index_path = ROOT_DIR / "index.html"
    if index_path.exists():
        try:
            import re
            html_text = index_path.read_text(encoding="utf-8")
            books_json_inline = json.dumps(books, ensure_ascii=False)
            html_text = re.sub(
                r"const INITIAL_NOTION_BOOKS = \[.*?\];",
                lambda _: f"const INITIAL_NOTION_BOOKS = {books_json_inline};",
                html_text,
                count=1,
            )
            index_path.write_text(html_text, encoding="utf-8")
        except Exception:
            pass

    return payload


class StrictReadOnlyHandler(SimpleHTTPRequestHandler):
    """
    오직 GET 요청만 허용하는 Strict Read-Only 로컬 서버 핸들러.
    POST / PATCH / DELETE / PUT 요청은 405 Method Not Allowed로 원천 차단합니다.
    """

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        self._block_write_method()

    def do_PATCH(self):
        self._block_write_method()

    def do_DELETE(self):
        self._block_write_method()

    def do_PUT(self):
        self._block_write_method()

    def _block_write_method(self):
        self.send_response(405)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        err = {
            "ok": False,
            "error": "Strict Read-Only Mode: Notion DB 수정/추가/삭제(Write)는 원천 차단되어 있습니다.",
        }
        self.wfile.write(json.dumps(err, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path.startswith("/api/sync"):
            try:
                payload = sync_from_notion_readonly()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                # 토큰 미설정 시 기존 정적 JSON 스냅샷 반환 안내
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                fallback = {"connected": False, "readOnly": True, "error": str(e)}
                if DATA_FILE.exists():
                    try:
                        saved = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                        fallback.update(saved)
                        fallback["connected"] = False
                        fallback["notice"] = str(e)
                    except Exception:
                        pass
                self.wfile.write(json.dumps(fallback, ensure_ascii=False).encode("utf-8"))
            return

        if self.path.startswith("/api/books/") and "/content" in self.path:
            book_id = self.path.split("/api/books/", 1)[1].split("/content")[0].strip("/")
            load_env_file()
            token = os.environ.get("NOTION_TOKEN", "").strip()
            body_text = ""
            if DATA_FILE.exists():
                try:
                    saved = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                    for b in saved.get("books", []):
                        if b.get("id") == book_id and b.get("bodyContent"):
                            body_text = b["bodyContent"]
                            break
                except Exception:
                    pass
            if not body_text and token:
                body_text = fetch_page_body_readonly(book_id, token)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                json.dumps({"ok": True, "readOnly": True, "bodyContent": body_text}, ensure_ascii=False).encode("utf-8")
            )
            return

        return super().do_GET()


def main():
    parser = argparse.ArgumentParser(description="Strict Read-Only Notion ➔ Web Sync Tool")
    parser.add_argument("--once", action="store_true", help="수동 1회 동기화 실행 후 data/notion_books.json 갱신")
    parser.add_argument("--serve", action="store_true", help="로컬 읽기 전용 서버 실행 (기본 포트: 8000)")
    parser.add_argument("--port", type=int, default=8000, help="로컬 서버 포트 번호")
    args = parser.parse_args()

    if args.serve:
        os.chdir(ROOT_DIR)
        server = HTTPServer(("127.0.0.1", args.port), StrictReadOnlyHandler)
        print(f"🔒 [Strict Read-Only Server] http://localhost:{args.port} 실행 중 (Notion 쓰기 원천 차단됨)")
        server.serve_forever()
    else:
        payload = sync_from_notion_readonly()
        print(
            f"✅ [Read-Only Sync 완료] '{payload['databaseTitle']}' 총 {len(payload['books'])}권 동기화됨 ({payload['syncedAt']})"
        )


if __name__ == "__main__":
    main()
