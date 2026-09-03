# GitHub App 설정

운영 도메인을 확정한 뒤 `${PUBLIC_BASE_URL}`을 실제 HTTPS origin으로
치환합니다. App 이름은 GitHub에서 사용할 수 있는 고유한 이름을 선택하고,
표시 이름과 bot login은 배포 환경에 설정합니다.

이 App은 조직 내부 전용으로 생성합니다.

- App owner: `inryeok-office`
- Where can this GitHub App be installed?: `Only on this account`
- 외부 사용자 설치: 허용하지 않음
- GitHub Marketplace: 등록하지 않음

GitHub의 계정 제한과 별도로 서버의 `ALLOWED_GITHUB_ACCOUNTS`에도
`inryeok-office`를 설정합니다.

| GitHub App 항목 | 입력값 |
| --- | --- |
| Homepage URL | `${PUBLIC_BASE_URL}/admin` |
| Callback URL | `${PUBLIC_BASE_URL}/auth/github/callback` |
| Setup URL | `${PUBLIC_BASE_URL}/admin` |
| Webhook URL | `${PUBLIC_BASE_URL}/webhooks/github` |
| Expire user authorization tokens | Enabled 권장 |
| Request user authorization during installation | Disabled |

## Repository permissions

- Contents: Read-only
- Metadata: Read-only
- Pull requests: Read & write
- Issues: Read-only
- Checks: Read and write

## Webhook events

직접 구독하는 이벤트는 다음과 같습니다.

- Pull request
- Issue comment

서버는 GitHub App lifecycle의 `installation`과
`installation_repositories` delivery도 처리합니다.
`installation_repositories`는 모든 GitHub App에 자동으로 전달되므로 별도의
구독 항목이 표시되지 않을 수 있습니다.

설치 또는 저장소 접근 권한 추가 시 기본 저장소 설정이 생성됩니다. 제거, 설치 삭제 또는 설치 중지 시 기록을 지우지 않고 해당 저장소를 비활성화합니다.
