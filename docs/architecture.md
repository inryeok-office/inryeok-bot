# 아키텍처

- `app/github`: Webhook 검증, delivery 멱등성, 설치 동기화, GitHub 인증 및 API
- `app/jobs`: PostgreSQL 기반 작업 claim, 재시도, stale 작업 복구
- `app/review`: checkout과 diff 분석, finding 검증·중복 제거, GitHub Review 게시
- `app/codex`: 읽기 전용 Codex 실행, 구조화 결과, timeout 및 취소
- `app/admin`: GitHub 사용자 인증과 Jinja2/HTMX 관리 UI
- `migrations`: Alembic schema 이력

Webhook 요청은 리뷰를 직접 실행하지 않고 작업을 저장한 뒤 즉시 응답합니다.
Worker는 PostgreSQL row locking과 `SKIP LOCKED`로 작업을 하나씩 claim하며,
여러 worker가 같은 작업을 동시에 실행하지 않도록 합니다.

리뷰 결과는 변경 파일과 실제 RIGHT side 변경 라인, confidence, severity,
repository 설정을 기준으로 검증한 뒤 하나의 GitHub Review로 게시합니다.
Worker는 `Inryeok Review` Check Run을 `in_progress`로 만들고 결과에 따라
`success`, `neutral`, `failure`, `skipped` 중 하나로 완료합니다.
