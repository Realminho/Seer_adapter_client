"""설정 파일 내용이 잘못됐을 때 쓰는 공용 예외.

부팅을 멈춰야 하는 오류와 "멈추되 살아서 보고할 수 있는" 오류를 가르는 것은
예외의 **타입**이다 (main.run_config_error_mode 참고). 타입이 갈라져 있으면
새로 추가된 검증이 그 중 하나를 빠뜨리기 쉽고, 빠뜨린 오류는 라벨 없는
TypeError/KeyError가 되어 systemd 크래시 루프로 나타난다 — journalctl을 열 수
있는 사람만 원인을 아는 상태다.

그래서 "설정 내용이 잘못됐다"는 뜻은 이 한 타입이 갖는다. extensions.hcl /
recipes.hcl 전용 예외는 이걸 상속하므로 기존 ``except ExtensionsError``는 그대로
동작한다.
"""

from pathlib import Path
from typing import Optional, Union


class ConfigError(ValueError):
    """설정 파일 내용이 잘못됐다. ``path``는 운영자가 열어야 할 파일이다.

    ``path``가 None이면 호출부가 아는 기본 파일 이름을 대신 쓴다 — configPath가
    비어 나가면 운영자에게 단서가 아예 남지 않는다.
    """

    def __init__(
        self, message: str, *, path: Optional[Union[str, Path]] = None
    ) -> None:
        super().__init__(message)
        self.path = str(path) if path is not None else None
