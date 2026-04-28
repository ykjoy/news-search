# ---[1. 필요한 도구(라이브러리) 가져오기] ---
import streamlit as st # 웹사이트 화면을 예쁘게 만들어주는 핵심 도구입니다.
import os # 컴퓨터(또는 서버)의 숨겨진 환경 설정에서 비밀번호를 꺼내오는 도구입니다.
import pandas as pd # 데이터를 엑셀(CSV) 표 형태로 예쁘게 정리해 주는 도구입니다.
import feedparser # 구글 검색 API 없이도 무료로 뉴스를 긁어오게 해주는 마법의 도구입니다.
import urllib.parse # 우리가 입력한 한글 검색어를 인터넷 주소에 맞게 변환해 주는 도구입니다.
import google.generativeai as genai # 구글의 최신 인공지능 Gemini를 사용하기 위한 도구입니다.

# ---[2. 비밀 열쇠(API 키) 준비하기] ---
# 깃허브 비밀 금고(Secrets)에 숨겨둔 내 Gemini 키를 몰래 꺼내옵니다.
GEMINI_API_KEY = "AIzaSyDoeK48UFVINqZBN38maVMi_TXri9xdf6o"    #os.getenv("GEMINI_API_KEY")

# 만약 키를 잘 꺼내왔다면, AI에게 "이 열쇠로 준비해 줘!"라고 세팅합니다.
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- [3. 핵심 기능 1: 구글 뉴스 무료 검색 함수] ---
def search_google_news_free(keyword):
    try:
        # 1. 한글 검색어(예: '인공지능')를 인터넷용 글자로 바꿉니다.
        encoded_keyword = urllib.parse.quote(keyword)
        # 2. 구글 뉴스의 한국어 최신 기사를 가져오는 무료 RSS 주소를 만듭니다.
        rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=ko&gl=KR&ceid=KR:ko"
        # 3. feedparser 도구를 써서 뉴스 목록을 싹 긁어옵니다.
        feed = feedparser.parse(rss_url)
        # 4. 뉴스가 너무 많으면 안 되니까, 맨 위에서부터 딱 5개만 잘라서 돌려줍니다.
        return feed.entries[:5] 
    except Exception as e:
        # 혹시 에러가 나면 화면에 빨간색으로 알려줍니다.
        st.error(f"뉴스 검색 중 에러가 났어요!: {e}")
        return []

# ---[4. 핵심 기능 2: Gemini AI 요약 함수] ---
def summarize_with_gemini(text):
    try:
        # 1. 가장 똑똑한 최신 AI 모델인 'gemini-1.5-pro'를 불러옵니다.
        model = genai.GenerativeModel("gemini-1.5-pro")
        # 2. AI에게 시킬 명령(프롬프트)을 구체적으로 적어줍니다.
        prompt = f"다음 뉴스 제목을 보고 어떤 내용일지 유추해서, 비전문가도 이해하기 쉽게 딱 2~3문장으로 요약해줘.\n\n뉴스 제목: {text}"
        # 3. AI에게 명령을 내리고 답변을 받아옵니다.
        response = model.generate_content(prompt)
        # 4. AI가 대답한 텍스트만 쏙 뽑아서 돌려줍니다.
        return response.text
    except Exception as e:
        # 에러가 나면 이유를 알려줍니다.
        return f"AI 요약 실패 ㅠㅠ: {e}"

# --- [5. 웹사이트 화면 디자인하기 (Streamlit)] ---
# 웹 브라우저 탭의 이름과 아이콘을 설정합니다.
st.set_page_config(page_title="AI 뉴스 요약 로봇", page_icon="🤖")

# 화면 맨 위에 보일 큰 제목과 설명을 적습니다.
st.title("🤖 똑똑한 AI 뉴스 검색 & 요약기")
st.write("키워드만 입력하면 최신 뉴스 5개를 찾아 AI가 3문장으로 요약해 드립니다!")

# 검색 결과를 화면이 새로고침 되어도 까먹지 않게 '기억 창고(session_state)'에 저장할 준비를 합니다.
if "news_data" not in st.session_state:
    st.session_state.news_data =[] # 처음엔 빈 창고로 시작합니다.

# 사용자에게 검색어를 입력받는 칸을 만듭니다.
search_keyword = st.text_input("궁금한 뉴스 키워드를 입력하세요. (예: 전기차, 챗GPT)")

# '검색 시작' 버튼을 만들고, 이 버튼을 눌렀을 때 일어날 일들을 정해줍니다.
if st.button("🚀 뉴스 검색 및 요약 시작"):
    # 에러 방지 1: API 키가 없으면 경고!
    if not GEMINI_API_KEY:
        st.error("앗! GitHub 비밀 금고에 API 키가 없어요. 설정을 확인해 주세요!")
    # 에러 방지 2: 검색어를 안 넣었으면 경고!
    elif not search_keyword:
        st.warning("검색어를 입력해야 찾을 수 있어요!")
    # 다 정상이라면 작업 시작!
    else:
        # 작업하는 동안 빙글빙글 도는 로딩 애니메이션을 띄웁니다.
        with st.spinner("구글에서 뉴스를 찾고 AI가 열심히 요약하고 있어요... 잠시만요! ⏳"):
            
            # 위에서 만든 뉴스 검색 함수를 실행합니다.
            news_items = search_google_news_free(search_keyword)
            
            # 뉴스를 못 찾았을 때
            if not news_items:
                st.info("조건에 맞는 뉴스를 찾지 못했어요.")
            # 뉴스를 찾았을 때
            else:
                results =[] # 결과를 담을 빈 바구니를 만듭니다.
                
                # 찾은 뉴스 5개를 하나씩 꺼내서 반복 작업합니다.
                for item in news_items:
                    title = item.title # 뉴스 제목을 가져옵니다.
                    link = item.link   # 뉴스 링크(URL)를 가져옵니다.
                    
                    # 제목을 바탕으로 AI 요약 함수를 실행합니다.
                    summary = summarize_with_gemini(title)
                    
                    # 제목, 링크, 요약 내용을 하나로 예쁘게 묶어서 바구니에 담습니다.
                    results.append({
                        "제목": title,
                        "URL": link,
                        "AI 요약 (3문장)": summary
                    })
                
                # 바구니에 담긴 결과를 '기억 창고'에 최종 저장합니다.
                st.session_state.news_data = results
                # 화면에 초록색으로 성공했다고 알려줍니다.
                st.success("짜잔! 검색과 요약이 완료되었습니다!")

# --- [6. 결과 화면에 보여주고 CSV로 다운받기] ---
# 기억 창고에 데이터가 있다면 (즉, 검색이 완료되었다면) 아래 내용을 화면에 그립니다.
if st.session_state.news_data:
    st.markdown("---") # 가로줄을 하나 긋습니다.
    
    # 기억 창고에 있는 뉴스를 하나씩 꺼내서 화면에 예쁘게 카드 형태로 출력합니다.
    for i, data in enumerate(st.session_state.news_data):
        with st.container():
            st.markdown(f"### {i+1}. {data['제목']}") # 제목 (예: 1. 애플 신제품 출시)
            st.markdown(f"**🔗 원본 기사 읽기:** [{data['URL']}]({data['URL']})") # 클릭 가능한 링크
            st.info(f"**🤖 AI 요약:**\n\n{data['AI 요약 (3문장)']}") # 파란 바탕의 요약 내용
            st.write("") # 한 줄 띄우기
            
    # 결과를 엑셀처럼 다루기 위해 Pandas의 DataFrame으로 바꿉니다.
    df = pd.DataFrame(st.session_state.news_data)
    
    # DataFrame을 CSV 파일 형식으로 변환합니다. (한글이 안 깨지게 utf-8-sig를 씁니다.)
    csv_data = df.to_csv(index=False, encoding="utf-8-sig")
    
    st.markdown("---") # 가로줄 긋기
    
    # 다운로드 버튼을 화면에 만듭니다. 누르면 csv_data를 다운받게 됩니다.
    st.download_button(
        label="📥 요약 결과 다운로드 (CSV 파일)",
        data=csv_data,
        file_name="news_summary.csv",
        mime="text/csv"
    )