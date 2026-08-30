# ubuntu-wifi-check

### 목표
- Ubuntu에서 Wi-Fi가 잡히지 않을 때의 확인 방법 안내

### 지금
- 직접 스캔이 EINVAL로 실패하며 firmware 60~65와 regulatory.db 누락 확인

### 완료
- Intel AX210 하드웨어 인식은 정상이나 커스텀 ARM64 5.10 무선 스택의 펌웨어/규제 DB 구성이 불완전한 것으로 진단

### 다음
- linux-firmware와 wireless-regdb 재설치 후 재부팅·제한 주파수 스캔, 미해결 시 보드 공급자 커널 5.15/6.1 이상 적용

### 검증
- 커널 로그의 firmware fallback, regulatory.db ENOENT, iw scan EINVAL을 상호 확인
