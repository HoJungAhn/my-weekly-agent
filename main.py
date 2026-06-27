"""개발용 진입점 — `python main.py ...` 는 Relay CLI(`relay ...`)로 위임한다.

배포 시 진입점은 `pyproject.toml` 의 `[project.scripts] relay` 이다.
이 파일은 가상환경 활성화 없이 빠르게 돌려보기 위한 편의용이다.
"""

from relay.cli import app

if __name__ == "__main__":
    app()
