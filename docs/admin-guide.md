# 관리자 운영 안내

관리자 UI에서 전역 기본값과 저장소별 override를 관리합니다. 저장소 설정이 비어 있으면 전역값을 상속합니다.

## 자동 리뷰 정책

기본 trigger는 `opened`, `reopened`, `ready_for_review`가 활성화되고 `synchronize`는 비활성화됩니다. 저장소에서 push 자동 리뷰를 켜면 기본 debounce는 60초이며, 연속 webhook은 최신 head만 실행합니다. `/review` 명령의 기본 cooldown도 60초입니다.

## 설정

언어(`ko`/`en`), 프로필(`CONSERVATIVE`/`BALANCED`/`THOROUGH`), 모델 allowlist, domain AUTO/MANUAL, Finding 제한, debounce/cooldown을 설정할 수 있습니다. 서버 안전 상한과 하한은 UI에서 우회할 수 없습니다. GENERAL domain은 항상 포함됩니다.

저장소를 일시 중지하려면 저장소의 enabled 또는 auto review를 끄고 저장합니다. 기존 이력은 보존됩니다. 설정 변경은 audit log에 기록됩니다.

## Job과 재리뷰

Job 상세에서 trigger, 예약 시각, superseded 여부, 적용 설정, 필터 단계별 개수를 확인할 수 있습니다. 실패 Job만 정책에 따라 재시도하며 동일 head의 중복 게시를 marker로 방지합니다. 재리뷰 비교는 모델의 비결정성을 고려해 `다시 발견되지 않음`을 `해결`로 단정하지 않습니다.

## 운영 주의

Quick Tunnel은 개발 테스트 전용입니다. 운영 전에는 Linux 배포 환경의 Codex 격리와 이미지 취약점(Trivy)을 별도로 검증하고, PostgreSQL 백업 절차를 준비하세요. secret, token, PEM은 문서나 UI에 기록하지 않습니다.
