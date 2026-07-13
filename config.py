import os
from pathlib import Path


def _load_dotenv():
    env_paths = [
        Path.cwd() / ".env",
        Path.home() / ".pluto" / ".env",
        Path(__file__).parent / ".env",
    ]
    for env_file in env_paths:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("\"'")
                if not os.environ.get(key):
                    os.environ[key] = val


_load_dotenv()


def get_api_key() -> str:
    env_key = os.environ.get("GROQ_API_KEY")
    if env_key:
        return env_key
    key_file = Path.home() / ".pluto" / "groq_key"
    if key_file.exists():
        return key_file.read_text().strip()
    return ""


def save_api_key(key: str) -> None:
    key_dir = Path.home() / ".pluto"
    key_dir.mkdir(parents=True, exist_ok=True)
    (key_dir / "groq_key").write_text(key)
    (key_dir / "groq_key").chmod(0o600)


MODES = {
    "daily": {
        "name": "Плуто/daily",
        "prompt": "daily.md",
        "description": "Повседневное общение",
    },
    "coding": {
        "name": "Плуто/coding",
        "prompt": "coding.md",
        "description": "Фокус на код, терминал",
    },
    "kind": {
        "name": "Плуто/kind",
        "prompt": "kind.md",
        "description": "Добрый и тёплый, поддержка",
    },
    "linux": {
        "name": "Плуто/linux",
        "prompt": "linux.md",
        "description": "Фанат Linux и FOSS, ненавидит Microsoft и Apple",
    },
}


def get_mode() -> str:
    mode_file = Path.home() / ".pluto" / "mode"
    if mode_file.exists():
        mode = mode_file.read_text().strip()
        if mode in MODES:
            return mode
    return "daily"


def set_mode(mode: str) -> None:
    mode_dir = Path.home() / ".pluto"
    mode_dir.mkdir(parents=True, exist_ok=True)
    (mode_dir / "mode").write_text(mode)


def get_prompt_path(mode: str) -> str:
    return str(Path(__file__).parent / "prompts" / MODES[mode]["prompt"])


class Config:
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    GROQ_MAX_TOKENS: int = 4096
    GROQ_TEMPERATURE: float = 0.9

    HISTORY_FILE: str = str(Path.home() / ".pluto" / "history.json")
    MAX_HISTORY_MESSAGES: int = 100

    EDITOR: str = os.environ.get("EDITOR", "vim")
    TERMINAL_WIDTH: int = 80

    CREATURE_NAME: str = "Плуто"

    @classmethod
    def load_api_key(cls) -> str:
        key = get_api_key()
        if key:
            cls.GROQ_API_KEY = key
        return key

    @classmethod
    def is_ready(cls) -> bool:
        return bool(cls.GROQ_API_KEY or get_api_key())

    @classmethod
    def get_mode_info(cls, mode: str) -> dict:
        return MODES.get(mode, MODES["daily"])

    @classmethod
    def list_modes(cls) -> list:
        return [(k, v["name"], v["description"]) for k, v in MODES.items()]
