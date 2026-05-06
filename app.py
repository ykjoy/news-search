"""
=========================================================
최신 뉴스 검색 웹앱 (Streamlit + Gemini Search Grounding + Supabase)
---------------------------------------------------------
[기능 요약]
1) 키워드 입력 → Gemini의 Google Search 도구로 최신 뉴스 5건 검색
2) 검색 결과를 카드 UI로 출력 + 체크박스로 선택 → Supabase 저장
3) 저장된 뉴스 조회 화면
4) 대시보드 (키워드별/일자별 차트)
5) CSV 다운로드 버튼

[중요 제약]
- Gemini 의 Google Search 도구는 'response_mime_type=application/json' 같은
  강제 JSON 포맷과 동시에 사용할 수 없습니다.
  → 그래서 프롬프트로 "JSON만 출력" 을 강하게 요구하고, 모델 응답 텍스트를
     직접 파싱(`json.loads`)합니다. 코드 펜스(```json ... ```)도 안전하게 제거합니다.
- 모델이 만들어내는 URL 은 가끔 깨지거나(404) Google redirect 라퍼로 감싸지므로,
  HEAD/GET 요청으로 실제 살아있는지 검증하고, 실패한 항목은 한 번 더 재검색합니다.
=========================================================
"""

import os
import re
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
import pandas as pd
import altair as alt
import streamlit as st

from google import genai
from google.genai import types
from supabase import create_client, Client


# =========================================================
# 0) 기본 설정 / 상수
# =========================================================

# 무료 티어에서 "가장 빠르고 처리량이 높은" 모델은 2.5 Flash-Lite (15 RPM, 1,000 RPD).
# Search Grounding 품질을 더 원하면 gemini-2.5-flash 로 바꿔도 됩니다(10 RPM).
DEFAULT_MODEL = "gemini-2.5-flash-lite"

# 한 번에 가져올 뉴스 개수
NUM_NEWS = 5

# URL 검증 시 타임아웃(초)과 정상으로 간주할 HTTP 상태 코드
URL_TIMEOUT = 6
OK_STATUS = {200, 201, 202, 203, 301, 302, 303, 307, 308}

# 페이지 전체 설정 (Streamlit 최상단에서 1회만 호출)
st.set_page_config(
    page_title="📰 최신 뉴스 검색기",
    page_icon="📰",
    layout="wide",
)


# =========================================================
# 1) 비밀키 로드 (Streamlit Secrets 우선, 없으면 환경변수)
# =========================================================

def _get_secret(key: str, default: str = "") -> str:
    """Streamlit Cloud의 Secrets 또는 로컬 환경변수에서 키를 읽어옵니다."""
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        # secrets.toml 이 없을 때도 에러나지 않도록 처리
        pass
    return os.environ.get(key, default)


GEMINI_API_KEY = _get_secret("GEMINI_API_KEY")
SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_KEY = _get_secret("SUPABASE_KEY")  # anon 또는 service_role 키


# =========================================================
# 2) 클라이언트 초기화 (캐싱: 매번 새로 만들지 않도록)
# =========================================================

@st.cache_resource(show_spinner=False)
def get_gemini_client() -> genai.Client:
    """google-genai 클라이언트 (앱 전체에서 1개만 생성)."""
    if not GEMINI_API_KEY:
        st.error("⚠️ GEMINI_API_KEY 가 설정되지 않았습니다. Secrets 를 확인하세요.")
        st.stop()
    return genai.Client(api_key=GEMINI_API_KEY)


@st.cache_resource(show_spinner=False)
def get_supabase_client() -> Client:
    """Supabase 클라이언트 (앱 전체에서 1개만 생성)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.warning("⚠️ SUPABASE_URL / SUPABASE_KEY 가 설정되지 않았습니다. 저장 기능이 비활성화됩니다.")
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================================================
# 3) Gemini 호출 - 검색 + JSON 파싱
# =========================================================

# 모델에게 항상 같은 형식으로 응답하도록 지시하는 시스템 프롬프트
SYSTEM_PROMPT = """\
당신은 한국어 뉴스 큐레이터입니다.
사용자가 준 키워드에 대한 '최신' 뉴스 기사를 Google Search 도구로 찾아서
반드시 아래 JSON 스키마에 맞춰 한국어로 답하세요.

규칙:
- 코드 펜스 없이 순수 JSON 만 출력합니다.
- 정확히 N개의 객체를 가진 JSON 배열을 반환합니다.
- 각 기사는 실제로 존재해야 하며, url 은 기사 원문 페이지 URL 이어야 합니다.
- vertexaisearch.cloud.google.com / google.com/url 같은 리다이렉트 링크 대신
  최종 언론사 도메인의 직링크를 사용하세요.
- 같은 기사를 중복해서 넣지 마세요.
- summary 는 한국어 3~4문장으로 작성합니다.

JSON 스키마:
[
  {
    "title": "기사 제목 (문자열)",
    "source": "언론사명 (예: 연합뉴스, Reuters)",
    "published_at": "YYYY-MM-DD 또는 'YYYY-MM-DD HH:MM' 형태. 정확한 날짜를 모르면 'unknown'",
    "url": "실제 기사 원문 URL (https://...)",
    "summary": "기사 핵심 내용을 담은 한국어 3~4문장 요약"
  }
]
"""


def _strip_code_fence(text: str) -> str:
    """모델이 ```json ... ``` 으로 감싸 보낸 경우 코드 펜스를 제거합니다."""
    text = text.strip()
    # ```json 또는 ``` 로 시작하는 펜스 제거
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_array(text: str):
    """본문에서 첫 번째 '[' ~ 마지막 ']' 구간을 잘라 JSON 으로 파싱합니다."""
    text = _strip_code_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 본문 안에 설명이 섞여 있을 수 있으니 배열만 추출 시도
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def call_gemini_search(keyword: str, model_name: str, n: int = NUM_NEWS) -> list[dict]:
    """
    Gemini 에 Google Search 도구를 켜고 호출합니다.
    ⚠️ Google Search 도구와 response_mime_type=application/json 은 함께 못 씁니다.
       그래서 프롬프트로 'JSON 만 출력' 을 강하게 지시하고 텍스트를 직접 파싱합니다.
    """
    client = get_gemini_client()

    # Search Grounding 도구 정의
    grounding_tool = types.Tool(google_search=types.GoogleSearch())

    # 생성 설정: tools 만 지정 (response_mime_type 같이 못 씀!)
    config = types.GenerateContentConfig(
        tools=[grounding_tool],
        temperature=0.2,                # 낮은 온도로 안정적인 JSON 형식 유지
        system_instruction=SYSTEM_PROMPT,
    )

    user_prompt = (
        f"키워드: '{keyword}'\n"
        f"위 키워드에 대한 가장 최근의 신뢰할 만한 뉴스 기사 {n}건을 찾아주세요. "
        f"반드시 정확히 {n}개의 객체로 구성된 JSON 배열만 출력하세요."
    )

    response = client.models.generate_content(
        model=model_name,
        contents=user_prompt,
        config=config,
    )

    raw_text = (response.text or "").strip()
    if not raw_text:
        raise RuntimeError("Gemini 응답이 비어있습니다.")

    items = _extract_json_array(raw_text)
    if not isinstance(items, list):
        raise RuntimeError("응답이 JSON 배열이 아닙니다.")

    # grounding_chunks 의 실제 URL/도메인을 보조 정보로 함께 보관 (URL 검증/대체 후보)
    grounding_urls = []
    try:
        gm = response.candidates[0].grounding_metadata
        if gm and gm.grounding_chunks:
            for ch in gm.grounding_chunks:
                if ch.web and ch.web.uri:
                    grounding_urls.append({"uri": ch.web.uri, "title": ch.web.title or ""})
    except Exception:
        pass

    # 너무 많이/적게 받은 경우 보정
    items = items[:n]
    return items, grounding_urls


# =========================================================
# 4) URL 유효성 검증 (실제 살아있는 기사인지 확인)
# =========================================================

def is_valid_news_url(url: str) -> bool:
    """기사 URL 이 실제로 응답하는지 HEAD → 실패 시 GET 으로 재시도해 확인합니다."""
    if not url or not url.startswith(("http://", "https://")):
        return False

    # Gemini 가 vertexaisearch 리다이렉트 URL 을 넣으면 원문이 아니므로 실패 처리
    host = urlparse(url).netloc.lower()
    if "vertexaisearch.cloud.google.com" in host or host.endswith("google.com"):
        # 단, news.google.com 같은 정상 뉴스 도메인은 허용
        if not host.startswith("news.google.com"):
            return False

    headers = {
        # 일부 언론사가 봇을 차단하므로 일반 브라우저 UA 로 위장
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    }
    try:
        r = requests.head(url, headers=headers, allow_redirects=True, timeout=URL_TIMEOUT)
        if r.status_code in OK_STATUS:
            return True
        # HEAD 를 막는 사이트들이 많아 GET 으로 재시도 (본문은 일부만 받음)
        r = requests.get(url, headers=headers, allow_redirects=True, timeout=URL_TIMEOUT, stream=True)
        return r.status_code in OK_STATUS
    except requests.RequestException:
        return False


def validate_and_repair(items: list[dict], keyword: str, model_name: str, max_retry: int = 1) -> list[dict]:
    """
    각 기사 URL 을 검증하고, 깨진 항목이 있으면 한 번 더 Gemini 에 재요청해 보완합니다.
    """
    valid, broken = [], []
    for it in items:
        if is_valid_news_url(it.get("url", "")):
            valid.append(it)
        else:
            broken.append(it)

    # 모자란 만큼 재요청 (최대 max_retry 회)
    retry = 0
    while len(valid) < NUM_NEWS and retry < max_retry:
        retry += 1
        try:
            extra_items, _ = call_gemini_search(
                keyword + " (직링크 우선, 깨지지 않은 URL)",
                model_name,
                n=NUM_NEWS - len(valid),
            )
        except Exception:
            break
        for it in extra_items:
            if it.get("url") in {v["url"] for v in valid}:
                continue  # 중복 제거
            if is_valid_news_url(it.get("url", "")):
                valid.append(it)
            if len(valid) >= NUM_NEWS:
                break

    return valid[:NUM_NEWS]


# =========================================================
# 5) Supabase 저장/조회 함수
# =========================================================

def save_articles(articles: list[dict], keyword: str) -> tuple[int, int]:
    """선택된 기사들을 news_history 테이블에 INSERT. (성공, 중복) 개수 반환."""
    sb = get_supabase_client()
    if sb is None:
        return 0, 0

    inserted, duplicated = 0, 0
    for a in articles:
        row = {
            "keyword": keyword,
            "title": a.get("title", ""),
            "source": a.get("source", ""),
            "published_at": a.get("published_at", ""),
            "url": a.get("url", ""),
            "summary": a.get("summary", ""),
            "searched_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            sb.table("news_history").insert(row).execute()
            inserted += 1
        except Exception as e:
            # url unique 제약에 걸리면 중복으로 카운트
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                duplicated += 1
            else:
                st.error(f"저장 실패: {e}")
    return inserted, duplicated


@st.cache_data(ttl=30, show_spinner=False)
def fetch_history(limit: int = 500) -> pd.DataFrame:
    """저장된 뉴스 이력을 데이터프레임으로 반환 (30초 캐시)."""
    sb = get_supabase_client()
    if sb is None:
        return pd.DataFrame()
    res = (
        sb.table("news_history")
        .select("*")
        .order("searched_at", desc=True)
        .limit(limit)
        .execute()
    )
    df = pd.DataFrame(res.data or [])
    if not df.empty:
        df["searched_at"] = pd.to_datetime(df["searched_at"], errors="coerce", utc=True)
    return df


# =========================================================
# 6) UI 헬퍼 - 카드 렌더링
# =========================================================

def render_card(idx: int, article: dict, with_checkbox: bool = True) -> bool:
    """기사 1건을 카드 UI 로 렌더링. 체크박스 선택 여부를 반환."""
    with st.container(border=True):
        st.markdown(f"### {idx}. {article.get('title', '(제목 없음)')}")
        meta_cols = st.columns([2, 2, 6])
        meta_cols[0].markdown(f"**📰 출처:** {article.get('source', '-')}")
        meta_cols[1].markdown(f"**🗓 날짜:** {article.get('published_at', '-')}")
        url = article.get("url", "")
        meta_cols[2].markdown(f"**🔗 [원문 링크 열기]({url})**" if url else "**🔗 -**")
        st.write(article.get("summary", ""))
        if with_checkbox:
            return st.checkbox(
                "💾 이 기사 저장하기",
                key=f"save_{idx}_{url}",
                value=False,
            )
    return False


# =========================================================
# 7) 메인 앱 - 사이드바 + 3개 탭
# =========================================================

st.title("📰 최신 뉴스 검색기")

# 무료 티어 안내 (요구사항 4번)
st.info(
    "ℹ️ **Gemini API 무료 티어 안내** — "
    "본 앱은 기본적으로 `gemini-2.5-flash-lite` 모델을 사용합니다. "
    "무료 티어 한도는 모델별로 다르며 대표적으로 **분당 15회(RPM), 일 1,000회(RPD)** 수준입니다. "
    "한도를 초과하면 `429 RESOURCE_EXHAUSTED` 에러가 발생하니, 잠시 기다렸다 다시 시도해 주세요. "
    "(최신 한도는 [공식 문서](https://ai.google.dev/gemini-api/docs/rate-limits) 참고)"
)

with st.sidebar:
    st.header("⚙️ 설정")
    model_name = st.selectbox(
        "Gemini 모델",
        options=["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"],
        index=0,
        help="무료 티어에서 가장 빠른 모델이 기본값입니다.",
    )
    st.caption("Search Grounding 은 강제 JSON(response_mime_type) 과 동시 사용 불가하여, 프롬프트로 JSON 형식을 유도합니다.")

tab_search, tab_saved, tab_dash = st.tabs(["🔍 검색", "📚 저장된 뉴스", "📊 대시보드"])


# ---------- (탭1) 검색 ----------
with tab_search:
    st.subheader("🔍 키워드로 최신 뉴스 검색")

    col_in, col_btn = st.columns([5, 1])
    keyword = col_in.text_input("검색 키워드", placeholder="예: 인공지능 반도체, 한국은행 기준금리, 손흥민 ...")
    do_search = col_btn.button("검색", type="primary", use_container_width=True)

    if do_search and keyword.strip():
        with st.spinner("Gemini 가 Google 에서 최신 기사를 검색하고 있습니다..."):
            try:
                items, _grounding = call_gemini_search(keyword.strip(), model_name, NUM_NEWS)
            except Exception as e:
                st.error(f"검색 실패: {e}")
                st.stop()

            with st.status("기사 URL 유효성 검증 중...", expanded=False) as status:
                items = validate_and_repair(items, keyword.strip(), model_name, max_retry=1)
                status.update(label=f"검증 완료: {len(items)}건", state="complete")

        if not items:
            st.warning("유효한 기사를 찾지 못했습니다. 키워드를 바꿔서 다시 시도해 주세요.")
        else:
            # 세션에 저장 (저장 버튼/CSV 다운로드에 재사용)
            st.session_state["last_keyword"] = keyword.strip()
            st.session_state["last_items"] = items

    # 결과 표시 (검색 직후 또는 새로고침 시 세션값 활용)
    items = st.session_state.get("last_items", [])
    last_keyword = st.session_state.get("last_keyword", "")

    if items:
        st.success(f"'{last_keyword}' 키워드로 {len(items)}건의 기사를 찾았습니다.")

        selected_flags = []
        for i, art in enumerate(items, start=1):
            selected_flags.append(render_card(i, art, with_checkbox=True))

        # 저장 버튼
        save_col, csv_col = st.columns([1, 1])
        with save_col:
            if st.button("✅ 선택한 기사 Supabase 에 저장", use_container_width=True):
                chosen = [art for art, flag in zip(items, selected_flags) if flag]
                if not chosen:
                    st.warning("저장할 기사를 1개 이상 체크해 주세요.")
                else:
                    inserted, duplicated = save_articles(chosen, last_keyword)
                    st.success(f"저장 완료! 신규 {inserted}건, 중복 {duplicated}건")
                    # 캐시 무효화 → 저장된 뉴스 탭에 즉시 반영
                    fetch_history.clear()

        # CSV 다운로드 버튼
        with csv_col:
            df_export = pd.DataFrame(items)
            df_export.insert(0, "keyword", last_keyword)
            csv_bytes = df_export.to_csv(index=False).encode("utf-8-sig")  # 엑셀 한글 깨짐 방지
            st.download_button(
                "⬇️ 검색 결과 CSV 다운로드",
                data=csv_bytes,
                file_name=f"news_{last_keyword}_{datetime.now():%Y%m%d_%H%M%S}.csv",
                mime="text/csv",
                use_container_width=True,
            )


# ---------- (탭2) 저장된 뉴스 ----------
with tab_saved:
    st.subheader("📚 저장된 뉴스 목록")

    df = fetch_history(limit=500)
    if df.empty:
        st.info("저장된 뉴스가 없습니다. '검색' 탭에서 기사를 저장해 보세요.")
    else:
        # 키워드 필터
        kw_options = ["(전체)"] + sorted(df["keyword"].dropna().unique().tolist())
        sel_kw = st.selectbox("키워드 필터", kw_options)
        view = df if sel_kw == "(전체)" else df[df["keyword"] == sel_kw]

        st.caption(f"총 {len(view)}건")
        # 보기 좋은 컬럼 순서
        show_cols = ["searched_at", "keyword", "title", "source", "published_at", "url", "summary"]
        st.dataframe(
            view[show_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("원문 링크"),
                "searched_at": st.column_config.DatetimeColumn("저장 시각", format="YYYY-MM-DD HH:mm"),
            },
        )


# ---------- (탭3) 대시보드 ----------
with tab_dash:
    st.subheader("📊 대시보드")

    df = fetch_history(limit=2000)
    if df.empty:
        st.info("표시할 데이터가 없습니다.")
    else:
        # 상단 KPI
        c1, c2, c3 = st.columns(3)
        c1.metric("총 저장 건수", f"{len(df):,}")
        c2.metric("고유 키워드 수", f"{df['keyword'].nunique():,}")
        c3.metric("최근 7일 저장",
                  f"{(df['searched_at'] >= pd.Timestamp.utcnow() - pd.Timedelta(days=7)).sum():,}")

        st.divider()

        # 1) 키워드별 저장 건수 (Top 15)
        st.markdown("#### 🔠 키워드별 저장 건수 (Top 15)")
        kw_count = (
            df.groupby("keyword").size().reset_index(name="count")
            .sort_values("count", ascending=False).head(15)
        )
        chart_kw = (
            alt.Chart(kw_count)
            .mark_bar()
            .encode(
                x=alt.X("count:Q", title="건수"),
                y=alt.Y("keyword:N", sort="-x", title="키워드"),
                tooltip=["keyword", "count"],
            )
            .properties(height=400)
        )
        st.altair_chart(chart_kw, use_container_width=True)

        # 2) 일자별 저장 건수
        st.markdown("#### 📅 일자별 저장 건수")
        daily = (
            df.assign(date=df["searched_at"].dt.tz_convert("Asia/Seoul").dt.date)
            .groupby("date").size().reset_index(name="count")
        )
        chart_day = (
            alt.Chart(daily)
            .mark_line(point=True)
            .encode(
                x=alt.X("date:T", title="날짜"),
                y=alt.Y("count:Q", title="건수"),
                tooltip=["date", "count"],
            )
            .properties(height=350)
        )
        st.altair_chart(chart_day, use_container_width=True)

        # 3) 출처(언론사)별 비중
        st.markdown("#### 🏢 출처별 비중 (Top 10)")
        src_count = (
            df.assign(source=df["source"].fillna("(미상)"))
            .groupby("source").size().reset_index(name="count")
            .sort_values("count", ascending=False).head(10)
        )
        st.bar_chart(src_count, x="source", y="count", use_container_width=True)

