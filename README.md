# skill-manage-viewer

내 PC에 설치된 AI 에이전트 스킬(Claude Code · Codex · Antigravity)을 한 화면에서 보고 관리하는 **Windows·macOS** 트레이(메뉴 막대) 프로그램입니다.

- 왼쪽 **카테고리 목록**을 누르면 그 카테고리의 스킬이 오른쪽에 쭉 나옵니다. 카테고리마다 색이 다르고, 스킬마다 **한국어 한 줄 용도**와 **출처(git 커밋 / 로컬 / 플러그인 …)** 가 보입니다.
- **링크로 설치**: GitHub 저장소·폴더·`SKILL.md` 링크 또는 `owner/repo[/경로]`를 넣으면 스킬을 찾아 골라서 설치합니다.
- **새로 만들기 / 수정 / 삭제 / 업데이트** 를 모두 여기서 합니다.
- **트레이 아이콘**으로 열고 닫습니다. 최소화·닫기 버튼은 창을 트레이로 숨기고, 종료는 트레이 메뉴에서 합니다. 화면 우상단 ↔ 좌하단 위치 전환, 라이트(기본)/다크 전환.
- 글꼴은 저장소에 들어 있는 Pretendard입니다. 기울임꼴은 쓰지 않고, 모든 글씨는 배경과의 명도 대비 기준(WCAG AA)을 테스트로 확인합니다.

## 설치

### 필요한 것

| 항목 | Windows | macOS |
|---|---|---|
| OS | Windows 10 / 11 | macOS 10.15 이상 |
| Python | 3.12 이상, [python.org](https://www.python.org/downloads/)에서 설치할 때 **Add python.exe to PATH** 체크 | 3.12 이상, **python.org 설치 프로그램**을 권장(Tk 8.6 포함). Homebrew라면 `brew install python@3.12 python-tk@3.12` |
| Git | 링크로 설치·업데이트할 때만 필요 | 같음 (`xcode-select --install`로도 설치됨) |

> macOS 기본 `/usr/bin/python3`의 Tk(8.5)는 창이 비어 보일 수 있으니 쓰지 마세요.

### 설치 순서

1. 저장소를 받습니다.

   ```bash
   git clone https://github.com/sgustjd2/skill-manage-viewer.git
   cd skill-manage-viewer
   ```

   git이 없으면 GitHub의 **Code → Download ZIP**으로 받아 압축을 풀어도 됩니다.

2. 처음 한 번 실행합니다. 필요한 패키지가 없으면 먼저 설치합니다.

   | | Windows | macOS |
   |---|---|---|
   | 더블클릭 | `start.bat` | `start.command` (처음에는 우클릭 → 열기) |
   | 터미널 | `pip install -r requirements.txt`<br>`pythonw skill_viewer.py` | `python3 -m pip install -r requirements.txt`<br>`python3 skill_viewer.py` |

3. 창이 뜨고 아이콘이 생깁니다. Windows는 작업 표시줄 오른쪽 트레이(Windows 11은 `^` 안), macOS는 화면 위쪽 **메뉴 막대**입니다.

### 이름으로 검색해서 실행하기

한 번만 아래를 실행하면 앞으로는 검색창에서 앱 이름으로 켤 수 있습니다.

```bash
python install.py              # Windows: python install.py
python3 install.py             # macOS
```

| | 검색창 | 만들어지는 것 |
|---|---|---|
| Windows | 시작 메뉴에서 **스킬 카탈로그** 또는 **Skill Catalog** | 시작 메뉴 바로 가기 (`pythonw`로 실행, 콘솔 창 없음) |
| macOS | Spotlight(`⌘ Space`)에서 **스킬 카탈로그** 또는 **Skill Catalog** | `~/Applications/스킬 카탈로그 (Skill Catalog).app` |

- 파일을 복사하지 않고 이 폴더를 가리키므로 **저장소 폴더를 옮기거나 지우면 다시 실행**해야 합니다.
- 로그인할 때 트레이로만 자동 시작하려면 `python install.py --startup`(Windows는 시작 프로그램 폴더, macOS는 LaunchAgent이며 다음 로그인부터 적용).
- 되돌리기: `python install.py --uninstall` (이 스크립트가 만든 것만 지웁니다).
- Python 경로가 바뀌면(가상환경 삭제 등) `install.py`를 다시 실행하세요.

### 실행 옵션

```bash
python skill_viewer.py            # 창과 함께 시작 (Windows는 pythonw)
python skill_viewer.py --hidden   # 트레이(메뉴 막대)로만 시작
python skill_viewer.py --check    # 창 없이 에이전트별 스킬 개수와 폴더 위치만 출력 (설치 확인용)
```

- 최소화나 닫기(X)를 누르면 창이 숨습니다. 트레이(메뉴 막대) 아이콘을 클릭하면 다시 열립니다.
- 완전히 끄려면 아이콘 메뉴 → **종료**.
- 이미 실행 중이면 두 번째 실행은 아무것도 하지 않고 끝납니다.

### 업데이트와 제거

- 앱 업데이트: 저장소 폴더에서 `git pull` 후 다시 실행합니다.
- 제거: 앱에서 링크로 설치한 스킬을 `⋯ → 삭제`로 정리하고, `python install.py --uninstall` 후 저장소 폴더와 `~/.skill-manager` 폴더를 지웁니다.
  - 앱이 만든 스킬 링크는 에이전트 폴더에 남아 있으면 그대로 동작합니다. 원본(`~/.skill-manager/sources`, `skills`)을 지우면 그 링크는 끊어집니다.

### 운영체제별로 다른 점

- 에이전트 폴더에 만드는 링크: Windows는 junction(관리자 권한 불필요), macOS는 심볼릭 링크입니다.
- 트레이: macOS에서는 메뉴 막대 아이콘이 **별도 프로세스**로 돕니다(앱을 닫으면 같이 종료됩니다).
- 글꼴: 두 OS 모두 `fonts/`의 Pretendard를 이 프로그램에서만 쓰도록 불러옵니다. 실패하면 Windows는 맑은 고딕, macOS는 Apple SD Gothic Neo로 보입니다.
- 창 위치: macOS는 메뉴 막대(약 25px)와 Dock(약 90px)을 대략 피해서 놓습니다.
- 최소화: 두 OS 모두 작업 표시줄·Dock에 남기지 않고 트레이(메뉴 막대)로 숨깁니다.

## 다른 PC에서 스킬을 찾는 위치

설치한 PC에서 **자동으로 찾아서** 보여 줍니다. 따로 설정할 것은 없습니다.

- Claude Code: `~/.claude/skills`와, 켜 둔 플러그인의 스킬. `CLAUDE_CONFIG_DIR`가 설정돼 있으면 그 위치를 씁니다.
- Codex: `~/.codex/skills`. `CODEX_HOME`이 설정돼 있으면 그 위치를 씁니다.
- Antigravity: `~/.gemini/config/skills`와 내장 스킬. (macOS도 같은 위치입니다.)
- 설치되지 않은 에이전트는 "설치된 스킬이 없습니다"로 표시되고, 다른 에이전트에는 영향이 없습니다. 깨진 설정 파일이나 형식이 이상한 `SKILL.md`가 있어도 그 항목만 건너뜁니다.
- 한국어 용도 설명은 `skill_notes.json`에 있는 스킬만 한국어이고, 나머지는 스킬 원문 설명의 첫 문장을 보여 줍니다. 앱에서 `⋯ → 용도 설명 수정`으로 바꿀 수 있습니다.

## 기능

| 하는 일 | 방법 |
|---|---|
| 카테고리별 보기 | 왼쪽 목록에서 카테고리 클릭 (마우스를 올리면 아래쪽에 그 카테고리의 기준이 나옵니다) |
| 링크로 설치 | 헤더 `＋ 추가` → 링크로 설치 → 링크 입력 → 불러오기 → 스킬·에이전트 선택 → 설치 |
| 새 스킬 만들기 | `＋ 추가` → 새로 만들기 (SKILL.md 템플릿을 만들고 편집기를 엽니다) |
| 업데이트 | 헤더 `⇧ 업데이트` 또는 스킬 `⋯` → 저장소 업데이트 확인 (새 커밋이 있으면 `git pull --ff-only`) |
| 수정 | `⋯` → SKILL.md 열어서 수정 / 용도 설명 수정 |
| 삭제 | `⋯` → 삭제 |
| 이름 복사 | 스킬을 클릭 |
| 화면 전환 | 헤더 `다크`/`라이트`, `⇄`(우상단 ↔ 좌하단) |

지원하는 링크 형식:

```
mattpocock/skills                                     저장소 전체에서 스킬 찾기
mattpocock/skills/skills/engineering/tdd              저장소 안의 경로
https://github.com/mattpocock/skills                  저장소 링크
https://github.com/o/r/tree/main/skills/foo           브랜치·폴더 링크
https://github.com/o/r/blob/main/skills/foo/SKILL.md  파일 링크 (그 폴더를 설치)
```

https 링크만 받습니다(`file:`, `ext::`, ssh, 계정 정보가 들어간 링크는 거부). 브랜치 이름에 `/`가 있으면 저장소 루트 링크를 쓰세요.

## 카테고리

스킬은 **"언제 쓰는가"** 하나의 기준으로 나누고, 한 스킬은 정확히 한 카테고리에만 들어갑니다. 아는 스킬은 모두 `skill_categories.json`에 이름으로 적혀 있고, 같은 이름이 두 카테고리에 있으면 테스트가 실패합니다.

| 카테고리 | 기준 |
|---|---|
| 기획·설계 | 코드를 쓰기 전에 요구사항·계획·구조를 정할 때 |
| 구현·테스트 | 정한 계획을 코드와 테스트로 옮길 때 |
| 디버깅·검증 | 문제의 원인을 찾거나, 끝내기 전에 실제로 동작하는지 확인할 때 |
| 리뷰·품질 | 완성된 변경을 다른 눈으로 점검하고 품질 부채를 관리할 때 |
| Git·협업 | 커밋·PR·브랜치와 저장소 규칙으로 변경을 공유할 때 |
| UI·디자인 | 화면·브랜드·이미지 같은 시각물을 만들거나 고치거나 점검할 때 |
| 문서·자료 | 문서·발표·표 파일을 만들거나, 자료를 조사해 정리할 때 |
| 자동화·브라우저 | 브라우저·PC를 대신 조작하거나 예약·반복 작업을 돌릴 때 |
| 에이전트·스킬 설정 | 에이전트 자체(스킬·플러그인·설정·메모리)를 만들거나 관리할 때 |
| 기타 | 위 어디에도 맞지 않는 스킬 |

목록에 없는 새 스킬은 각 카테고리의 `keywords`를 위 순서대로 이름 → 설명 순으로 맞춰 보고 처음 맞는 곳에 넣습니다.

## 동작 방식

```
~/.skill-manager/sources/<저장소>/   링크로 받은 git 클론 (얕은 클론)
~/.skill-manager/skills/<이름>/      여기서 새로 만든 스킬
~/.skill-manager/trash/              삭제·교체된 것이 이동하는 곳 (영구 삭제 없음)
~/.skill-manager/notes.json          직접 고친 용도 설명
~/.skill-manager/settings.json       라이트/다크 선택
```

각 에이전트 폴더(`~/.claude/skills`, `~/.codex/skills`, `~/.gemini/config/skills`)에는 위 실제 폴더를 가리키는
**junction(폴더 링크)** 이 만들어집니다. 그래서 복사본이 없고, 업데이트는 한 번 `git pull` 하면 세 에이전트에 동시에 반영됩니다.

- **삭제**: 링크는 링크만 해제(원본 유지), 직접 만든 실제 폴더는 `trash/`로 이동합니다. 에이전트마다 따로 삭제됩니다.
- **같은 이름이 이미 있을 때**: 기본은 건너뜁니다. "교체"를 켜면 기존 것을 `trash/`에 백업한 뒤 교체합니다.
- **업데이트로 폴더가 옮겨졌을 때**: 끊어진 링크를 같은 이름의 새 위치로 다시 연결하고, 저장소에서 사라진 스킬은 알려 줍니다.
- 플러그인·내장·앱 동기화 스킬은 보기만 가능합니다(관리는 해당 플러그인/앱에서).
- 이전에 만든 `~/.mattpocock-skills-update.ps1`(로그인 시 자동 업데이트)이 있으면, 삭제한 스킬이 다시 연결되지 않도록 그 목록에서도 이름을 뺍니다.

> ⚠ 스킬은 에이전트가 그대로 따르는 지시문이고 스크립트를 포함할 수 있습니다. 신뢰할 수 있는 출처만 설치하세요.

## 개발 환경

### 준비

Windows 또는 macOS에서 Python 3.12 이상과 git이 필요합니다. macOS 쪽 코드는 GitHub Actions의 macOS 실행기에서 같은 테스트로 확인합니다(`.github/workflows/ci.yml`).

```bash
git clone https://github.com/sgustjd2/skill-manage-viewer.git
cd skill-manage-viewer
py -3.12 -m venv .venv           # macOS: python3.12 -m venv .venv
.venv\Scripts\activate           # macOS: source .venv/bin/activate
pip install -r requirements.txt ruff pyright
```

`pyright`는 처음 실행할 때 Node.js를 쓰거나 내려받습니다.

### 확인 명령

```bash
python -m unittest test_manager test_viewer test_install -v     # 테스트 (임시 폴더에서만 동작, 실제 ~/.claude 등은 쓰지 않음)
python -m ruff check . --line-length 140 --select E,F,W,B,UP,SIM  # 린트
python -m ruff format --line-length 140 test_viewer.py test_manager.py
python -m pyright skill_manager.py skill_viewer.py              # 타입 검사
python skill_viewer.py --check                                  # 이 PC의 스킬 탐색 결과
```

GUI 테스트는 실제로 창을 띄웠다 닫으므로 화면이 잠깐 깜빡입니다. 테스트가 확인하는 것:

- `test_manager`: 링크 해석(허용·거부), 설치·업데이트·재연결·삭제, 이름 충돌과 교체, 휴지통 이동, 새 스킬 생성
- `test_install`: macOS `.app` 구성(Info.plist·launcher·아이콘·로그인 항목)과 제거 범위, Windows 시작 메뉴 바로 가기(다른 OS에서는 건너뜀)
- `test_viewer`: 트레이·최소화·위치 전환, 카테고리 목록과 검색, 메뉴, 추가 대화상자, 업데이트, 테마, 글꼴(Pretendard·기울임 없음),
  **모든 글씨의 명도 대비(라이트·다크, 마우스 올림·선택·비활성 상태 포함)**, 카테고리 중복 없음, 다른 PC 환경(환경변수, 없는 폴더, 깨진 파일),
  OS 공통 동작(폴더 링크, 중복 실행 방지, 우클릭 이벤트, 트레이 명령 허용 목록)

### 파일 구성

| 파일 | 내용 |
|---|---|
| `skill_manager.py` | 설치·업데이트·삭제·생성 로직 (Tk 없음) |
| `skill_viewer.py` | 창, 트레이, 추가 대화상자, 색·글꼴 |
| `platform_support.py` | Windows/macOS에서 달라지는 것(폴더 링크, 파일 열기, 글꼴 로드, 화면 영역, 중복 실행 방지)을 한곳에 모음 |
| `install.py` | 시작 메뉴 바로 가기 / macOS `.app` 만들기와 제거 |
| `start.bat`, `start.command` | 패키지 확인 후 실행 (Windows / macOS) |
| `.github/workflows/ci.yml` | Windows·macOS에서 린트와 테스트 실행 |
| `skill_categories.json` | 카테고리 기준(`about`), 소속 스킬(`skills`), 새 스킬 분류용 `keywords` |
| `skill_notes.json` | 스킬별 한국어 용도 (이름 → 한 줄) |
| `fonts/` | Pretendard Regular·Bold (SIL OFL 1.1, `fonts/LICENSE.txt`) |

### 스킬을 새로 분류하거나 설명을 붙일 때

1. `skill_categories.json`에서 맞는 카테고리의 `skills`에 이름을 추가합니다(한 곳에만).
2. `skill_notes.json`에 `"이름": "한 줄 용도"`를 추가합니다.
3. `python -m unittest test_viewer`로 중복·누락이 없는지 확인합니다. 설명이 있는데 카테고리가 없으면 테스트가 알려 줍니다.

색을 바꿀 때는 `skill_viewer.py`의 `CAT_COLORS`·`LIGHT`·`DARK`를 고치고 같은 테스트로 대비를 확인합니다.
