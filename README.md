# skill-manage-viewer

내 PC에 설치된 AI 에이전트 스킬(Claude Code · Codex · Antigravity)을 한 화면에서 보고 관리하는 Windows 트레이 프로그램입니다.

- 에이전트별 스킬을 **카테고리로 묶어** 보여 주고, 스킬마다 **한국어 한 줄 용도**와 **출처(git 커밋 / 로컬 / 플러그인 …)** 를 표시합니다.
- **링크로 설치**: GitHub 저장소·폴더·`SKILL.md` 링크 또는 `owner/repo[/경로]`를 넣으면 스킬을 찾아 골라서 설치합니다.
- **새로 만들기 / 수정 / 삭제 / 업데이트** 를 모두 여기서 합니다.
- 작업 표시줄이 아니라 **트레이 아이콘**으로 열고 닫습니다. 화면 우상단 ↔ 좌하단 위치 전환, 다크/라이트 자동.

## 실행

```bash
pip install -r requirements.txt
pythonw skill_viewer.py            # 창과 함께 시작 (X를 누르면 트레이로 숨김)
pythonw skill_viewer.py --hidden   # 트레이로만 시작
python  skill_viewer.py --check    # GUI 없이 에이전트별 개수만 출력
```

종료는 트레이 아이콘 우클릭 → 종료입니다. 트레이 아이콘은 Windows 11에서 `^` 안에 숨어 있을 수 있습니다.

## 기능

| 하는 일 | 방법 |
|---|---|
| 링크로 설치 | 헤더 `＋` → 링크로 설치 → 링크 입력 → 불러오기 → 스킬·에이전트 선택 → 설치 |
| 새 스킬 만들기 | `＋` → 새로 만들기 (SKILL.md 템플릿을 만들고 편집기를 엽니다) |
| 업데이트 | 헤더 `⇧` 또는 스킬 `⋯` → 저장소 업데이트 확인 (새 커밋이 있으면 `git pull --ff-only`) |
| 수정 | `⋯` → SKILL.md 열어서 수정 / 용도 설명 수정 |
| 삭제 | `⋯` → 삭제 |
| 이름 복사 | 스킬을 클릭 |

지원하는 링크 형식:

```
mattpocock/skills                                   저장소 전체에서 스킬 찾기
mattpocock/skills/skills/engineering/tdd            저장소 안의 경로
https://github.com/mattpocock/skills                저장소 링크
https://github.com/o/r/tree/main/skills/foo         브랜치·폴더 링크
https://github.com/o/r/blob/main/skills/foo/SKILL.md  파일 링크 (그 폴더를 설치)
```

https 링크만 받습니다(`file:`, `ext::`, ssh, 계정 정보가 들어간 링크는 거부). 브랜치 이름에 `/`가 있으면 저장소 루트 링크를 쓰세요.

## 동작 방식

```
~/.skill-manager/sources/<저장소>/   링크로 받은 git 클론 (얕은 클론)
~/.skill-manager/skills/<이름>/      여기서 새로 만든 스킬
~/.skill-manager/trash/              삭제·교체된 것이 이동하는 곳 (영구 삭제 없음)
~/.skill-manager/notes.json          직접 고친 용도 설명
```

각 에이전트 폴더(`~/.claude/skills`, `~/.codex/skills`, `~/.gemini/config/skills`)에는 위 실제 폴더를 가리키는
**junction(폴더 링크)** 이 만들어집니다. 그래서 복사본이 없고, 업데이트는 한 번 `git pull` 하면 세 에이전트에 동시에 반영됩니다.

- **삭제**: 링크는 링크만 해제(원본 유지), 직접 만든 실제 폴더는 `trash/`로 이동합니다. 에이전트마다 따로 삭제됩니다.
- **같은 이름이 이미 있을 때**: 기본은 건너뜁니다. "교체"를 켜면 기존 것을 `trash/`에 백업한 뒤 교체합니다.
- **업데이트로 폴더가 옮겨졌을 때**: 끊어진 링크를 같은 이름의 새 위치로 다시 연결하고, 저장소에서 사라진 스킬은 알려 줍니다.
- 플러그인·내장·앱 동기화 스킬은 보기만 가능합니다(관리는 해당 플러그인/앱에서).
- 이전에 만든 `~/.mattpocock-skills-update.ps1`(로그인 시 자동 업데이트)이 있으면, 삭제한 스킬이 다시 연결되지 않도록 그 목록에서도 이름을 뺍니다.

> ⚠ 스킬은 에이전트가 그대로 따르는 지시문이고 스크립트를 포함할 수 있습니다. 신뢰할 수 있는 출처만 설치하세요.

## 개발

```bash
python -m unittest test_manager test_viewer -v   # 임시 폴더에서만 동작 (실제 ~/.claude 등은 쓰지 않음)
python -m ruff check . --line-length 140
python -m pyright skill_manager.py skill_viewer.py
```

- `skill_manager.py` — 설치·업데이트·삭제·생성 로직 (Tk 없음)
- `skill_viewer.py` — 창, 트레이, 추가 대화상자
- `skill_notes.json` — 스킬별 한국어 용도 (이름 → 한 줄). 새 스킬은 여기에 추가하거나 앱에서 `용도 설명 수정`
- 카테고리 규칙은 `skill_viewer.py` 위쪽 `CATEGORIES` 정규식 목록
