"""
📰 최신 뉴스 검색 웹앱
================================================================
- Google Gemini API (Search Grounding) 로 최신 뉴스 검색
- Supabase 에 선택한 기사 저장
- Streamlit 으로 검색 / 저장 조회 / 대시보드 화면 제공
================================================================
"""

# ─────────────────────────────────────────────────────────────
# 0. 라이브러리 import
# ─────────────────────────────────────────────────────────────
import json
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# google-genai 는 신규 SDK (옛 google-generativeai 와 다름)
from google import genai
from google.genai import types

from supabase import Client, create_client


# ─────────────────────────────────────────────────────────────
# 1. 페이지 기본 설정
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="📰 최신 뉴스 검색",
    page_icon="📰",
    layout="wide",
)


# ─────────────────────────────────────────────────────────────
# 2. 시크릿 / API 키 불러오기
#    - 로컬:  .streamlit/secrets.toml
#    - 배포:  Streamlit Cloud > App settings > Secrets
# ─────────────────────────────────────────────────────────────
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SUPABASE_URL   = st.secrets["SUPABASE_URL"]
SUPABASE_KEY   = st.secrets["SUPABASE_KEY"]

# 무료 티어에서 가장 빠르고 처리량이 큰 모델
# (변경하고 싶으면 "gemini-2.5-flash" 로 교체 → 품질↑, RPM/RPD↓)
MODEL_NAME = "gemini-2.5-flash-lite"


# ─────────────────────────────────────────────────────────────
# 3. 클라이언트 초기화 (앱 실행 동안 1번만)
# ─────────────────────────────────────────────────────────────
@st.cache_resource
def init_gemini() -> genai.Client:
    """Gemini 클라이언트 생성"""
    return genai.Client(api_key=GEMINI_API_KEY)


@st.cache_resource
def init_supabase() -> Client:
    """Supabase 클라이언트 생성"""
    return create_client(SUPABASE_URL, SUPABASE_KEY)


gemini = init_gemini()
db = init_supabase()


# ─────────────────────────────────────────────────────────────
# 4. URL 유효성 검증 함수
#    - 일부 뉴스 사이트는 HEAD 요청을 막으므로 GET 으로 재시도
#    - User-Agent 가 없으면 봇으로 판단하여 차단되는 사이트 다수
# ─────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def verify_url(url: str, timeout: int = 6) -> bool:
    """URL 이 실제로 살아있는지(HTTP 200) 확인"""
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        # 1차 시도: HEAD (가벼움)
        r = requests.head(url, headers=HEADERS, allow_redirects=True, timeout=timeout)
        if r.status_code == 200:
            return True

        # 2차 시도: GET (HEAD 막은 사이트 대비, stream=True 로 본문은 받지 않음)
        r = requests.get(
            url,
            headers=HEADERS,
            allow_redirects=True,
            timeout=timeout,
            stream=True,
        )
        ok = r.status_code == 200
        r.close()
        return ok
    except requests.RequestException:
        return False


# ─────────────────────────────────────────────────────────────
# 5. 1단계 — Gemini + Google Search 로 뉴스 검색
#    ⚠️ Search Grounding 과 response_schema(JSON 강제) 는 함께 못 씀.
#    → 여기서는 일반 텍스트로 받은 뒤, 다음 단계에서 JSON 으로 변환
# ─────────────────────────────────────────────────────────────
def search_with_grounding(keyword: str) -> tuple[str, list[dict]]:
    """
    Returns
    -------
    (text, grounding_sources)
        text              : Gemini 가 만든 본문 텍스트
        grounding_sources : Google Search 가 실제로 참조한 (uri, title) 목록
    """
    prompt = f"""당신은 한국어 뉴스 큐레이터입니다.
'{keyword}' 에 대한 **가장 최근에 발행된 뉴스 기사 5건** 을 Google Search 로 찾아주세요.

각 기사마다 다음을 정리해주세요.
- 제목: 정확한 헤드라인
- 출처: 언론사 이름
- 발행일: YYYY-MM-DD
- URL: **검색 결과에 실제로 표시된 URL 그대로** (변형 금지, 가짜 금지)
- 요약: 3~4 문장

엄격한 규칙
1) 반드시 검색 결과에 실재하는 기사여야 합니다.
2) URL 은 절대 임의로 만들지 마세요.
3) 동일 기사를 중복하지 마세요. 가능하면 서로 다른 언론사를 포함하세요.
4) 가능하면 최근 30일 이내 기사를 우선합니다.

답변 형식 (이 형식을 그대로 지켜주세요)
[1]
제목: ...
출처: ...
발행일: ...
URL: ...
요약: ...

[2]
...
"""

    response = gemini.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        # ⭐ Google Search 도구 활성화 (= Search Grounding)
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.2,
        ),
    )

    text = response.text or ""

    # 검색이 실제 참조한 출처(redirect URL) — 항상 살아있음 → 보강용
    grounding_sources: list[dict] = []
    try:
        gm = response.candidates[0].grounding_metadata
        if gm and gm.grounding_chunks:
            for ch in gm.grounding_chunks:
                if ch.web and ch.web.uri:
                    grounding_sources.append(
                        {"uri": ch.web.uri, "title": ch.web.title or ""}
                    )
    except (AttributeError, IndexError):
        pass

    return text, grounding_sources


# ─────────────────────────────────────────────────────────────
# 6. 2단계 — 위 텍스트를 JSON 으로 변환
#    이 호출에서는 search 도구를 끄므로 response_mime_type=JSON 사용 가능
# ─────────────────────────────────────────────────────────────
def text_to_json(raw_text: str) -> list[dict]:
    """[{title, source, published_date, url, summary}, ...] 형태 반환"""
    if not raw_text.strip():
        return []

    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "title":          {"type": "STRING"},
                "source":         {"type": "STRING"},
                "published_date": {"type": "STRING"},
                "url":            {"type": "STRING"},
                "summary":        {"type": "STRING"},
            },
            "required": ["title", "source", "published_date", "url", "summary"],
        },
    }

    prompt = f"""다음 텍스트에서 뉴스 기사 정보를 추출하여 JSON 배열로 변환하세요.
- URL 은 원문 그대로 (한 글자도 변형 없이) 사용하세요.
- title/source/published_date/url/summary 5개 필드를 모두 채우세요.
- summary 는 3~4 문장, 한국어로 작성하세요.

[원본 텍스트]
{raw_text}
"""

    response = gemini.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
        ),
    )

    # response.text 가 ```json ... ``` 로 감싸져 올 가능성에 대비한 방어 코드
    raw = (response.text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


# ─────────────────────────────────────────────────────────────
# 7. 3단계 — 검색 → JSON 변환 → URL 검증을 묶어 "여러 번" 시도
# ─────────────────────────────────────────────────────────────
def search_and_verify(keyword: str, max_attempts: int = 2) -> list[dict]:
    """검증된 기사 5건을 모을 때까지 최대 max_attempts 회 시도"""
    verified: list[dict] = []
    last_grounding: list[dict] = []

    for attempt in range(max_attempts):
        raw_text, grounding_sources = search_with_grounding(keyword)
        last_grounding = grounding_sources or last_grounding
        articles = text_to_json(raw_text)

        # URL 검증 + 중복 제거
        for art in articles:
            url = (art.get("url") or "").strip()
            if not url:
                continue
            if any(a["url"] == url for a in verified):
                continue
            if verify_url(url):
                verified.append(art)
                if len(verified) >= 5:
                    break

        if len(verified) >= 5:
            break

    # 그래도 5건 미만이면 grounding_sources(반드시 살아있는 redirect URL)로 보강
    if len(verified) < 5 and last_grounding:
        for src in last_grounding:
            if any(a["url"] == src["uri"] for a in verified):
                continue
            verified.append({
                "title":          src["title"] or "(제목 없음)",
                "source":         urlparse(src["uri"]).netloc,
                "published_date": "",
                "url":            src["uri"],
                "summary":        "원본 텍스트에서 충분한 요약을 추출하지 못해 검색 결과 링크로 대체합니다.",
            })
            if len(verified) >= 5:
                break

    return verified[:5]


# ─────────────────────────────────────────────────────────────
# 8. Supabase 저장 / 조회 함수
# ─────────────────────────────────────────────────────────────
def save_article(keyword: str, art: dict) -> bool:
    try:
        db.table("news_history").insert({
            "keyword":        keyword,
            "title":          art.get("title", ""),
            "source":         art.get("source", ""),
            "published_date": art.get("published_date", ""),
            "url":            art.get("url", ""),
            "summary":        art.get("summary", ""),
        }).execute()
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False


def fetch_history(limit: int = 1000) -> pd.DataFrame:
    try:
        res = (
            db.table("news_history")
              .select("*")
              .order("saved_at", desc=True)
              .limit(limit)
              .execute()
        )
        return pd.DataFrame(res.data)
    except Exception as e:
        st.error(f"조회 실패: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────
# 9. 사이드바 (메뉴 + 무료 티어 안내)
# ─────────────────────────────────────────────────────────────
st.sidebar.title("📰 메뉴")
page = st.sidebar.radio(
    "이동할 페이지",
    ["🔎 뉴스 검색", "💾 저장된 뉴스", "📊 대시보드"],
)

with st.sidebar.expander("ℹ️ Gemini API 무료 티어 한도", expanded=True):
    st.markdown(
        f"""
**현재 모델**: `{MODEL_NAME}`

무료 티어의 일반적 한도 (변동될 수 있음)
- 분당 요청 (RPM): **약 15회**
- 일일 요청 (RPD): **약 1,000회**
- 분당 토큰 (TPM): 약 250,000

자세한 내용:
[Google AI Studio 한도 페이지](https://ai.google.dev/gemini-api/docs/rate-limits)
"""
    )


# ─────────────────────────────────────────────────────────────
# 10. 페이지 1 ─ 뉴스 검색
# ─────────────────────────────────────────────────────────────
if page == "🔎 뉴스 검색":
    st.title("🔎 최신 뉴스 검색")
    st.info(
        f"💡 **Gemini ({MODEL_NAME}) + Google Search** 로 최신 뉴스 5건을 가져옵니다.\n\n"
        "⚠️ 무료 티어는 분당 약 15회 요청 한도가 있어요. "
        "검색 한 번에 Gemini 호출 2회(검색 + JSON 변환)가 발생합니다."
    )

    keyword = st.text_input(
        "검색 키워드",
        placeholder="예: 인공지능, 환율, 삼성전자, 미국 금리",
    )
    if st.button("🔍 검색", type="primary", use_container_width=True):
        if not keyword.strip():
            st.warning("키워드를 입력해주세요.")
        else:
            with st.spinner("Google에서 최신 뉴스를 검색하고 URL을 검증하는 중... (10~30초)"):
                results = search_and_verify(keyword.strip())

            if not results:
                st.error("검색 결과가 없거나 모든 URL 검증에 실패했습니다. 다른 키워드로 시도해보세요.")
            else:
                st.session_state["last_results"] = results
                st.session_state["last_keyword"] = keyword.strip()
                st.success(f"{len(results)}건의 뉴스를 찾았습니다.")

    # 검색 결과 출력 (세션에 보관해 페이지 새로고침 없이 저장 버튼 동작 가능)
    if "last_results" in st.session_state:
        results: list[dict] = st.session_state["last_results"]
        keyword_used: str = st.session_state.get("last_keyword", "")

        st.subheader(f"📋 '{keyword_used}' 검색 결과")

        # 카드 2열 레이아웃
        for i in range(0, len(results), 2):
            cols = st.columns(2)
            for j, col in enumerate(cols):
                idx = i + j
                if idx >= len(results):
                    break
                art = results[idx]
                with col:
                    with st.container(border=True):
                        st.markdown(f"### {art.get('title', '제목 없음')}")
                        st.caption(
                            f"🏢 **{art.get('source', '출처미상')}**  ·  "
                            f"📅 {art.get('published_date', '날짜미상')}"
                        )
                        st.write(art.get("summary", ""))
                        st.markdown(f"🔗 [원문 보기]({art.get('url', '#')})")

                        if st.button("💾 저장", key=f"save_{idx}", use_container_width=True):
                            if save_article(keyword_used, art):
                                st.toast("저장되었습니다!", icon="✅")

        # CSV 다운로드 버튼
        st.divider()
        df_csv = pd.DataFrame(results)
        # 한글 깨짐 방지를 위해 utf-8-sig 사용 (엑셀 호환)
        csv_bytes = df_csv.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "⬇️ CSV로 다운로드",
            data=csv_bytes,
            file_name=f"news_{keyword_used}_{datetime.now():%Y%m%d_%H%M}.csv",
            mime="text/csv",
            use_container_width=True,
        )


# ─────────────────────────────────────────────────────────────
# 11. 페이지 2 ─ 저장된 뉴스
# ─────────────────────────────────────────────────────────────
elif page == "💾 저장된 뉴스":
    st.title("💾 저장된 뉴스")

    df = fetch_history()
    if df.empty:
        st.info("아직 저장된 뉴스가 없습니다. '뉴스 검색' 페이지에서 저장해보세요.")
    else:
        # 키워드 필터
        keywords = ["전체"] + sorted(df["keyword"].dropna().unique().tolist())
        sel = st.selectbox("키워드 필터", keywords)
        view = df if sel == "전체" else df[df["keyword"] == sel]

        st.write(f"총 **{len(view)}**건")

        for _, row in view.iterrows():
            saved_at_str = str(row.get("saved_at", ""))[:19] if row.get("saved_at") else ""
            with st.container(border=True):
                st.markdown(f"### {row['title']}")
                st.caption(
                    f"🔑 `{row['keyword']}`  ·  "
                    f"🏢 {row.get('source', '')}  ·  "
                    f"📅 {row.get('published_date', '')}  ·  "
                    f"💾 저장: {saved_at_str}"
                )
                st.write(row.get("summary", ""))
                st.markdown(f"🔗 [원문 보기]({row['url']})")


# ─────────────────────────────────────────────────────────────
# 12. 페이지 3 ─ 대시보드
# ─────────────────────────────────────────────────────────────
elif page == "📊 대시보드":
    st.title("📊 대시보드")

    df = fetch_history()
    if df.empty:
        st.info("아직 저장된 뉴스가 없습니다.")
    else:
        # 핵심 지표(KPI)
        c1, c2, c3 = st.columns(3)
        c1.metric("총 저장 건수", len(df))
        c2.metric("고유 키워드 수", df["keyword"].nunique())
        c3.metric(
            "고유 출처 수",
            df["source"].nunique() if "source" in df.columns else 0,
        )

        st.divider()

        # 키워드별 저장 건수
        st.subheader("🔑 키워드별 저장 건수")
        kw_count = (
            df["keyword"].value_counts()
              .reset_index()
              .rename(columns={"count": "건수", "keyword": "키워드"})
        )
        # pandas 버전에 따라 컬럼명이 'index/keyword' 가 될 수 있으니 보정
        kw_count.columns = ["키워드", "건수"]
        fig1 = px.bar(kw_count, x="키워드", y="건수", text="건수")
        fig1.update_traces(textposition="outside")
        st.plotly_chart(fig1, use_container_width=True)

        # 일자별 저장 건수
        st.subheader("📅 일자별 저장 건수")
        df["saved_date"] = pd.to_datetime(df["saved_at"]).dt.date
        date_count = (
            df.groupby("saved_date").size()
              .reset_index(name="건수")
              .sort_values("saved_date")
        )
        fig2 = px.line(date_count, x="saved_date", y="건수", markers=True)
        fig2.update_layout(xaxis_title="날짜", yaxis_title="건수")
        st.plotly_chart(fig2, use_container_width=True)

        # 출처(언론사)별 점유율
        if "source" in df.columns and df["source"].notna().any():
            st.subheader("🏢 출처별 점유율")
            src_count = (
                df["source"].fillna("(미상)")
                  .value_counts()
                  .head(10)
                  .reset_index()
            )
            src_count.columns = ["출처", "건수"]
            fig3 = px.pie(src_count, names="출처", values="건수", hole=0.4)
            st.plotly_chart(fig3, use_container_width=True)
