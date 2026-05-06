# =====================================================================
# app.py
# 최신 뉴스 검색 & 저장 웹앱 (Gemini Search Grounding + Supabase)
# ---------------------------------------------------------------------
# [동작 흐름]
#   1) 사용자가 키워드 입력 → Gemini API의 Google Search 도구로 최신 뉴스 검색
#   2) 결과를 카드 형태로 화면에 표시 + CSV 다운로드 가능
#   3) 마음에 드는 기사를 체크박스로 선택해서 Supabase DB에 저장
#   4) 별도 탭에서 저장된 뉴스 조회
#   5) 별도 탭에서 키워드별/일자별 통계 대시보드
#
# [중요한 기술 포인트 - 초보자가 꼭 알아둘 것]
#   • Gemini의 Search Grounding 도구는 "강제 JSON 출력 모드"와 동시에
#     사용할 수 없습니다. 그래서 프롬프트로 JSON을 요청하고,
#     응답 텍스트에서 JSON을 직접 파싱하는 전략을 씁니다.
#   • 모델이 가짜 URL을 만들어내는 것(hallucination)을 막기 위해,
#     ① grounding_metadata(검색 근거)에 실제로 등장한 URL인지 확인하고
#     ② HTTP 요청으로 페이지가 살아있는지(200~399 응답) 한 번 더 검증합니다.
# =====================================================================

# ---------------------------------------------------------------------
# 0. 라이브러리 임포트
# ---------------------------------------------------------------------
import json                       # 모델 응답에서 JSON 문자열을 파싱
import re                         # 응답에서 ```json ... ``` 코드펜스 등을 정리
import time                       # 재시도 사이의 대기 시간
from datetime import datetime, date
from urllib.parse import urlparse, urlunparse, unquote
from typing import Optional       # 함수 반환 타입 힌트 작성용

import requests                   # URL 유효성을 HTTP로 검증
import pandas as pd               # 표/CSV/차트 데이터 핸들링
import plotly.express as px       # 대시보드 차트
import streamlit as st            # 웹앱 프레임워크

# Google Gen AI SDK (최신 google-genai 패키지)
from google import genai
from google.genai import types

# Supabase 파이썬 클라이언트
from supabase import create_client, Client


# ---------------------------------------------------------------------
# 1. 페이지 기본 설정
#    - st.set_page_config() 는 반드시 첫 Streamlit 명령이어야 합니다.
# ---------------------------------------------------------------------
st.set_page_config(
    page_title="📰 AI 뉴스 검색기",
    page_icon="📰",
    layout="wide",                # 'wide' = 화면을 가로로 넓게 사용
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------
# 2. 비밀 키 / 환경 설정 로드
#    - Streamlit Cloud 에서는 secrets.toml 형태로 입력
#    - 로컬에서는 .streamlit/secrets.toml 파일에 동일하게 작성
#    - 즉, 별도의 .env 파일이 필요 없습니다.
# ---------------------------------------------------------------------
# 사용할 모델: 무료 티어에서 가장 빠르고 처리량이 높은 모델
#   gemini-2.5-flash-lite : 15 RPM / 1,000 RPD (가장 높은 일일 한도)
#   참고: 검색 결과 품질을 더 중시하면 gemini-2.5-flash 로 변경하세요.
GEMINI_MODEL = "gemini-2.5-flash-lite"

# secrets에서 키 읽기 (없으면 친절한 에러 메시지 표시)
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except KeyError as e:
    st.error(
        f"❌ 비밀키가 설정되지 않았습니다: {e}\n\n"
        "**.streamlit/secrets.toml** 또는 Streamlit Cloud 의 Secrets 메뉴에 "
        "`GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY` 를 등록하세요."
    )
    st.stop()  # 비밀키가 없으면 앱 실행 중단


# ---------------------------------------------------------------------
# 3. 클라이언트 객체 초기화
#    - @st.cache_resource 는 무거운 객체(클라이언트)를 한 번만 만들고
#      재사용하도록 캐시합니다. 매 실행마다 다시 만들면 느려지기 때문.
# ---------------------------------------------------------------------
@st.cache_resource
def get_gemini_client() -> genai.Client:
    """Gemini API 클라이언트 생성 (앱 전체에서 1회만 만들어짐)."""
    return genai.Client(api_key=GEMINI_API_KEY)


@st.cache_resource
def get_supabase_client() -> Client:
    """Supabase 클라이언트 생성 (앱 전체에서 1회만 만들어짐)."""
    return create_client(SUPABASE_URL, SUPABASE_KEY)


gemini_client = get_gemini_client()
supabase = get_supabase_client()


# =====================================================================
# 4. 핵심 기능 함수들
# =====================================================================

def extract_json_from_text(text: str) -> Optional[list]:
    """
    Gemini가 반환한 응답 텍스트에서 JSON 배열을 추출합니다.

    Search Grounding 사용 시에는 강제 JSON 출력(response_mime_type)을
    쓸 수 없기 때문에, 프롬프트로 JSON을 요청한 뒤 직접 파싱해야 합니다.
    모델이 ```json ... ``` 같은 코드펜스나 부가 설명을 섞을 수 있어
    아래 단계로 안전하게 추출합니다.
    """
    if not text:
        return None

    # (1) ```json ... ``` 또는 ``` ... ``` 코드펜스 제거
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = fence_match.group(1) if fence_match else text

    # (2) 첫 '['부터 마지막 ']' 까지를 JSON 배열 후보로 자르기
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    json_str = candidate[start:end + 1]

    # (3) 진짜 JSON으로 파싱
    try:
        parsed = json.loads(json_str)
        if isinstance(parsed, list):
            return parsed
        return None
    except json.JSONDecodeError:
        return None


def collect_grounding_urls(response) -> set:
    """
    Gemini 응답의 grounding_metadata 에서 검색 도구가 실제로
    참조한 URL 들을 추출해 set 으로 반환합니다.

    이 URL 목록에 들어있지 않은 링크는 모델이 만들어낸 가짜일 가능성이
    높으므로, 1차 검증 기준으로 사용합니다.
    """
    urls: set = set()
    try:
        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            gm = getattr(cand, "grounding_metadata", None)
            if not gm:
                continue
            chunks = getattr(gm, "grounding_chunks", None) or []
            for ch in chunks:
                web = getattr(ch, "web", None)
                if web and getattr(web, "uri", None):
                    urls.add(web.uri)
    except Exception:
        # 메타데이터 파싱 실패해도 앱은 계속 동작해야 함
        pass
    return urls


def normalize_url(u: str) -> str:
    """URL 비교를 위해 fragment(#...) 와 끝의 '/' 를 정리."""
    try:
        p = urlparse(u.strip())
        # fragment(#...) 제거, 마지막 '/' 한 개 제거
        cleaned = urlunparse((p.scheme, p.netloc, p.path.rstrip("/"),
                              p.params, p.query, ""))
        return cleaned.lower()
    except Exception:
        return u.strip().lower()


def verify_url_alive(url: str, timeout: int = 6) -> bool:
    """
    실제로 그 URL 이 살아있는 페이지인지 HTTP 요청으로 확인.
    - HEAD 요청을 먼저 시도하고, 메서드를 막는 사이트가 많아
      403/405 등이 나오면 GET 으로 재시도.
    - 2xx 또는 3xx 응답이면 살아있는 것으로 판단.
    """
    headers = {
        # 일부 뉴스 사이트는 봇 차단을 위해 User-Agent 를 검사함
        "User-Agent": (
            "Mozilla/5.0 (compatible; NewsApp/1.0; "
            "+https://streamlit.io)"
        )
    }
    try:
        r = requests.head(url, headers=headers, timeout=timeout,
                          allow_redirects=True)
        if 200 <= r.status_code < 400:
            return True
        # HEAD 차단 사이트 대응
        if r.status_code in (403, 405, 501):
            r = requests.get(url, headers=headers, timeout=timeout,
                             allow_redirects=True, stream=True)
            r.close()
            return 200 <= r.status_code < 400
        return False
    except requests.RequestException:
        return False


def build_news_prompt(keyword: str, count: int = 5) -> str:
    """뉴스 검색 프롬프트 생성. 모델에게 강하게 'JSON만 출력' 요청."""
    today = date.today().isoformat()
    return f"""오늘 날짜는 {today} 입니다.
"{keyword}" 키워드와 관련된 **최신 뉴스 기사 {count}건**을 Google 검색을 통해 찾아주세요.

[필수 규칙]
1. 반드시 Google 검색 도구를 사용하여 **실제로 존재하는** 기사만 가져오세요.
2. 가능한 한 최근 7일 이내의 기사를 우선 선택하세요.
3. URL 은 검색 결과에 등장한 **원문 그대로의 정확한 링크**여야 합니다.
   짧게 줄이거나 추측해서 만들지 마세요.
4. URL 이 의심스럽거나 확실치 않으면 그 기사는 결과에서 제외하세요.
5. 출력은 **JSON 배열 한 개만**, 다른 설명/머리말/코드펜스 없이 출력하세요.

[JSON 스키마 - 각 항목]
{{
  "title": "기사 제목 (원어 그대로)",
  "source": "언론사명 (예: 연합뉴스, Reuters)",
  "news_date": "YYYY-MM-DD",
  "url": "https://...   (검색 결과의 실제 원문 URL)",
  "summary": "기사 핵심 내용을 한국어 3~4문장으로 정리"
}}

위 규칙을 지켜 정확히 {count}건을 JSON 배열로만 출력하세요."""


def search_news_with_gemini(keyword: str, target_count: int = 5) -> list:
    """
    Gemini + Google Search Grounding 으로 뉴스 검색 → 검증 → 반환.

    [검증 절차 - 가짜 링크 방지]
      ① 모델이 반환한 URL 이 grounding_metadata 의 실제 검색 URL
         도메인과 일치하는지 확인
      ② 그래도 부족하면 HTTP 요청으로 페이지 생존 확인
      ③ 결과가 부족하면 1회 재시도(추가 검색)
    """
    # Search Grounding 도구 정의
    grounding_tool = types.Tool(google_search=types.GoogleSearch())
    config = types.GenerateContentConfig(
        tools=[grounding_tool],
        # 사실성 우선이므로 temperature 낮게
        temperature=0.2,
    )

    verified_articles: list = []
    seen_urls: set = set()
    attempts = 0
    max_attempts = 2  # 최대 2회까지 호출 (1회 + 부족 시 재시도)

    while len(verified_articles) < target_count and attempts < max_attempts:
        attempts += 1
        # 부족분만큼만 더 요청
        need = target_count - len(verified_articles)
        prompt = build_news_prompt(keyword, count=max(need + 2, 5))
        # 재시도 시에는 이미 본 URL을 제외하라고 알려줌
        if attempts > 1 and seen_urls:
            prompt += (
                "\n\n[추가 지시] 다음 URL 들은 이미 보았으므로 제외하세요:\n- "
                + "\n- ".join(list(seen_urls)[:10])
            )

        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
        except Exception as e:
            st.error(f"Gemini API 호출 중 오류: {e}")
            break

        # 1차 검증: grounding 메타데이터에서 실제 검색 URL 도메인 추출
        grounding_urls = collect_grounding_urls(response)
        grounding_domains = set()
        for u in grounding_urls:
            try:
                grounding_domains.add(urlparse(u).netloc.lower())
            except Exception:
                pass

        # 응답 텍스트에서 JSON 추출
        articles = extract_json_from_text(response.text or "") or []

        # 각 기사를 검증
        for art in articles:
            if len(verified_articles) >= target_count:
                break
            if not isinstance(art, dict):
                continue

            url = (art.get("url") or "").strip()
            title = (art.get("title") or "").strip()
            if not url or not title:
                continue

            norm = normalize_url(url)
            if norm in seen_urls:
                continue  # 중복 제거

            # ② HTTP 검증: 실제로 살아있는 페이지인가?
            if not verify_url_alive(url):
                continue

            # 1차 검증 - grounding 메타에 같은 도메인이 있으면 신뢰도↑
            try:
                art_domain = urlparse(url).netloc.lower()
            except Exception:
                art_domain = ""
            # grounding 메타가 없는 경우(드뭄)에는 HTTP 검증만으로 통과
            if grounding_domains and art_domain not in grounding_domains:
                # 모델이 메타에 없는 도메인을 만들어냈을 가능성 → 제외
                continue

            # 통과! 정규화된 데이터로 저장
            verified_articles.append({
                "title": title,
                "source": (art.get("source") or "").strip() or "(미상)",
                "news_date": (art.get("news_date") or "").strip(),
                "url": url,
                "summary": (art.get("summary") or "").strip(),
            })
            seen_urls.add(norm)

        # 살짝 대기 (Rate Limit 방지)
        if len(verified_articles) < target_count and attempts < max_attempts:
            time.sleep(1.0)

    return verified_articles[:target_count]


# =====================================================================
# 5. Supabase 헬퍼 함수
# =====================================================================

def save_article_to_db(keyword: str, article: dict) -> tuple[bool, str]:
    """
    뉴스 기사를 news_history 테이블에 저장.
    URL 컬럼이 UNIQUE 제약이므로 중복은 DB가 막아줍니다.
    Returns: (성공여부, 메시지)
    """
    try:
        row = {
            "keyword": keyword,
            "title": article["title"],
            "source": article.get("source", ""),
            # 날짜가 비어있을 수도 있으니 None 으로 변환
            "news_date": article.get("news_date") or None,
            "url": article["url"],
            "summary": article.get("summary", ""),
        }
        supabase.table("news_history").insert(row).execute()
        return True, "저장 완료"
    except Exception as e:
        msg = str(e)
        if "duplicate" in msg.lower() or "unique" in msg.lower():
            return False, "이미 저장된 기사입니다 (URL 중복)"
        return False, f"저장 실패: {msg}"


def fetch_saved_news(limit: int = 1000) -> pd.DataFrame:
    """저장된 뉴스 전체 조회 → DataFrame 으로 반환."""
    try:
        res = (supabase.table("news_history")
               .select("*")
               .order("created_at", desc=True)
               .limit(limit)
               .execute())
        data = res.data or []
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"DB 조회 실패: {e}")
        return pd.DataFrame()


def delete_article(article_id: int) -> bool:
    """저장된 기사 1건 삭제."""
    try:
        supabase.table("news_history").delete().eq("id", article_id).execute()
        return True
    except Exception as e:
        st.error(f"삭제 실패: {e}")
        return False


# =====================================================================
# 6. UI - 사이드바 & 상단 안내
# =====================================================================

# 사이드바 - 사용법 안내
with st.sidebar:
    st.title("📰 AI 뉴스 검색기")
    st.markdown(
        """
        **사용 방법**
        1. 키워드를 입력하고 검색
        2. 마음에 드는 기사를 ✅ 체크
        3. **선택 항목 저장** 버튼 클릭
        4. *저장된 뉴스* 탭에서 다시 보기
        5. *대시보드* 탭에서 통계 확인
        """
    )
    st.divider()
    st.caption(f"사용 모델: `{GEMINI_MODEL}`")
    st.caption("Powered by Gemini + Supabase")

# 상단 헤더
st.title("📰 AI 기반 최신 뉴스 검색")

# 무료 티어 한도 안내 (요구사항 4)
st.info(
    "💡 **Gemini API 무료 티어 한도 안내**  \n"
    f"현재 사용 모델: **{GEMINI_MODEL}**  \n"
    "• 분당 요청 수: 약 **15 RPM**  \n"
    "• 일일 요청 수: 약 **1,000 RPD**  \n"
    "• 분당 토큰 수: 약 **250,000 TPM**  \n"
    "한도를 초과하면 일시적으로 검색이 제한될 수 있어요. "
    "Google AI Studio 의 정책에 따라 수치는 변동될 수 있습니다."
)


# =====================================================================
# 7. 탭 구성: 검색 / 저장된 뉴스 / 대시보드
# =====================================================================
tab_search, tab_saved, tab_dashboard = st.tabs(
    ["🔍 뉴스 검색", "💾 저장된 뉴스", "📊 대시보드"]
)


# ---------------------------------------------------------------------
# 7-1. 검색 탭
# ---------------------------------------------------------------------
with tab_search:
    st.subheader("🔍 키워드로 최신 뉴스 검색")

    # 검색 폼: Enter 키로도 제출되도록 form 사용
    with st.form("search_form", clear_on_submit=False):
        col_kw, col_btn = st.columns([4, 1])
        with col_kw:
            keyword_input = st.text_input(
                "검색 키워드",
                placeholder="예) 인공지능, 반도체, 비트코인, 한국은행 금리",
                label_visibility="collapsed",
            )
        with col_btn:
            submitted = st.form_submit_button("🔎 검색", use_container_width=True)

    # 세션 상태에 검색 결과 보관 (페이지가 다시 그려져도 유지됨)
    if "search_results" not in st.session_state:
        st.session_state.search_results = []
    if "search_keyword" not in st.session_state:
        st.session_state.search_keyword = ""

    # 검색 실행
    if submitted and keyword_input.strip():
        with st.spinner("Gemini가 Google 검색으로 뉴스를 모으고 있어요... (10~30초)"):
            results = search_news_with_gemini(keyword_input.strip(), target_count=5)
        st.session_state.search_results = results
        st.session_state.search_keyword = keyword_input.strip()

        if not results:
            st.warning(
                "검증된 기사를 찾지 못했어요. 다른 키워드로 시도하거나 잠시 후 "
                "다시 검색해 주세요."
            )

    # 결과 표시
    results = st.session_state.search_results
    keyword = st.session_state.search_keyword

    if results:
        st.success(
            f"✅ 키워드 **‘{keyword}’** 에 대해 검증된 기사 **{len(results)}건** 을 찾았습니다."
        )
        st.markdown("---")

        # 카드 형태 출력 + 저장용 체크박스
        # 각 카드를 컬럼 박스 컨테이너로 감싸 깔끔하게 표시
        selected_indices: list[int] = []

        for idx, article in enumerate(results):
            with st.container(border=True):
                # 좌(체크박스) + 우(내용)
                left, right = st.columns([0.05, 0.95])
                with left:
                    checked = st.checkbox(
                        " ", key=f"chk_{idx}", label_visibility="collapsed"
                    )
                    if checked:
                        selected_indices.append(idx)
                with right:
                    # 제목 (마크다운 헤더처럼)
                    st.markdown(f"### {article['title']}")
                    # 메타 정보 한 줄
                    meta_parts = []
                    if article.get("source"):
                        meta_parts.append(f"📰 **{article['source']}**")
                    if article.get("news_date"):
                        meta_parts.append(f"📅 {article['news_date']}")
                    if meta_parts:
                        st.caption(" · ".join(meta_parts))
                    # 요약문
                    st.write(article.get("summary", ""))
                    # 원본 링크 (별도 줄, 새 탭으로 열기)
                    st.markdown(
                        f"🔗 [원본 기사 보기]({article['url']})",
                        unsafe_allow_html=False,
                    )

        st.markdown("---")

        # 저장 버튼 + CSV 다운로드 버튼을 같은 줄에
        col_save, col_csv = st.columns([1, 1])

        with col_save:
            if st.button(
                f"💾 선택한 {len(selected_indices)}건 저장하기",
                disabled=(len(selected_indices) == 0),
                use_container_width=True,
                type="primary",
            ):
                ok_count, fail_count, dup_count = 0, 0, 0
                for i in selected_indices:
                    success, msg = save_article_to_db(keyword, results[i])
                    if success:
                        ok_count += 1
                    elif "중복" in msg:
                        dup_count += 1
                    else:
                        fail_count += 1
                # 결과 요약
                if ok_count:
                    st.success(f"✅ {ok_count}건 저장 완료!")
                if dup_count:
                    st.info(f"ℹ️ {dup_count}건은 이미 저장되어 건너뛰었습니다.")
                if fail_count:
                    st.error(f"❌ {fail_count}건 저장 실패")

        with col_csv:
            # CSV 다운로드 버튼 (요구사항 3-1)
            df_csv = pd.DataFrame(results)
            df_csv.insert(0, "keyword", keyword)
            # BOM 추가 → 엑셀에서 한글이 깨지지 않게
            csv_bytes = df_csv.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "⬇️ 검색 결과 CSV 다운로드",
                data=csv_bytes,
                file_name=f"news_{keyword}_{date.today().isoformat()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

    elif submitted:
        # 검색은 눌렀지만 결과가 0건인 경우
        pass  # 위에서 이미 warning 표시함
    else:
        # 첫 진입 안내
        st.markdown(
            """
            👋 **사용 팁**
            - 키워드는 구체적일수록 좋아요. (예: '한국은행 금리 인하')
            - 결과의 **원본 링크**는 모두 살아있는 페이지인지 자동 검증됩니다.
            - 처리에 보통 10~30초 정도 걸립니다 (검색 + URL 검증).
            """
        )


# ---------------------------------------------------------------------
# 7-2. 저장된 뉴스 탭
# ---------------------------------------------------------------------
with tab_saved:
    st.subheader("💾 저장된 뉴스 모아보기")

    df_saved = fetch_saved_news()

    if df_saved.empty:
        st.info("아직 저장된 뉴스가 없습니다. 먼저 [🔍 뉴스 검색] 탭에서 저장해 보세요!")
    else:
        # 상단 필터: 키워드 / 검색어
        col_f1, col_f2 = st.columns([1, 2])
        with col_f1:
            kw_options = ["전체"] + sorted(df_saved["keyword"].dropna().unique().tolist())
            selected_kw = st.selectbox("키워드 필터", kw_options)
        with col_f2:
            text_filter = st.text_input(
                "제목/요약 검색어",
                placeholder="제목이나 요약에 포함된 단어를 입력 (선택)",
            )

        # 필터링 적용
        df_view = df_saved.copy()
        if selected_kw != "전체":
            df_view = df_view[df_view["keyword"] == selected_kw]
        if text_filter.strip():
            t = text_filter.strip().lower()
            mask = (
                df_view["title"].fillna("").str.lower().str.contains(t)
                | df_view["summary"].fillna("").str.lower().str.contains(t)
            )
            df_view = df_view[mask]

        st.caption(f"총 **{len(df_view)}건** 표시 (전체 {len(df_saved)}건)")
        st.markdown("---")

        # 카드 형태로 출력 + 삭제 버튼
        for _, row in df_view.iterrows():
            with st.container(border=True):
                top_l, top_r = st.columns([0.85, 0.15])
                with top_l:
                    st.markdown(f"### {row['title']}")
                    meta = []
                    if row.get("source"):
                        meta.append(f"📰 {row['source']}")
                    if row.get("news_date"):
                        meta.append(f"📅 {row['news_date']}")
                    if row.get("keyword"):
                        meta.append(f"🏷️ {row['keyword']}")
                    if row.get("created_at"):
                        # created_at 은 ISO 문자열 → 보기 좋게 자름
                        meta.append(f"💾 {str(row['created_at'])[:19]}")
                    if meta:
                        st.caption(" · ".join(meta))
                    if row.get("summary"):
                        st.write(row["summary"])
                    if row.get("url"):
                        st.markdown(f"🔗 [원본 기사 보기]({row['url']})")
                with top_r:
                    if st.button(
                        "🗑️ 삭제",
                        key=f"del_{row['id']}",
                        use_container_width=True,
                    ):
                        if delete_article(int(row["id"])):
                            st.success("삭제 완료")
                            st.rerun()  # 화면 새로고침


# ---------------------------------------------------------------------
# 7-3. 대시보드 탭
# ---------------------------------------------------------------------
with tab_dashboard:
    st.subheader("📊 저장 뉴스 통계 대시보드")

    df_all = fetch_saved_news(limit=10000)

    if df_all.empty:
        st.info("아직 저장된 뉴스가 없어 표시할 통계가 없습니다.")
    else:
        # 상단 KPI 카드 ----------------------------------------------------
        col1, col2, col3 = st.columns(3)
        col1.metric("📑 총 저장 기사", f"{len(df_all):,} 건")
        col2.metric("🏷️ 고유 키워드 수", f"{df_all['keyword'].nunique():,} 개")
        col3.metric("📰 출처(언론사) 수", f"{df_all['source'].nunique():,} 개")

        st.markdown("---")

        # (1) 키워드별 저장 건수 -----------------------------------------
        st.markdown("#### 🏷️ 키워드별 저장 건수")
        kw_counts = (df_all["keyword"]
                     .value_counts()
                     .reset_index()
                     .rename(columns={"keyword": "키워드", "count": "건수"}))
        # value_counts() 의 컬럼명은 pandas 버전에 따라 'count' 또는 'keyword' 가
        # 될 수 있어 양쪽을 모두 처리합니다.
        if "건수" not in kw_counts.columns:
            kw_counts.columns = ["키워드", "건수"]

        fig_kw = px.bar(
            kw_counts,
            x="키워드",
            y="건수",
            text="건수",
            color="건수",
            color_continuous_scale="Blues",
        )
        fig_kw.update_layout(
            xaxis_title=None,
            yaxis_title="저장 건수",
            showlegend=False,
            height=400,
        )
        fig_kw.update_traces(textposition="outside")
        st.plotly_chart(fig_kw, use_container_width=True)

        # (2) 일자별 저장 건수 (기사 자체의 날짜 기준) --------------------
        st.markdown("#### 📅 일자별 뉴스 저장 건수")

        # news_date 가 있으면 그것을, 없으면 created_at 의 날짜 부분을 사용
        df_date = df_all.copy()
        df_date["display_date"] = (
            df_date["news_date"].fillna("").astype(str).str.strip()
        )
        # 빈 값은 created_at 의 날짜 부분으로 대체
        empty_mask = df_date["display_date"] == ""
        if "created_at" in df_date.columns:
            df_date.loc[empty_mask, "display_date"] = (
                df_date.loc[empty_mask, "created_at"].astype(str).str[:10]
            )

        # 잘못된 날짜는 NaT 로 만들고 제거
        df_date["display_date"] = pd.to_datetime(
            df_date["display_date"], errors="coerce"
        )
        df_date = df_date.dropna(subset=["display_date"])

        if df_date.empty:
            st.info("표시할 일자별 데이터가 부족합니다.")
        else:
            daily = (df_date.groupby(df_date["display_date"].dt.date)
                            .size()
                            .reset_index(name="건수")
                            .rename(columns={"display_date": "날짜"}))
            daily["날짜"] = pd.to_datetime(daily["날짜"])
            daily = daily.sort_values("날짜")

            fig_date = px.line(
                daily, x="날짜", y="건수",
                markers=True,
            )
            fig_date.update_layout(height=350, xaxis_title=None)
            st.plotly_chart(fig_date, use_container_width=True)

        # (3) 출처별 저장 건수 (Top 10) ----------------------------------
        st.markdown("#### 📰 언론사 Top 10")
        src_counts = (df_all["source"]
                      .replace("", pd.NA)
                      .dropna()
                      .value_counts()
                      .head(10)
                      .reset_index())
        src_counts.columns = ["언론사", "건수"]
        if src_counts.empty:
            st.info("출처 정보가 부족합니다.")
        else:
            fig_src = px.bar(
                src_counts.sort_values("건수"),
                x="건수", y="언론사",
                orientation="h",
                text="건수",
                color="건수",
                color_continuous_scale="Tealgrn",
            )
            fig_src.update_layout(height=400, showlegend=False)
            fig_src.update_traces(textposition="outside")
            st.plotly_chart(fig_src, use_container_width=True)

        # (4) 원본 데이터 테이블 (요약본) -------------------------------
        with st.expander("🗂️ 원본 데이터 보기 (최근 100건)"):
            cols_show = [c for c in
                         ["created_at", "keyword", "title", "source",
                          "news_date", "url"]
                         if c in df_all.columns]
            st.dataframe(
                df_all[cols_show].head(100),
                use_container_width=True,
                hide_index=True,
            )


# =====================================================================
# 8. 푸터
# =====================================================================
st.divider()
st.caption(
    "🛠️ Built with Streamlit · Gemini API (Google Search Grounding) · Supabase"
)
