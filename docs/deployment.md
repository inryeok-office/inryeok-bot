# 배포 가이드

운영 도메인은 배포 시 결정하며 코드나 저장소에 고정하지 않습니다.
production의 `PUBLIC_BASE_URL`에는 HTTPS origin이 필요합니다.

```bash
docker compose build
docker compose run --rm worker codex login
docker compose up -d postgres
docker compose run --rm web alembic upgrade head
docker compose up -d
```

web과 worker는 동일한 non-root 이미지를 사용합니다. Codex 인증 및 checkout
volume은 worker에만 제공되고 Caddy가 `DOMAIN` 환경변수를 사용해 HTTPS와
reverse proxy를 제공합니다.

이미지에는 재현 가능한 버전의 Codex CLI가 설치됩니다. Codex 인증 파일은
worker volume 안에서 관리하고 파일 내용을 출력하거나 이미지에 복사하지
마십시오.
