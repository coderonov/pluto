#!/usr/bin/env python3
import os
import io
import sys
import time
import signal
import random
import shutil
import asyncio
import textwrap
from pathlib import Path
from typing import Optional
from datetime import datetime
from urllib.parse import urlparse

from prompt_toolkit import Application
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import get_app
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.widgets import TextArea, Frame
from prompt_toolkit.widgets import base as pt_base
from prompt_toolkit.formatted_text import to_formatted_text, ANSI
from prompt_toolkit.styles import Style

from rich.console import Console as RichConsole
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax
from rich.text import Text as RichText
from rich.live import Live
from rich.align import Align
from rich import box

from config import Config, save_api_key
from client import PlutoClient


pt_base.Border.TOP_LEFT = "╭"
pt_base.Border.TOP_RIGHT = "╮"
pt_base.Border.BOTTOM_LEFT = "╰"
pt_base.Border.BOTTOM_RIGHT = "╯"


console = RichConsole()
transcript_fragments: list = []
busy = False
loading_start = 0.0
spinner_idx = 0
verb_idx = 0
last_user_message = ""
last_ai_response = ""

SPINNER_FRAMES = ["✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳"]
LOADING_VERBS = [
    "Booting", "Разгоняюсь", "Marinating", "Собираюсь с мыслями",
    "Percolating", "Прогреваюсь", "Noodling", "Вычисляю смысл жизни",
    "Simmering", "Почти готов",
]

THEMES = {
    "оранж": "#D97757",
    "мятный": "#4EC9B0",
    "розовый": "#F783AC",
    "фиолет": "#9775FA",
    "голубой": "#61AFEF",
    "лаймовый": "#A6E22E",
}
THEME_NAMES = list(THEMES.keys())
theme_idx = 0

BAR_WIDTH = 36


def accent() -> str:
    return THEMES[THEME_NAMES[theme_idx]]


def build_style() -> Style:
    a = accent()
    return Style.from_dict({
        "frame.border": f"fg:{a}",
        "accent": f"fg:{a}",
        "accent.bold": f"bold fg:{a}",
        "prompt": f"fg:{a} bold",
    })



def build_progress_bar(progress: float) -> RichText:
    filled = int(BAR_WIDTH * progress)
    bar = RichText()
    bar.append("█" * filled, style=f"bold {accent()}")
    bar.append("░" * (BAR_WIDTH - filled), style="dim grey37")
    return bar


def build_loading_line(elapsed: float, spinner_index: int, verb: str) -> RichText:
    spinner = SPINNER_FRAMES[spinner_index % len(SPINNER_FRAMES)]
    line = RichText()
    line.append(f"{spinner} ", style=f"bold {accent()}")
    line.append(f"{verb}… ", style=f"bold {accent()}")
    line.append(f"({elapsed:.0f}s · ", style="dim")
    line.append("esc", style="dim underline")
    line.append(" to interrupt)", style="dim")
    return line


def animate_splash():
    console.clear()
    duration = 2.2
    start = time.time()
    spinner_index = 0
    verb_index = 0
    last_verb_switch = start

    header = RichText("✳ Плуто", style=f"bold {accent()}")

    with Live(console=console, refresh_per_second=12, screen=True) as live:
        while True:
            now = time.time()
            elapsed = now - start
            progress = min(elapsed / duration, 1.0)
            if elapsed >= duration:
                break
            if now - last_verb_switch > 1.6:
                verb_index = (verb_index + 1) % len(LOADING_VERBS)
                last_verb_switch = now

            body = RichText()
            body.append_text(header)
            body.append("\n\n")
            body.append_text(build_progress_bar(progress))
            body.append(f"  {int(progress * 100):3d}%\n\n")
            body.append_text(build_loading_line(elapsed, spinner_index, LOADING_VERBS[verb_index]))

            live.update(Align.center(body, vertical="middle"))
            time.sleep(0.08)
            spinner_index += 1

        final = RichText()
        final.append_text(header)
        final.append("\n\n")
        final.append_text(build_progress_bar(1.0))
        final.append("  100%\n\n")
        final.append("✔ Готово", style="bold green")
        live.update(Align.center(final, vertical="middle"))
        time.sleep(0.3)

    console.clear()


def welcome_panel() -> Panel:
    tips = RichText()
    tips.append("✳ ", style=f"bold {accent()}")
    tips.append("Добро пожаловать в Плуто!\n\n", style="bold white")
    tips.append("  Милый чубрик-компаньон для кода и разговоров.\n\n", style="dim")
    tips.append("  /help", style=f"bold {accent()}")
    tips.append("   список команд\n", style="dim")
    tips.append("  /clear", style=f"bold {accent()}")
    tips.append("  очистить историю и память\n", style="dim")
    tips.append("  /cls", style=f"bold {accent()}")
    tips.append("    просто очистить экран\n", style="dim")
    tips.append("  /exit", style=f"bold {accent()}")
    tips.append("   закончить разговор\n\n", style="dim")
    tips.append(f"  модель: {Config.GROQ_MODEL}", style="dim")

    return Panel(tips, border_style=accent(), box=box.ROUNDED, padding=(1, 2), width=64)


def _term_width() -> int:
    return max(shutil.get_terminal_size((100, 24)).columns - 4, 20)


def _render_rich(renderable) -> list:
    buf = io.StringIO()
    rc = RichConsole(
        file=buf, width=_term_width(), force_terminal=True,
        color_system="truecolor", highlight=False, legacy_windows=False,
    )
    rc.print(renderable)
    return to_formatted_text(ANSI(buf.getvalue()))


def _invalidate():
    if app is not None:
        try:
            app.invalidate()
        except Exception:
            pass


def append_fragments(frags: list):
    transcript_fragments.extend(frags)
    _invalidate()


def append_rich(renderable):
    append_fragments(_render_rich(renderable))


def append_text(text: str, style: str = ""):
    if not text.endswith("\n"):
        text += "\n"
    append_fragments([(style, text)])


def append_ai_response(response: str):
    append_fragments([("class:accent.bold", "⏺ ")])
    append_rich(Markdown(response or "*(пустой ответ)*"))


def get_transcript_fragments():
    frags = list(transcript_fragments)
    frags.append(("[SetCursorPosition]", ""))
    return frags


def get_status_fragments():
    mode_name = pluto_client.mode if pluto_client else "daily"
    mode_info = Config.get_mode_info(mode_name)
    mode_label = mode_info["name"]
    if busy:
        elapsed = time.time() - loading_start
        spinner = SPINNER_FRAMES[spinner_idx % len(SPINNER_FRAMES)]
        verb = LOADING_VERBS[verb_idx % len(LOADING_VERBS)]
        return [
            ("class:accent.bold", f" {spinner} "),
            ("class:accent.bold", f"{verb}… "),
            ("", f"({elapsed:.0f}с · "),
            ("underline", "Ctrl-C"),
            ("", " не прерывает, но /retry потом повторит)"),
        ]
    return [
        ("class:accent.bold", f" [{mode_label}] "),
        ("dim", " /help /mode"),
    ]


async def _spinner_ticker():
    global spinner_idx, verb_idx
    last_switch = time.time()
    try:
        while True:
            spinner_idx += 1
            now = time.time()
            if now - last_switch > 1.6:
                verb_idx = (verb_idx + 1) % len(LOADING_VERBS)
                last_switch = now
            _invalidate()
            await asyncio.sleep(0.08)
    except asyncio.CancelledError:
        pass


async def run_busy(func, *args, **kwargs):
    global busy, loading_start
    busy = True
    loading_start = time.time()
    ticker = asyncio.ensure_future(_spinner_ticker())
    try:
        return await asyncio.to_thread(lambda: func(*args, **kwargs))
    finally:
        busy = False
        ticker.cancel()
        _invalidate()


def cmd_help():
    table = Table(title="Команды Плуто", box=box.ROUNDED, border_style=accent())
    table.add_column("Команда", style=accent(), no_wrap=True)
    table.add_column("Описание", style="white")
    table.add_row("/exit", "Помахать лапкой и попрощаться")
    table.add_row("/clear", "Очистить историю разговора и память бота")
    table.add_row("/cls", "Просто очистить экран (память не трогает)")
    table.add_row("/help", "Показать эту подсказку")
    table.add_row("/read <file>", "Прочитать содержимое файла")
    table.add_row("/write <file>", "Записать текст в файл (многострочный)")
    table.add_row("/edit <file>", "Открыть файл в редакторе")
    table.add_row("/ls [dir]", "Показать содержимое директории")
    table.add_row("/export <file>", "Экспортировать историю в Markdown")
    table.add_row("/prompt", "Показать текущий системный промпт")
    table.add_row("/reload", "Перезагрузить промпт из файла")
    table.add_row("/model", "Показать текущую модель")
    table.add_row("/search <query>", "Поискать в интернете")
    table.add_row("/fetch <url>", "Прочитать страницу из интернета")
    table.add_row("/info", "Информация о сессии")
    table.add_row("/stats", "Статистика разговора")
    table.add_row("/mode [daily|coding|kind]", "Сменить режим общения")
    table.add_row("", "")
    table.add_row("/retry", "Повторить последний вопрос заново")
    table.add_row("/copy", "Скопировать последний ответ в буфер обмена")
    table.add_row("/save [file]", "Сохранить последний ответ в файл")
    table.add_row("/pin <заметка>", "Запомнить заметку в контексте разговора")
    table.add_row("/joke", "Попросить Плуто рассказать шутку")
    table.add_row("/time", "Который час и какой сегодня день")
    table.add_row("/rand [a] [b]", "Случайное число (по умолчанию 1-100)")
    table.add_row("/theme", "Сменить цветовую тему интерфейса")
    append_rich(table)


def cmd_read(filepath: str):
    path = Path(filepath).expanduser().resolve()
    if not path.exists():
        append_text(f"Ой, файл {path} не найден. Может, поищем в другом месте?", style="fg:red")
        return
    if path.is_dir():
        cmd_ls(filepath)
        return
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.count("\n") + 1
        ext = path.suffix.lstrip(".")
        lang = ext if ext else "text"
        syntax = Syntax(content, lang, theme="monokai", line_numbers=True)
        append_rich(Panel(
            syntax,
            title=f"[{accent()}]{path}[/{accent()}] ({lines} строк)",
            border_style=accent(),
            box=box.ROUNDED,
        ))
    except Exception as e:
        append_text(f"Не могу прочитать {path}: {e}", style="fg:red")


def cmd_ls(dirpath: str = "."):
    path = Path(dirpath).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        append_text("Ой, такой директории нет. Давай попробуем другую?", style="fg:red")
        return
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        table = Table(title=f"📁 {path}", box=box.SIMPLE, border_style=accent())
        table.add_column("Имя", style="white")
        table.add_column("Тип", style="dim", width=8)
        table.add_column("Размер", style="dim", width=10)
        for entry in entries:
            name = entry.name
            if entry.is_dir():
                table.add_row(f"[{accent()}]{name}/[/{accent()}]", "папка", "")
            else:
                size = entry.stat().st_size
                size_str = f"{size:,} B" if size < 1024 else f"{size/1024:.1f} KB"
                table.add_row(name, "файл", size_str)
        append_rich(table)
    except PermissionError:
        append_text(f"Хм, нет доступа к {path}. Может, нужны права?", style="fg:red")


async def cmd_write_async(filepath: str):
    def _do():
        path = Path(filepath).expanduser().resolve()
        print("Введи текст (пустая строка с . — закончить):")
        lines = []
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip() == ".":
                break
            lines.append(line)
        content = "\n".join(lines)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return (f"Записано {len(content)} символов в {path}", "green")
        except Exception as e:
            return (f"Ошибка записи: {e}", "fg:red")

    message, style = await run_in_terminal(_do, in_executor=True)
    append_text(message, style=style)


async def cmd_edit_async(filepath: str):
    path = Path(filepath).expanduser().resolve()
    editor = Config.EDITOR

    def _do():
        try:
            os.system(f'{editor} "{path}"')
            return f"Файл {path} закрыт. Отличная работа!"
        except Exception as e:
            return f"Не могу открыть редактор: {e}"

    message = await run_in_terminal(_do, in_executor=True)
    append_text(message, style="dim")


def cmd_info():
    table = Table(box=box.ROUNDED, border_style=accent())
    table.add_column("Параметр", style=accent())
    table.add_column("Значение", style="white")
    table.add_row("Существо", Config.CREATURE_NAME)
    table.add_row("Модель", Config.GROQ_MODEL)
    table.add_row("Сообщений в истории", str(pluto_client.get_history_count() if pluto_client else 0))
    table.add_row("Рабочая директория", str(Path.cwd()))
    table.add_row("Платформа", sys.platform)
    table.add_row("Тема", THEME_NAMES[theme_idx])
    append_rich(table)


def cmd_stats():
    if not pluto_client:
        return
    count = pluto_client.get_history_count()
    text = RichText()
    text.append("История: ", style=f"{accent()}")
    text.append(f"{count} сообщений (включая системный промпт)\n")
    text.append("Модель: ", style=f"{accent()}")
    text.append(f"{Config.GROQ_MODEL}\n")
    text.append("Температура: ", style=f"{accent()}")
    text.append(f"{Config.GROQ_TEMPERATURE}\n")
    text.append("Милый факт: чубрики любят статистику почти так же, как обнимашки ✿", style="dim")
    append_rich(text)


def cmd_export(filepath: str):
    if not pluto_client:
        return
    path = Path(filepath).expanduser().resolve()
    try:
        pluto_client.export_history(str(path))
        append_text(f"История экспортирована в {path}", style="green")
    except Exception as e:
        append_text(f"Ошибка экспорта: {e}", style="fg:red")


def _do_search(query: str):
    from duckduckgo_search import DDGS
    return list(DDGS().text(query, max_results=5, region="wt-wt"))


async def cmd_search_async(query: str):
    if not query:
        append_text("Что ищем? Напиши /search <запрос>", style="fg:red")
        return

    append_text(f"Ищу: {query}...", style="dim")
    try:
        results = await run_busy(_do_search, query)
    except Exception:
        append_text("Не могу выполнить поиск. Библиотека duckduckgo_search не установлена или нет соединения.", style="fg:red")
        append_text("Установи: pip install duckduckgo_search", style="dim")
        return

    if not results:
        append_text("Ничего не нашлось. Попробуем другой запрос?", style="dim")
        return

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        body = r.get("body", "")
        href = r.get("href", "")
        lines.append(f"{i}. {title}")
        lines.append(f"   {textwrap.shorten(body, width=100, placeholder='...')}")
        lines.append(f"   [dim]{href}[/dim]")
        lines.append("")

    text = RichText.from_markup("\n".join(lines))
    append_rich(Panel(text, title=f"[{accent()}]результаты поиска[/{accent()}]", border_style="dim", box=box.ROUNDED))

    content = "\n".join(f"{r.get('title','')}: {r.get('body','')}" for r in results)
    if pluto_client:
        pluto_client.history.add("system", f"[поиск по запросу: {query}]\n{content}")


def _do_fetch(url: str) -> str:
    import requests
    from bs4 import BeautifulSoup
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n[...]"
    return text


async def cmd_fetch_async(url: str):
    if not url:
        append_text("Укажи URL. Например: /fetch https://example.com", style="fg:red")
        return

    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url

    append_text(f"Читаю {url}...", style="dim")

    try:
        text = await run_busy(_do_fetch, url)
    except ImportError:
        append_text("Нужны библиотеки requests и beautifulsoup4", style="fg:red")
        append_text("Установи: pip install requests beautifulsoup4", style="dim")
        return
    except Exception as e:
        append_text(f"Не могу прочитать {url}: {e}", style="fg:red")
        return

    syntax = Syntax(text, "html", theme="monokai", line_numbers=False)
    append_rich(Panel(
        syntax,
        title=f"[{accent()}]{url}[/{accent()}] ({len(text)} символов)",
        border_style="dim",
        box=box.ROUNDED,
    ))

    if not pluto_client:
        return

    pluto_client.history.add("system", f"[статья: {url}]\n{text}")

    try:
        response = await run_busy(
            pluto_client.ask,
            "Вот статья, которую я загрузил. Расскажи, о чём она. "
            "Потом предложи, что можно обсудить. Будь краток.",
            stream=True,
        )
    except Exception as e:
        append_text(f"Ошибка: {e}", style="fg:red")
        return

    global last_ai_response
    last_ai_response = response or ""
    append_ai_response(response)


def show_prompt():
    if not pluto_client:
        return
    append_rich(Panel(
        Markdown(pluto_client.system_prompt),
        title=f"[{accent()}]Промпт Плуто[/{accent()}]",
        border_style=accent(),
        box=box.ROUNDED,
    ))


def cmd_theme():
    global theme_idx
    theme_idx = (theme_idx + 1) % len(THEME_NAMES)
    if app is not None:
        app.style = build_style()
    append_text(f"✳ Тема сменена: {THEME_NAMES[theme_idx]}", style="class:accent.bold")


def cmd_time():
    now = datetime.now()
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    append_rich(Panel(
        f"[bold]{now.strftime('%H:%M:%S')}[/bold]\n{days[now.weekday()]}, {now.strftime('%d.%m.%Y')}",
        title="⏰ Время",
        border_style=accent(),
        box=box.ROUNDED,
        width=32,
    ))


def cmd_rand(arg: str):
    parts = arg.split()
    try:
        if len(parts) >= 2:
            lo, hi = int(parts[0]), int(parts[1])
        elif len(parts) == 1:
            lo, hi = 1, int(parts[0])
        else:
            lo, hi = 1, 100
        if lo > hi:
            lo, hi = hi, lo
        value = random.randint(lo, hi)
        append_text(f"🎲 {value}  [{lo}–{hi}]", style="class:accent.bold")
    except ValueError:
        append_text("Использование: /rand [мин] [макс]", style="fg:red")


def cmd_pin(note: str):
    if not note:
        append_text("Что запомнить? Напиши /pin <текст заметки>", style="fg:red")
        return
    if pluto_client:
        pluto_client.history.add("system", f"[заметка от пользователя] {note}")
    append_text(f"📌 Запомнил: {note}", style="green")


def cmd_copy():
    if not last_ai_response:
        append_text("Пока нечего копировать — сначала спроси что-нибудь.", style="dim")
        return
    try:
        import pyperclip
        pyperclip.copy(last_ai_response)
        append_text("📋 Последний ответ скопирован в буфер обмена.", style="green")
    except Exception:
        append_text("Не могу скопировать — установи пакет pyperclip (pip install pyperclip).", style="fg:red")


def cmd_save(filepath: str):
    if not last_ai_response:
        append_text("Пока нечего сохранять — сначала спроси что-нибудь.", style="dim")
        return
    path = Path(filepath or "pluto_last.md").expanduser().resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(last_ai_response, encoding="utf-8")
        append_text(f"💾 Ответ сохранён в {path}", style="green")
    except Exception as e:
        append_text(f"Ошибка сохранения: {e}", style="fg:red")


async def cmd_retry_async():
    if not last_user_message:
        append_text("Пока нечего повторять — ещё не было вопросов.", style="dim")
        return
    append_text("🔁 Повторяю последний вопрос...", style="dim")
    await send_message_async(last_user_message)


async def cmd_joke_async():
    append_text("🎭 Прошу Плуто сострить...", style="dim")
    await send_message_async(
        "Расскажи одну короткую и смешную шутку — про программирование, "
        "котиков или чубриков. Только саму шутку, без предисловий."
    )


async def send_message_async(text: str):
    global last_user_message, last_ai_response
    last_user_message = text
    append_fragments([("bold", f"> {text}\n")])

    if not pluto_client:
        return
    try:
        response = await run_busy(pluto_client.ask, text, stream=True)
    except Exception as e:
        append_text(f"Ошибка: {e}", style="fg:red")
        return

    last_ai_response = response or ""
    append_ai_response(response)


async def handle_slash(command: str):
    parts = command.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit"):
        get_app().exit()
        return

    if cmd == "/clear":
        if pluto_client:
            pluto_client.clear_history()
        transcript_fragments.clear()
        append_text("История и экран очищены! Чистый листочек, начинаем сначала ✿", style="dim")
        return

    if cmd == "/cls":
        transcript_fragments.clear()
        _invalidate()
        return

    if cmd == "/help":
        cmd_help()
        return

    if cmd == "/info":
        cmd_info()
        return

    if cmd == "/stats":
        cmd_stats()
        return

    if cmd == "/prompt":
        show_prompt()
        return

    if cmd == "/model":
        append_text(f"Модель: {Config.GROQ_MODEL}", style="class:accent.bold")
        return

    if cmd == "/reload":
        if pluto_client:
            pluto_client._load_mode_prompt()
            append_text("Промпт перезагружен из файла.", style="green")
        return

    if cmd == "/mode":
        if not arg:
            modes = Config.list_modes()
            text = "Режимы:\n" + "\n".join(f"  /mode {k} — {desc}" for k, _, desc in modes)
            append_text(text, style="class:accent.bold")
            return
        if not pluto_client:
            return
        msg = pluto_client.switch_mode(arg.strip())
        append_text(msg, style="green")
        append_text("История очищена — начали с чистого листа в новом режиме.", style="dim")
        return

    if cmd == "/theme":
        cmd_theme()
        return

    if cmd == "/time":
        cmd_time()
        return

    if cmd == "/rand":
        cmd_rand(arg)
        return

    if cmd == "/pin":
        cmd_pin(arg)
        return

    if cmd == "/copy":
        cmd_copy()
        return

    if cmd == "/save":
        cmd_save(arg)
        return

    if cmd == "/retry":
        await cmd_retry_async()
        return

    if cmd == "/joke":
        await cmd_joke_async()
        return

    if cmd == "/read":
        if not arg:
            append_text("Укажи аргумент. Например: /read <файл>", style="fg:red")
            return
        cmd_read(arg)
        return

    if cmd == "/write":
        if not arg:
            append_text("Укажи аргумент. Например: /write <файл>", style="fg:red")
            return
        await cmd_write_async(arg)
        return

    if cmd == "/edit":
        if not arg:
            append_text("Укажи аргумент. Например: /edit <файл>", style="fg:red")
            return
        await cmd_edit_async(arg)
        return

    if cmd == "/ls":
        cmd_ls(arg if arg else ".")
        return

    if cmd == "/export":
        cmd_export(arg if arg else "pluto_history.md")
        return

    if cmd == "/search":
        await cmd_search_async(arg)
        return

    if cmd == "/fetch":
        await cmd_fetch_async(arg)
        return

    append_text(f"Неизвестная команда: {cmd}. Напиши /help", style="fg:red")


async def handle_input(text: str):
    text = text.strip()
    if not text:
        return
    if text.startswith("/"):
        try:
            await handle_slash(text)
        except Exception as e:
            append_text(f"Ошибка: {e}", style="fg:red")
    else:
        await send_message_async(text)


def build_app() -> Application:
    global app, input_area

    pt_history = FileHistory(str(Path.home() / ".pluto" / "shell_history"))

    input_area = TextArea(
        prompt=[("class:prompt", "> ")],
        multiline=False,
        wrap_lines=False,
        history=pt_history,
        auto_suggest=AutoSuggestFromHistory(),
    )

    transcript_window = Window(
        content=FormattedTextControl(get_transcript_fragments, focusable=False),
        wrap_lines=True,
        always_hide_cursor=True,
    )

    status_window = Window(content=FormattedTextControl(get_status_fragments), height=1)

    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        if busy:
            return
        text = input_area.text
        input_area.text = ""
        if text.strip():
            asyncio.ensure_future(handle_input(text))

    @kb.add("c-c")
    def _soft_interrupt(event):
        if busy:
            append_text("Плуто уже думает — потерпи чуть-чуть ✿ (или дождись и напиши /retry)", style="dim")
        else:
            append_text("Тише-тише, я тут ✿ (Ctrl-D или /exit — чтобы выйти)", style="dim")

    @kb.add("c-d")
    def _quit(event):
        event.app.exit()

    root = HSplit([
        transcript_window,
        status_window,
        Frame(input_area, style="class:frame.border"),
    ])

    application = Application(
        layout=Layout(root, focused_element=input_area),
        key_bindings=kb,
        full_screen=True,
        style=build_style(),
        mouse_support=False,
    )
    app = application
    return application


def main():
    global pluto_client

    api_key = Config.load_api_key()
    if not api_key:
        console.print("[yellow]GROQ_API_KEY не найден.[/yellow]")
        console.print("Его можно задать через:")
        console.print("  1. export GROQ_API_KEY=your_key_here")
        console.print("  2. Создать файл ~/.pluto/groq_key")
        key = input("\nВведи ключ сейчас (или Enter чтобы выйти): ").strip()
        if not key:
            console.print("[dim]Без ключа я, увы, не смогу с тобой поболтать. До скорой встречи![/dim]")
            sys.exit(0)
        save_api_key(key)
        Config.GROQ_API_KEY = key
        api_key = key

    pluto_client = PlutoClient(api_key)
    animate_splash()

    application = build_app()
    append_rich(welcome_panel())

    try:
        asyncio.run(application.run_async())
    except (KeyboardInterrupt, EOFError):
        pass

    console.print("[dim]До скорой встречи, я буду ждать! ✿[/dim]")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    main()
