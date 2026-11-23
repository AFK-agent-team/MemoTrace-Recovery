#!/usr/bin/env python3
"""Export one day of a named group's synced history from the Linux WeChat GUI."""

import argparse
import csv
import fcntl
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, time as midnight, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageChops, ImageOps


WECHAT_CLASS = "wechat.wechat"
X_ENV = dict(os.environ, DISPLAY=os.environ.get("DISPLAY") or ":0")
CLIPBOARD_MARKER = b"memotrace-copy-pending"
MAX_SCROLLS = 160
MAX_SELECTED_MESSAGES = 500
HEADER = re.compile(r"(?m)(?:^|(?<=\n\n))([^\n]+)\n(\d{4}/\d{1,2}/\d{1,2})[ \t]+(\d{1,2}:\d{2})(?:\n|$)")
VISIBLE_TIME = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*(\d{1,2})\s*:\s*(\d{2})")
EMAIL = re.compile(r"[A-Za-z0-9.+_%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
LONG_NUMBER = re.compile(r"(?<!\d)\d{8,}(?!\d)")
TOKEN = re.compile(r"\b(?:gh[pousr]_|github_pat_|sk-[A-Za-z0-9]+-)[A-Za-z0-9_-]{12,}")
URL_PARAMETERS = re.compile(r"(https?://[^\s?#]+)[?#][^\s]*")


@dataclass(frozen=True)
class Window:
    id: str
    x: int
    y: int
    width: int
    height: int
    title: str


def run(*args, input=None, timeout=8):
    return subprocess.run(args, env=X_ENV, input=input, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=True).stdout


def windows():
    result = []
    for line in run("wmctrl", "-lGx").decode().splitlines():
        fields = line.split(None, 8)
        if len(fields) == 9 and fields[6] == WECHAT_CLASS:
            result.append(Window(fields[0], *map(int, fields[2:6]), fields[8]))
    return result


def wait_until(get, label, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = get()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(label)


def frame(window, rect=None):
    args = ["import", "-window", window]
    if rect is not None:
        x, y, width, height = rect
        args += ["-crop", f"{width}x{height}+{x}+{y}", "+repage"]
    return Image.open(io.BytesIO(run(*args, "png:-", timeout=6))).convert("RGB")


def ocr(img, *, sparse=False, language="eng"):
    gray = ImageOps.autocontrast(ImageOps.grayscale(img))
    gray = gray.resize((gray.width * 3, gray.height * 3), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    gray.save(buf, format="PNG")
    args = ["tesseract", "stdin", "stdout", "-l", language, "--psm", "11" if sparse else "7"]
    if not sparse:
        return run(*args, input=buf.getvalue()).decode().strip()
    lines = {}
    data = run(*args, "tsv", input=buf.getvalue()).decode()
    for row in csv.DictReader(io.StringIO(data), delimiter="\t"):
        if not row["text"].strip():
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        lines.setdefault(key, []).append(row)
    return [
        (" ".join(word["text"] for word in words),
         min(int(word["left"]) for word in words) // 3,
         min(int(word["top"]) for word in words) // 3,
         max(int(word["height"]) for word in words) // 3)
        for words in lines.values()
    ]


def click(x, y, button="1"):
    run("xdotool", "mousemove", str(x), str(y), "click", button)


def active(window):
    root = run("xprop", "-root", "_NET_ACTIVE_WINDOW").decode()
    match = re.search(r"0x[0-9a-f]+", root)
    return bool(match and int(match[0], 16) == int(window.id, 16))


def history_window(group):
    title = re.escape(f'Chat history of "{group}"')
    return next((w for w in windows() if re.fullmatch(title + r"\s*\(\d+\)", w.title)), None)


def normalize_group_label(value):
    # Tesseract may insert spaces between Chinese characters. The native window
    # title is still checked against the exact requested name before any copy.
    value = " ".join(value.split())
    return re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value).casefold()


def open_history(group, ocr_language):
    previous = history_window(group)
    if previous is not None:
        run("wmctrl", "-ic", previous.id)
        wait_until(lambda: history_window(group) is None, "Previous history window did not close")
    main = next((w for w in windows() if w.title == "Weixin" and w.width >= 850 and w.height >= 600), None)
    if main is None:
        raise RuntimeError("WeChat main window is unavailable. Open the client and sign in on the X11 desktop.")
    run("wmctrl", "-ia", main.id)
    wait_until(lambda: active(main), "The desktop could not activate the WeChat window")

    group_label = normalize_group_label(group)

    def header_matches():
        width = min(400, main.width - 450)
        title = ocr(frame(main.id, (310, 43, width, 28)), language=ocr_language)
        without_count = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", title)
        return group_label in (normalize_group_label(title), normalize_group_label(without_count))

    if not header_matches():
        click(main.x + 137, main.y + 57)
        run("xdotool", "key", "ctrl+a")
        try:
            set_clipboard(group.encode("utf-8"))
            run("xdotool", "key", "ctrl+v")
            time.sleep(0.75)
        finally:
            set_clipboard(b"")
        left, top = main.x + 72, main.y + 75
        lines = ocr(frame("root", (left, top, 260, 139)), sparse=True, language=ocr_language)
        heading = next((y for label, _, y, _ in lines if label.casefold() == "group chats"), None)
        matches = [
            (x, y, height)
            for label, x, y, height in lines
            if normalize_group_label(label) == group_label and heading is not None and y > heading
        ]
        if len(matches) != 1:
            run("xdotool", "key", "Escape")
            raise RuntimeError("The signed-in account did not show one matching group. Open it in WeChat and retry.")
        x, y, height = matches[0]
        click(left + x + 14, top + y + height // 2)
        wait_until(header_matches, "The requested group header could not be verified")
    if not active(main):
        raise RuntimeError("WeChat focus changed before opening chat history")
    click(main.x + main.width - 117, main.y + 56)
    history = wait_until(lambda: history_window(group), "WeChat did not open the exact group history window")
    if (history.width, history.height) != (560, 720):
        raise RuntimeError(f"The history window size changed to {history.width}×{history.height}")
    wait_until(lambda: active(history), "The group history window did not become active")
    return history


def visible_times(screen, bottom):
    top = 142
    for label, _, y, height in ocr(screen.crop((395, top, 545, bottom)), sparse=True):
        match = VISIBLE_TIME.search(label)
        if match:
            try:
                stamp = datetime(*map(int, match.groups()))
            except ValueError:
                continue
            yield stamp, top + y + height // 2


def enter_selection(history, screen):
    # The timeline has timestamps in the right gutter. The content below each
    # sender starts at x=71, for both text and media.
    for _, row_y in list(visible_times(screen, 666))[:4]:
        x, y = history.x + 82, history.y + row_y + 24
        click(x, y, "3")
        time.sleep(0.18)
        left, top = x - 3, max(history.y + 120, y - 231)
        menu = ocr(frame("root", (left, top, 210, min(460, history.y + 716 - top))), sparse=True)
        choices = [(dx, dy, height) for label, dx, dy, height in menu
                   if re.fullmatch(r"Select\s*[.…]*", label, re.I) and dx < 100]
        if len(choices) == 1:
            dx, dy, height = choices[0]
            click(left + dx + 14, top + dy + height // 2)
            time.sleep(0.23)
            if ocr(frame(history.id, (202, 650, 60, 27))).strip().lower() == "copy":
                return
            raise RuntimeError("WeChat did not show the multi-message Copy toolbar")
        run("xdotool", "key", "Escape")
    raise RuntimeError("No Select command was visible on a message in the approved group")


def checkbox_rows(screen, selected):
    pixels = screen.load()

    def gray(value, low, high):
        return low <= value[0] <= high and max(value) - min(value) <= 4

    def green(value):
        red, g, blue = value
        return red < 100 and g > 145 and 45 < blue < 166 and g - blue > 45

    def ring(y):
        if selected:
            return green(pixels[26, y]) and green(pixels[35, y])
        return (
            gray(pixels[31, y], 224, 255)
            and gray(pixels[23, y], 145, 210)
            and gray(pixels[38, y], 145, 210)
            and any(gray(pixels[31, y - dy], 145, 210) for dy in (6, 7, 8))
            and any(gray(pixels[31, y + dy], 145, 210) for dy in (6, 7, 8))
        )

    groups = []
    for y in range(147, 574):
        if ring(y):
            if groups and y - groups[-1][-1] <= (3 if selected else 1):
                groups[-1].append(y)
            else:
                groups.append([y])
    return [(points[0] + points[-1]) // 2 for points in groups]


def set_clipboard(value):
    subprocess.run(("xclip", "-selection", "clipboard", "-i"), env=X_ENV,
                   input=value, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=4, check=True)


def read_clipboard(history):
    if not active(history):
        raise RuntimeError("WeChat focus changed before Copy")
    if ocr(frame(history.id, (202, 650, 60, 27))).lower() != "copy":
        raise RuntimeError("The verified Copy control is no longer visible")

    def copied():
        try:
            value = run("xclip", "-selection", "clipboard", "-o", "-t", "UTF8_STRING", timeout=2)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        return value.decode() if value and value != CLIPBOARD_MARKER else None

    set_clipboard(CLIPBOARD_MARKER)
    try:
        click(history.x + 230, history.y + 638)
        return wait_until(copied, "The native Copy action did not return text. Check for a WeChat prompt.")
    finally:
        set_clipboard(b"")


def parse_clipboard(content, source_timezone, report_timezone):
    content = content.replace("\r\n", "\n")
    headers = list(HEADER.finditer(content))
    if not headers or headers[0].start() != 0:
        raise RuntimeError("WeChat clipboard did not contain sender/time message records")
    rows = []
    for index, match in enumerate(headers):
        sender, day, clock = match.groups()
        stamp = datetime.strptime(f"{day} {clock}", "%Y/%m/%d %H:%M")
        right = headers[index + 1].start() if index + 1 < len(headers) else len(content)
        body = content[match.end():right].rstrip("\n")
        rows.append({
            "sender": sender,
            "time": stamp.replace(tzinfo=source_timezone).astimezone(report_timezone),
            "text": body,
        })
    return rows


def recap_text(value):
    # The report needs message meaning and attribution, not account details,
    # tracking parameters, or locally generated attachment filenames.
    if value.startswith(("[Photo]", "[Sticker]", "[Video]", "[Audio]", "[Voice]")):
        return value.split("]", 1)[0] + "]"
    value = URL_PARAMETERS.sub(r"\1", value)
    value = EMAIL.sub("[邮箱]", value)
    value = TOKEN.sub("[访问凭证]", value)
    return LONG_NUMBER.sub("[长数字]", value)


def read_day(day, *, group, source_timezone, report_timezone, ocr_language):
    start = datetime.combine(day, midnight.min, report_timezone)
    end = start + timedelta(days=1)
    now = datetime.now(report_timezone)
    if day > now.date():
        raise RuntimeError("The requested date is in the future in the report timezone")
    first_needed = start.astimezone(source_timezone).replace(tzinfo=None)
    history = open_history(group, ocr_language)
    screen = frame(history.id)
    timeline = list(visible_times(screen, 658))
    if not timeline:
        raise RuntimeError("No timestamped group records were visible. Verify that the account has synced the group.")
    enter_selection(history, screen)
    screen = frame(history.id)
    previous_checks = checkbox_rows(screen, selected=True)
    if len(previous_checks) != 1:
        raise RuntimeError("Could not verify the initial selected message")
    count = 1
    unchanged = 0
    reached = None
    for _ in range(MAX_SCROLLS):
        if not active(history):
            raise RuntimeError("Focus changed away from the approved group history")
        marks = checkbox_rows(screen, selected=False)
        if marks:
            args = ["xdotool"]
            for y in marks:
                args += ["mousemove", str(history.x + 31), str(history.y + y), "click", "1"]
            run(*args, timeout=12)
            count += len(marks)
            if count > MAX_SELECTED_MESSAGES:
                raise RuntimeError("The history exceeded the single-run selection limit")
            screen = frame(history.id)
            verified = checkbox_rows(screen, selected=True)
            if not all(any(abs(y - checked) <= 3 for checked in verified) for y in marks):
                raise RuntimeError("A native message checkbox changed position or did not select")
        stamps = [stamp for stamp, _ in visible_times(screen, 570)]
        if sum(stamp < first_needed for stamp in stamps) >= 2:
            reached = "earlier_than_requested_day"
            break
        before = screen.crop((53, 142, 533, 579))
        run("xdotool", "mousemove", str(history.x + 544), str(history.y + 495),
            "click", "--repeat", "4", "--delay", "52", "5")
        time.sleep(0.26)
        screen = frame(history.id)
        after = screen.crop((53, 142, 533, 579))
        unchanged = unchanged + 1 if not ImageChops.difference(before, after).getbbox() else 0
        if unchanged >= 2:
            reached = "oldest_synced_message"
            break
    if reached is None:
        raise RuntimeError("The requested date boundary was not reached before the scroll limit")
    records = parse_clipboard(read_clipboard(history), source_timezone, report_timezone)
    if len(records) != count:
        raise RuntimeError(f"WeChat returned {len(records)} records for {count} verified checkbox selections")
    if reached == "earlier_than_requested_day" and min(record["time"] for record in records) >= start:
        raise RuntimeError("The clipboard did not confirm the earlier date boundary")
    result = []
    for record in sorted(records, key=lambda item: item["time"]):
        if start <= record["time"] < end:
            result.append(dict(record, time=record["time"].isoformat(), text=recap_text(record["text"])))
    return {
        "group": group, "date": day.isoformat(), "timezone": report_timezone.key,
        "partial_day": day == now.date(), "checked_at": now.isoformat(timespec="seconds"),
        "stop_reason": reached, "source_rows_checked": count, "records": result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, help="exact name of an existing WeChat group")
    parser.add_argument("--timezone", default="Asia/Shanghai", help="IANA timezone for the report date")
    parser.add_argument("--source-timezone", help="IANA timezone displayed by WeChat, default: Linux system timezone")
    parser.add_argument("--ocr-language", default="eng", help="OCR languages for the group name, such as eng+chi_sim")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--date", type=date.fromisoformat, help="calendar date (YYYY-MM-DD), default: yesterday")
    mode.add_argument("--today", action="store_true", help="read the current, possibly incomplete calendar day")
    args = parser.parse_args()
    if not args.group.strip() or "\n" in args.group or "\r" in args.group:
        parser.error("--group must be a nonempty, single-line group name")
    try:
        report_timezone = ZoneInfo(args.timezone)
        if args.source_timezone:
            source_timezone = ZoneInfo(args.source_timezone)
        else:
            with Path("/etc/localtime").open("rb") as localtime:
                source_timezone = ZoneInfo.from_file(localtime)
    except ZoneInfoNotFoundError as exc:
        parser.error(f"unknown timezone: {exc}")
    today = datetime.now(report_timezone).date()
    day = args.date or (today if args.today else today - timedelta(days=1))
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    lock_path = runtime / f"memotrace-wechat-{os.getuid()}.lock"
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another MemoTrace Linux export is using the desktop") from None
        report = read_day(day, group=args.group, source_timezone=source_timezone,
                          report_timezone=report_timezone, ocr_language=args.ocr_language)
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"WeChat history read stopped: {exc}", file=sys.stderr)
        sys.exit(1)
