# 설정 가이드

전체 설정 키와 안전한 placeholder는 `.env.example`을 기준으로 관리합니다.

## 주요 설정 그룹

- App 및 URL: 실행 환경, 공개 URL, GitHub App 식별자와 표시 이름
- GitHub 인증: Private Key, Webhook Secret, bot login
- 운영 범위: `ALLOWED_GITHUB_ACCOUNTS` 계정 allowlist
- 관리자 인증: GitHub App Client ID/Secret, session secret
- DB 및 worker: PostgreSQL URL, checkout 및 Codex 경로
- 리뷰 제한: timeout, 최대 파일 수, 파일 및 diff 크기
- 기본 정책: confidence, finding 수, LOW 포함 여부, draft 및 ignore pattern

GitHub Private Key는 인라인 값이 파일 경로보다 우선합니다. production에서는
HTTPS `PUBLIC_BASE_URL`과 충분히 긴 `ADMIN_SESSION_SECRET`이 필요합니다.
`ALLOWED_GITHUB_ACCOUNTS`도 하나 이상 필요하며 여러 계정은 쉼표로 구분합니다.

`ENVIRONMENT=development`와 `ADMIN_LOCAL_BYPASS=true`를 함께 지정한 경우에만
개발용 관리자 모드를 사용할 수 있습니다. production에서는 이 우회가 항상
거부됩니다.
계정 제한 완화 역시 development에서 `ALLOW_UNLISTED_GITHUB_ACCOUNTS=true`를
명시한 경우에만 허용됩니다.
