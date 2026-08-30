# ----------------------- 범용 라이브러리 import ------------
# 비동기 코드 실행을 위한 asyncio 라이브러리 import
import asyncio
# ----------- custom 라이브러리 import ---------------------
# TTS_Util 클래스 import
from custom_package.tts_commu import TTS_Util

# 메인 함수 선언
def main():
    # tts 객체 선언
    tts = TTS_Util()
    # 텍스트 선언
    text = "주행을 시작합니다."
    # 음성 출력 
    asyncio.run(tts.speak(text))

# 메인함수 실행
if __name__ =='__main__':
    main()