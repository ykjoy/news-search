import streamlit as st
from google import genai
from google.genai import types
import json
import re
import pandas as pd
from supabase import create_client, Client
import plotly.express as px

# -------------------------------------------------------------------
# 1. 페이지 기본 설정 및 환경변수(Secrets) 불러오기
# -------------------------------------------------------------------
st.set_page_config(page_title="최신 뉴스 검색 AI", page_icon="📰", layout="wide")

# Streamlit secrets에서 API 키와 Supabase 정보를 가져옵니다.
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# Supabase 클라이언트 초기화
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Gemini 클라이언트 초기화
client = genai.Client(api_key=GEMINI_API_KEY)

# -------------------------------------------------------------------
# 2. 헬퍼 함수 (Helper Functions)
# -------------------------------------------------------------------
def extract_json(text):
    """
    LLM의 응답에서 마크다운(```json ... ```)을 제거하고 순수 JSON 객체로 변환합니다.
    (검색 기능과 강제 JSON 포맷을 동시에 사용할 수 없으므로, 프롬프트 엔지니어링 후 직접 파싱)
    """
    match = re.search(r'```json\n(.*?)\n```', text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return json.loads(text)

def search_news(keyword):
    """Gemini API의 Search Grounding(구글 검색 도구)을 활용해 뉴스를 검색하고 요약합니다."""
    prompt = f"""
    당신은 뉴스 큐레이터입니다. '{keyword}'에 대한 가장 최신 뉴스 5건을 구글 검색을 통해 찾아주세요.
    검색 결과를 바탕으로 아래의 JSON 배열 형식으로만 응답해야 합니다. 마크다운 외의 다른 설명은 절대 추가하지 마세요.
    요약(summary)은 반드시 한국어로 3~4문장으로 자세히 작성해주세요.[
        {{
            "title": "뉴스 제목",
            "source": "언론사 이름",
            "date": "YYYY-MM-DD",
            "url": "기사 원본 URL",
            "summary": "기사 내용 3~4문장 요약"
        }}
    ]
    """
    
    # 모델은 요구사항에 맞게 빠르고 가벼운 gemini-2.5-flash-lite 사용
    response = client.models.generate_content(
        model='gemini-2.5-flash-lite',
        contents=prompt,
        config=types.GenerateContentConfig(
            # Google Search 도구 활성화
            tools=[{"google_search": {}}],
            temperature=0.2,
        )
    )
    
    return extract_json(response.text)

def save_to_supabase(news_item, keyword):
    """선택한 뉴스 1건을 Supabase에 저장합니다."""
    data = {
        "keyword": keyword,
        "title": news_item["title"],
        "source": news_item["source"],
        "date": news_item["date"],
        "url": news_item["url"],
        "summary": news_item["summary"]
    }
    # supabase 삽입 쿼리
    supabase.table("news_history").insert(data).execute()

# -------------------------------------------------------------------
# 3. 사이드바(Sidebar) UI 및 안내 문구
# -------------------------------------------------------------------
st.sidebar.title("📰 AI 뉴스 큐레이터")
menu = st.sidebar.radio("메뉴 이동", ["🔍 뉴스 검색", "💾 저장된 뉴스", "📊 대시보드"])

#[요구사항 4] 모델 제한 안내 문구
st.sidebar.markdown("---")
st.sidebar.info(
    "💡 **무료 티어 제한 안내**\n\n"
    "본 앱은 `gemini-2.5-flash-lite` 모델의 무료 티어를 사용합니다. "
    "(요청 한도: **분당 15회**, 일 1,500회). "
    "한도 초과 시 검색이 일시적으로 제한될 수 있습니다."
)

# -------------------------------------------------------------------
# 4. 메인 화면 구성 (메뉴에 따른 라우팅)
# -------------------------------------------------------------------

# ================= 메뉴 1: 뉴스 검색 =================
if menu == "🔍 뉴스 검색":
    st.title("🔍 키워드로 최신 뉴스 검색")
    
    # 검색어 입력
    keyword = st.text_input("검색하고 싶은 뉴스 키워드를 입력하세요. (예: AI, 반도체, 올림픽)")
    
    if st.button("검색하기", type="primary"):
        if keyword:
            with st.spinner('구글 검색을 통해 최신 뉴스를 수집하고 요약하는 중입니다...'):
                try:
                    # 검색 실행 및 Session State에 저장 (버튼 클릭 시 화면 초기화 방지)
                    results = search_news(keyword)
                    st.session_state["news_results"] = results
                    st.session_state["current_keyword"] = keyword
                except Exception as e:
                    st.error(f"오류가 발생했습니다. (JSON 변환 오류 또는 API 한도 초과일 수 있습니다)\n\n상세: {e}")
        else:
            st.warning("키워드를 입력해주세요.")

    # Session State에 검색 결과가 있다면 화면에 출력
    if "news_results" in st.session_state:
        st.markdown("---")
        results = st.session_state["news_results"]
        curr_keyword = st.session_state["current_keyword"]
        
        st.subheader(f"'{curr_keyword}' 검색 결과 (총 {len(results)}건)")
        
        #[요구사항 3-1] 카드 형태로 출력
        for i, news in enumerate(results):
            # st.container(border=True)를 이용해 예쁜 카드 형태 구현
            with st.container(border=True):
                st.markdown(f"### {news['title']}")
                st.caption(f"**출처**: {news['source']} | **날짜**: {news['date']}")
                st.write(news['summary'])
                
                # 버튼을 가로로 배치
                col1, col2 = st.columns([1, 8])
                with col1:
                    # 동적인 key 할당으로 각각의 버튼이 독립적으로 동작하게 함
                    if st.button("💾 저장", key=f"save_{i}"):
                        save_to_supabase(news, curr_keyword)
                        st.toast(f"'{news['title'][:15]}...' 뉴스가 저장되었습니다!", icon="✅")
                with col2:
                    st.markdown(f"[🔗 원본 기사 보러가기]({news['url']})")

        # CSV 다운로드 버튼
        st.markdown("---")
        df_results = pd.DataFrame(results)
        # 한글 깨짐 방지를 위해 utf-8-sig로 인코딩
        csv = df_results.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 검색 결과 CSV로 다운로드",
            data=csv,
            file_name=f"{curr_keyword}_news.csv",
            mime="text/csv",
        )

# ================= 메뉴 2: 저장된 뉴스 =================
elif menu == "💾 저장된 뉴스":
    st.title("💾 저장된 뉴스 목록")
    
    # Supabase에서 데이터 조회
    response = supabase.table("news_history").select("*").order("created_at", desc=True).execute()
    data = response.data
    
    if data:
        df_saved = pd.DataFrame(data)
        # 화면에 보여줄 열 순서 정리 및 이름 변경
        df_display = df_saved[['keyword', 'title', 'source', 'date', 'summary', 'url', 'created_at']]
        st.dataframe(
            df_display, 
            column_config={
                "url": st.column_config.LinkColumn("원본 링크")
            },
            hide_index=True,
            use_container_width=True
        )
    else:
        st.info("아직 저장된 뉴스가 없습니다.")

# ================= 메뉴 3: 대시보드 =================
elif menu == "📊 대시보드":
    st.title("📊 뉴스 저장 통계 대시보드")
    
    response = supabase.table("news_history").select("keyword, created_at").execute()
    data = response.data
    
    if data:
        df = pd.DataFrame(data)
        # 시간 데이터를 날짜(YYYY-MM-DD) 형식으로 변환
        df['created_date'] = pd.to_datetime(df['created_at']).dt.date
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("🔑 키워드별 저장 건수")
            keyword_counts = df['keyword'].value_counts().reset_index()
            keyword_counts.columns = ['keyword', 'count']
            fig1 = px.pie(keyword_counts, values='count', names='keyword', hole=0.4)
            st.plotly_chart(fig1, use_container_width=True)
            
        with col2:
            st.subheader("📅 일자별 저장 건수")
            date_counts = df['created_date'].value_counts().reset_index()
            date_counts.columns = ['date', 'count']
            date_counts = date_counts.sort_values('date')
            fig2 = px.bar(date_counts, x='date', y='count', text='count')
            st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("통계를 생성할 데이터가 부족합니다.")
