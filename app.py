import streamlit as st
import pandas as pd
import json
import re
from datetime import datetime
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
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# Supabase 클라이언트 연결
@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

# Gemini 클라이언트 연결
client = genai.Client(api_key=GEMINI_API_KEY)

# -------------------------------------------------------------------
# 3. 화면 UI 구성
# -------------------------------------------------------------------
st.title("📰 AI 최신 뉴스 검색 & 자동 저장기")
st.info("💡 안내: Google 검색을 통해 실제 뉴스 링크를 가져오며, 결과는 Supabase DB에 자동 저장됩니다.")

tab1, tab2, tab3 = st.tabs(["🔍 검색하기", "💾 저장된 뉴스 보기", "📊 통계 분석"])

# ==========================================
# 탭 1: 검색하기 및 자동 저장 로직
# ==========================================
with tab1:
    st.subheader("새로운 뉴스 검색")
    keyword = st.text_input("검색할 뉴스 키워드를 입력하세요 (예: 테슬라 주가, 인공지능 트렌드)")
    
    if st.button("뉴스 검색 및 자동 저장", type="primary"):
        if not keyword:
            st.warning("키워드를 입력해주세요!")
        else:
            with st.spinner("최신 뉴스를 실시간 검색 중입니다..."):
                try:
                    # 현재 날짜 정보 제공 (최신성 보장)
                    current_date = datetime.now().strftime("%Y-%m-%d")
                    
                    # 프롬프트: 정확한 URL 추출에 집중
                    prompt = f"""
                    오늘 날짜는 {current_date}입니다. 
                    키워드 '{keyword}'에 대한 가장 최신 뉴스 5건을 Google Search를 통해 검색하고 아래 형식으로 요약해주세요.

                    [중요 요구사항]
                    1. **실제 URL**: 검색 결과에 있는 원본 뉴스 기사의 '실제 URL'을 반드시 그대로 사용하세요. 절대 URL을 임의로 생성하거나 추측하지 마세요.
                    2. **신뢰도**: 공식 언론사(예: 연합뉴스, 매일경제, BBC 등)의 기사를 우선하세요.
                    3. **응답 형식**: 반드시 아래의 JSON 배열 형식으로만 응답하세요. 다른 설명은 생략하세요.

                    [
                      {{
                        "title": "실제 뉴스 제목",
                        "source": "언론사명",
                        "news_date": "YYYY-MM-DD",
                        "url": "확인된 실제 뉴스 URL",
                        "summary": "3~4문장의 핵심 요약"
                      }}
                    ]
                    """
                    
                    # 모델 호출 (안정적인 gemini-2.0-flash 사용)
                    response = client.models.generate_content(
                        model='gemini-2.5-flash-lite',
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            tools=[{"google_search": {}}],
                            temperature=0.0  # 창의성을 낮추어 사실 관계(URL) 정확도 향상
                        )
                    )
                    
                    # JSON 결과 추출
                    raw_text = response.text
                    # JSON 부분만 정규식으로 추출
                    json_match = re.search(r'\[.*\]', raw_text, re.DOTALL)
                    if json_match:
                        news_data = json.loads(json_match.group(0))
                    else:
                        # 정규식 실패 시 텍스트 전체 시도
                        news_data = json.loads(raw_text)
                    
                    if not news_data:
                        st.error("검색 결과를 가져오지 못했습니다. 다시 시도해주세요.")
                    else:
                        saved_count = 0
                        duplicate_count = 0
                        
                        st.success(f"'{keyword}'에 대한 실제 뉴스 검색 완료!")
                        
                        for news in news_data:
                            # 1. URL 유효성 간단 체크 (http로 시작하는지)
                            news_url = news.get('url', '#')
                            if not news_url.startswith('http'):
                                continue

                            # 2. 화면 출력
                            with st.container(border=True):
                                st.markdown(f"#### [{news.get('title')}]({news_url})")
                                st.caption(f"🏢 **출처:** {news.get('source')} | 📅 **날짜:** {news.get('news_date')}")
                                st.write(news.get('summary'))
                                st.markdown(f"🔗 [기사 원문 읽기]({news_url})")
                            
                            # 3. DB 저장
                            db_record = {
                                "keyword": keyword,
                                "title": news.get("title"),
                                "source": news.get("source"),
                                "news_date": news.get("news_date"),
                                "url": news_url,
                                "summary": news.get("summary")
                            }
                            
                            try:
                                supabase.table("news_history").insert(db_record).execute()
                                saved_count += 1
                            except Exception as db_e:
                                if "23505" in str(db_e): # 유니크 제약 조건 위반 (중복)
                                    duplicate_count += 1
                                else:
                                    st.error(f"DB 저장 중 에러: {db_e}")
                        
                        st.toast(f"✅ 신규 저장: {saved_count}건 | 🔄 중복 제외: {duplicate_count}건")

                except Exception as e:
                    st.error(f"오류가 발생했습니다: {e}")
                    st.write("상세 에러 내용:", e)

# ==========================================
# 탭 2: 저장된 뉴스 보기
# ==========================================
with tab2:
    st.subheader("데이터베이스에 저장된 뉴스 목록")
    
    try:
        response = supabase.table("news_history").select("*").order("created_at", desc=True).execute()
        db_data = response.data
        
        if db_data:
            df = pd.DataFrame(db_data)
            
            search_term = st.text_input("목록 내 필터링 (제목 또는 키워드)", "")
            if search_term:
                df = df[df["keyword"].str.contains(search_term, case=False, na=False) | 
                        df["title"].str.contains(search_term, case=False, na=False)]
            
            # URL을 클릭 가능한 링크로 변환하여 보여주기 위해 컬럼 설정
            st.dataframe(
                df[["keyword", "title", "source", "news_date", "url", "created_at"]], 
                use_container_width=True,
                hide_index=True,
                column_config={
                    "url": st.column_config.LinkColumn("기사 링크")
                }
            )
            
            csv_data = df.to_csv(index=False, encoding='utf-8-sig')
            st.download_button("📥 CSV 다운로드", data=csv_data, file_name="news_history.csv", mime="text/csv")
        else:
            st.info("저장된 뉴스가 없습니다.")
            
    except Exception as e:
        st.error(f"데이터 로드 중 오류: {e}")

# ==========================================
# 탭 3: 통계 분석
# ==========================================
with tab3:
    st.subheader("검색 통계")
    if 'db_data' in locals() and db_data:
        df_stats = pd.DataFrame(db_data)
        col1, col2 = st.columns(2)
        with col1:
            st.write("**📌 키워드별 검색 건수**")
            st.bar_chart(df_stats['keyword'].value_counts())
        with col2:
            st.write("**📌 날짜별 저장 추이**")
            df_stats['date_only'] = pd.to_datetime(df_stats['created_at']).dt.date
            st.line_chart(df_stats['date_only'].value_counts().sort_index())
    else:
        st.info("데이터가 없습니다.")
