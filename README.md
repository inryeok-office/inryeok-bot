# Inryeok Bot

GitHub Pull Request의 변경사항을 Codex로 분석하고, 실제 변경된 라인에서
발견한 중요한 문제를 알려주는 팀 코드리뷰 봇입니다. GitHub App을 저장소에
설치하면 별도 설정 파일이나 GitHub Actions 없이 사용할 수 있습니다.

## 주요 기능

- Pull Request 자동 리뷰
- 변경 라인 중심의 인라인 코멘트
- 중요도와 신뢰도 기반 결과 필터링
- 중복 리뷰 및 중복 코멘트 방지
- `/review` 수동 리뷰
- 저장소별 리뷰 설정

## 설치 및 사용 방법

> GitHub App 설치 링크는 준비 중입니다.

1. Inryeok Bot GitHub App을 설치합니다.
2. 코드리뷰를 적용할 저장소를 선택합니다.
3. Pull Request를 생성하면 자동으로 리뷰가 실행됩니다.
4. 수동 리뷰가 필요하면 Pull Request 댓글에 `/review`를 작성합니다.
5. 저장소별 설정은 GitHub App 설정 화면에서 관리 페이지로 이동해 변경합니다.

## `/review` 명령

Pull Request를 수동으로 다시 리뷰하려면 댓글의 별도 줄에 다음 명령만 작성합니다.

```text
/review
```

저장소에서 `write`, `maintain`, `admin` 중 하나의 권한이 있는 사용자만 실행할 수 있습니다.
