import json
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

from groq import Groq

from config import Config, MODES, get_mode, set_mode, get_prompt_path


class ConversationHistory:
    def __init__(self, file_path: str, max_messages: int = 100):
        self.file_path = Path(file_path)
        self.max_messages = max_messages
        self.messages: List[Dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text())
                self.messages = data.get("messages", [])
            except (json.JSONDecodeError, KeyError):
                self.messages = []

    def _save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "messages": self.messages[-self.max_messages:],
            "updated_at": datetime.now().isoformat(),
        }
        self.file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
        self._save()

    def set_system_prompt(self, content: str) -> None:
        if self.messages and self.messages[0]["role"] == "system":
            self.messages[0]["content"] = content
        else:
            self.messages.insert(0, {"role": "system", "content": content})
        self._save()

    def get_context(self, limit: Optional[int] = None) -> List[Dict[str, str]]:
        msgs = self.messages
        if limit and len(msgs) > limit + 1:
            keep = limit + 1
            msgs = [msgs[0]] + msgs[-keep + 1:]
        return msgs

    def clear(self) -> None:
        system = self.messages[0] if self.messages and self.messages[0]["role"] == "system" else None
        self.messages = [system] if system else []
        self._save()

    def count(self) -> int:
        return len(self.messages)

    def export(self, file_path: str) -> None:
        text = []
        for msg in self.messages:
            if msg["role"] == "system":
                continue
            role = "Ты" if msg["role"] == "user" else Config.CREATURE_NAME
            text.append(f"## {role}\n{msg['content']}\n")
        Path(file_path).write_text("\n".join(text), encoding="utf-8")


class PlutoClient:
    def __init__(self, api_key: str):
        self.client = Groq(api_key=api_key)
        self.history = ConversationHistory(Config.HISTORY_FILE, Config.MAX_HISTORY_MESSAGES)
        self.mode = get_mode()
        self._load_mode_prompt()

    def _load_mode_prompt(self) -> None:
        path = Path(get_prompt_path(self.mode))
        if path.exists():
            self.system_prompt = path.read_text(encoding="utf-8")
        else:
            self.system_prompt = ""
        self.history.set_system_prompt(self.system_prompt)

    def switch_mode(self, new_mode: str) -> str:
        available = list(MODES.keys())
        if new_mode not in available:
            return f"Нет такого режима: {new_mode}. Доступно: {', '.join(available)}"
        self.mode = new_mode
        set_mode(new_mode)
        self._load_mode_prompt()
        self.history.clear()
        info = Config.get_mode_info(new_mode)
        return f"Переключён на режим {info['name']}: {info['description']}"

    def ask(self, user_input: str, stream: bool = True) -> str:
        self.history.add("user", user_input)
        messages = self.history.get_context(limit=Config.MAX_HISTORY_MESSAGES)

        if not messages or messages[0].get("role") != "system":
            messages.insert(0, {"role": "system", "content": self.system_prompt})

        try:
            response = self.client.chat.completions.create(
                model=Config.GROQ_MODEL,
                messages=messages,
                temperature=Config.GROQ_TEMPERATURE,
                max_tokens=Config.GROQ_MAX_TOKENS,
                stream=True,
            )

            full = ""
            for chunk in response:
                delta = chunk.choices[0].delta.content or ""
                full += delta

            self.history.add("assistant", full)
            return full

        except Exception as e:
            return f"\n[ошибка] {e}"

    def clear_history(self) -> None:
        self.history.clear()

    def get_history_count(self) -> int:
        return self.history.count()

    def export_history(self, file_path: str) -> None:
        self.history.export(file_path)
