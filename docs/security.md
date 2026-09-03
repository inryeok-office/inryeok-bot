# 보안 경계

Webhook은 원본 request body와 `X-Hub-Signature-256`을 사용해 검증하며 모든
delivery를 멱등 처리합니다. GitHub token, Private Key, Webhook Secret 및 Codex
인증 파일은 로그나 오류에 노출하지 않습니다.

운영 GitHub App은 `inryeok-office` 계정에만 설치 가능한 내부 App으로
설정합니다. 서버도 `ALLOWED_GITHUB_ACCOUNTS` allowlist를 대소문자 구분 없이
검사하며, 허용되지 않은 installation과 repository의 Webhook, 동기화, 작업 생성,
관리 UI 노출을 거부합니다. production에서는 비어 있는 allowlist로 시작할 수
없습니다. 제한 완화는 development에서 명시적으로 활성화한 경우에만 가능합니다.

checkout은 읽기와 diff 분석에만 사용합니다. 대상 저장소의 build, test,
Gradle, npm script, Makefile 또는 임의 실행 파일을 실행하지 않습니다. Git과
Codex subprocess에는 셸 문자열 대신 인자 배열을 사용하며 path traversal,
외부 symlink, binary 및 크기 제한을 검사합니다.

저장소 내용은 prompt injection을 포함할 수 있는 신뢰할 수 없는 입력으로
취급합니다. Codex는 읽기 전용 sandbox에서 실행하고 작업 후 임시 checkout을
제거합니다.

관리 UI는 동일 GitHub App의 OAuth flow를 사용합니다. OAuth state와 redirect를
검증하고 access token은 브라우저에 노출하지 않은 채 암호화된 서버 측
session에 저장합니다. 설정 변경과 재시도에는 CSRF 검증 및 대상 저장소 admin
권한 검사를 적용합니다.

실제 `.env`, PEM/key, `auth.json`, `.codex`, DB, checkout 및 로그 파일을
저장소에 커밋하지 마십시오. `scripts/check_secrets.py`로 대표적인 secret 형식을
검사할 수 있습니다.
