# 07. 학습 UI & serve.py

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

## serve.py 완성본

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
3. **챕터 완료 여부는 사용자가 명시적으로 누르는 "이 챕터 완료로 표시" 버튼으로만 토글**. 스크롤 비율 자동 측정은 사용하지 않는다.
4. 객관식 채점: 선택 즉시 정/오답 판정, `mc_score` 갱신 후 throttle(2초) POST
5. 단답/주관/확장: 텍스트 입력 시 debounce(1초) POST, "모범답안 보기" 클릭 시 `viewed_answer: true` POST
6. 완료 버튼 토글 → `state.completed` 즉시 변경 + 사이드바 항목 ✓ 즉시 갱신 + POST

`reading_progress` 같은 진행률 필드는 더 이상 progress 파일 스키마에 없다. 사용자가 어느 섹션까지 봤는지는 `last_position`만 남기고, 완료 여부는 명시적 boolean으로만 관리한다.

progress 파일 스키마는 [05-data-schemas.md](./05-data-schemas.md#progress-학습-시-servepy가-생성관리) 참고.
