"""
📰 Gemini 뉴스 검색기 (Streamlit 웹앱)

사용자가 키워드를 입력하면 Google Gemini API의 google_search 도구
(Search Grounding)를 활용해 관련 최신 뉴스 5건을 실시간으로 검색하고,
화면에 카드 형태로 보여주며, 결과를 CSV 파일로 다운로드할 수 있습니다.

[기술 스택]
- Streamlit: 파이썬 한 파일로 웹앱을 만드는 프레임워크
- google-genai (새 SDK): Gemini API 호출
- pandas: CSV 변환

[환경변수]
- GEMINI_API_KEY : GitHub Codespaces Secrets에 등록된 Gemini API 키
"""

import os          # 환경변수(컴퓨터에 저장된 비밀값) 읽기용
import json        # 모델이 보내준 JSON 응답 해석용
import re          # 정규식: 응답에서 [...] 부분만 추출용
from datetime import datetime  # CSV 파일명에 시각 붙이기용

import streamlit as st        # 웹 화면 만드는 도구
import pandas as pd           # 표 데이터를 다루고 CSV로 내보내기
from google import genai      # 새 Gemini SDK (옛 google-generativeai 아님!)
from google.genai import types  # GoogleSearch 도구 정의용


# ──────────────────────────────────────────────────────────────
# 1. Streamlit 페이지 기본 설정
# ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Gemini 뉴스 검색기",  # 브라우저 탭에 보이는 이름
    page_icon="📰",                   # 탭 아이콘
    layout="centered",                # 화면 가운데 정렬
)


# ──────────────────────────────────────────────────────────────
# 2. API 키 가져오기 함수
# ──────────────────────────────────────────────────────────────
def get_api_key() -> str:
    """
    환경변수 GEMINI_API_KEY 값을 읽어서 돌려준다.

    GitHub Codespaces Secrets에 GEMINI_API_KEY를 등록해두면,
    Codespace가 시작될 때 자동으로 이 환경변수가 만들어진다.
    값이 없으면 친절한 한글 안내 메시지를 보여주고 앱을 멈춘다.

    Returns:
        str: 'AIza' 로 시작하는 39자리 Gemini API 키

    Side Effects:
        키가 없으면 화면에 안내를 띄우고 st.stop() 으로 앱 중단.
    """
    # 환경변수에서 키 읽기 (없으면 None 반환)
    api_key = os.environ.get("GEMINI_API_KEY")

    # 키가 비어있다면 등록 방법까지 자세히 안내
    if not api_key:
        st.error("❌ **GEMINI_API_KEY 가 설정되지 않았습니다.**")
        st.markdown(
            """
            ### 🔑 등록 방법 (5분 안에 끝나요)

            1. **API 키 발급** : https://aistudio.google.com/apikey 접속
               → `Create API key` → 키 복사 (`AIza...` 로 시작하는 39자)
            2. **GitHub Secrets 등록** : https://github.com/settings/codespaces
               → `New secret`
               → Name: `GEMINI_API_KEY` (대소문자 정확히)
               → Value: 1단계에서 복사한 키
               → Repository access: 이 저장소 선택
            3. **Codespace 재시작** (필수!)
               → `Ctrl + Shift + P` → `Codespaces: Rebuild Container`

            #### ✅ 등록 확인 방법
            터미널에서 다음 명령을 실행해보세요.
            ```bash
            echo -n "$GEMINI_API_KEY" | wc -c
            ```
            결과가 **39** 이면 정상입니다. 다른 숫자가 나오면 공백이
            끼어 있거나 잘못 입력된 것이니 다시 등록해주세요.
            """
        )
        st.stop()  # 여기서 앱 실행 중단

    return api_key


# ──────────────────────────────────────────────────────────────
# 3. Gemini API로 뉴스 검색하는 함수
# ──────────────────────────────────────────────────────────────
def search_news_with_gemini(keyword: str) -> list[dict]:
    """
    Gemini 에게 google_search 도구를 활성화한 채로 뉴스 5건을 찾아달라고
    요청하고, 응답을 JSON 으로 파싱하여 파이썬 리스트로 돌려준다.

    Args:
        keyword (str): 사용자가 입력한 검색 키워드

    Returns:
        list[dict]: 뉴스 5건이 담긴 리스트.
            각 원소는 다음 키를 가진 dict:
              - title          : 기사 제목
              - source         : 언론사
              - published_date : 발행일 (YYYY-MM-DD)
              - url            : 원본 기사 URL
              - summary        : 한국어 3~4문장 요약

    Raises:
        ValueError : 응답을 JSON 으로 변환할 수 없을 때
        Exception  : API 호출 실패, 네트워크 오류 등
    """
    # ─── 1) Gemini 클라이언트 생성 (새 SDK 방식) ───────────────
    client = genai.Client(api_key=get_api_key())

    # ─── 2) Google Search 도구 정의 (실시간 웹 검색 활성화) ─────
    grounding_tool = types.Tool(google_search=types.GoogleSearch())

    # ─── 3) 모델에게 보낼 한국어 프롬프트 ──────────────────────
    #   - 정확히 5건
    #   - JSON 배열 형식만 응답하도록 강하게 지시
    #   - 코드펜스(```)나 머리말 절대 금지
    prompt = f"""당신은 뉴스 큐레이션 전문가입니다.
아래 키워드와 관련된 가장 최신의 뉴스 기사 5건을 Google 검색으로 찾아주세요.

키워드: "{keyword}"

각 기사에 대해 다음 정보를 **JSON 배열 형식으로만** 응답하세요.
설명, 인사말, 머리말, 코드 블록(```)은 절대 포함하지 마세요.
응답은 오직 [ ... ] 로 시작하고 끝나는 순수한 JSON 배열이어야 합니다.

JSON 형식 예시:
[
  {{
    "title": "기사 제목",
    "source": "언론사 이름",
    "published_date": "2026-04-29",
    "url": "https://원본기사주소",
    "summary": "기사 핵심 내용을 한국어 3~4문장으로 요약"
  }}
]

규칙:
- 정확히 5건을 반환할 것
- url 은 실제 기사 원본 URL (구글 검색 결과 페이지 URL 금지)
- summary 는 반드시 한국어로 3~4문장
- published_date 는 YYYY-MM-DD 형식
- 가능한 한 최근(최근 7일 이내) 기사 우선
"""

    # ─── 4) Gemini 호출 ───────────────────────────────────────
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[grounding_tool],
            temperature=1.0,  # Search Grounding 사용 시 Google 권장 값
        ),
    )

    # ─── 5) 응답 텍스트에서 JSON 만 추출 ───────────────────────
    text = (response.text or "").strip()

    # 모델이 ```json ... ``` 으로 감싸 보내는 경우 제거
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # ─── 6) JSON 파싱 (1차 시도) ──────────────────────────────
    try:
        articles = json.loads(text)
    except json.JSONDecodeError:
        # 1차 실패 → 응답에 잡설이 섞였을 수 있음
        # [...] 부분만 정규식으로 추출해 재시도
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            raise ValueError(
                "Gemini 응답을 JSON 으로 변환할 수 없습니다.\n\n"
                f"원문 일부:\n{text[:500]}"
            )
        articles = json.loads(match.group(0))

    # ─── 7) 형식 검증 ──────────────────────────────────────────
    if not isinstance(articles, list):
        raise ValueError("Gemini 응답이 JSON 배열이 아닙니다.")

    return articles


# ──────────────────────────────────────────────────────────────
# 4. 뉴스 카드 한 건 그리는 함수
# ──────────────────────────────────────────────────────────────
def render_article_card(idx: int, article: dict) -> None:
    """
    뉴스 1건을 테두리가 있는 카드 형태로 화면에 출력한다.

    Args:
        idx (int): 카드 번호 (1부터 시작)
        article (dict): 뉴스 1건 정보
            - title, source, published_date, url, summary
    """
    # 키가 없을 경우 기본값을 넣어 안전하게 처리
    title   = article.get("title",          "(제목 없음)")
    source  = article.get("source",         "(언론사 미상)")
    date    = article.get("published_date", "")
    url     = article.get("url",            "#")
    summary = article.get("summary",        "(요약 없음)")

    # 테두리 있는 컨테이너에 한 묶음으로 표시
    with st.container(border=True):
        # 제목을 마크다운 링크로 → 클릭 시 새 탭에서 원본 기사 열림
        st.markdown(f"### {idx}. [{title}]({url})")
        # 메타정보 (언론사 · 날짜)
        st.caption(f"📰 {source}  ·  📅 {date}")
        # 본문 요약
        st.write(summary)


# ──────────────────────────────────────────────────────────────
# 5. 메인 화면 구성
# ──────────────────────────────────────────────────────────────
def main() -> None:
    """Streamlit 앱의 진입점. 화면 전체 레이아웃을 구성한다."""

    # ── 헤더 ───────────────────────────────────────────────
    st.title("📰 Gemini 뉴스 검색기")
    st.markdown(
        "키워드를 입력하면 **Google Gemini** 가 실시간으로 검색해 "
        "관련 최신 뉴스 5건을 찾아드립니다."
    )

    # ── 세션 상태 초기화 (검색 결과 보관용) ─────────────────
    # session_state 에 저장하면 페이지 재실행에도 값이 유지됨
    if "articles" not in st.session_state:
        st.session_state.articles = []
    if "last_keyword" not in st.session_state:
        st.session_state.last_keyword = ""

    # ── 입력 폼 ────────────────────────────────────────────
    with st.form("search_form"):
        keyword = st.text_input(
            "검색 키워드",
            placeholder="예: 인공지능, 반도체, 기후변화 ...",
        )
        submitted = st.form_submit_button(
            "🔍 검색",
            use_container_width=True,
        )

    # ── 검색 버튼이 눌렸을 때 ───────────────────────────────
    if submitted:
        if not keyword.strip():
            st.warning("⚠️ 키워드를 입력해 주세요.")
        else:
            # 로딩 스피너 표시 (검색이 끝날 때까지 빙글빙글)
            with st.spinner(f"'{keyword}' 관련 최신 뉴스를 검색 중입니다..."):
                try:
                    articles = search_news_with_gemini(keyword.strip())
                    # 검색 결과를 세션 상태에 저장
                    st.session_state.articles = articles
                    st.session_state.last_keyword = keyword.strip()
                    st.success(
                        f"✅ '{keyword}' 관련 뉴스 {len(articles)}건을 찾았습니다."
                    )
                except ValueError as e:
                    # JSON 파싱 실패
                    st.session_state.articles = []
                    st.error(f"❌ 응답을 해석하지 못했습니다.\n\n{e}")
                except Exception as e:
                    # API 호출 실패, 네트워크 오류, 키 오류 등 모든 예외
                    st.session_state.articles = []
                    st.error(
                        "❌ 검색 중 오류가 발생했습니다.\n\n"
                        f"오류 내용: `{type(e).__name__}: {e}`"
                    )

    # ── 결과 표시 (세션에 저장된 값을 그림) ─────────────────
    if st.session_state.articles:
        st.divider()
        st.subheader(f"🔎 '{st.session_state.last_keyword}' 검색 결과")

        # 카드 5건을 차례로 그림
        for i, article in enumerate(st.session_state.articles, start=1):
            render_article_card(i, article)

        st.divider()

        # ── CSV 다운로드 버튼 ───────────────────────────────
        df = pd.DataFrame(st.session_state.articles)
        # utf-8-sig 로 저장해야 엑셀에서 한글이 깨지지 않음
        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        # 파일명에 현재 시각을 붙여 중복 방지
        filename = (
            f"news_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        st.download_button(
            label="💾 CSV 로 다운로드",
            data=csv_bytes,
            file_name=filename,
            mime="text/csv",
            use_container_width=True,
        )


# ──────────────────────────────────────────────────────────────
# 6. 진입점
# ──────────────────────────────────────────────────────────────
# 이 파일을 직접 실행할 때만 main() 호출
if __name__ == "__main__":
    main()
