#!/usr/bin/env bash
# Claude Code から MCP サーバーとして起動するためのラッパー。
# リポジトリ直下の .venv を使う。環境変数:
#   ROBOT_REALTIME=1.0   実時間比（0 で最速）
#   ROBOT_RENDER=640x480 描画サイズ
#   ROBOT_LAYOUT=basic   初期レイアウト
#   ROBOT_TTS=1          macOS の say で発話を読み上げる
cd "$(dirname "$0")"
exec .venv/bin/python -m robot_mcp.server
