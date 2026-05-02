import streamlit as st
import pandas as pd
import json
import re
import os
from google import genai
from google.genai import types

# 1. 페이지 기본 설정 (앱의 제목과 아이콘 설정)
st.set_page_config(page_title="최신 뉴스 검색기", page_icon="📰", layout="centered")

# 2. 세션 상태(Session State) 초기화
# Streamlit은 버튼을 누를 때마다 화면이 새로고침 됩니다.
# 다운로드 버튼을 눌렀을 때 검색 결과가 날아가지 않도록 데이터를 임시 저장하는 공간입니다.
if "news_data" not in st.session_state:
    st.session_state.news_data = None

# 3. 화면 UI 구성
st.title("📰 최신 뉴스 검색 앱")
st.warning("💡 **Gemini API 무료 티어 제한 안내:** 분당 최대 15회까지만 요청이 가능합니다. 너무 자주 검색 버튼을 누르면 오류가 발생할 수 있으니 천천히 사용해주세요.")

# 사용자로부터 검색어 입력받기
keyword = st.text_input("검색할 뉴스 키워드를 입력하세요 (예: 인공지능, 전기차, 애플 등)")

# 4. 검색 버튼이 눌렸을 때의 동작
if st.button("뉴스 검색"):
    if not keyword:
        st.error("키워드를 입력해주세요!")
    else:
        with st.spinner("최신 뉴스를 검색하고 요약하는 중입니다... (잠시만 기다려주세요)"):
            try:
                # 환경변수에 등록한 GEMINI_API_KEY를 자동으로 읽어와 클라이언트를 생성합니다.
                client = genai.Client()
                
                # 모델에게 구체적인 JSON 형태의 응답을 요구하는 프롬프트 작성
                # (Search 도구와 JSON 강제 모드를 같이 쓸 수 없기 때문에 말로 설명해서 유도합니다)
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
                
                # Gemini 2.5 Flash Lite 모델 호출 및 구글 검색 기능 활성화
                response = client.models.generate_content(
                    model='gemini-2.5-flash-lite',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{"google_search": {}}], # 구글 검색(Search Grounding) 활성화
                        temperature=0.2, # 일관된 출력을 위해 창의성을 약간 낮춤
                    )
                )
                
                # 5. 텍스트 결과물에서 JSON 데이터만 안전하게 추출하기
                response_text = response.text.strip()
                
                # AI가 혹시라도 ```json [내용] ``` 형태로 답변할 경우를 대비해 대괄호 안의 내용만 뽑아냅니다.
                json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
                if json_match:
                    clean_json_text = json_match.group(0)
                else:
                    clean_json_text = response_text
                    
                # 문자열을 파이썬 딕셔너리 리스트로 변환
                news_list = json.loads(clean_json_text)
                
                # 변환된 데이터를 세션 상태에 저장하여 다운로드 시에도 유지되게 함
                st.session_state.news_data = news_list
                
            except Exception as e:
                st.error(f"오류가 발생했습니다.\n(검색결과를 처리하는 중 문제가 발생했거나 무료 제공량을 초과했을 수 있습니다.)\n에러 내용: {e}")

# 6. 결과 화면 출력 및 CSV 다운로드 (데이터가 있을 때만 표시)
if st.session_state.news_data:
    st.divider() # 가로줄 긋기
    st.subheader(f"✨ '{keyword}' 관련 최신 뉴스 결과")
    
    # 카드 형태로 뉴스 하나씩 예쁘게 출력하기
    for item in st.session_state.news_data:
        # border=True 옵션으로 카드 느낌의 테두리를 만들어줍니다.
        with st.container(border=True):
            st.markdown(f"### {item.get('title', '제목 없음')}")
            st.caption(f"🏢 **출처:** {item.get('source', '알 수 없음')} &nbsp;|&nbsp; 🕒 **날짜:** {item.get('date', '알 수 없음')}")
            st.write(item.get('summary', '요약 내용이 없습니다.'))
            st.markdown(f"[🔗 원본 기사 읽기]({item.get('url', '#')})")
            
    st.divider()
    
    # 7. CSV 다운로드 버튼 구현
    # 리스트 데이터를 pandas 데이터프레임으로 변환
    df = pd.DataFrame(st.session_state.news_data)
    # 한글이 엑셀에서 깨지지 않도록 utf-8-sig 로 인코딩
    csv_data = df.to_csv(index=False).encode('utf-8-sig')
    
    st.download_button(
        label="📥 검색 결과 CSV로 다운로드",
        data=csv_data,
        file_name=f"news_search_{keyword}.csv",
        mime="text/csv"
    )