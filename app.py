import streamlit as st
import json
import re
import pandas as pd
from google import genai
from google.genai import types

# ----------------------------------------------------
# 1. 페이지 기본 설정 및 안내 문구
# ----------------------------------------------------
st.set_page_config(page_title="최신 뉴스 검색기", page_icon="📰", layout="centered")

st.title("최신 뉴스 검색기 📰")
# 무료 티어 제한 안내 문구 (요구사항 3)
st.warning("💡 **안내:** 현재 Gemini API 무료 티어를 사용 중입니다. (분당 15회 요청 제한이 발생할 수 있습니다.)")

# ----------------------------------------------------
# 2. 검색 UI 및 Session State 초기화
# ----------------------------------------------------
# 다운로드 버튼을 눌러도 화면이 날아가지 않도록 상태(State)를 저장합니다.
if 'news_data' not in st.session_state:
    st.session_state['news_data'] = None

keyword = st.text_input("궁금한 최신 뉴스 키워드를 입력하세요 (예: AI 인공지능, 한국 야구):")

# ----------------------------------------------------
# 3. 뉴스 검색 버튼 클릭 시 동작
# ----------------------------------------------------
if st.button("뉴스 검색 🔍"):
    if not keyword:
        st.error("키워드를 입력해주세요!")
    else:
        with st.spinner(f"'{keyword}' 관련 최신 뉴스를 검색하고 요약하는 중입니다..."):
            try:
                # 클라이언트 초기화 (Codespaces Secret에 등록한 GEMINI_API_KEY를 자동 인식함)
                client = genai.Client()
                
                # 프롬프트: 검색 도구와 강제 JSON 기능은 동시 사용이 안되므로, 
                # 프롬프트 자체에서 JSON 형태의 텍스트만 출력하도록 강력하게 지시합니다.
                prompt = f"""
                다음 키워드에 대한 최신 뉴스 5건을 검색해줘: {keyword}
                
                반드시 아래의 JSON 배열(Array) 형식으로만 응답해야 해. 
                다른 인사말이나 마크다운(```json 등)은 절대 포함하지 말고, 오직 순수한 JSON만 출력해.[
                    {{
                        "title": "뉴스 기사의 정확한 제목",
                        "url": "뉴스 기사의 원본 링크",
                        "summary": "해당 뉴스 내용에 대한 3~4문장의 상세한 요약"
                    }}
                ]
                """
                
                # Gemini 2.5 Flash Lite 모델 호출 (Google Search 도구 포함)
                response = client.models.generate_content(
                    model='gemini-2.5-flash-lite',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{"google_search": {}}], # 구글 검색 기능 활성화
                        temperature=0.2, # 일관된 JSON 출력을 위해 온도를 낮춤
                    )
                )
                
                # 모델이 응답한 텍스트에서 안전하게 JSON 부분만 추출 (정규표현식 사용)
                raw_text = response.text
                match = re.search(r'\[\s*\{.*?\}\s*\]', raw_text, re.DOTALL)
                
                if match:
                    json_str = match.group(0)
                    # 문자열을 파이썬 리스트(딕셔너리)로 변환
                    news_data = json.loads(json_str) 
                    # 결과를 세션 스테이트에 저장 (화면 유지용)
                    st.session_state['news_data'] = news_data
                else:
                    st.error("결과를 JSON으로 변환하는 데 실패했습니다. 다시 시도해 주세요.")
                    st.write("원본 응답:", raw_text)
                    
            except Exception as e:
                st.error(f"API 호출 중 오류가 발생했습니다: {e}")

# ----------------------------------------------------
# 4. 결과 출력 및 CSV 다운로드 (요구사항 2)
# ----------------------------------------------------
if st.session_state['news_data']:
    news_list = st.session_state['news_data']
    
    st.success("검색이 완료되었습니다!")
    
    # CSV 다운로드 기능 준비
    df = pd.DataFrame(news_list)
    # 한글 깨짐 방지를 위해 utf-8-sig로 인코딩
    csv = df.to_csv(index=False).encode('utf-8-sig') 
    
    # 다운로드 버튼 (누르더라도 Session State 덕분에 화면이 지워지지 않음)
    st.download_button(
        label="📥 결과 CSV 파일로 다운로드",
        data=csv,
        file_name=f"latest_news_{keyword}.csv",
        mime="text/csv",
    )
    
    st.markdown("---")
    
    # 카드 형태로 결과 예쁘게 보여주기
    for idx, news in enumerate(news_list):
        # 테두리가 있는 컨테이너 생성 (카드 UI 효과)
        with st.container(border=True):
            st.subheader(f"{idx+1}. {news.get('title', '제목 없음')}")
            st.write(f"**요약:** {news.get('summary', '요약 정보가 없습니다.')}")
            st.markdown(f"[🔗 원본 기사 읽기]({news.get('url', '#')})")