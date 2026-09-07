# 운영 자동화

이 문서는 운영자가 백업과 정기 점검을 설치할 때 사용하는 최소 절차입니다.

## PostgreSQL 백업

백업 스크립트는 `/var/backups/inryeok-bot` 아래에만 파일을 만들고, 먼저
`.partial` 파일로 `pg_dump`를 실행한 뒤 성공한 경우에만 최종 이름으로
원자적으로 변경합니다. 백업 파일은 `0600`, 디렉터리는 `0700`이며,
실패한 partial 파일은 남기지 않습니다. 비밀번호는 명령행 인자나 로그로
전달하지 않습니다.

수동 실행:

```sh
sudo APP_DIR=/opt/inryeok-bot/app \
  BACKUP_DIR=/var/backups/inryeok-bot \
  /opt/inryeok-bot/app/scripts/backup_postgres.sh
```

자동 백업을 사용하려면 unit과 timer를 `/etc/systemd/system/`에 설치한 뒤
다음처럼 활성화합니다. `RETENTION_DAYS`는 정확한 백업 디렉터리 안의
`inryeok-bot-*.sql` 파일에만 적용되며 기본값은 자동 삭제 안 함입니다.

```sh
sudo install -m 0644 deploy/inryeok-bot-backup.service /etc/systemd/system/
sudo install -m 0644 deploy/inryeok-bot-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inryeok-bot-backup.timer
sudo systemctl status inryeok-bot-backup.timer --no-pager
```

백업 성공 여부는 서비스의 종료 상태와 파일 권한으로 확인합니다. 파일
내용, 데이터 값, 자격 증명은 로그나 보고서에 출력하지 않습니다.

복원은 운영 서비스와 분리한 PostgreSQL 17 임시 컨테이너·볼륨에서 먼저
검증합니다. 운영 볼륨에 `down -v`, `prune`, 초기화 명령을 사용하지 않습니다.

## 로그와 상태 점검

Compose 서비스는 `json-file` 로그를 파일당 10MiB, 최대 3개로 제한합니다.
호스트에서는 systemd journal의 보존 용량을 별도 정책으로 제한하고, 디스크
사용량·인증서 만료·백업 최신 시각을 주기적으로 확인합니다. 점검 출력에는
상태, 시각, 오류 분류와 correlation ID만 남기고 prompt, source, token,
stderr 원문과 내부 자격 증명은 남기지 않습니다.

운영 이미지와 Codex executor는 자동 업그레이드하지 않습니다. 새 공식
이미지 digest 또는 보안 advisory가 확인되면 임시 환경에서 검사·백업·복원
검증 후 수동 승인하여 순차 배포합니다.

