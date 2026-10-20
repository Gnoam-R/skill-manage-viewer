@AGENTS.md

## Claude Code 전용

- 이 저장소의 `.claude/settings.json`이 보안 검토 플러그인(security-guidance)의 비용을 줄입니다.
  - `ENABLE_STOP_REVIEW=0`: 답변이 끝날 때마다 도는 diff 검토를 끕니다.
  - `SECURITY_REVIEW_MODEL`, `SG_AGENTIC_MODEL`: 커밋·푸시 때의 검토를 Sonnet으로 돌립니다.
  - 커밋·푸시 검토는 그대로 켜져 있습니다. 보안 지적이 오면 고치고 넘어가세요.
- 대화가 길어지면 다음 작업 전에 새 세션을 여세요. 현재 사용량은 `/context`로 볼 수 있습니다.
