# GitHub README 동영상 연결 방법

GitHub 저장소에 커밋된 MP4 파일을 직접 링크하면 GitHub 파일 뷰어에서 오류가 나거나 다운로드로 처리될 수 있습니다.
README에서 네이티브 동영상 플레이어를 사용하려면 GitHub가 생성한 `user-attachments` URL을 사용합니다.

1. GitHub 웹에서 `README.md`를 Edit로 엽니다.
2. `docs/media/seer_amr_test_web.mp4`를 편집창에 드래그 앤 드롭합니다.
3. GitHub가 생성한 `https://github.com/user-attachments/assets/...` 주소를 복사합니다.
4. README의 `SEER_AMR_GITHUB_VIDEO` 주석 바로 아래에 URL을 한 줄로 단독 붙여넣습니다.
5. `docs/media/seer_docking_success_web.mp4`도 같은 방식으로 업로드하여 `SEER_DOCKING_GITHUB_VIDEO` 아래에 붙여넣습니다.

URL을 Markdown 링크 `[텍스트](URL)`로 감싸지 말고 한 줄에 URL 자체만 두는 것이 GitHub의 네이티브 동영상 렌더링에 가장 적합합니다.
