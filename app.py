"""
📰 Gemini 뉴스 검색기

사용자가 키워드를 입력하면 Google Gemini API의 google_search 도구(Search Grounding)를
이용해 관련 최신 뉴스 5건을 검색하고, 화면에 카드 형태로 보여주며,
결과를 CSV 파일로 다운로드할 수 있는 Streamlit 웹앱입니다.
"""

import os
import json
import re
from datetime import datetime

import streamlit as st
import pandas as pd
from google import genai
from google.genai import types


# ──────────────────────────────────────────────────────────────
# 1. Streamlit 페이지 기본 설정
# ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Gemini 뉴스 검색기",
    page_icon="📰",
    layout="centered",
)


# ──────────────────────────────────────────────────────────────
# 2. API 키 가져오기
# ──────────────────────────────────────────────────────────────
def get_api_key() -> str:
    """
    환경변수에서 GEMINI_API_KEY 값을 읽어옵니다.

    GitHub Codespace의 Secrets에 GEMINI_API_KEY를 등록해 두면
    Codespace가 시작될 때 자동으로 환경변수로 주입됩니다.

    Returns:
        Gemini API 키 문자열

    Raises:
        st.stop()을 호출해 앱을 멈춥니다 (키가 없을 경우).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        st.error(
            "❌ **GEMINI_API_KEY 환경변수가 설정되지 않았습니다.**\n\n"
            "1. https://github.com/settings/codespaces 접속\n"
            "2. **Codespaces secrets** → **New secret**\n"
            "3. Name: `GEMINI_API_KEY`, Value: 발급받은 키 입력\n"
            "4. Codespace를 재시작 후 다시 시도해 주세요."
        )
        st.stop()
    return api_key


# ──────────────────────────────────────────────────────────────
# 3. Gemini API로 뉴스 검색하기
# ──────────────────────────────────────────────────────────────
def search_news_with_gemini(keyword: str) -> list[dict]:
    """
    Gemini에게 google_search 도구를 활성화한 채로 뉴스 5건을 찾아달라고 요청하고,
    응답을 JSON으로 파싱해 파이썬 리스트(딕셔너리 5개)로 변환해 돌려줍니다.

    Args:
        keyword: 사용자가 입력한 검색 키워드

    Returns:
        뉴스 5건이 담긴 리스트. 각 원소는 다음 키를 가진 딕셔너리:
        - title (제목)
        - source (언론사)
        - published_date (발행일)
        - url (원본 URL)
        - summary (3~4문장 한국어 요약)
    """
    # 1) Gemini 클라이언트 생성
    client = genai.Client(api_key=get_api_key())

    # 2) Google Search grounding 도구 정의 (실시간 웹 검색 활성화)
    grounding_tool = types.Tool(google_search=types.GoogleSearch())

    # 3) 모델에게 보낼 한국어 프롬프트
    #    - 정확히 5건
    #    - JSON 배열만 응답하도록 강하게 지시
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
    "published_date": "2026-04-28",
    "url": "https://원본기사주소",
    "summary": "기사 핵심 내용을 한국어 3~4문장으로 요약"
  }}
]

규칙:
- 정확히 5건을 반환할 것
- url은 실제 기사 원본 URL이어야 함 (구글 검색 결과 페이지 URL 금지)
- summary는 반드시 한국어로 3~4문장
- published_date는 YYYY-MM-DD 형식
- 가능한 한 최근(최근 7일 이내) 기사 우선
"""

    # 4) Gemini 호출
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[grounding_tool],
            temperature=1.0,  # Search Grounding 사용 시 Google 권장 값
        ),
    )

    # 5) 응답 텍스트에서 JSON만 추출
    text = (response.text or "").strip()

    # 모델이 ```json ... ``` 으로 감싸 보내는 경우 제거
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # 6) JSON 파싱 시도
    try:
        articles = json.loads(text)
    except json.JSONDecodeError:
        # 응답에 앞뒤 잡설이 섞여있을 경우, [ ... ] 부분만 추출 재시도
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            raise ValueError(
                "Gemini 응답을 JSON으로 변환할 수 없습니다.\n\n"
                f"원문 일부:\n{text[:500]}"
            )
        articles = json.loads(match.group(0))

    # 7) 형식 검증
    if not isinstance(articles, list):
        raise ValueError("Gemini 응답이 JSON 배열이 아닙니다.")

    return articles


# ──────────────────────────────────────────────────────────────
# 4. 뉴스 1건을 카드 형태로 그리기
# ──────────────────────────────────────────────────────────────
def render_article_card(idx: int, article: dict) -> None:
    """
    뉴스 1건을 테두리가 있는 카드 형태로 화면에 그립니다.

    Args:
        idx: 카드 번호 (1부터 시작)
        article: 뉴스 1건 정보가 담긴 딕셔너리
    """
    title = article.get("title", "(제목 없음)")
    source = article.get("source", "(언론사 미상)")
    date = article.get("published_date", "")
    url = article.get("url", "#")
    summary = article.get("summary", "(요약 없음)")

    with st.container(border=True):
        # 제목 (마크다운 링크 → 클릭 시 새 탭에서 원본 기사 열림)
        st.markdown(f"### {idx}. [{title}]({url})")
        # 메타 정보 (언론사 · 날짜)
        st.caption(f"📰 {source}  ·  📅 {date}")
        # 본문 요약
        st.write(summary)


# ──────────────────────────────────────────────────────────────
# 5. 메인 화면 구성
# ──────────────────────────────────────────────────────────────
def main() -> None:
    """Streamlit 앱의 진입점."""

    # ── 헤더 ───────────────────────────────────
    st.title("📰 Gemini 뉴스 검색기")
    st.markdown(
        "키워드를 입력하면 **Google Gemini**가 실시간으로 검색해 "
        "관련 최신 뉴스 5건을 찾아드립니다."
    )

    # ── session_state 초기화 (검색 결과 보관용) ──
    if "articles" not in st.session_state:
        st.session_state.articles = []
    if "last_keyword" not in st.session_state:
        st.session_state.last_keyword = ""

    # ── 입력 폼 ────────────────────────────────
    with st.form("search_form"):
        keyword = st.text_input(
            "검색 키워드",
            placeholder="예: 인공지능, 반도체, 기후변화 ...",
        )
        submitted = st.form_submit_button(
            "🔍 검색",
            use_container_width=True,
        )

    # ── 검색 실행 ──────────────────────────────
    if submitted:
        if not keyword.strip():
            st.warning("⚠️ 키워드를 입력해 주세요.")
        else:
            with st.spinner(f"'{keyword}' 관련 최신 뉴스를 검색 중입니다..."):
                try:
                    articles = search_news_with_gemini(keyword.strip())
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
                    # 그 외 모든 에러 (API 호출 실패, 네트워크 등)
                    st.session_state.articles = []
                    st.error(
                        "❌ 검색 중 오류가 발생했습니다.\n\n"
                        f"오류 내용: `{type(e).__name__}: {e}`"
                    )

    # ── 결과 표시 (session_state에 저장된 값을 사용) ──
    if st.session_state.articles:
        st.divider()
        st.subheader(f"🔎 '{st.session_state.last_keyword}' 검색 결과")

        # 카드 5건 그리기
        for i, article in enumerate(st.session_state.articles, start=1):
            render_article_card(i, article)

        st.divider()

        # ── CSV 다운로드 버튼 ──────────────────
        df = pd.DataFrame(st.session_state.articles)
        # utf-8-sig로 저장해야 엑셀에서 한글이 깨지지 않음
        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        filename = (
            f"news_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        st.download_button(
            label="💾 CSV로 다운로드",
            data=csv_bytes,
            file_name=filename,
            mime="text/csv",
            use_container_width=True,
        )


# ──────────────────────────────────────────────────────────────
# 6. 진입점
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
