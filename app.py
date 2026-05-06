import streamlit as st
import pandas as pd
import json
import re
from supabase import create_client, Client
from google import genai
from google.genai import types

# --- [페이지 기본 설정] ---
st.set_page_config(page_title="AI 최신 뉴스 검색기", page_icon="📰", layout="wide")

# --- [시크릿 변수 불러오기 및 초기화] ---
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except KeyError:
    st.error("🚨 환경 변수(Secrets)가 설정되지 않았습니다. 배포 설정에서 시크릿을 추가해주세요.")
    st.stop()

# 클라이언트 초기화
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

st.title("📰 AI 최신 뉴스 검색 & 자동 저장기")
st.markdown("Gemini API의 구글 검색을 활용해 최신 뉴스를 찾고 데이터베이스에 기록합니다.")

# --- [화면 탭 구성] ---
tab1, tab2, tab3 = st.tabs(["🔍 검색하기", "💾 저장된 뉴스 보기", "📊 통계 분석"])

# ==========================================
# Tab 1: 뉴스 검색 및 저장
# ==========================================
with tab1:
    st.subheader("키워드로 최신 뉴스 검색")
    keyword = st.text_input("검색할 뉴스 키워드를 입력하세요 (예: 인공지능, 테슬라, 올림픽 등)")
    
    if st.button("🚀 검색 및 저장", type="primary"):
        if not keyword.strip():
            st.warning("키워드를 입력해주세요!")
        else:
            with st.spinner("구글 검색을 통해 최신 뉴스를 찾고 분석하는 중입니다..."):
                try:
                    # 1. 프롬프트 작성 (JSON 포맷 강제)
                    prompt = f"""
                    '{keyword}'에 대한 가장 최신 뉴스 딱 **2건**만 구글에서 검색해.
                    반드시 아래 형식의 JSON 배열(Array)로만 응답하고 절대 URL을 지어내지 마.
                    JSON 외의 설명은 하지 마.[
                        {{
                            "title": "기사 제목",
                            "source": "언론사 이름",
                            "news_date": "YYYY-MM-DD 또는 N시간 전",
                            "url": "기사 원본 링크",
                            "summary": "기사 내용 3줄 요약"
                        }}
                    ]
                    """
                    
                    # 2. Gemini API 호출 (구글 검색 도구 활성화, Temperature 0.0)
                    response = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.0,
                            tools=[{"google_search": {}}]
                        )
                    )
                    
                    # 3. JSON 추출 및 파싱
                    response_text = response.text
                    json_match = re.search(r"```json\n(.*?)\n```", response_text, re.DOTALL)
                    if json_match:
                        raw_json = json_match.group(1)
                    else:
                        raw_json = response_text # 백틱이 없는 경우 대비
                    
                    news_data = json.loads(raw_json)
                    
                    # 4. [URL 환각 완벽 방지 로직] 실제 검색 데이터(Grounding Metadata)와 크로스체크
                    try:
                        chunks = response.candidates[0].grounding_metadata.grounding_chunks
                        valid_links = {}
                        
                        for chunk in chunks:
                            # 웹 검색 결과가 있는 청크인지 확인
                            if hasattr(chunk, 'web') and chunk.web:
                                chunk_title = chunk.web.title
                                chunk_uri = chunk.web.uri
                                
                                # 실제 링크 조건 검사 (http 시작 & 임시 리다이렉트 링크 아님)
                                if chunk_uri.startswith("http") and "grounding-api-redirect" not in chunk_uri:
                                    valid_links[chunk_title] = chunk_uri
                        
                        # 생성된 JSON 데이터의 URL을 실제 URL로 덮어쓰기
                        for item in news_data:
                            for v_title, v_uri in valid_links.items():
                                # 제목이 일부라도 일치하면 확실한 진짜 URL로 교체
                                if v_title in item['title'] or item['title'] in v_title:
                                    item['url'] = v_uri
                                    break
                    except Exception as e:
                        st.warning("URL 검증 중 일부 메타데이터를 찾을 수 없으나 진행합니다.")

                    # 5. 화면 출력 및 Supabase 저장 로직
                    success_count = 0
                    duplicate_count = 0
                    
                    st.success(f"🎉 '{keyword}'에 대한 최신 뉴스 2건을 찾았습니다!")
                    
                    for idx, item in enumerate(news_data):
                        # UI 카드 출력
                        with st.container(border=True):
                            st.markdown(f"### [{item['title']}]({item['url']})")
                            st.caption(f"🗞️ {item['source']} | 🕒 {item['news_date']}")
                            st.write(item['summary'])
                            st.markdown(f"[👉 원본 기사 읽기]({item['url']})")
                            
                        # DB 저장 (에러 핸들링으로 중복 체크)
                        try:
                            supabase.table("news_history").insert({
                                "keyword": keyword,
                                "title": item['title'],
                                "source": item['source'],
                                "news_date": item['news_date'],
                                "url": item['url'],
                                "summary": item['summary']
                            }).execute()
                            success_count += 1
                        except Exception as db_e:
                            if "23505" in str(db_e): # 고유키(UNIQUE) 중복 에러 코드
                                duplicate_count += 1
                            else:
                                st.error(f"DB 저장 에러: {db_e}")
                                
                    # 6. 토스트 알림
                    st.toast(f"✅ DB 저장 완료: {success_count}건 / 🔄 중복 생략됨: {duplicate_count}건", icon="💾")

                except Exception as e:
                    st.error(f"오류가 발생했습니다. AI 응답을 확인해주세요.\n\n상세 에러: {e}")

# ==========================================
# Tab 2: 저장된 뉴스 보기
# ==========================================
with tab2:
    st.subheader("💾 데이터베이스에 저장된 뉴스 목록")
    
    # DB에서 최신순으로 데이터 가져오기
    try:
        db_response = supabase.table("news_history").select("*").order("created_at", desc=True).execute()
        df = pd.DataFrame(db_response.data)
        
        if df.empty:
            st.info("아직 저장된 뉴스가 없습니다. 탭 1에서 뉴스를 검색하고 저장해보세요!")
        else:
            # 검색 필터링 창
            search_query = st.text_input("🔍 제목 또는 키워드로 결과 내 검색")
            if search_query:
                df = df[df['title'].str.contains(search_query, case=False) | df['keyword'].str.contains(search_query, case=False)]
            
            # DataFrame 표출
            st.dataframe(
                df[['id', 'keyword', 'title', 'source', 'news_date', 'url', 'created_at']],
                use_container_width=True,
                hide_index=True
            )
            
            # CSV 다운로드 버튼
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 현재 목록 CSV로 다운로드",
                data=csv,
                file_name='saved_news.csv',
                mime='text/csv',
            )
    except Exception as e:
        st.error(f"DB 데이터를 불러오는 데 실패했습니다: {e}")

# ==========================================
# Tab 3: 통계 분석
# ==========================================
with tab3:
    st.subheader("📊 한눈에 보는 뉴스 수집 통계")
    
    if 'df' in locals() and not df.empty:
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**📌 키워드별 누적 검색(저장) 건수**")
            keyword_counts = df['keyword'].value_counts()
            st.bar_chart(keyword_counts)
            
        with col2:
            st.markdown("**📈 일자별 저장 건수 추이**")
            # YYYY-MM-DD 형태로 날짜 추출
            df['date_only'] = pd.to_datetime(df['created_at']).dt.strftime('%Y-%m-%d')
            daily_counts = df['date_only'].value_counts().sort_index()
            st.line_chart(daily_counts)
    else:
        st.info("통계를 표시할 데이터가 부족합니다.")