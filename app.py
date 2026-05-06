import streamlit as st
import pandas as pd
import json
import re
from google import genai
from google.genai import types
from supabase import create_client, Client

# -------------------------------------------------------------------
# 1. 페이지 기본 설정
# -------------------------------------------------------------------
st.set_page_config(page_title="최신 뉴스 검색 및 저장 앱", page_icon="📰", layout="wide")

# -------------------------------------------------------------------
# 2. 비밀 키(Secrets) 불러오기 및 초기화
# -------------------------------------------------------------------
# Streamlit Cloud의 Secrets(또는 로컬의 .streamlit/secrets.toml)에서 키를 읽어옵니다.
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# Supabase 클라이언트 연결
@st.cache_resource # 데이터베이스 연결을 매번 하지 않고 캐싱(저장)해두어 속도를 높입니다.
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

# Gemini 클라이언트 연결 (API 키 명시적 전달)
client = genai.Client(api_key=GEMINI_API_KEY)

# -------------------------------------------------------------------
# 3. 화면 UI 구성 (3개의 탭 만들기)
# -------------------------------------------------------------------
st.title("📰 AI 최신 뉴스 검색 & 자동 저장기")
st.info("💡 안내: Gemini API 무료 티어를 사용하며, 검색된 결과는 자동으로 Supabase DB에 저장됩니다.")

# 화면을 3개의 탭으로 나눕니다.
tab1, tab2, tab3 = st.tabs(["🔍 검색하기", "💾 저장된 뉴스 보기", "📊 통계 분석"])

# ==========================================
# 탭 1: 검색하기 및 자동 저장 로직
# ==========================================
with tab1:
    st.subheader("새로운 뉴스 검색")
    keyword = st.text_input("검색할 뉴스 키워드를 입력하세요 (예: 테슬라, 올림픽)")
    
    if st.button("뉴스 검색 및 자동 저장", type="primary"):
        if not keyword:
            st.warning("키워드를 입력해주세요!")
        else:
            with st.spinner("최신 뉴스를 검색하고 DB에 저장하는 중입니다..."):
                try:
                    # 1. Gemini AI에 검색 및 요약 요청
                    #    ⚠️ URL은 모델에게 직접 만들게 하지 않습니다 (hallucination 방지).
                    #    대신 "몇 번째 grounding source를 참고했는지"만 정수로 답하게 합니다.
                    prompt = f"""
                    다음 키워드에 대한 가장 최신 뉴스 2건을 검색하고 요약해주세요: '{keyword}'

                    [매우 중요한 규칙 - 반드시 지킬 것]
                    1. 반드시 Google Search 도구를 사용해 실제 검색 결과(grounding sources)에 기반해서만 작성하세요.
                    2. URL/링크는 절대 직접 작성하거나 추측하지 마세요. 시스템이 grounding metadata에서 실제 URL을 자동으로 채워 넣습니다.
                    3. 실제로 검색되지 않은 기사를 만들어내지 마세요. 검색 결과에 없는 정보는 출력하지 마세요.
                    4. 각 뉴스 항목은 반드시 검색 결과의 어느 출처를 참조했는지 source_index로 명시해야 합니다.

                    [요구사항]
                    각 뉴스별로 다음 항목을 작성하세요:
                    - title: 검색된 실제 기사의 제목
                    - source: 언론사 이름
                    - news_date: YYYY-MM-DD 형식의 날짜
                    - source_index: 이 뉴스가 참조한 grounding source의 번호
                                    (1부터 시작하는 정수. Google Search가 반환한 출처 목록의 N번째 항목)
                    - summary: 3~4문장의 요약

                    응답은 반드시 아래 형태의 JSON 배열(Array)로만 출력해야 합니다:
                    [
                        {{
                            "title": "뉴스 제목",
                            "source": "언론사 이름",
                            "news_date": "YYYY-MM-DD",
                            "source_index": 1,
                            "summary": "3~4문장의 요약 내용"
                        }}
                    ]
                    """
                    
                    response = client.models.generate_content(
                        model='gemini-2.5-flash-lite',
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            tools=[{"google_search": {}}],
                            temperature=0.2
                        )
                    )
                    
                    # 2. ⭐ Grounding Metadata에서 "실제 검색된 URL" 추출 (Hallucination 방지의 핵심)
                    #    모델이 만들어낸 가짜 URL이 아니라, Google Search가 실제로 반환한 URI를 사용합니다.
                    grounding_sources = []
                    try:
                        if response.candidates and response.candidates[0].grounding_metadata:
                            metadata = response.candidates[0].grounding_metadata
                            chunks = getattr(metadata, 'grounding_chunks', None) or []
                            for chunk in chunks:
                                web = getattr(chunk, 'web', None)
                                if web and getattr(web, 'uri', None):
                                    grounding_sources.append({
                                        'uri': web.uri,
                                        'title': getattr(web, 'title', '') or ''
                                    })
                    except Exception as ge:
                        st.warning(f"Grounding metadata 추출 중 경고: {ge}")

                    if not grounding_sources:
                        # 실제 검색 결과가 비어 있다면 hallucination 위험이 100%이므로 중단
                        st.error("⚠️ 실제 검색 결과(grounding sources)를 가져오지 못했습니다. "
                                 "키워드를 바꾸거나 잠시 후 다시 시도해주세요.")
                    else:
                        # 3. JSON 결과 추출
                        raw_text = response.text
                        match = re.search(r'\[\s*\{.*?\}\s*\]', raw_text, re.DOTALL)
                        clean_json_str = match.group(0) if match else raw_text
                        news_data = json.loads(clean_json_str)

                        # 4. ⭐ source_index 유효성 검증 + 실제 URL 매핑
                        #    모델이 잘못된 인덱스를 줬거나 URL이 없는 항목은 제외합니다.
                        validated_news = []
                        for news in news_data:
                            idx = news.get('source_index')
                            if isinstance(idx, int) and 1 <= idx <= len(grounding_sources):
                                real_source = grounding_sources[idx - 1]
                                news['url'] = real_source['uri']  # ✅ 진짜 검색 URL로 덮어쓰기
                                validated_news.append(news)
                            # 유효하지 않은 source_index 항목은 hallucination 가능성이 높아 제외

                        if not validated_news:
                            st.warning("실제 검색 결과로 검증된 뉴스 항목이 없습니다. 다시 시도해보세요.")
                        else:
                            # 5. 화면에 출력 및 Supabase DB에 저장
                            saved_count = 0
                            duplicate_count = 0

                            st.success(f"'{keyword}'에 대한 검색이 완료되었습니다! "
                                       f"(검증된 결과 {len(validated_news)}건 / 전체 출처 {len(grounding_sources)}건)")

                            for news in validated_news:
                                # 화면에 카드 형태로 보여주기
                                with st.container(border=True):
                                    st.markdown(f"#### [{news.get('title')}]({news.get('url')})")
                                    st.caption(f"🏢 **출처:** {news.get('source')} | 📅 **날짜:** {news.get('news_date')}")
                                    st.write(news.get('summary'))

                                # DB 저장을 위한 데이터 조립 (keyword 추가)
                                db_record = {
                                    "keyword": keyword,
                                    "title": news.get("title"),
                                    "source": news.get("source"),
                                    "news_date": news.get("news_date"),
                                    "url": news.get("url"),
                                    "summary": news.get("summary")
                                }

                                # DB에 저장 시도
                                try:
                                    supabase.table("news_history").insert(db_record).execute()
                                    saved_count += 1
                                except Exception as db_e:
                                    # url이 UNIQUE이므로, 이미 존재하는 URL이면 에러가 발생합니다.
                                    # 이를 이용해 중복 저장을 건너뜁니다.
                                    if "duplicate key value" in str(db_e) or "23505" in str(db_e):
                                        duplicate_count += 1
                                    else:
                                        st.error(f"DB 저장 중 에러 발생: {db_e}")

                            # 저장 결과 요약 알림
                            st.toast(f"✅ 새로 저장됨: {saved_count}건 | 🔄 중복 생략됨: {duplicate_count}건")

                except Exception as e:
                    st.error(f"오류가 발생했습니다: {e}")

# ==========================================
# 탭 2: 저장된 뉴스 보기
# ==========================================
with tab2:
    st.subheader("데이터베이스에 저장된 뉴스 목록")
    
    # Supabase에서 데이터 불러오기 (최신순 정렬)
    try:
        response = supabase.table("news_history").select("*").order("created_at", desc=True).execute()
        db_data = response.data
        
        if db_data:
            df = pd.DataFrame(db_data)
            
            # 사용자 편의를 위한 키워드 검색 필터 제공
            search_term = st.text_input("목록 내 키워드 필터링 (제목, 키워드 기준)", "")
            
            if search_term:
                # 대소문자 구분 없이 필터링
                df = df[df["keyword"].str.contains(search_term, case=False, na=False) | 
                        df["title"].str.contains(search_term, case=False, na=False)]
            
            # 화면에 표(Dataframe) 형태로 보여주기
            st.dataframe(
                df[["keyword", "title", "source", "news_date", "url", "created_at"]], 
                use_container_width=True,
                hide_index=True
            )
            
            # CSV 다운로드 기능
            csv_data = df.to_csv(index=False, encoding='utf-8-sig')
            st.download_button(
                label="📥 현재 표의 데이터 CSV 다운로드",
                data=csv_data,
                file_name="saved_news_history.csv",
                mime="text/csv"
            )
        else:
            st.info("아직 저장된 뉴스가 없습니다. 탭 1에서 뉴스를 검색해보세요!")
            
    except Exception as e:
        st.error(f"DB 데이터를 불러오는 중 오류가 발생했습니다: {e}")

# ==========================================
# 탭 3: 통계 분석 (차트)
# ==========================================
with tab3:
    st.subheader("검색 통계 대시보드")
    
    if 'db_data' in locals() and db_data: # 탭 2에서 불러온 데이터가 있다면 활용
        df_stats = pd.DataFrame(db_data)
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**📌 키워드별 누적 검색 건수**")
            # 키워드별로 그룹화하여 개수 세기
            keyword_counts = df_stats['keyword'].value_counts()
            # Streamlit 내장 바 차트로 그리기
            st.bar_chart(keyword_counts)
            
        with col2:
            st.markdown("**📌 일자별 DB 저장 건수**")
            # created_at (예: 2024-05-01T12:00:00) 문자열에서 날짜(YYYY-MM-DD)만 추출
            df_stats['date_only'] = pd.to_datetime(df_stats['created_at']).dt.date
            # 일자별로 개수 세기
            date_counts = df_stats['date_only'].value_counts().sort_index()
            # Streamlit 내장 라인 차트로 그리기
            st.line_chart(date_counts)
    else:
        st.info("통계를 표시할 데이터가 부족합니다.")
