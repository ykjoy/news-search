"""
==========================================================
📰 Gemini 최신 뉴스 검색 웹앱 (Streamlit + Supabase) — 1콜 빠른 버전
==========================================================
설계 핵심
  • 검색 1건당 Gemini API 호출은 정확히 1회 (무료 티어 일일 한도 절약)
  • 모델이 만든 URL은 신뢰하지 않음. grounding_chunks 의 실제 출처 URL만 사용.
  • 리다이렉트 해소(google → 실제 기사)와 응답 200 검증을 병렬로 수행.
  • 제목은 URL 슬러그에서, 출처는 도메인 매핑에서, 요약은 Grounding 응답
    본문 텍스트에서 해당 URL 도메인이 언급된 문장 주변을 잘라 사용.
  • 5xx 발생 시 같은 모델로 짧게 재시도, 429/지속 5xx 시 다음 모델로 폴백.
  • 한국어 요약의 '~' 문자가 마크다운 취소선으로 해석되는 문제는
    표시 직전에 '\\~' 로 이스케이프하여 회피.
"""

# =========================================================
# 0. 라이브러리
# =========================================================
import json
import re
import io
import time
import concurrent.futures as cf
from datetime import datetime
from urllib.parse import urlparse

import requests
import pandas as pd
import altair as alt
import streamlit as st

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
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
# =========================================================
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SUPABASE_URL   = st.secrets["SUPABASE_URL"]
SUPABASE_KEY   = st.secrets["SUPABASE_KEY"]

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]


# =========================================================
# 3. 클라이언트 초기화
# =========================================================
@st.cache_resource
def get_gemini_client() -> genai.Client:
    return genai.Client(api_key=GEMINI_API_KEY)


@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


gemini = get_gemini_client()
sb     = get_supabase()


# =========================================================
# 4. 유틸리티
# =========================================================
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# 도메인 → 한국어 언론사명
SOURCE_MAP = {
    "yna.co.kr": "연합뉴스",
    "yonhapnewstv.co.kr": "연합뉴스TV",
    "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보",
    "donga.com": "동아일보",
    "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문",
    "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제",
    "sedaily.com": "서울경제",
    "edaily.co.kr": "이데일리",
    "mt.co.kr": "머니투데이",
    "fnnews.com": "파이낸셜뉴스",
    "kbs.co.kr": "KBS",
    "imnews.imbc.com": "MBC뉴스",
    "news.sbs.co.kr": "SBS",
    "ytn.co.kr": "YTN",
    "jtbc.co.kr": "JTBC",
    "news1.kr": "뉴스1",
    "newsis.com": "뉴시스",
    "naver.com": "네이버뉴스",
    "daum.net": "다음뉴스",
    "zdnet.co.kr": "ZDNet Korea",
    "etnews.com": "전자신문",
    "bloter.net": "블로터",
}


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def guess_source(url: str) -> str:
    """도메인을 보고 언론사명 추정. 매핑에 없으면 도메인 그대로."""
    host = domain_of(url)
    for k, v in SOURCE_MAP.items():
        if host.endswith(k):
            return v
    return host


def escape_markdown(text: str) -> str:
    """'~' → '\\~' 로 이스케이프 (마크다운 취소선 방지)."""
    if not text:
        return ""
    return text.replace("~", r"\~")


def resolve_real_url(url: str, timeout: int = 4) -> str | None:
    """
    Google grounding 리다이렉트 URL을 따라가서 실제 기사 최종 URL 반환.
    - HEAD 우선, 막혀있으면 GET
    - 최종 도메인이 google* 이면 무효 처리
    """
    try:
        r = requests.head(
            url, headers=BROWSER_HEADERS, timeout=timeout,
            allow_redirects=True,
        )
        if not (200 <= r.status_code < 400):
            r = requests.get(
                url, headers=BROWSER_HEADERS, timeout=timeout,
                allow_redirects=True, stream=True,
            )
        if 200 <= r.status_code < 400 and r.url:
            host = urlparse(r.url).netloc
            if "google.com" in host or "googleusercontent" in host:
                return None
            return r.url
    except Exception:
        return None
    return None


def _slug_to_title(url: str) -> str:
    """URL 슬러그에서 백업용 제목 생성."""
    try:
        path = urlparse(url).path.rstrip("/")
        slug = path.split("/")[-1] if path else ""
        slug = re.sub(r"\.(html?|aspx?|php|do|jsp)$", "", slug)
        slug = re.sub(r"[-_]+", " ", slug).strip()
        return slug[:80] if slug else "(제목 없음)"
    except Exception:
        return "(제목 없음)"


def _split_into_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _extract_summary_for_url(body: str, url: str, n_sent: int = 3) -> str:
    """본문에서 해당 URL/도메인이 언급된 문장 주변을 뽑아 요약."""
    if not body:
        return "해당 키워드와 관련된 기사로 보입니다."
    host = domain_of(url)
    sentences = _split_into_sentences(body)
    if not sentences:
        return "해당 키워드와 관련된 기사로 보입니다."
    for i, s in enumerate(sentences):
        if (url and url in s) or (host and host in s):
            window = sentences[max(0, i - 1): i + n_sent]
            return " ".join(window)[:400]
    return " ".join(sentences[:n_sent])[:400]


# =========================================================
# 5. Gemini 호출 — 재시도 + 모델 폴백
# =========================================================
def _generate_with_retry(model: str, prompt: str,
                         config: types.GenerateContentConfig):
    """5xx만 짧게 재시도. 4xx(429 포함)는 호출자가 폴백 처리."""
    last_exc = None
    for attempt in range(2):
        try:
            return gemini.models.generate_content(
                model=model, contents=prompt, config=config
            )
        except genai_errors.ServerError as e:
            last_exc = e
            time.sleep(1.5 * (attempt + 1))
        except genai_errors.ClientError:
            raise
    raise last_exc


def _generate_with_fallback(prompt: str,
                            config: types.GenerateContentConfig):
    """후보 모델을 순차 시도. 5xx 또는 429면 다음 모델로 폴백."""
    last_error: Exception | None = None
    for model in GEMINI_MODELS:
        try:
            resp = _generate_with_retry(model, prompt, config)
            st.session_state["_used_model"] = model
            return resp
        except genai_errors.ServerError as e:
            last_error = e
            continue
        except genai_errors.ClientError as e:
            last_error = e
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg.upper():
                continue            # 쿼터 초과 → 다음 모델
            break                    # 그 외 4xx → 즉시 중단
    raise last_error if last_error else RuntimeError("Gemini 호출 실패")


# =========================================================
# 6. 메인 검색 파이프라인 (Gemini API 1회 호출)
# =========================================================
def search_news_with_retry(keyword: str, target: int = 5) -> list[dict]:
    """
    1) Search Grounding 으로 1회 호출 → 본문 텍스트 + grounding URL 수집
    2) URL을 병렬로 리다이렉트 해소 (실제 기사 URL)
    3) 본문 컨텍스트로 요약, 도메인으로 출처, 슬러그로 제목 생성
    """
    cache: dict = st.session_state.setdefault("_search_cache", {})
    if keyword in cache:
        return cache[keyword][:target]

    # --- 1. Gemini Grounding 호출 ---
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
    )
    prompt = (
        f'키워드 "{keyword}" 와 관련된 최근 7일 이내 한국어 뉴스 기사를 '
        f'서로 다른 출처에서 8건 검색해 주세요. '
        f'각 기사가 어떤 출처에서 무엇을 보도했는지 한국어 3문장으로 정리해 주세요. '
        f'각 정리 끝에 해당 기사의 출처 도메인을 "[도메인:example.com]" 형식으로 표기해 주세요.'
    )
    response = _generate_with_fallback(prompt, config)
    body = response.text or ""

    # grounding URL 수집
    ground_uris: list[str] = []
    try:
        meta = response.candidates[0].grounding_metadata
        if meta and meta.grounding_chunks:
            for ch in meta.grounding_chunks:
                if ch.web and ch.web.uri:
                    ground_uris.append(ch.web.uri)
    except Exception:
        pass

    if not ground_uris:
        return []

    # --- 2. 병렬 리다이렉트 해소 ---
    candidates = ground_uris[: target * 2 + 4]   # 여유분
    real_urls: list[str] = []
    seen: set[str] = set()
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for real in ex.map(resolve_real_url, candidates):
            if not real:
                continue
            key = real.split("?")[0].rstrip("/")
            if key in seen:
                continue
            if len(urlparse(real).path) < 5:
                continue
            seen.add(key)
            real_urls.append(real)
            if len(real_urls) >= target:
                break

    if not real_urls:
        return []

    # --- 3. 메타데이터 합성 ---
    articles: list[dict] = []
    for url in real_urls:
        articles.append({
            "title":     _slug_to_title(url),
            "source":    guess_source(url),
            "news_date": "",
            "url":       url,
            "summary":   _extract_summary_for_url(body, url),
        })

    cache[keyword] = articles
    return articles[:target]


# =========================================================
# 7. Supabase CRUD
# =========================================================
def save_article(keyword: str, article: dict) -> tuple[bool, str]:
    payload = {
        "keyword":   keyword,
        "title":     article.get("title"),
        "source":    article.get("source"),
        "news_date": article.get("news_date") or None,
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
    res = (
        sb.table("news_history")
          .select("*")
          .order("created_at", desc=True)
          .limit(limit)
          .execute()
    )
    return pd.DataFrame(res.data or [])


# =========================================================
# 8. UI — 사이드바 메뉴
# =========================================================
st.sidebar.title("📰 뉴스 앱")
menu = st.sidebar.radio(
    "메뉴",
    ["🔎 뉴스 검색", "🗂 저장된 뉴스", "📊 대시보드"],
)

used_model = st.session_state.get("_used_model")
if used_model:
    st.sidebar.caption(f"마지막 사용 모델: `{used_model}`")

if st.sidebar.button("🧹 검색 캐시 비우기"):
    st.session_state["_search_cache"] = {}
    st.sidebar.success("캐시를 비웠습니다.")

st.info(
    "ℹ️ **Gemini API 무료 티어 안내** — 본 앱은 검색 1건당 Gemini API를 **1회만** 호출합니다. "
    f"우선순위 모델: {', '.join(f'`{m}`' for m in GEMINI_MODELS)}. "
    "무료 티어는 모델별로 **하루 약 20~50회(Per Day)** 한도가 적용되며 시기에 따라 변동됩니다. "
    "한도 초과 시 자동으로 다음 모델로 폴백하며, 모두 소진되면 리셋까지 기다려야 합니다. "
    "[공식 한도 문서](https://ai.google.dev/gemini-api/docs/rate-limits)",
    icon="ℹ️",
)


# =========================================================
# 9. 페이지 1 — 뉴스 검색
# =========================================================
if menu == "🔎 뉴스 검색":
    st.title("🔎 키워드로 최신 뉴스 검색")

    col1, col2 = st.columns([4, 1])
    with col1:
        keyword = st.text_input(
            "키워드를 입력하세요",
            placeholder="예: AI 반도체, 금리 인하, 기후변화 …",
        )
    with col2:
        st.write("")
        run = st.button("검색", type="primary", use_container_width=True)

    if "last_results" not in st.session_state:
        st.session_state.last_results = []
        st.session_state.last_keyword = ""

    if run and keyword.strip():
        with st.spinner("Gemini 검색 + 링크 검증 중… (보통 5~10초)"):
            try:
                results = search_news_with_retry(keyword.strip(), target=5)
                st.session_state.last_results = results
                st.session_state.last_keyword = keyword.strip()
            except genai_errors.ServerError as e:
                st.error(
                    "🛠️ Gemini 서버가 일시적으로 응답하지 않습니다(5xx). "
                    "1~2분 뒤 다시 시도해 주세요.\n\n"
                    f"세부 메시지: `{e}`"
                )
            except genai_errors.ClientError as e:
                msg = str(e)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg.upper():
                    st.error(
                        "⏱️ 모든 후보 모델의 무료 티어 한도를 초과했습니다. "
                        "메시지에 표시된 시간만큼 기다린 뒤 다시 시도해 주세요."
                    )
                else:
                    st.error(f"요청 오류: {e}")
            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")

    results = st.session_state.last_results
    keyword_now = st.session_state.last_keyword

    if results:
        st.success(f"'{keyword_now}' 관련 검증된 기사 {len(results)}건을 찾았습니다.")

        for idx, art in enumerate(results):
            with st.container(border=True):
                st.markdown(
                    f"### {escape_markdown(art.get('title','(제목 없음)'))}"
                )
                meta = " · ".join(filter(None, [
                    f"🏷 **{escape_markdown(art.get('source',''))}**",
                    f"📅 {art.get('news_date','')}" if art.get("news_date") else "",
                    f"🔗 [{domain_of(art.get('url',''))}]({art.get('url','')})",
                ]))
                st.markdown(meta)
                st.markdown(escape_markdown(art.get("summary", "")))

                if st.button("💾 이 기사 저장", key=f"save_{idx}"):
                    ok, msg = save_article(keyword_now, art)
                    (st.success if ok else st.warning)(msg)

        df = pd.DataFrame(results)
        csv_buf = io.StringIO()
        df.to_csv(csv_buf, index=False, encoding="utf-8-sig")
        st.download_button(
            label="⬇️ 검색 결과 CSV 다운로드",
            data=csv_buf.getvalue(),
            file_name=f"news_{keyword_now}_{datetime.now():%Y%m%d_%H%M}.csv",
            mime="text/csv",
        )
    elif run and not st.session_state.last_results:
        st.warning(
            "유효한 기사를 찾지 못했습니다. 키워드를 더 구체적으로 바꿔 시도해 보세요."
        )


# =========================================================
# 10. 페이지 2 — 저장된 뉴스
# =========================================================
elif menu == "🗂 저장된 뉴스":
    st.title("🗂 저장된 뉴스")

    df = fetch_history()
    if df.empty:
        st.info("아직 저장된 뉴스가 없습니다.")
    else:
        kw_filter = st.text_input("키워드 필터 (부분 일치)").strip()
        view = df.copy()
        if kw_filter:
            view = view[view["keyword"].str.contains(kw_filter, case=False, na=False)]

        st.caption(f"총 {len(view)}건")
        for _, row in view.iterrows():
            with st.container(border=True):
                st.markdown(f"### {escape_markdown(row['title'])}")
                st.markdown(
                    f"🏷 **{escape_markdown(row.get('source') or '')}** · "
                    f"📅 {row.get('news_date') or ''} · "
                    f"🔎 키워드 `{row['keyword']}` · "
                    f"🔗 [{domain_of(row['url'])}]({row['url']})"
                )
                if row.get("summary"):
                    st.markdown(escape_markdown(row["summary"]))
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

    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    df["created_date"] = df["created_at"].dt.date

    c1, c2, c3 = st.columns(3)
    c1.metric("총 저장 건수",   len(df))
    c2.metric("키워드 종류",    df["keyword"].nunique())
    c3.metric("출처(언론사) 수", df["source"].nunique())

    st.divider()

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

    st.subheader("📰 출처별 저장 건수 (Top 10)")
    src_count = (
        df.groupby("source").size().reset_index(name="count")
          .sort_values("count", ascending=False).head(10)
    )
    st.dataframe(src_count, use_container_width=True, hide_index=True)

