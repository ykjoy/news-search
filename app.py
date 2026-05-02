import streamlit as st
import pandas as pd
import json
import re
from google import genai
from google.genai import types
from supabase import create_client, Client

# 1. 페이지 기본 설정
st.set_page_config(page_title="최신 뉴스 검색기", page_icon="📰", layout="wide")

# 2. 비밀 키(Secrets) 불러오기
# st.secrets를 통해 Streamlit Cloud에 저장된 보안 키들을 안전하게 가져옵니다.
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# Supabase 클라이언트 연결 설정
# 이 클라이언트를 통해 데이터베이스에 데이터를 넣거나 뺄 수 있습니다.
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 3. 세션 상태(Session State) 초기화
if "news_data" not in st.session_state:
    st.session_state.news_data = None
if "last_keyword" not in st.session_state:
    st.session_state.last_keyword = ""

# 4. 화면을 2개의 탭으로 분리
tab1, tab2 = st.tabs(["🔍 뉴스 검색하기", "💾 저장된 뉴스 보기"])

# ==========================================
# 탭 1: 검색하기 (기존 기능 + 자동 저장)
# ==========================================
with tab1:
    st.title("📰 최신 뉴스 검색 앱")
    st.warning("💡 **Gemini API 안내:** 분당 최대 15회까지만 요청이 가능합니다. 천천히 사용해주세요.")

    # 사용자로부터 검색어 입력받기
    keyword = st.text_input("검색할 뉴스 키워드를 입력하세요 (예: 인공지능, 전기차, 애플 등)")

    # 검색 버튼이 눌렸을 때의 동작
    if st.button("뉴스 검색"):
        if not keyword:
            st.error("키워드를 입력해주세요!")
        else:
            with st.spinner("최신 뉴스를 검색하고 요약 및 저장하는 중입니다... (잠시만 기다려주세요)"):
                try:
                    # Gemini 클라이언트 생성 (비밀키 직접 전달)
                    client = genai.Client(api_key=GEMINI_API_KEY)
                    
                    # 프롬프트 작성
                    prompt = f"""
                    다음 키워드에 대한 가장 최신 뉴스 5건을 검색해주세요: "{keyword}"
                    
                    검색된 결과를 바탕으로 반드시 아래의 JSON 배열 형식으로만 응답해주세요. 
                    마크다운 코드 블록(```json 등)을 절대 사용하지 말고, 순수 JSON 텍스트만 출력하세요.[
                      {{
                        "title": "뉴스 기사 제목",
                        "source": "언론사 이름",
                        "date": "발행일 또는 시간",
                        "url": "기사 원본 링크",
                        "summary": "기사 내용에 대한 3~4문장 분량의 상세하고 알기 쉬운 요약"
                      }}
                    ]
                    """
                    
                    # 모델 호출 및 구글 검색 기능 활성화
                    response = client.models.generate_content(
                        model='gemini-2.5-flash-lite',
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            tools=[{"google_search": {}}], 
                            temperature=0.2, 
                        )
                    )
                    
                    # JSON 데이터만 안전하게 추출
                    response_text = response.text.strip()
                    json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
                    if json_match:
                        clean_json_text = json_match.group(0)
                    else:
                        clean_json_text = response_text
                        
                    # 문자열을 파이썬 리스트(딕셔너리)로 변환
                    news_list = json.loads(clean_json_text)
                    
                    # 추출한 데이터를 세션에 저장
                    st.session_state.news_data = news_list
                    st.session_state.last_keyword = keyword
                    
                    # [추가된 기능] Supabase에 자동 저장
                    save_count = 0
                    for item in news_list:
                        db_data = {
                            "keyword": keyword,
                            "title": item.get("title", ""),
                            "source": item.get("source", ""),
                            "news_date": item.get("date", ""),
                            "url": item.get("url", ""),
                            "summary": item.get("summary", "")
                        }
                        # upsert: url이 같으면 덮어쓰고, 다르면 새로 추가 (중복 방지)
                        # on_conflict="url" 옵션은 url 컬럼이 UNIQUE로 설정되어 있어야 작동합니다.
                        supabase.table("news_history").upsert(db_data, on_conflict="url").execute()
                        save_count += 1
                        
                    st.success(f"검색 완료! {save_count}개의 기사가 데이터베이스에 자동 저장되었습니다.")
                    
                except Exception as e:
                    st.error(f"오류가 발생했습니다.\n에러 내용: {e}")

    # 결과 화면 출력
    if st.session_state.news_data:
        st.divider() 
        st.subheader(f"✨ '{st.session_state.last_keyword}' 관련 최신 뉴스 결과")
        
        for item in st.session_state.news_data:
            with st.container(border=True):
                st.markdown(f"### {item.get('title', '제목 없음')}")
                st.caption(f"🏢 **출처:** {item.get('source', '알 수 없음')} &nbsp;|&nbsp; 🕒 **날짜:** {item.get('date', '알 수 없음')}")
                st.write(item.get('summary', '요약 내용이 없습니다.'))
                st.markdown(f"[🔗 원본 기사 읽기]({item.get('url', '#')})")


# ==========================================
# 탭 2: 저장된 뉴스 보기 (키워드 검색 + 표로 표시)
# ==========================================
with tab2:
    st.title("💾 저장된 뉴스 기록 보기")
    st.write("데이터베이스에 저장된 과거의 뉴스 검색 기록을 확인합니다.")
    
    # DB에서 검색할 키워드 입력
    search_kw = st.text_input("필터링할 키워드를 입력하세요 (비워두면 전체 보기)", key="db_search")
    
    if st.button("저장된 데이터 불러오기"):
        with st.spinner("데이터베이스에서 불러오는 중..."):
            try:
                # 키워드가 있으면 해당 키워드가 포함된 데이터만 검색 (ilike는 대소문자 구분 없이 검색)
                if search_kw:
                    response = supabase.table("news_history").select("*").ilike("keyword", f"%{search_kw}%").order("created_at", desc=True).execute()
                else:
                    # 키워드가 없으면 전체 데이터 최신순으로 가져오기
                    response = supabase.table("news_history").select("*").order("created_at", desc=True).execute()
                
                db_data = response.data
                
                if not db_data:
                    st.info("저장된 데이터가 없습니다.")
                else:
                    st.success(f"총 {len(db_data)}개의 데이터를 불러왔습니다.")
                    
                    # 데이터를 표(Dataframe) 형태로 변환하여 예쁘게 출력
                    df = pd.DataFrame(db_data)
                    
                    # 사용자에게 보여주기 좋게 컬럼 이름 변경 및 순서 정렬
                    df = df[['keyword', 'title', 'source', 'news_date', 'summary', 'url', 'created_at']]
                    df.columns =['검색 키워드', '기사 제목', '언론사', '발행일', '요약', '링크', '저장시간']
                    
                    # st.dataframe으로 깔끔한 표 출력
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    
            except Exception as e:
                st.error(f"데이터를 불러오는 중 오류가 발생했습니다: {e}")