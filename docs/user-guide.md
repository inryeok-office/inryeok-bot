# 사용자 안내

Inryeok Bot은 GitHub App 설치만으로 Pull Request를 검토합니다. 저장소별 설정 파일이나 GitHub Actions는 필요하지 않습니다.

## 리뷰 시작

- 일반 PR은 `opened`, `reopened`, `ready_for_review`에서 자동 리뷰됩니다.
- Draft PR은 건너뛰며, Ready for review 전환 때 검토합니다.
- `synchronize`(push) 자동 리뷰는 기본적으로 꺼져 있습니다. 관리자가 저장소별로 켤 수 있으며, 켠 경우 연속 push는 debounce 후 최신 head 하나만 검토합니다.
- 즉시 다시 검토하려면 PR 일반 댓글에 한 줄로 `/review`를 입력합니다. 명령에는 짧은 cooldown이 적용됩니다.

리뷰는 PR base부터 현재 head까지의 전체 변경 범위를 확인하고, 코멘트는 현재 변경된 RIGHT-side 라인에만 게시합니다.

## 결과 읽기

리뷰에는 심각도별 표와 Finding이 포함됩니다. 재리뷰에서는 `새로운 Finding`, `계속 확인된 Finding`, `이번 리뷰에서 다시 발견되지 않음`을 구분합니다. 다시 발견되지 않았다는 표시는 코드가 해결됐다는 확정이 아닙니다.

오류가 발생하면 사용량 한도, 인증, rate limit, 일시적 외부 장애, 내부 오류를 구분해 안내합니다. 다시 시도해야 할 때는 안내에 따라 `/review`를 사용하세요.

언어와 리뷰 프로필은 관리자 설정을 따르며 기본 언어는 한국어입니다.
