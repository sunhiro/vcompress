#!/usr/bin/env bash
# pop_monitor.sh — Reliable macOS Pop-up Monitor script for iTerm2 & Terminal
# Usage: ./pop_monitor.sh <log_file_path> [window_title]

LOG_FILE="${1:-vcompress_abav1.log}"
TITLE="${2:-vcompress 转码监控}"

# 转换为绝对路径，确保新终端窗口（默认进入 ~ 目录）能准确找到目标日志
if [[ "$LOG_FILE" != /* ]]; then
    LOG_FILE="$(pwd)/$LOG_FILE"
fi

if [ ! -f "$LOG_FILE" ]; then
    mkdir -p "$(dirname "$LOG_FILE")"
    touch "$LOG_FILE"
fi

# Prefer iTerm2 if installed or running, fallback to Terminal.app
if [ -d "/Applications/iTerm.app" ] || pgrep -f "iTerm" >/dev/null 2>&1; then
    osascript -e "
    tell application \"iTerm\"
        activate
        set newWin to (create window with default profile)
        tell current session of newWin
            write text \"echo \\\"=== ${TITLE} ===\\\"; if command -v lnav >/dev/null 2>&1; then lnav '${LOG_FILE}'; else tail -f '${LOG_FILE}'; fi\"
        end tell
    end tell"
else
    osascript -e "
    tell application \"Terminal\"
        activate
        do script \"echo \\\"=== ${TITLE} ===\\\"; if command -v lnav >/dev/null 2>&1; then lnav '${LOG_FILE}'; else tail -f '${LOG_FILE}'; fi\"
    end tell"
fi
