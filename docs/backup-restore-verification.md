# 백업 복원 검증

운영 백업은 생성 성공만으로 복구 가능하다고 판단하지 않습니다. 별도
PostgreSQL 17 컨테이너와 임시 Docker volume에 복원한 뒤 Alembic revision과
핵심 테이블 존재 여부만 확인합니다. 데이터 행이나 credential은 출력하지
않습니다.

```bash
sudo BACKUP_DIR=/var/backups/inryeok-bot \
  /opt/inryeok-bot/app/scripts/backup_postgres.sh
sudo POSTGRES_IMAGE=inryeok-postgres:17.7-gosu1.19 \
  /opt/inryeok-bot/app/scripts/verify_backup_restore.sh \
  /var/backups/inryeok-bot/<backup-file>.sql
```

`POSTGRES_IMAGE`는 이미 로컬에 존재하는 이미지여야 합니다. 스크립트는
암묵적인 image pull을 수행하지 않으며, 운영 PostgreSQL volume을 절대
마운트하지 않습니다. 검증이 끝나면 스크립트가 자신이 만든 컨테이너와
volume만 제거합니다. 동시 실행은 lock directory로 차단합니다.

## 브라우저 smoke

Playwright가 설치된 환경에서는 다음 read-only smoke를 실행할 수 있습니다.

```bash
ADMIN_E2E_BASE_URL=http://127.0.0.1:8000 python scripts/admin_playwright_smoke.py
```

로그인 세션을 테스트 전용 storage state로 제공하면 `/admin`,
`/admin/operations`, `/admin/jobs`의 렌더링도 확인합니다.

```bash
PLAYWRIGHT_STORAGE_STATE=/secure/test-state.json \
  ADMIN_E2E_BASE_URL=http://127.0.0.1:8000 \
  python scripts/admin_playwright_smoke.py
```

운영 OAuth credential과 session cookie를 저장소나 환경 예시에 넣지
않습니다. Playwright가 없는 환경에서는 기존 pytest HTTP/template 테스트를
대체 검증으로 사용합니다.
