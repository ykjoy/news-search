import streamlit as st
import pandas as pd
import json
import re
from google import genai
from google.genai import types
from supabase import create_client, Client

# ==========================================
# 1. 초기 설정 및 시크릿 불러오기
# ==========================================
st.set_page_config(page_title="AI 최신 뉴스 자동 저장기", page_icon="📰", layout="wide")

# Supabase 클라이언트 초기화 (캐싱하여 재사용)
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

# Gemini 클라이언트 초기화
gemini_client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

# ==========================================
# 2. 화면 UI 구성 (3개의 탭)
# ==========================================
st.title("📰 AI 최신 뉴스 검색 & 자동 저장기")
st.markdown("관심 있는 키워드의 최신 뉴스를 AI가 검색하고 요약하여 데이터베이스에 자동 저장합니다.")

tab1, tab2, tab3 = st.tabs(["🔍 검색하기", "💾 저장된 뉴스 보기", "📊 통계 분석"])

# ------------------------------------------
# Tab 1: 검색 및 저장 로직
# ------------------------------------------
with tab1:
    st.subheader("새로운 뉴스 검색")
    keyword = st.text_input("검색할 뉴스 키워드를 입력하세요 (예: 인공지능, 전기차, 한국경제)")
    
    if st.button("🚀 검색 및 자동 저장", type="primary"):
        if not keyword:
            st.warning("키워드를 입력해주세요!")
        else:
            with st.spinner(f"'{keyword}' 관련 최신 뉴스를 구글에서 검색하고 분석 중입니다..."):
                try:
                    # [주의] Gemini API는 Google 검색 도구와 강제 JSON 모드를 동시 지원하지 않으므로 프롬프트로 JSON 포맷을 강제합니다.
                    prompt = f"""
                    '{keyword}'에 대한 가장 최신 뉴스 딱 2건만 구글에서 검색해줘.
                    응답은 반드시 아래 JSON 배열 형식으로만 출력해야 해. 마크다운 ```json 과 ``` 로 감싸줘.
                    절대 URL을 지어내거나 가상의 링크를 만들지 마.[
                        {{
                            "title": "뉴스 제목",
                            "source": "언론사명",
                            "news_date": "YYYY-MM-DD",
                            "url": "기사 원본 링크",
                            "summary": "뉴스 내용 3줄 요약"
                        }}
                    ]
                    """
                    
                    # Gemini API 호출 (검색 도구 활성화, Temperature 0.0)
                    response = gemini_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.0,
                            tools=[types.Tool(google_search=types.GoogleSearch())]
                        )
                    )
                    
                    # 응답 텍스트에서 JSON 추출
                    response_text = response.text
                    json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
                    if json_match:
                        raw_json_str = json_match.group(1)
                    else:
                        raw_json_str = response_text # fallback
                        
                    news_data = json.loads(raw_json_str)
                    
                    # [매우 중요: URL 환각 완벽 방지 로직]
                    # 구글 검색 Grounding 데이터에서 실제 참조한 기사의 진짜 URL을 추출하여 덮어씁니다.
                    url_map = {}
                    if response.candidates and response.candidates[0].grounding_metadata:
                        grounding_chunks = response.candidates[0].grounding_metadata.grounding_chunks
                        if grounding_chunks:
                            for chunk in grounding_chunks:
                                if hasattr(chunk, 'web') and chunk.web:
                                    real_title = chunk.web.title
                                    real_url = chunk.web.uri
                                    # http로 시작하고 구글 임시 리다이렉트 링크가 아닌 실제 링크만 추출
                                    if real_url.startswith("http") and "grounding-api-redirect" not in real_url:
                                        url_map[real_title] = real_url
                    
                    # 추출한 실제 URL로 생성된 JSON 데이터 덮어쓰기
                    for item in news_data:
                        for g_title, g_url in url_map.items():
                            # 제목이 일부라도 일치하면 실제 URL로 교체 (생성된 제목이 약간 다를 수 있음)
                            if g_title in item['title'] or item['title'] in g_title:
                                item['url'] = g_url
                                break
                    
                    # 화면 출력 및 DB 저장
                    success_count = 0
                    duplicate_count = 0
                    
                    for news in news_data:
                        # 1. 화면에 카드 형태로 출력
                        with st.container(border=True):
                            st.markdown(f"### [{news['title']}]({news['url']})")
                            st.caption(f"🏢 {news.get('source', '출처 미상')} | 📅 {news.get('news_date', '날짜 미상')}")
                            st.write(news.get('summary', '요약 없음'))
                        
                        # 2. Supabase DB 저장
                        db_record = {
                            "keyword": keyword,
                            "title": news['title'],
                            "source": news.get('source', ''),
                            "news_date": news.get('news_date', ''),
                            "url": news['url'],
                            "summary": news.get('summary', '')
                        }
                        
                        try:
                            supabase.table("news_history").insert(db_record).execute()
                            success_count += 1
                        except Exception as e:
                            # 23505는 PostgreSQL의 UNIQUE 제약 조건 위반(중복) 에러 코드
                            if "23505" in str(e):
                                duplicate_count += 1
                            else:
                                st.error(f"DB 저장 중 오류 발생: {str(e)}")
                                
                    # 작업 완료 알림 (Toast)
                    st.toast(f"✅ 새 뉴스 {success_count}건 저장됨 (중복 생략: {duplicate_count}건)", icon="🎉")
                    
                except Exception as e:
                    st.error(f"처리 중 오류가 발생했습니다: {str(e)}")

# ------------------------------------------
# Tab 2: 저장된 뉴스 보기
# ------------------------------------------
with tab2:
    st.subheader("💾 데이터베이스에 저장된 뉴스 목록")
    
    try:
        # DB에서 최신순으로 데이터 가져오기
        response = supabase.table("news_history").select("*").order("created_at", desc=True).execute()
        data = response.data
        
        if data:
            df = pd.DataFrame(data)
            
            # 검색 필터링
            search_query = st.text_input("🔍 제목이나 키워드로 검색해보세요:")
            if search_query:
                # 대소문자 구분 없이 제목이나 키워드에 검색어가 포함된 행 필터링
                df = df[df['title'].str.contains(search_query, case=False, na=False) | 
                        df['keyword'].str.contains(search_query, case=False, na=False)]
            
            # 불필요한 id 컬럼 숨기고 출력
            display_df = df.drop(columns=['id'])
            st.dataframe(display_df, use_container_width=True)
            
            # CSV 다운로드 버튼
            csv = df.to_csv(index=False).encode('utf-8-sig') # utf-8-sig로 한글 깨짐 방지
            st.download_button(
                label="📥 현재 목록 CSV로 다운로드",
                data=csv,
                file_name='saved_news.csv',
                mime='text/csv',
            )
        else:
            st.info("아직 저장된 뉴스가 없습니다. '검색하기' 탭에서 뉴스를 검색해보세요!")
    except Exception as e:
        st.error(f"데이터를 불러오는 중 오류가 발생했습니다: {str(e)}")

# ------------------------------------------
# Tab 3: 통계 분석 (대시보드)
# ------------------------------------------
with tab3:
    st.subheader("📊 뉴스 수집 통계 대시보드")
    
    try:
        response = supabase.table("news_history").select("*").execute()
        data = response.data
        
        if data:
            df = pd.DataFrame(data)
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("**📌 키워드별 누적 검색(저장) 건수**")
                keyword_counts = df['keyword'].value_counts()
                st.bar_chart(keyword_counts)
                
            with col2:
                st.markdown("**📈 일자별 수집(저장) 건수 추이**")
                # created_at에서 YYYY-MM-DD 날짜만 추출
                df['date'] = pd.to_datetime(df['created_at']).dt.strftime('%Y-%m-%d')
                daily_counts = df['date'].value_counts().sort_index()
                st.line_chart(daily_counts)
        else:
            st.info("통계를 표시할 데이터가 부족합니다.")
    except Exception as e:
        st.error(f"통계 데이터를 불러오는 중 오류가 발생했습니다: {str(e)}")
