import streamlit as st
import pandas as pd
import os
from pydantic import BaseModel
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. 구조화된 출력(JSON)을 위한 Pydantic 데이터 모델 정의
# ---------------------------------------------------------
class NewsItem(BaseModel):
    title: str
    url: str
    summary: str

class NewsList(BaseModel):
    news: list[NewsItem]

# ---------------------------------------------------------
# 2. Streamlit UI 기본 설정 및 헤더
# ---------------------------------------------------------
st.set_page_config(page_title="최신 뉴스 검색기", page_icon="📰", layout="centered")

st.title("📰 최신 뉴스 검색 앱 (with Gemini)")
# 무료 티어 제한 안내 문구 (요구사항 3)
st.info("💡 **안내:** 이 앱은 `gemini-2.5-flash-lite` 모델의 무료 티어를 사용합니다.\n"
        "(제한: 분당 15회, 일일 1,500회 요청 가능). \n"
        "너무 짧은 시간에 연속으로 검색하면 에러가 발생할 수 있으니 천천히 이용해 주세요.")

# ---------------------------------------------------------
# 3. API 키 확인 및 Gemini 클라이언트 초기화
# ---------------------------------------------------------
# GitHub Codespaces Secret에 등록한 키를 자동으로 불러옵니다.
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    st.error("🚨 환경 변수에 'GEMINI_API_KEY'가 설정되지 않았습니다. GitHub Settings에서 Secret을 확인하세요.")
    st.stop()

# 최신 google-genai SDK 클라이언트 생성
client = genai.Client()

# ---------------------------------------------------------
# 4. 사용자 입력 및 검색 실행
# ---------------------------------------------------------
keyword = st.text_input("🔍 검색하고 싶은 뉴스 키워드를 입력하세요 (예: 인공지능, 전기차, 부동산 등)")

if st.button("뉴스 검색하기", type="primary"):
    if not keyword:
        st.warning("키워드를 입력해 주세요!")
    else:
        with st.spinner(f"'{keyword}'에 대한 최신 뉴스를 구글에서 검색하고 요약하는 중입니다... ⏳"):
            try:
                # 프롬프트 작성
                prompt = (
                    f"'{keyword}'에 대한 최신 뉴스를 구글에서 검색해줘. "
                    f"정확히 5건의 서로 다른 최근 뉴스를 찾아야 해. "
                    f"각 뉴스에 대해 '기사 제목', '원본 기사 URL', 그리고 '3~4문장 분량의 한국어 요약'을 제공해줘."
                )

                # Gemini API 호출 (Search Grounding 및 JSON 스키마 적용)
                response = client.models.generate_content(
                    model='gemini-2.5-flash-lite',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        # 구글 검색 도구(Search Grounding) 활성화
                        tools=[{"google_search": {}}],
                        # JSON 형태로 응답하도록 강제하며, Pydantic 모델을 스키마로 사용
                        response_mime_type="application/json",
                        response_schema=NewsList,
                        temperature=0.3,
                    )
                )

                # 응답 결과를 Pydantic 객체로 바로 접근 가능
                news_data = response.parsed.news
                
                if not news_data:
                    st.warning("검색된 뉴스 결과가 없습니다.")
                else:
                    st.success("뉴스 검색 및 요약이 완료되었습니다! 🎉")
                    
                    # ---------------------------------------------------------
                    # 5. 카드 형태 UI 출력 (요구사항 2)
                    # ---------------------------------------------------------
                    # CSV 다운로드를 위해 데이터를 리스트로 모아둡니다.
                    csv_data =[]
                    
                    for i, item in enumerate(news_data):
                        # st.container(border=True)를 사용해 예쁜 카드 형태로 표시
                        with st.container(border=True):
                            st.subheader(f"{i+1}. {item.title}")
                            st.write(f"**📝 요약:** {item.summary}")
                            st.markdown(f"**🔗 원본 링크:**[기사 보러가기]({item.url})")
                            
                        # CSV용 데이터 수집
                        csv_data.append({
                            "번호": i + 1,
                            "제목": item.title,
                            "요약": item.summary,
                            "URL": item.url
                        })
                    
                    # ---------------------------------------------------------
                    # 6. CSV 다운로드 버튼 생성 (요구사항 2)
                    # ---------------------------------------------------------
                    df = pd.DataFrame(csv_data)
                    # 한글이 깨지지 않도록 utf-8-sig로 인코딩
                    csv = df.to_csv(index=False).encode('utf-8-sig')
                    
                    st.write("---")
                    st.download_button(
                        label="📥 검색 결과 CSV 파일로 다운로드",
                        data=csv,
                        file_name=f"{keyword}_최신뉴스.csv",
                        mime="text/csv"
                    )

            except Exception as e:
                st.error(f"오류가 발생했습니다. (무료 티어 한도 초과이거나 검색 결과를 가져오는 중 문제가 생겼을 수 있습니다.)\n\n상세 오류: {e}")