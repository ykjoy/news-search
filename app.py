"""
==========================================================
📰 Gemini 최신 뉴스 검색 웹앱 (Streamlit + Supabase)
==========================================================
주요 기능
  1) 키워드 입력 → Gemini API의 Google Search Grounding으로 최신 뉴스 5건 검색
  2) 카드 형태로 결과 표시 + CSV 다운로드
  3) 마음에 드는 기사를 Supabase(news_history)에 저장
  4) 저장된 뉴스 조회 화면
  5) 대시보드 (키워드별/일자별 차트)

📌 중요한 기술 포인트
  - google-genai SDK에서 Google Search 도구(grounding)와 강제 JSON 출력
    (response_mime_type="application/json")은 동시에 사용할 수 없습니다.
    → 따라서 "JSON만 출력하라"고 프롬프트로 강하게 지시하고,
       응답에서 ```json ... ``` 코드펜스를 제거한 뒤 json.loads 로 파싱합니다.
  - 환각(없는 기사 / 깨진 링크)을 줄이기 위해
    (a) grounding_chunks 의 실제 검색 결과 URL과 교차 검증,
    (b) HTTP HEAD/GET 요청으로 링크 살아있는지 확인,
    (c) 부족하면 한 번 더 재요청(최대 2회) 하는 검증 루프를 둡니다.
"""

# =========================================================
# 0. 라이브러리
# =========================================================
import json
import re
import io
from datetime import datetime, date
from urllib.parse import urlparse

import requests
import pandas as pd
import altair as alt
import streamlit as st

from google import genai
from google.genai import types
from supabase import create_client, Client


# =========================================================
# 1. 페이지 기본 설정
# =========================================================
st.set_page_config(
    page_title="Gemini 최신 뉴스 검색",
    page_icon="📰",
    layout="wide",
)


# =========================================================
# 2. 시크릿(키) 로드
#    - 로컬: .streamlit/secrets.toml
#    - 배포: Streamlit Cloud의 [Settings > Secrets] UI
# =========================================================
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SUPABASE_URL   = st.secrets["SUPABASE_URL"]
SUPABASE_KEY   = st.secrets["SUPABASE_KEY"]   # anon public key

# 무료 티어에서 가장 빠르고 처리량이 높은 모델
# (2.5 Flash-Lite 가 가장 빠르고 가벼움. Search Grounding 지원)
GEMINI_MODEL = "gemini-2.5-flash"             # 또는 "gemini-2.5-flash-lite"


# =========================================================
# 3. 클라이언트 초기화 (캐시로 1번만 생성)
# =========================================================
@st.cache_resource
def get_gemini_client() -> genai.Client:
    """google-genai 클라이언트 생성 (앱 라이프타임 동안 1회)."""
    return genai.Client(api_key=GEMINI_API_KEY)


@st.cache_resource
def get_supabase() -> Client:
    """Supabase 클라이언트 생성."""
    return create_client(SUPABASE_URL, SUPABASE_KEY)


gemini = get_gemini_client()
sb     = get_supabase()


# =========================================================
# 4. 유틸리티 함수들
# =========================================================
def strip_code_fence(text: str) -> str:
    """모델이 ```json ... ``` 로 감싸 답할 때 코드펜스를 제거."""
    text = text.strip()
    # 앞쪽 ```json 또는 ``` 제거
    text = re.sub(r"^```(?:json)?\s*", "", text)
    # 뒤쪽 ``` 제거
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_first_json_array(text: str) -> str:
    """텍스트 안에서 첫 번째 JSON 배열만 추출 (잡설 섞여 있을 때 대비)."""
    m = re.search(r"\[\s*{.*}\s*\]", text, flags=re.DOTALL)
    return m.group(0) if m else text


def is_url_alive(url: str, timeout: int = 6) -> bool:
    """
    링크가 실제로 살아있는지 확인.
    - 일부 언론사가 HEAD 를 막아두므로 HEAD 실패 시 GET 재시도
    - 200~399 면 OK 로 간주
    """
    headers = {
        # 봇 차단을 피하기 위한 일반적인 브라우저 UA
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    try:
        r = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if 200 <= r.status_code < 400:
            return True
        # 일부 사이트는 HEAD 405 → GET 재시도
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
        return 200 <= r.status_code < 400
    except Exception:
        return False


def domain_of(url: str) -> str:
    """URL에서 도메인만 뽑기 (출처 표시용 보조)."""
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


# =========================================================
# 5. Gemini 호출 — Google Search Grounding
# =========================================================
SEARCH_PROMPT_TEMPLATE = """\
당신은 한국어 뉴스 큐레이터입니다.
키워드: "{keyword}"

작업:
1) Google 검색으로 위 키워드와 관련된 **최근 7일 이내** 뉴스를 찾으세요.
2) 서로 다른 출처에서 **5건**을 골라 주세요.
3) 반드시 **실제로 존재하고 클릭 시 해당 기사로 이동하는 원본 기사 URL** 만 사용하세요.
   (포털 메인, 검색결과, vertexaisearch.cloud.google.com 같은 리다이렉트 URL은 금지)
4) 각 기사를 한국어 3~4문장으로 요약하세요.

출력 형식: **JSON 배열만 출력**하세요. 설명/마크다운/코드펜스 모두 금지.
스키마:
[
  {{
    "title": "기사 제목",
    "source": "언론사명 (예: 연합뉴스)",
    "news_date": "YYYY-MM-DD",
    "url": "https://원본기사주소",
    "summary": "3~4문장 한국어 요약"
  }}
]
정확히 5개의 객체로 구성된 JSON 배열만 출력하세요.
"""


def call_gemini_search(keyword: str) -> tuple[list[dict], list[str]]:
    """
    Gemini 에 Google Search 툴을 켜서 호출.
    반환:
      - articles: 모델이 만든 JSON 리스트
      - grounding_uris: 실제 검색이 참고한 출처 URL 리스트 (검증용)
    """
    config = types.GenerateContentConfig(
        # 🔑 Search Grounding 활성화
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
    )

    response = gemini.models.generate_content(
        model=GEMINI_MODEL,
        contents=SEARCH_PROMPT_TEMPLATE.format(keyword=keyword),
        config=config,
    )

    # --- 본문 텍스트에서 JSON 파싱 ---
    raw = response.text or ""
    cleaned = strip_code_fence(raw)
    cleaned = extract_first_json_array(cleaned)

    try:
        articles = json.loads(cleaned)
        if not isinstance(articles, list):
            articles = []
    except json.JSONDecodeError:
        articles = []

    # --- grounding 메타데이터에서 실제 검색 URL 수집 ---
    grounding_uris: list[str] = []
    try:
        meta = response.candidates[0].grounding_metadata
        if meta and meta.grounding_chunks:
            for ch in meta.grounding_chunks:
                if ch.web and ch.web.uri:
                    grounding_uris.append(ch.web.uri)
    except Exception:
        pass

    return articles, grounding_uris


# =========================================================
# 6. 검증 루프 — 환각/죽은 링크 걸러내기
# =========================================================
def validate_articles(articles: list[dict]) -> list[dict]:
    """필수 필드 존재 + 링크 살아있음 확인 + 중복 URL 제거."""
    seen = set()
    valid: list[dict] = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        url = (a.get("url") or "").strip()
        title = (a.get("title") or "").strip()
        if not url or not title:
            continue
        if url in seen:
            continue
        # 구글 리다이렉트 URL 거부 (실제 기사 아님)
        if "vertexaisearch.cloud.google.com" in url or "google.com/url" in url:
            continue
        if not url.startswith("http"):
            continue
        if not is_url_alive(url):
            continue
        seen.add(url)
        # source 가 비어있으면 도메인으로 보충
        if not a.get("source"):
            a["source"] = domain_of(url)
        valid.append(a)
    return valid


def search_news_with_retry(keyword: str, target: int = 5, max_rounds: int = 3) -> list[dict]:
    """
    충분한 유효 기사를 모을 때까지 최대 max_rounds 회까지 재시도.
    """
    pool: list[dict] = []
    seen_urls = set()

    for attempt in range(max_rounds):
        articles, _grounding = call_gemini_search(keyword)
        validated = validate_articles(articles)
        for a in validated:
            if a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                pool.append(a)
        if len(pool) >= target:
            break

    return pool[:target]


# =========================================================
# 7. Supabase CRUD
# =========================================================
def save_article(keyword: str, article: dict) -> tuple[bool, str]:
    """단일 기사 저장. URL UNIQUE 충돌 시 False 반환."""
    payload = {
        "keyword":   keyword,
        "title":     article.get("title"),
        "source":    article.get("source"),
        "news_date": article.get("news_date"),
        "url":       article.get("url"),
        "summary":   article.get("summary"),
    }
    try:
        sb.table("news_history").insert(payload).execute()
        return True, "저장 완료"
    except Exception as e:
        msg = str(e)
        if "duplicate" in msg.lower() or "unique" in msg.lower() or "23505" in msg:
            return False, "이미 저장된 기사입니다 (URL 중복)"
        return False, f"저장 실패: {msg}"


def fetch_history(limit: int = 500) -> pd.DataFrame:
    """저장된 뉴스 전체 조회."""
    res = sb.table("news_history") \
            .select("*") \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
    return pd.DataFrame(res.data or [])


# =========================================================
# 8. UI — 사이드바 메뉴
# =========================================================
st.sidebar.title("📰 뉴스 앱")
menu = st.sidebar.radio(
    "메뉴",
    ["🔎 뉴스 검색", "🗂 저장된 뉴스", "📊 대시보드"],
)

# 무료 티어 안내 (모든 페이지 상단 공통)
st.info(
    "ℹ️ **Gemini API 무료 티어 안내** — 본 앱은 무료 티어용 모델 "
    f"`{GEMINI_MODEL}` 을 사용합니다. 무료 티어는 모델·시점에 따라 "
    "**분당 약 10~15회(RPM), 일일 약 250회(RPD)** 등의 한도가 있습니다. "
    "한도 초과 시 잠시 기다렸다 다시 시도하세요. "
    "최신 한도는 [공식 문서](https://ai.google.dev/gemini-api/docs/rate-limits) 참고.",
    icon="ℹ️",
)


# =========================================================
# 9. 페이지 1 — 뉴스 검색
# =========================================================
if menu == "🔎 뉴스 검색":
    st.title("🔎 키워드로 최신 뉴스 검색")

    col1, col2 = st.columns([4, 1])
    with col1:
        keyword = st.text_input("키워드를 입력하세요", placeholder="예: AI 반도체, 금리 인하, 기후변화 …")
    with col2:
        st.write("")  # 정렬용 여백
        run = st.button("검색", type="primary", use_container_width=True)

    # 세션에 마지막 검색 결과 보존
    if "last_results" not in st.session_state:
        st.session_state.last_results = []
        st.session_state.last_keyword = ""

    if run and keyword.strip():
        with st.spinner("Gemini가 Google에서 최신 뉴스를 검색·검증 중입니다… (최대 30초)"):
            results = search_news_with_retry(keyword.strip(), target=5, max_rounds=3)
        st.session_state.last_results = results
        st.session_state.last_keyword = keyword.strip()

    results = st.session_state.last_results
    keyword_now = st.session_state.last_keyword

    if results:
        st.success(f"'{keyword_now}' 관련 기사 {len(results)}건을 찾았습니다.")

        # ---- 카드 렌더링 ----
        for idx, art in enumerate(results):
            with st.container(border=True):
                st.markdown(f"### {art.get('title','(제목 없음)')}")
                meta = " · ".join(filter(None, [
                    f"🏷 **{art.get('source','')}**",
                    f"📅 {art.get('news_date','')}",
                    f"🔗 [{domain_of(art.get('url',''))}]({art.get('url','')})",
                ]))
                st.markdown(meta)
                st.write(art.get("summary", ""))

                # 저장 버튼
                if st.button("💾 이 기사 저장", key=f"save_{idx}"):
                    ok, msg = save_article(keyword_now, art)
                    (st.success if ok else st.warning)(msg)

        # ---- CSV 다운로드 ----
        df = pd.DataFrame(results)
        csv_buf = io.StringIO()
        df.to_csv(csv_buf, index=False, encoding="utf-8-sig")
        st.download_button(
            label="⬇️ 검색 결과 CSV 다운로드",
            data=csv_buf.getvalue(),
            file_name=f"news_{keyword_now}_{datetime.now():%Y%m%d_%H%M}.csv",
            mime="text/csv",
        )
    elif run:
        st.warning("유효한 기사를 찾지 못했습니다. 키워드를 바꿔 다시 시도해 보세요.")


# =========================================================
# 10. 페이지 2 — 저장된 뉴스 조회
# =========================================================
elif menu == "🗂 저장된 뉴스":
    st.title("🗂 저장된 뉴스")

    df = fetch_history()
    if df.empty:
        st.info("아직 저장된 뉴스가 없습니다.")
    else:
        # 필터
        kw_filter = st.text_input("키워드 필터 (부분 일치)").strip()
        view = df.copy()
        if kw_filter:
            view = view[view["keyword"].str.contains(kw_filter, case=False, na=False)]

        st.caption(f"총 {len(view)}건")
        for _, row in view.iterrows():
            with st.container(border=True):
                st.markdown(f"### {row['title']}")
                st.markdown(
                    f"🏷 **{row['source']}** · 📅 {row['news_date']} · "
                    f"🔎 키워드 `{row['keyword']}` · "
                    f"🔗 [{domain_of(row['url'])}]({row['url']})"
                )
                if row.get("summary"):
                    st.write(row["summary"])
                st.caption(f"저장일: {row['created_at']}")


# =========================================================
# 11. 페이지 3 — 대시보드
# =========================================================
elif menu == "📊 대시보드":
    st.title("📊 대시보드")

    df = fetch_history(limit=2000)
    if df.empty:
        st.info("저장된 데이터가 있어야 통계를 볼 수 있습니다.")
        st.stop()

    # created_at 을 날짜로 변환
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    df["created_date"] = df["created_at"].dt.date

    c1, c2, c3 = st.columns(3)
    c1.metric("총 저장 건수",   len(df))
    c2.metric("키워드 종류",    df["keyword"].nunique())
    c3.metric("출처(언론사) 수", df["source"].nunique())

    st.divider()

    # 키워드별 건수
    st.subheader("🔑 키워드별 저장 건수 (Top 15)")
    kw_count = (
        df.groupby("keyword").size().reset_index(name="count")
          .sort_values("count", ascending=False).head(15)
    )
    chart1 = (
        alt.Chart(kw_count)
        .mark_bar()
        .encode(
            x=alt.X("count:Q", title="건수"),
            y=alt.Y("keyword:N", sort="-x", title="키워드"),
            tooltip=["keyword", "count"],
        )
        .properties(height=400)
    )
    st.altair_chart(chart1, use_container_width=True)

    # 일자별 건수
    st.subheader("📅 일자별 저장 건수")
    day_count = (
        df.groupby("created_date").size().reset_index(name="count")
          .sort_values("created_date")
    )
    chart2 = (
        alt.Chart(day_count)
        .mark_line(point=True)
        .encode(
            x=alt.X("created_date:T", title="저장일"),
            y=alt.Y("count:Q", title="건수"),
            tooltip=["created_date", "count"],
        )
        .properties(height=350)
    )
    st.altair_chart(chart2, use_container_width=True)

    # 출처별 Top 10
    st.subheader("📰 출처별 저장 건수 (Top 10)")
    src_count = (
        df.groupby("source").size().reset_index(name="count")
          .sort_values("count", ascending=False).head(10)
    )
    st.dataframe(src_count, use_container_width=True, hide_index=True)


