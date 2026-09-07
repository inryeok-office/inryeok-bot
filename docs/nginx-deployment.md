# Nginx 운영 ingress

운영 외부 TLS는 Ubuntu 호스트의 Nginx와 Certbot이 담당한다. Compose의
Caddy는 기본 기동에서 제외되며 `legacy-caddy` profile에서 rollback 용도로만
유지된다. Caddy volume은 삭제하지 않는다.

## 준비

```bash
sudo apt-get install nginx certbot python3-certbot-nginx
sudo mkdir -p /var/www/certbot
sudo cp deploy/nginx/inryeok-bot.conf.template /etc/nginx/sites-available/inryeok-bot.conf
```

인증서 발급 전에는 80 포트의 ACME challenge가 동작하는 HTTP 설정만 활성화한다.
인증서 발급 후 템플릿을 다시 설치하고 `sudo nginx -t`를 통과시킨 뒤 reload한다.

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d inryeok-bot.duckdns.org
sudo ln -s /etc/nginx/sites-available/inryeok-bot.conf /etc/nginx/sites-enabled/inryeok-bot.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot renew --dry-run
```

Install `deploy/nginx/certbot-reload-nginx.sh` as a root-owned executable under
`/etc/letsencrypt/renewal-hooks/deploy/` so a renewed certificate is loaded by
Nginx automatically.

Nginx는 `127.0.0.1:8000`만 upstream으로 사용하며 PostgreSQL과 애플리케이션
포트를 공인망에 노출하지 않는다. Webhook body 제한은 애플리케이션 제한과 같은
2 MiB로 유지한다.

## 전환과 rollback

1. 현재 Caddy 상태와 HTTPS health를 기록한다.
2. Nginx 설정 검증과 인증서 발급/갱신 검증을 완료한다.
3. 짧은 전환 창에서 Caddy만 중지하고 Nginx를 시작한다.
4. 외부 live/ready와 인증서 hostname을 확인한다.

실패하면 Nginx를 중지하고 다음으로 Caddy를 복구한다.

```bash
sudo systemctl stop nginx
cd /opt/inryeok-bot/app
docker compose --profile legacy-caddy up -d caddy
```

복구 후 두 health endpoint가 정상인지 확인하기 전에는 worker나 executor를
시작하지 않는다.
