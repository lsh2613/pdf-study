# MCP 로컬 venv 설치

pdf-learner MCP는 전역 Python이 아니라 이 저장소 안의 프로젝트 로컬 `.venv`로
실행한다. 이 문서는 기존 설치 링크와 북마크를 위한 빠른 진입 안내다.

## 빠른 설치

저장소 루트에서 다음 명령을 실행한다.

```bash
./scripts/setup_mcp.sh
```

스크립트는 `.venv`를 만들고 MCP 실행에 필요한 의존성을 설치·검증한 뒤 Codex CLI의
전역 MCP 설정을 자동 적용한다. Codex 전역 승인 정책이 `never`이면 모든 granular
승인 범주를 `true`로 둬 각 승인과 MCP form을 사용자에게 표시하도록 변환하며, 원본
설정을 백업한다.

개발 환경, 설정 출력과 환경 확인, 운영체제별 의존성 처리 등 상세 절차는
[운영 절차의 설치](operations.md#설치)를 따른다.
