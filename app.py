import streamlit as st
import pandas as pd
import os
import json
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. Streamlit UI 기본 설정 및 헤더
# ---------------------------------------------------------
st.set_page_config(page_title="최신 뉴스 검색기", page_icon="📰", layout="centered")

st.title("📰 최신 뉴스 검색 앱 (with Gemini)")
st.info("💡 **안내:** 이 앱은 `gemini-2.5-flash-lite` 모델의 무료 티어를 사용합니다.\n"
        "(제한: 분당 15회, 일일 1,500회 요청 가능). \n"
        "너무 짧은 시간에 연속으로 검색하면 에러가 발생할 수 있으니 천천히 이용해 주세요.")

# ---------------------------------------------------------
# 2. API 키 확인 및 Gemini 클라이언트 초기화
# ---------------------------------------------------------
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    st.error("🚨 환경 변수에 'GEMINI_API_KEY'가 설정되지 않았습니다.")
    st.stop()

# 명시적으로 API 키를 전달
client = genai.Client(api_key=api_key)

# ---------------------------------------------------------
# 3. 사용자 입력 및 검색 실행
# ---------------------------------------------------------
keyword = st.text_input("🔍 검색하고 싶은 뉴스 키워드를 입력하세요 (예: 인공지능, 전기차, 부동산 등)")

if st.button("뉴스 검색하기", type="primary"):
    if not keyword:
        st.warning("키워드를 입력해 주세요!")
    else:
        with st.spinner(f"'{keyword}'에 대한 최신 뉴스를 구글에서 검색하고 요약하는 중입니다... ⏳"):
            try:
                # 프롬프트를 아주 강력하게 작성 (JSON 형태 요구)
                prompt = f"""
'{keyword}'에 대한 최신 뉴스를 구글에서 검색해줘.
정확히 5건의 서로 다른 최근 뉴스를 찾아야 해.

[주의사항]
1. 각 뉴스에 대해 'title'(기사 제목), 'url'(원본 기사 URL), 'summary'(3~4문장 분량의 한국어 요약)를 포함해.
2. 답변은 반드시 아래 예시와 같이 완벽한 JSON 배열(Array) 형태로만 출력해.
3. JSON 외에 인사말이나 설명 등 다른 텍스트는 절대 포함하지 마.[출력 예시][
  {{
    "title": "첫번째 기사 제목",
    "url": "https://...",
    "summary": "첫번째 기사 요약..."
  }},
  {{
    "title": "두번째 기사 제목",
    "url": "https://...",
    "summary": "두번째 기사 요약..."
  }}
]
"""

                # Gemini API 호출 (에러가 나던 JSON 설정 제거, 검색 도구만 유지)
                response = client.models.generate_content(
                    model='gemini-2.5-flash-lite',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{"google_search": {}}],
                        temperature=0.2, # 온도를 낮춰서 더 규칙을 잘 따르도록 설정
                    )
                )

                # ---------------------------------------------------------
                # 4. 문자열(Text) 응답을 파이썬 List/Dict(JSON)로 변환
                # ---------------------------------------------------------
                raw_text = response.text.strip()
                
                # AI가 마크다운 코드블록(```json ... ```)으로 감싸서 줄 경우를 대비해 벗겨내기
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                if raw_text.startswith("```"):
                    raw_text = raw_text[3:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                    
                raw_text = raw_text.strip()

                # 문자열을 파이썬 객체로 변환
                news_data = json.loads(raw_text)
                
                if not news_data:
                    st.warning("검색된 뉴스 결과가 없습니다.")
                else:
                    st.success("뉴스 검색 및 요약이 완료되었습니다! 🎉")
                    
                    # ---------------------------------------------------------
                    # 5. 카드 형태 UI 출력 및 CSV 데이터 수집
                    # ---------------------------------------------------------
                    csv_data =[]
                    
                    for i, item in enumerate(news_data):
                        with st.container(border=True):
                            st.subheader(f"{i+1}. {item.get('title', '제목 없음')}")
                            st.write(f"**📝 요약:** {item.get('summary', '요약 없음')}")
                            st.markdown(f"**🔗 원본 링크:** [기사 보러가기]({item.get('url', '#')})")
                            
                        csv_data.append({
                            "번호": i + 1,
                            "제목": item.get('title', ''),
                            "요약": item.get('summary', ''),
                            "URL": item.get('url', '')
                        })
                    
                    # ---------------------------------------------------------
                    # 6. CSV 다운로드 버튼 생성
                    # ---------------------------------------------------------
                    df = pd.DataFrame(csv_data)
                    csv = df.to_csv(index=False).encode('utf-8-sig')
                    
                    st.write("---")
                    st.download_button(
                        label="📥 검색 결과 CSV 파일로 다운로드",
                        data=csv,
                        file_name=f"{keyword}_최신뉴스.csv",
                        mime="text/csv"
                    )

            except json.JSONDecodeError:
                st.error("AI가 올바른 JSON 형태로 답변을 주지 않았습니다. 다시 시도해 주세요.")
                st.write("원본 응답 데이터:", response.text) # 원인 파악을 위해 출력
            except Exception as e:
                st.error(f"오류가 발생했습니다. 상세 오류: {e}")