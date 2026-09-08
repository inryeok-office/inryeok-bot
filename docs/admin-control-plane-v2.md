# 관리자 Control Plane V2 계약

이 문서는 다음 관리자 콘솔 개편에서 사용할 backend 계약이다. 화면은 raw ORM 값을 해석하지 않고 아래 read model과 command 결과만 사용한다.

## 설정 분류

### 일반 관리자 설정

- 전역 리뷰 기본값: 리뷰, 자동 리뷰, 수동 `/review`, 언어, 프로필
- 자동 리뷰 이벤트: opened, reopened, ready-for-review, synchronize
- 리뷰 품질: 프로필과 안전 범위 내 고급 임계값
- 모델과 reasoning: 허용 목록에 있는 값만
- 분야: AUTO 또는 MANUAL과 선택 분야

### 저장소별 override

Boolean은 `INHERIT`, `ON`, `OFF` 세 상태이며 DB에서는 `NULL`, `true`, `false`로 저장한다. 값형 설정의 `NULL`은 전역 설정 상속이다. 설치 동기화는 override를 변경하지 않는다.

### 시스템 자동 관리 상태

`installed`, installation 활성/중지/제거, repository access, installation ID, repository ID, Webhook 상태, Job 상태, worker/executor heartbeat, migration revision은 관리자 정책이 아니다. 일반 설정 API로 변경할 수 없고 동기화 또는 운영 시스템이 관리한다.

### 배포 전용 설정

App ID/PEM, Webhook secret, OAuth/session secret, DB URL, CODEX_HOME, Unix socket, systemd sandbox, executor hard limit은 환경과 배포에서만 관리한다.

## Effective policy

`resolve_repository_policy(global_settings, repository, settings)`가 전역값, 저장소 override, 설치 상태, processing 상태를 결합한다. 반환값에는 effective 값, 각 필드의 provenance, block reason, 수동/자동 실행 eligibility가 포함된다.

provenance 값은 `GLOBAL_DEFAULT`, `REPOSITORY_OVERRIDE`, `PROFILE_DEFAULT`, `INSTALLATION_STATE`, `PROCESSING_STATE`, `ENVIRONMENT_LIMIT` 중 하나다. 주요 block reason은 `GLOBAL_PROCESSING_PAUSED`, `INSTALLATION_INACTIVE`, `REVIEW_DISABLED`, `AUTO_REVIEW_DISABLED`, `COMMAND_REVIEW_DISABLED`다.

Webhook, Job claim, 관리자 목록/상세, reconciliation preview는 동일 규칙을 사용한다. SQL claim은 resolver와 parity fixture로 검증한다.

## Command 규칙

설정 변경은 PATCH semantics다.

- 요청에 없는 필드는 보존한다.
- `null`은 명시적인 상속 복귀이며 누락과 다르다.
- 허용되지 않은 필드와 범위 밖 값은 거부한다.
- 저장 후 effective policy를 재계산한다.
- 모든 변경은 actor, source, 대상, before/after, reason을 audit한다.
- 동시 수정 방어를 위해 version 또는 updated_at 검증을 제공한다.

운영 제어인 pause/resume은 일반 설정 PATCH와 분리하며 repository policy를 변경하지 않는다.

## 관리자 Read model

- `RepositorySummary`: 설치 상태, access, stored/override/global/effective policy, provenance, block reason, 최근 Job/Review/Webhook
- `RepositoryDetail`: 위 정보와 품질·모델·이벤트·분야·게시·경로·제한 설정, version, audit
- `DashboardSummary`: processing, queue, 최근 성공률, 설치/저장소 수, 정책 불일치, 서비스와 백업 상태
- `JobDetail`: stage, attempts, snapshot, prompt/model/reasoning, Finding pipeline, rejection counts, safe error, retryability, Review URL
- `WebhookDeliverySummary`: event/action, installation/repository, 상태, safe reason, Job 관계, correlation, retryability

확인할 수 없는 값은 추측하지 않고 `UNKNOWN`으로 반환한다.

## Installation 신뢰 경계

조직명 allowlist는 실행 허용 조건이 아니다. 유효한 서명 Webhook, active installation, 선택된 repository access, 필요한 GitHub 권한, effective policy가 모두 충족되어야 한다. token cache와 GitHub client 호출은 installation ID를 키로 사용한다.

repository 이름은 표시용 metadata이며 numeric repository ID와 installation 관계를 우선한다. 제거·중지·삭제 시 이력과 override는 보존하고 access만 차단한다.

## Webhook과 중복 실행

Delivery는 `RECEIVED → PROCESSING → PROCESSED/IGNORED/FAILED_RETRYABLE/FAILED_FINAL` 상태를 가진다. 원본 payload, signature, token은 저장하지 않는다. `PROCESSED` delivery는 재실행하지 않고, retryable 실패만 제한적으로 재처리한다.

Review identity는 installation, repository, PR, head SHA, policy fingerprint, prompt version, model, reasoning, profile의 조합이다. 같은 identity의 성공 결과가 있으면 Codex와 Review를 다시 만들지 않는다.

## Executor 결과 보존

executor는 전용 state directory에 execution ID, fingerprint, 상태, 안전한 오류 metadata와 검증된 최소 결과만 atomic record로 보존한다. directory는 0700, record는 0600이며 TTL과 상한을 둔다. prompt, archive, raw stdout/stderr, credential은 저장하지 않는다.

## UI에 노출하지 않는 항목

PEM/token/secret, raw payload/prompt/source/stderr, executor sandbox/systemd 보안 옵션, 내부 DB credential은 read model이나 관리자 화면에 포함하지 않는다.

## 다음 화면별 필요 데이터

| 화면 | 사용 계약 |
|---|---|
| 대시보드 | DashboardSummary |
| 저장소 목록 | RepositorySummary 목록과 필터 결과 |
| 저장소 상세 | RepositoryDetail 및 PATCH 결과 |
| 리뷰 작업 | JobSummary 목록 |
| Job 상세 | JobDetail |
| 이벤트 | WebhookDeliverySummary 목록 |
| 감사 | 구조화된 audit 행 |
| 운영 | OperationSummary |
| 설정 | 전역 설정 read model과 PATCH 결과 |

## Legacy 호환

기존 HTML route와 form은 당분간 유지하되 command service를 통해 적용한다. legacy `enabled`/`auto_review` materialized 값은 effective 정책의 독립 source of truth가 아니다. 기존 Job/Review/Finding/audit는 새 contract로 다시 쓰지 않고 읽기 호환한다.
