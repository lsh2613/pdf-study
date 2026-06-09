# 07. 학습 UI & study_html.py

## index.html 책 정보 섹션

```html
<header class="book-info">
  <h1>{{ book.title }}</h1>
  <p class="meta">
    {{ book.author }}{% if book.publisher %} · {{ book.publisher }}{% endif %}
    {% if book.publication_year %} ({{ book.publication_year }}){% endif %}
  </p>
  {% if book.preface_summary %}
  <details class="preface">
    <summary>책 소개</summary>
    <p>{{ book.preface_summary }}</p>
  </details>
  {% endif %}
</header>

<nav class="chapter-list">
  {% for ch in chapters %}
  <a href="{{ ch.id }}.html" class="chapter-link">
    <span class="chapter-title">{{ ch.title }}</span>
    <span class="progress" data-chapter="{{ ch.id }}"></span>
  </a>
  {% endfor %}
</nav>
```

단일 챕터일 때는 `index.html` 생략하고 `main.html` 상단에 동일한 책 정보 섹션을 둔다.

## 요약 본문 렌더링 (마크다운)

`summary`는 마크다운 문자열이라, HtmlRenderer가 `markdown-it-py`로 HTML 변환한다
(`_summary_section`). `##` 소제목·**굵게**·목록·코드블록·표가 살아나고, 본문 헤딩은
섹션 제목 '요약'(h2) 아래로 한 단계 낮춰 계층을 맞춘다. (평문 escape 방식은 폐기 —
마크다운이 글자 그대로 노출되던 문제 해결.) **그림(figure)은 다루지 않는다** — 요약은
텍스트/마크다운만 렌더하며 학습 자료에 별도 이미지를 싣지 않는다.

> **이중 이스케이프 자가복구**: 일부 에이전트가 summary에 진짜 개행 대신 리터럴
> `\n`(역슬래시+n)을 넣어 저장하면 마크다운이 한 줄로 뭉쳐 깨진다. `_load_all`이
> 로딩 시 `_unescape_if_double_escaped`로 **진짜 개행이 하나도 없고 리터럴 `\n`만
> 있을 때만** 개행으로 복구한다(정상 요약·코드블록 내 `\n`은 안 건드림). 이미
> 저장된 깨진 JSON도 재-finalize만으로 정상 렌더된다. 근본 예방은 프롬프트가
> "summary에 실제 줄바꿈을 넣고 리터럴 `\n`을 타이핑하지 말라"고 지시(prompts.py).

> `markdown-it-py`는 rich가 끌고 오는 전이 의존성이라 **사용자가 따로 설치할 게
> 없다.** 만약 없어도 `_FallbackMd`(내장 최소 변환기)로 떨어져 마크다운을 그대로
> 텍스트로 노출하지 않는다 — 어떤 환경에서도 추가 설치 없이 동작한다. 단, 정적
> HTML은 한 번 생성되면 고정이므로, 렌더러 코드가 바뀌면 **MCP 서버 재시작 후
> `finalize_study`를 다시 호출**해야 새 결과가 반영된다.

## study_html.py 완성본 (HTML 출력용 launcher)

```python
#!/usr/bin/env python3
"""Study launcher: 정적 파일 + 진도 read/write API."""
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path
import json, webbrowser, threading, re

ROOT = Path(__file__).parent
PROGRESS_DIR = ROOT / "progress"
PROGRESS_DIR.mkdir(exist_ok=True)
PORT = 8765

# 안전한 파일명만 허용 (path traversal 방지)
SAFE_NAME = re.compile(r'^[a-zA-Z0-9_-]+$')

class StudyHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # API 라우팅:
    #   GET  /api/progress/global       → _global.json
    #   GET  /api/progress/ch{N}        → 챕터별 진도/답안
    #   POST /api/progress/global       → _global.json 갱신
    #   POST /api/progress/ch{N}        → 챕터별 진도/답안 갱신

    def do_GET(self):
        if self.path.startswith('/api/progress/'):
            self._handle_progress_get()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/progress/'):
            self._handle_progress_post()
        else:
            self.send_error(404)

    def _progress_file(self):
        key = self.path.rsplit('/', 1)[-1]
        if key == 'global':
            return PROGRESS_DIR / '_global.json'
        if SAFE_NAME.match(key):
            return PROGRESS_DIR / f"{key}.json"
        return None

    def _handle_progress_get(self):
        f = self._progress_file()
        if f is None:
            return self.send_error(400)
        data = json.loads(f.read_text(encoding='utf-8')) if f.exists() else None
        self._send_json(data)

    def _handle_progress_post(self):
        f = self._progress_file()
        if f is None:
            return self.send_error(400)
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8')
        # JSON 검증 후 저장 (잘못된 JSON 거부)
        try:
            json.loads(body)
        except json.JSONDecodeError:
            return self.send_error(400)
        f.write_text(body, encoding='utf-8')
        self._send_json({"ok": True})

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    server = HTTPServer(('localhost', PORT), StudyHandler)
    threading.Timer(0.5, lambda: webbrowser.open(f'http://localhost:{PORT}/index.html')).start()
    print(f"Study server running at http://localhost:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
```

## 사이드바 (멀티 챕터 챕터 페이지 한정)

- `index.html`과 단일 챕터 `main.html`은 그 자체가 챕터 목록이라 사이드바를 두지 않는다.
- 멀티 챕터 챕터 페이지(`ch{N}.html`)에는 좌측 고정 사이드바(데스크탑 260px / ≤900px 슬라이드 토글)가 책 제목 + 챕터 링크 + 완료 체크를 보여준다.
- 현재 챕터는 `.is-active` 클래스로 강조되고, 완료된 챕터는 `.is-completed` 로 ✓ 체크가 채워진다.

## 진도 UI 동작 (storage.js + grading.js)

### index.html 진입 시

1. `GET /api/progress/global` → `last_chapter`가 있으면 해당 챕터 카드를 `.last-read`로 강조
2. 각 챕터 카드의 `completed` 여부에 따라 ✓ 체크 + "완료 / 아직 학습하지 않음" 텍스트 표시 (mc_score가 있으면 함께)

### chapter.html 진입 시

1. `GET /api/progress/ch{N}` → 답안 복원 (객관식 선택 / 단답·주관 텍스트), `mc_score` 표시
2. `last_position`이 있으면 해당 섹션으로 scroll (`IntersectionObserver`로 추적)
3. **챕터 완료 여부는 사용자가 명시적으로 누르는 "이 챕터 완료로 표시" 버튼으로만 토글**. 스크롤 비율 자동 측정은 사용하지 않는다. 이 버튼(`.completion-control`)은 **우하단에 `position: fixed`로 떠 있어** 스크롤 위치와 무관하게 항상 보인다(최하단까지 내려갈 필요 없음). 좌측 사이드바·좌상단 토글과 겹치지 않는 코너를 골랐고, 토글 로직은 그대로 `.complete-btn` 하나만 본다.
4. 객관식 채점: 선택 즉시 정/오답 판정, `mc_score` 갱신 후 throttle(2초) POST
5. 단답/주관/확장: 텍스트 입력 시 debounce(1초) POST, "모범답안 보기" 클릭 시 `viewed_answer: true` POST
6. 완료 버튼 토글 → `state.completed` 즉시 변경 + 사이드바 항목 ✓ 즉시 갱신 + POST

`reading_progress` 같은 진행률 필드는 더 이상 progress 파일 스키마에 없다. 사용자가 어느 섹션까지 봤는지는 `last_position`만 남기고, 완료 여부는 명시적 boolean으로만 관리한다.

progress 파일 스키마는 [05-data-schemas.md](./05-data-schemas.md#progress-학습-시-servepy가-생성관리) 참고.

## md_tui 출력 (output_format="md_tui")

HTML 정적 사이트 대신, 챕터별 폴더 + 요약 마크다운 + 터미널 학습 TUI를 생성한다.
HTML과 동일한 중립 JSON(chapters/{summaries,quiz,extension_quiz})에서 렌더되며, 선택은
`finalize_study(output_format="md_tui")`로만 갈린다 (서버/파이프라인 변경 없음).

### 실행

```
python study_tui.py           # 루트: 챕터 선택 메뉴
cd ch1 && python study_tui.py # 특정 챕터로 바로 진입
```

`rich`는 MCP 서버 의존성이라 설치 시 venv에 이미 깔려 있다. `finalize_study`가
주는 실행 명령은 **서버와 같은 인터프리터**(`sys.executable`)를 가리키므로, 그대로
실행하면 추가 설치 없이 바로 동작한다. 다른 python(`python3` 등)으로 실행하면
`study_tui.py`가 ① rich 자동 설치를 시도하고 ② 불가능한 환경(pip 부재·오프라인·
권한·externally-managed 등)이면 **평문 모드로 폴백**해 그래도 실행된다(rich API
표면만 흉내 내는 셰임 내장).

`study_tui.py`(rich 엔진)는 출력 루트에 1벌, 각 `ch*/study_tui.py`는 그 엔진을
호출하는 얇은 shim이다. 엔진은 `pdf_study` 패키지에 의존하지 않는 독립 스크립트다.

### TUI 동작

1. 챕터 진입 → `[r]` 요약 읽기(`summary.md` 렌더) / `[s]` 문제 풀기 / `[q]` 종료
2. 문제 풀기: `quiz.json`의 활성 유형을 순회
   - 객관식: 보기 출력 → 번호 입력 → 자동 채점 + 해설
   - 단답/주관/확장: 답 입력(빈 줄 제출) → 모범답안·출처 공개(자기 채점)
3. 매 문제 직후 `progress.json`에 답안 저장 → 재실행 시 이어풀기
4. 종료 시 "이 챕터 완료로 표시" 확인 → `completed` 토글, 객관식 점수 요약

진도 스키마(answers/mc_score/completed)는 HTML의 `progress/ch{N}.json`과 동일하다.
다만 HTTP 대신 TUI가 챕터 폴더의 `progress.json`에 직접 기록한다.
