import streamlit as st
import pandas as pd
import json
from supabase import create_client, Client as SupabaseClient
from google import genai
from google.genai import types

# -------------------------------------------------------------------
# 1. 초기 설정 및 환경 변수(Secrets) 불러오기
# -------------------------------------------------------------------
st.set_page_config(page_title="AI 최신 뉴스 검색 & 저장기", page_icon="📰", layout="wide")

# Streamlit Cloud의 Secrets에서 정보 가져오기
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError as e:
    st.error(f"환경 변수(Secrets) 설정이 누락되었습니다: {e}")
    st.stop()

# Supabase 및 Gemini 클라이언트 초기화
@st.cache_resource
def init_connection():
    sb_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    gm_client = genai.Client(api_key=GEMINI_API_KEY)
    return sb_client, gm_client

supabase, gemini_client = init_connection()

# -------------------------------------------------------------------
# 2. 화면 구성 (3개의 탭)
# -------------------------------------------------------------------
st.title("📰 AI 최신 뉴스 검색 & 자동 저장기")
tab1, tab2, tab3 = st.tabs(["🔍 검색하기", "💾 저장된 뉴스 보기", "📊 통계 분석"])

# ==========================================
# Tab 1: 🔍 검색하기
# ==========================================
with tab1:
    st.subheader("관심 있는 키워드의 최신 뉴스를 검색하세요")
    keyword = st.text_input("검색할 키워드 입력 (예: 인공지능, 테슬라)", placeholder="키워드를 입력하세요...")
    
    if st.button("🚀 검색 및 자동 저장", type="primary") and keyword:
        with st.spinner('Gemini가 최신 웹 검색을 통해 뉴스를 요약 중입니다... (잠시만 기다려주세요)'):
            try:
                # 프롬프트 작성
                prompt = f"""
                키워드: '{keyword}'
                위 키워드에 대한 가장 최신 뉴스 딱 2건만 검색해. 
                반드시 아래 JSON 배열 형식으로만 응답하고, 절대 URL을 지어내지 마.[
                    {{"title": "기사 제목", "source": "언론사", "news_date": "보도 날짜", "url": "기사 링크", "summary": "기사 3줄 요약"}}
                ]
                """
                
                # Gemini API 호출 (검색 도구 활성화, JSON 응답, Temperature 0.0)
                response = gemini_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        response_mime_type="application/json",
                        tools=[{"google_search": {}}]
                    )
                )
                
                # JSON 파싱
                articles = json.loads(response.text)
                
                # [매우 중요] URL 환각 완벽 방지 로직 (Grounding Metadata 활용)
                try:
                    grounding_chunks = response.candidates[0].grounding_metadata.grounding_chunks
                    actual_links =[]
                    # 실제 참조한 웹 데이터 추출
                    for chunk in grounding_chunks:
                        if hasattr(chunk, 'web') and chunk.web is not None:
                            actual_links.append({"title": chunk.web.title, "uri": chunk.web.uri})
                    
                    # 생성된 JSON의 URL을 실제 URL로 덮어쓰기 (제목 유사도 검사)
                    for item in articles:
                        gen_title = item.get("title", "").replace(" ", "").lower()
                        for link in actual_links:
                            real_title = link["title"].replace(" ", "").lower()
                            # 생성된 제목과 실제 참조 제목이 겹치면 실제 URL로 교체
                            if gen_title in real_title or real_title in gen_title or real_title[:10] in gen_title:
                                item["url"] = link["uri"]
                                break
                except Exception as e:
                    pass # grounding_metadata 파싱 실패 시 원본 유지
                
                # 화면에 카드 형태로 결과 출력 및 Supabase 저장 로직
                success_count = 0
                duplicate_count = 0
                
                cols = st.columns(2)
                for idx, item in enumerate(articles):
                    with cols[idx % 2]:
                        st.info(f"**[{item.get('source', '출처 미상')}] {item.get('title', '제목 없음')}**")
                        st.caption(f"📅 {item.get('news_date', '')}")
                        st.write(item.get('summary', ''))
                        st.markdown(f"[🔗 원본 기사 보러가기]({item.get('url', '#')})")
                    
                    # Supabase DB 저장
                    try:
                        db_data = {
                            "keyword": keyword,
                            "title": item.get("title", ""),
                            "source": item.get("source", ""),
                            "news_date": item.get("news_date", ""),
                            "url": item.get("url", ""),
                            "summary": item.get("summary", "")
                        }
                        supabase.table("news_history").insert(db_data).execute()
                        success_count += 1
                    except Exception as e:
                        # 23505는 PostgreSQL(Supabase)의 Unique Violation 에러 코드
                        if "23505" in str(e) or "duplicate" in str(e).lower():
                            duplicate_count += 1
                        else:
                            st.error(f"저장 중 오류 발생: {e}")
                
                # 처리 결과 알림 (Toast)
                st.toast(f"✅ 완료! (새로 저장: {success_count}건 / 중복 생략: {duplicate_count}건)", icon="🎉")
                
            except Exception as e:
                st.error(f"뉴스 검색 중 오류가 발생했습니다: {e}")

# ==========================================
# Tab 2: 💾 저장된 뉴스 보기
# ==========================================
with tab2:
    st.subheader("데이터베이스에 저장된 전체 뉴스")
    
    # DB에서 데이터 가져오기
    try:
        response = supabase.table("news_history").select("*").order("created_at", desc=True).execute()
        df = pd.DataFrame(response.data)
        
        if not df.empty:
            # 필터링 창 추가
            col1, col2 = st.columns(2)
            search_keyword = col1.text_input("키워드로 필터링", "")
            search_title = col2.text_input("제목으로 필터링", "")
            
            filtered_df = df.copy()
            if search_keyword:
                filtered_df = filtered_df[filtered_df['keyword'].str.contains(search_keyword, case=False, na=False)]
            if search_title:
                filtered_df = filtered_df[filtered_df['title'].str.contains(search_title, case=False, na=False)]
            
            # 보기 편하게 컬럼 정리 및 표출
            display_df = filtered_df[['id', 'keyword', 'title', 'source', 'url', 'created_at']]
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            
            # CSV 다운로드 버튼
            csv = filtered_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 현재 데이터 CSV로 다운로드",
                data=csv,
                file_name='saved_news_data.csv',
                mime='text/csv',
            )
        else:
            st.info("아직 저장된 뉴스가 없습니다. '검색하기' 탭에서 뉴스를 검색해보세요!")
            
    except Exception as e:
        st.error(f"데이터를 불러오는 중 오류가 발생했습니다: {e}")

# ==========================================
# Tab 3: 📊 통계 분석
# ==========================================
with tab3:
    st.subheader("데이터 기반 통계 대시보드")
    
    try:
        response = supabase.table("news_history").select("keyword, created_at").execute()
        df_stats = pd.DataFrame(response.data)
        
        if not df_stats.empty:
            col_left, col_right = st.columns(2)
            
            with col_left:
                st.markdown("#### 📌 키워드별 누적 검색(저장) 건수")
                keyword_counts = df_stats['keyword'].value_counts()
                st.bar_chart(keyword_counts)
                
            with col_right:
                st.markdown("#### 📈 일자별 저장 건수")
                # 날짜 데이터 (YYYY-MM-DD) 추출
                df_stats['created_at'] = pd.to_datetime(df_stats['created_at'])
                df_stats['date'] = df_stats['created_at'].dt.strftime('%Y-%m-%d')
                date_counts = df_stats['date'].value_counts().sort_index()
                st.line_chart(date_counts)
        else:
            st.info("통계를 표시할 데이터가 부족합니다.")
    except Exception as e:
        st.error(f"통계 데이터를 불러오는 중 오류가 발생했습니다: {e}")