# 📰 Gemini 뉴스 검색기

키워드를 입력하면 **Google Gemini AI** 가 실시간으로 웹을 검색해 관련 최신 뉴스 5건을 찾아 보여주고, 결과를 CSV 파일로 다운로드할 수 있는 Streamlit 웹앱입니다. 비전공자가 따라할 수 있도록 한 단계씩 자세히 안내합니다.

---

## 🛠 사용 기술

| 기술 | 역할 | 비유 |
|------|------|------|
| Python 3.11 | 프로그램의 언어 | 요리 레시피의 언어 |
| Streamlit | 웹 화면 만들기 | 레스토랑 인테리어 |
| Google Gemini API | AI 뇌 | 똑똑한 요리사 |
| google-genai (새 SDK) | Gemini와 대화하는 도구 | 요리사와 통하는 무전기 |
| pandas | 표 데이터 처리 | 메뉴판 정리 |

> 💡 **왜 새 SDK(`google-genai`) 인가?**
> 옛 라이브러리(`google-generativeai`) 와는 사용법이 완전히 달라요. 인터넷에서 찾은 코드가 옛날 것이면 작동하지 않습니다. 이 프로젝트는 새 SDK 기준입니다.

---

## 🗺 전체 작업 흐름 한눈에 보기

```
┌──────────────────────────────────────────────────────────────┐
│  1. AI Studio                  키 발급 (AIza... 39자)          │
│        │                                                       │
│        ▼ 복사                                                  │
│  2. GitHub Repository          코드 3개 파일 업로드             │
│        │                                                       │
│        ▼                                                       │
│  3. GitHub Codespaces          브라우저 안 VS Code 열기         │
│        │                                                       │
│        ▼                                                       │
│  4. Codespaces Secrets         GEMINI_API_KEY 등록 → 재시작     │
│        │                                                       │
│        ▼                                                       │
│  5. 터미널에서 실행            pip install → streamlit run      │
│        │                                                       │
│        ▼                                                       │
│  🎉 브라우저에서 앱 사용                                       │
└──────────────────────────────────────────────────────────────┘
```

총 5단계, 처음이라도 약 30분이면 끝납니다.

---

## 📋 1단계 — AI Studio에서 Gemini API 키 발급

> **왜?** Gemini AI 와 대화하려면 *"내가 누군지"* 알리는 신분증(=API 키)이 필요해요.

### 1-1. 정확한 주소로 접속

```
https://aistudio.google.com/apikey
```

> ⚠️ **주의**: `api-keys` 가 아니라 `apikey` (하이픈 없이, 끝에 s 없이) 입니다.

### 1-2. 구글 계정 로그인

처음이면 *"AI Studio 약관 동의"* 화면이 뜹니다 → 체크 후 계속.

> 💡 **팁**: 여러 구글 계정이 있다면 우측 상단 프로필을 확인하세요. **앞으로 계속 쓸 계정**으로 로그인되어 있는지 확실히 해두세요. 나중에 다른 계정으로 보면 키가 안 보입니다.

### 1-3. 키 만들기

화면 가운데 또는 좌측 사이드바의 파란색 **`Create API key`** 버튼 클릭.

팝업이 뜨면:
- **`Create API key in new project`** ← 이걸 추천 (가장 간단)
- 또는 *"Search Google Cloud projects"* 로 기존 프로젝트 선택

### 1-4. 키 복사

생성된 키가 화면에 표시됩니다 (`AIzaSy...` 로 시작하는 **39자리** 문자열).

> 💡 **팁**: 키 옆의 **📋 클립보드 아이콘** 을 클릭해 복사하세요. 마우스로 드래그하면 공백이 같이 복사되는 사고가 자주 일어납니다.

> ⚠️ **절대 금지 사항**:
> - 키를 코드 안에 직접 적기 → GitHub에 업로드되면 봇이 몇 분 안에 발견하고 도용
> - 키를 누군가에게 메신저로 보내기
> - 키를 이메일/메모 클라우드에 평문으로 저장
>
> 키가 노출되면 **하룻밤에 수백만 원 청구** 될 수 있습니다. 구글이 GitHub 노출을 감지하면 자동으로 키를 폐기시키기도 합니다.

### 1-5. 무료 한도

- 분당 약 15회 호출
- 일일 약 1,500회 호출
- 카드 등록 없이 무료 시작 가능
- 학습/프로토타입 용도로는 충분합니다

---

## 📋 2단계 — GitHub에 코드 올리기

> **왜?** 코드를 GitHub 라는 인터넷 저장소에 올려야 Codespaces 라는 *"브라우저 안 컴퓨터"* 를 열 수 있어요.

### 2-1. GitHub 계정 만들기 (이미 있으면 스킵)

[https://github.com](https://github.com) 접속 → **Sign up** → 이메일/비밀번호 입력해 가입.

### 2-2. 새 저장소(Repository) 만들기

1. 우측 상단 **`+`** 아이콘 → **New repository** 클릭
2. 다음과 같이 설정:
   - **Repository name**: `gemini-news-search` (또는 원하는 이름)
   - **Public / Private**: 둘 다 가능 (혼자 쓸 거면 **Private** 권장)
   - **Add a README file**: ✅ 체크
3. 하단 **Create repository** 클릭

### 2-3. 3개 파일 업로드

이 프로젝트의 파일들을 올려야 합니다:
- `app.py`
- `requirements.txt`
- `README.md` (이 파일)

**방법 ① 드래그 & 드롭** (가장 쉬움)
1. 저장소 페이지 → **`Add file`** → **`Upload files`**
2. 파일 3개를 화면에 드래그 & 드롭
3. 하단 **`Commit changes`** 클릭

**방법 ② 직접 작성**
1. **`Add file`** → **`Create new file`**
2. 파일명 입력 → 내용 붙여넣기 → **`Commit new file`**
3. 3개 파일을 차례로 만들기

> 💡 **팁**: 기존 README가 있다면 새 파일로 덮어쓰기 됩니다. 걱정 마세요.

---

## 📋 3단계 — GitHub Codespaces 열기

> **왜?** Codespaces 는 *"브라우저 안에서 돌아가는 진짜 컴퓨터"* 예요. 내 노트북에 파이썬을 설치하지 않아도 되고, 어디서든 같은 환경에서 작업할 수 있습니다.

### 3-1. Codespace 만들기

1. 저장소 페이지에서 초록색 **`<> Code`** 버튼 클릭
2. 팝업에서 **Codespaces** 탭 선택
3. **`Create codespace on main`** 클릭
4. **30초 ~ 1분 대기** → 브라우저 안에서 VS Code 가 자동으로 열림

### 3-2. VS Code 화면 구성 익히기

```
┌──────────────────────────────────────────────────────────┐
│  좌측 사이드바         가운데 편집기 영역                 │
│  📁 EXPLORER          ┌──────────────────────────────┐    │
│   ├ app.py            │  app.py 파일 내용이 여기에      │    │
│   ├ requirements.txt  │  열려서 표시됩니다             │    │
│   └ README.md         │                                │    │
│                       └──────────────────────────────┘    │
│                       ┌──────────────────────────────┐    │
│                       │  하단 터미널 (검은 영역)       │    │
│                       │  $ █                            │    │
│                       │  여기에 명령어를 입력합니다       │    │
│                       └──────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

> 💡 **터미널이 안 보이면**: 상단 메뉴 **Terminal → New Terminal** (또는 `` Ctrl + ` ``)

> ⚠️ **무료 사용 시간**: Codespaces 는 개인 계정 기준 월 60시간 무료. **사용 안 할 때는 꼭 멈추세요** (저장소 페이지 → Code → Codespaces → ⋯ → Stop). 그냥 두면 30분 무활동 후 자동 정지되긴 하지만, 그 동안에도 시간이 차감됩니다.

---

## 📋 4단계 — Secrets에 GEMINI_API_KEY 등록 ⭐ 가장 중요

> **왜?** API 키를 코드에 적으면 GitHub 에 그대로 올라가 노출되고, 키가 폐기됩니다. **Secrets** 라는 별도의 비밀 금고에 넣어두면, Codespace 가 시작될 때만 잠깐 환경변수로 꺼내 쓰기 때문에 안전합니다.

### 4-1. 정확한 주소로 접속

```
https://github.com/settings/codespaces
```

> ⚠️ **주의**: 비슷한 페이지가 여러 개 있어서 헷갈립니다. 다음을 구별하세요:
> - ✅ **`/settings/codespaces`** ← 여기! Personal Codespaces secrets
> - ❌ `/[저장소]/settings/secrets/actions` ← GitHub Actions 용. **Codespace 에서 못 읽음**
>
> 위 정확한 URL 로 직접 가는 게 가장 안전합니다.

### 4-2. Secret 등록

1. **`Codespaces secrets`** 섹션의 **`New secret`** 클릭
2. 다음과 같이 입력:

   | 항목 | 값 | 주의 사항 |
   |------|-----|----------|
   | **Name** | `GEMINI_API_KEY` | 정확히 이대로! `Gemini_API_Key` 같은 다른 표기 금지 |
   | **Value** | (1단계에서 복사한 키) | 앞뒤 공백 절대 없이 |
   | **Repository access** | **Selected repositories** → 본인 저장소 선택 | 안 고르면 Codespace 가 못 읽음 |

3. **`Add secret`** 클릭

> 💡 **공백 사고 방지 팁**: 키를 메모장에 한 번 붙여넣었다가 다시 복사해서 GitHub Value 칸에 붙여넣으세요. 한 번 더 정제됩니다.

### 4-3. Codespace 재시작 ⚠️ 필수

Secrets 는 Codespace 가 **시작될 때만** 환경변수로 주입됩니다. 이미 켜져있는 Codespace 는 새 Secret 을 모릅니다. 반드시 다시 시작하세요.

**방법 ① Rebuild Container** (가장 확실, 추천)
1. Codespace 안에서 **`Ctrl + Shift + P`** (Mac: `Cmd + Shift + P`)
2. 입력창에 `Codespaces: Rebuild Container` 입력 → Enter
3. 확인 창이 뜨면 **`Rebuild`** 클릭
4. 1~2분 기다리면 새 환경으로 다시 열림

**방법 ② 완전히 끄고 다시 열기**
1. 저장소 페이지 → `<> Code` → Codespaces 탭
2. 현재 Codespace 옆 **⋯** → **Stop codespace**
3. 멈추면 다시 클릭해서 **Open**

### 4-4. 등록 확인 (매우 중요!)

재시작 후 하단 터미널에서 다음 명령을 실행하세요:

```bash
echo -n "$GEMINI_API_KEY" | wc -c
```

| 결과 | 의미 | 다음 행동 |
|------|------|----------|
| **`39`** | 정상 | 5단계 진행 ✅ |
| `0` 또는 빈 줄 | Secret 이 안 잡힘 | 4-2 다시 / 저장소 선택 확인 / 다시 Rebuild |
| 40 이상 | 공백/줄바꿈 끼어듦 | 4-2 다시 (값 깔끔히 붙여넣기) |
| 30~38 | 키가 잘림 | 4-2 다시 (전체 키 복사) |

> 💡 **추가 검증**: 키 앞 6글자가 `AIzaSy` 인지도 확인
> ```bash
> echo "${GEMINI_API_KEY:0:6}"
> ```
> 다른 글자가 나오면 잘못된 키입니다.

---

## 📋 5단계 — 앱 실행

> **왜?** 라이브러리(=재료) 를 설치하고 Streamlit(=주방) 을 가동하면 웹앱이 시작됩니다.

### 5-1. 라이브러리 설치 (처음 한 번만)

하단 터미널에서:

```bash
pip install -r requirements.txt
```

> 💡 처음엔 1~2분 걸립니다. *"Successfully installed ..."* 가 나오면 끝.

### 5-2. 앱 실행

```bash
streamlit run app.py
```

> ⚠️ **주의**: `streamlit run app.py` 입니다. 그냥 `python app.py` 는 작동 안 해요.

### 5-3. 브라우저에서 열기

잠시 후 화면 **우측 하단**에 알림이 뜹니다:

> Your application running on port 8501 is available.
> **`Open in Browser`**

이걸 클릭하면 새 탭에서 앱이 열립니다.

> 💡 **알림이 사라졌으면**: VS Code 좌측 사이드바의 **PORTS** 탭 → 8501 포트 옆 **🌐 지구본 아이콘** 클릭

> ⚠️ **localhost:8501 은 작동 안 합니다**: 앱이 내 컴퓨터가 아니라 클라우드 서버에서 돌고 있기 때문이에요. 주소창에는 `xxx-8501.app.github.dev` 같은 긴 URL 이 정상입니다.

### 5-4. 사용해보기

1. 검색창에 키워드 입력 (예: `인공지능`, `반도체`, `기후변화`)
2. **🔍 검색** 버튼 클릭
3. 잠시 기다리면 뉴스 5건이 카드로 표시됨
4. **카드 제목 클릭** → 원본 기사 새 탭에서 열림
5. **💾 CSV 다운로드** → 내 컴퓨터에 저장 (엑셀에서 바로 열림, 한글 깨짐 없음)

### 5-5. 종료

터미널에서 **`Ctrl + C`** (Mac도 동일)

---

## 🆘 자주 만나는 문제 해결

### 🚨 에러 메시지별 대처법

| 에러 메시지 | 원인 | 해결 |
|------------|------|------|
| `API_KEY_INVALID` / `API key not valid` | 키가 잘못됨/잘림/폐기됨 | 4-4 로 길이 확인 → 새 키 발급 → Secrets 갱신 → Rebuild |
| `❌ GEMINI_API_KEY 가 설정되지 않았습니다` | 환경변수 없음 | Codespace Rebuild 필수. 재시작 안 했을 가능성 99% |
| `ImportError: cannot import name 'genai'` | 라이브러리 미설치 | `pip install -r requirements.txt` 실행 |
| `ModuleNotFoundError: streamlit` | Streamlit 미설치 | `pip install -r requirements.txt` 실행 |
| `❌ 응답을 해석하지 못했습니다` | Gemini가 가끔 JSON 외 응답 | 다시 검색해보기 (보통 두 번째엔 됨) |

### ❓ 자주 묻는 질문

**Q. 터미널에서 `echo $GEMINI_API_KEY` 는 잘 나오는데 앱에서는 *"키가 없다"* 고 나와요**
→ 99% 는 **Streamlit 프로세스가 옛날 환경변수를 들고 있어서**. Streamlit 터미널에서 `Ctrl + C` 로 멈추고 다시 `streamlit run app.py` 실행하세요.

**Q. localhost:8501 이 안 열려요**
→ 정상입니다. 앱이 클라우드에 있어서 그래요. `Open in Browser` 알림이나 PORTS 탭의 8501 포트를 클릭하세요.

**Q. Codespace 가 자동으로 꺼졌어요**
→ 30분 무활동 시 자동 정지. 저장소 페이지 → `<> Code` → Codespaces 탭에서 다시 열고, 터미널에서 `streamlit run app.py` 만 다시 실행. (라이브러리는 이미 깔려있음)

**Q. 코드 수정 후 GitHub 에 반영하려면?**
→ VS Code 좌측 사이드바의 **Source Control** (분기 모양) 아이콘 클릭:
   1. **Changes** 옆 **`+`** → 변경 파일 모두 스테이징
   2. 메시지 입력 (예: *"디자인 수정"*)
   3. **`커밋 및 푸시`** 클릭

**Q. Secrets 등록했는데 안 잡혀요**
→ 다음 체크리스트:
   - [ ] URL 이 `github.com/settings/codespaces` 인가? (저장소의 secrets/actions 아님)
   - [ ] Name 이 정확히 `GEMINI_API_KEY` 인가? (대소문자, 언더스코어 위치)
   - [ ] Repository access 에 본인 저장소가 선택돼있는가?
   - [ ] **Rebuild Container** 했는가?
   - [ ] `echo -n "$GEMINI_API_KEY" | wc -c` 결과가 39 인가?

**Q. AI Studio 에서 만든 키가 안 보여요**
→ 가장 흔한 원인은 **다른 구글 계정 로그인**. 우측 상단 프로필 확인. 그래도 없으면 새로 발급받는 게 빠릅니다.

**Q. 다른 사람도 이 앱에 접속할 수 있나요?**
→ Codespace URL 은 **기본적으로 본인만 접속 가능** (Private). 친구에게 보여주려면 PORTS 탭에서 8501 포트 우클릭 → Visibility → Public. ⚠️ 그러면 검색이 모두 본인 키로 청구되니 친한 사람한테만 잠깐 보여주는 용도로만.

**Q. 24시간 누구나 접근 가능한 서비스로 만들고 싶어요**
→ Codespace 는 부적합 (자동 정지됨). **Streamlit Community Cloud** ([share.streamlit.io](https://share.streamlit.io)) 에 배포하세요. 무료고 GitHub 연동돼서 편함.

---

## 🎓 비전공자가 더 깊이 알고 싶을 때

### 한국어 학습 자료

| 주제 | 추천 자료 |
|------|----------|
| Streamlit 입문 | 공식 문서 한국어 페이지 ([docs.streamlit.io](https://docs.streamlit.io)) |
| Gemini API | Google AI for Developers 한국어 페이지 ([ai.google.dev](https://ai.google.dev)) |
| Git/GitHub 기초 | 생활코딩 GitHub 강좌 (YouTube) |
| Python 기초 | 점프 투 파이썬 (위키독스) |
| VS Code 사용법 | VS Code 공식 한국어 문서 |

### 막혔을 때 도움받는 곳

1. **에러 메시지 그대로 검색** — 구글에 *"에러 메시지 전체 + Korean"* 으로 검색하면 한국어 답변이 자주 나와요
2. **AI 어시스턴트에 질문** — Claude, ChatGPT, Gemini 등에 **에러 메시지 전체를 그대로** 붙여넣고 *"왜 그래?"* 물어보기. *"수정해줘"* 보다는 *"왜 이렇게 됐어?"* 가 더 좋은 답을 받습니다
3. **Streamlit Community Forum** — 영어지만 검색 잘 됨. 비슷한 질문이 거의 있어요
4. **Stack Overflow** — 코드 에러 검색의 표준. 구글 검색 결과에 자주 등장

### 다음 단계로 나아가려면

이 프로젝트를 끝낸 뒤 도전해볼 만한 것들:

- 🎨 **디자인 개선**: Streamlit 의 `st.columns`, `st.tabs` 로 레이아웃 다듬기
- 🌐 **공개 배포**: Streamlit Community Cloud 로 24시간 서비스 만들기
- 🔍 **검색 옵션 추가**: 기간 필터, 언론사 필터, 정렬 방식
- 💾 **검색 이력 저장**: SQLite 로 과거 검색 기록 보관
- 📈 **통계 시각화**: 키워드별 기사 수, 날짜별 추이를 그래프로

---

## 📁 파일 구성

| 파일 | 역할 |
|------|------|
| `app.py` | Streamlit 웹앱의 모든 로직 (검색·표시·CSV 저장) |
| `requirements.txt` | 설치할 파이썬 라이브러리 목록 |
| `README.md` | 이 설명서 |

---

## 🛡 보안 체크리스트

이 프로젝트를 운영하면서 늘 지켜야 할 것:

- [ ] API 키를 코드 파일에 절대 적지 않는다
- [ ] API 키를 Slack/카톡/이메일로 공유하지 않는다
- [ ] GitHub 저장소가 Public 이라도, Secrets 에만 키를 둔다
- [ ] 키가 노출됐을 가능성이 보이면 즉시 AI Studio 에서 키 삭제 → 새 키 발급
- [ ] 의심되면 Codespace 를 Public 으로 만들지 않는다
- [ ] 사용 안 할 때는 Codespace 를 Stop 한다 (시간 차감 방지)

---

> 💪 처음 한 번이 가장 어렵습니다. 한 번 끝까지 돌리면, 그 다음부터는 코드만 바꿔서 응용할 수 있어요. 막히면 메시지 그대로 들고 와서 물어보세요. 시행착오가 곧 실력입니다!
