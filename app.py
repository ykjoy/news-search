import streamlit as st
import pandas as pd
import json
import re
from google import genai
from google.genai import types
from supabase import create_client, Client

# ==========================================
# 1. 페이지 기본 설정 및 안내 문구
# ==========================================
st.set_page_config(page_title="최신 뉴스 검색 AI", page_icon="📰", layout="wide")

# 무료 티어 제한 안내 문구
st.info(
    "💡 **[안내] Gemini API 무료 티어 제한 사항**\n"
    "- 분당 최대 15회 요청 (15 RPM)\n"
    "- 하루 최대 1,500회 요청 (1,500 RPD)\n"
    "- 100만 토큰/분 제한이 적용되므로 연속적인 빠른 검색은 지양해 주세요."
)

st.title("📰 AI 최신 뉴스 검색 및 스크랩 보드")

# ==========================================
# 2. API 및 데이터베이스 초기화
# ==========================================
# Streamlit 비밀값(secrets)에서 API 키 불러오기
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except KeyError:
    st.error("비밀키 설정이 누락되었습니다. `.streamlit/secrets.toml` 파일을 확인해주세요.")
    st.stop()

# Gemini 클라이언트 초기화
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Supabase 클라이언트 연결 캐싱 (성능 최적화)
@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

# 검색 결과를 유지하기 위한 Session State 초기화
if "search_results" not in st.session_state:
    st.session_state.search_results =[]
if "current_keyword" not in st.session_state:
    st.session_state.current_keyword = ""

# ==========================================
# 3. 유틸리티 함수 정의
# ==========================================
def extract_json_from_text(text: str):
    """
    Gemini 응답 텍스트에서 JSON 배열 부분만 안전하게 추출하는 함수.
    (Search Grounding과 강제 JSON Schema를 동시에 쓸 수 없으므로, 
     프롬프트로 JSON을 유도한 뒤 정규식으로 파싱합니다.)
    """
    # ```json ... ``` 같은 마크다운 블록 제거
    text = text.replace("```json", "").replace("```", "").strip()
    
    # 첫 번째 '[' 와 마지막 ']' 사이의 문자열 추출
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None

def save_to_supabase(item: dict, keyword: str):
    """특정 뉴스 기사를 Supabase DB에 저장하는 함수"""
    data = {
        "keyword": keyword,
        "title": item.get("title", "제목 없음"),
        "source": item.get("source", "출처 불명"),
        "news_date": item.get("news_date", None),
        "url": item.get("url", ""),
        "summary": item.get("summary", "")
    }
    
    try:
        # Supabase 'news_history' 테이블에 데이터 삽입
        response = supabase.table("news_history").insert(data).execute()
        st.toast(f"✅ 저장 완료: {data['title'][:15]}...")
    except Exception as e:
        st.error("❌ 저장 실패! (이미 저장된 뉴스이거나 DB 오류입니다.)")

# ==========================================
# 4. 탭 기반 UI 구성
# ==========================================
tab1, tab2, tab3 = st.tabs(["🔍 뉴스 검색", "💾 저장된 뉴스", "📊 대시보드"])

# ------------------------------------------
# 탭 1: 뉴스 검색 및 결과 보기
# ------------------------------------------
with tab1:
    st.subheader("키워드로 최신 뉴스 검색")
    
    with st.form("search_form"):
        col1, col2 = st.columns([4, 1])
        with col1:
            keyword_input = st.text_input("검색할 키워드를 입력하세요", placeholder="예: 인공지능 트렌드, 전기차 배터리...")
        with col2:
            st.markdown("<br>", unsafe_allow_html=True) # 버튼 높이 맞춤
            submitted = st.form_submit_button("검색하기", use_container_width=True)

    if submitted and keyword_input:
        st.session_state.current_keyword = keyword_input
        with st.spinner(f"'{keyword_input}'에 대한 최신 뉴스를 검색 중입니다..."):
            # 프롬프트: JSON 형태로 반환하도록 강력하게 지시
            prompt = f"""
            최신 뉴스 검색기를 실행하여 '{keyword_input}'에 대한 가장 최신 뉴스 기사 5건을 검색해줘.[
            반드시 지켜야 할 규칙]
            1. 뉴스기사는 반드시 존재하고 링크도 실제 원본기사로 연결되도록 여러번 체크해.
            2. 응답은 반드시 마크다운(```json) 없이 순수한 JSON Array([]) 형태여야 해.
            3. 각 기사는 다음 키를 가진 객체여야 해.
               - "title": 기사 제목 (문자열)
               - "source": 언론사명 (문자열)
               - "news_date": 기사 발행 날짜 (YYYY-MM-DD 형식의 문자열, 모르면 null)
               - "url": 기사 원본 링크 (반드시 접속 가능한 실제 유효한 URL이어야 함)
               - "summary": 기사 요약 (핵심 내용 3~4문장)
            """
            
            try:
                # Gemini API 호출 (최신 SDK 문법)
                # 모델은 가볍고 빠른 gemini-2.5-flash-lite (도구 오류 시 gemini-2.5-flash로 변경)
                response = gemini_client.models.generate_content(
                    model='gemini-2.5-flash-lite',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        # Google Search Grounding 도구 활성화
                        tools=[{'google_search': {}}],
                        temperature=0.2, 
                    )
                )
                
                # 결과 파싱
                parsed_json = extract_json_from_text(response.text)
                
                if parsed_json and isinstance(parsed_json, list):
                    st.session_state.search_results = parsed_json
                    st.success("검색을 완료했습니다!")
                else:
                    st.error("JSON 파싱에 실패했습니다. 다시 시도해 주세요.")
                    st.write("응답 원본:", response.text)
            
            except Exception as e:
                st.error(f"API 호출 중 오류가 발생했습니다: {e}")

    # 검색 결과 출력 (카드 형태)
    if st.session_state.search_results:
        st.markdown("### 📋 검색 결과")
        
        for idx, item in enumerate(st.session_state.search_results):
            # Streamlit 컨테이너를 활용한 카드 UI
            with st.container(border=True):
                st.markdown(f"#### [{item.get('title')}]({item.get('url')})")
                st.caption(f"**출처:** {item.get('source')} | **날짜:** {item.get('news_date')}")
                st.write(item.get('summary'))
                
                # 원본 URL 링크 버튼 & DB 저장 버튼
                col_btn1, col_btn2 = st.columns([1, 1])
                with col_btn1:
                    st.link_button("🌐 원본 기사 보기", item.get('url'), use_container_width=True)
                with col_btn2:
                    # 버튼 클릭 시 on_click 콜백으로 DB 저장 함수 실행
                    st.button(
                        "💾 이 기사 저장하기", 
                        key=f"save_btn_{idx}", 
                        on_click=save_to_supabase, 
                        args=(item, st.session_state.current_keyword),
                        use_container_width=True
                    )

        # CSV 다운로드 기능
        st.divider()
        st.markdown("#### 📥 결과 다운로드")
        df_results = pd.DataFrame(st.session_state.search_results)
        # 한글 깨짐 방지를 위해 utf-8-sig 인코딩 사용
        csv_data = df_results.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📊 전체 검색 결과 CSV로 다운로드",
            data=csv_data,
            file_name=f"{st.session_state.current_keyword}_news_results.csv",
            mime="text/csv",
        )

# ------------------------------------------
# 탭 2: 저장된 뉴스 조회
# ------------------------------------------
with tab2:
    st.subheader("💾 데이터베이스에 저장된 뉴스")
    
    # Supabase에서 데이터 불러오기 버튼
    if st.button("🔄 저장된 데이터 새로고침"):
        st.rerun()

    try:
        # Supabase에서 전체 데이터 가져오기 (최신순)
        db_response = supabase.table("news_history").select("*").order("created_at", desc=True).execute()
        saved_data = db_response.data
        
        if saved_data:
            df_saved = pd.DataFrame(saved_data)
            # 보여줄 컬럼만 선택 및 이름 변경
            df_display = df_saved[['keyword', 'title', 'source', 'news_date', 'url', 'created_at']]
            df_display.columns =['키워드', '기사 제목', '언론사', '기사 날짜', 'URL', '저장 일시']
            
            # DataFrame으로 화면에 예쁘게 표출
            st.dataframe(
                df_display, 
                column_config={"URL": st.column_config.LinkColumn("기사 링크")}, 
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("아직 저장된 뉴스가 없습니다.")
    except Exception as e:
        st.error(f"데이터베이스 조회 중 오류가 발생했습니다: {e}")

# ------------------------------------------
# 탭 3: 대시보드
# ------------------------------------------
with tab3:
    st.subheader("📊 스크랩 통계 대시보드")
    
    try:
        # DB에서 데이터 가져오기
        dashboard_res = supabase.table("news_history").select("keyword, created_at").execute()
        dash_data = dashboard_res.data
        
        if dash_data:
            df_dash = pd.DataFrame(dash_data)
            # created_at을 날짜 형식으로 변환 (시간 제외)
            df_dash['created_at'] = pd.to_datetime(df_dash['created_at']).dt.date
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("**📌 키워드별 저장 건수**")
                keyword_counts = df_dash['keyword'].value_counts()
                st.bar_chart(keyword_counts)
                
            with col2:
                st.markdown("**📅 일자별 저장 건수**")
                date_counts = df_dash['created_at'].value_counts().sort_index()
                st.line_chart(date_counts)
        else:
            st.info("통계를 표시할 데이터가 부족합니다.")
    except Exception as e:
        st.error("대시보드 데이터를 불러오지 못했습니다.")
