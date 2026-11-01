# デモ動画の台本

## 方法 A: ヘッドレス実行から自動生成する（提出用の既定）

```bash
.venv/bin/python scripts/run_claude_demo.py --name final_demo --layout basic --render 1280x960 --max-turns 80 "<3 作業をまとめた指示文>"
.venv/bin/python scripts/compose_video.py --run recordings/final_demo --max-gap 4
.venv/bin/python scripts/finalize_video.py --run recordings/final_demo --speed 1 --end "<終了カードの文>"
```

`recordings/final_demo/final_1x.mp4`（1920×1080、約 2 分 20 秒、等速）ができる。左がシミュレータ（字幕は Claude の
`say()`）、右が Claude Code のチャット（指示・発話・ツール呼び出し・結果）。

## 方法 B: Claude デスクトップアプリの画面録画

動画要件: 1〜3 分（2 倍速可、倍速は明記）。氏名・所属・メールを映さない。

## 録画の準備

1. ターミナルで何も起動していなくてよい（MCP サーバーは Claude Code が自動起動する）。
2. Claude デスクトップアプリの Code タブで **このフォルダ（pai1_final）を新しいセッションで開く**。
   初回は MCP サーバー `robot` の承認ダイアログが出るので許可する。
3. ブラウザペイン（Cmd+Shift+B）で http://localhost:8765/ を開く。ライブ映像・字幕・ツールログが出る。
   チャットが左、ブラウザペインが右に並ぶようにウィンドウ幅を調整する。
4. macOS の画面収録（Cmd+Shift+5 → 「選択部分を収録」でアプリのウィンドウ範囲）を開始する。
   ※ アカウント名などの個人情報が画面に出ていないか確認（サイドバーは折りたたむ）。
5. 最初に Claude に `reset_scene("basic")` と `start_recording("scene1")` を頼むと、シミュレータ側の MP4 も同時に残る。

録画後の編集: 実時間で 5〜6 分になるので **2 倍速**にし、冒頭にタイトル、要所に説明テロップを入れる。
`scripts/compose_video.py` で作った合成動画（左: シミュレータ、右: チャット）を差し込んでもよい。

## 場面構成（目安: 2 倍速で合計 2 分半）

| # | 時間（実時間） | 入力する指示 | 見せたいこと |
|---|---|---|---|
| 0 | 10 秒 | （タイトルカード）「Claude Code が MCP でロボットを操作する」 | 構成図 docs/architecture.svg |
| 1 | 60 秒 | `赤いブロックを青いトレイに入れて` | look(top) → say → pick → place → verify の基本ループ。手首カメラの小窓 |
| 2 | 90 秒 | `reset_scene を sorting にして、ブロックを色ごとに仕分けして` | 同色 2 個ずつ。順番を宣言して進める。トレイ内の空き位置を自動で選ぶ |
| 3 | 60 秒 | `reset_scene を stacking にして、大きい順に積み上げて。終わったら前のカメラで見せて` | サイズを知覚から判断。段積み後の front 画像で確認 |
| 4 | 45 秒 | `（途中で）緑のブロックを 5cm 右にずらしてから、もう一度やり直して` | nudge_object で外乱 → 再検出して ID を維持したまま再実行 |
| 5 | 20 秒 | `今日の作業を 2 文でまとめて` | Claude が検証済みの事実だけを報告 |

曖昧さの例（余裕があれば）: sorting で `赤いブロックをトレイに入れて` とだけ言う → Claude が「赤は 2 個あり、
赤いトレイと青いトレイのどちらか」を確認してくる。

## 説明テロップ案

- 「AI（Claude）は判断に専念。位置決めは画像処理と IK」
- 「シミュレータの真値は使わない。カメラ画像だけで位置を推定（誤差 2〜4 mm）」
- 「ツールは成否と根拠を返す → Claude が検証してから報告」
- 「字幕は Claude の say() の出力」

## 概要欄に書くこと

- 2 倍速再生である旨
- GitHub リポジトリ URL
- 使用技術: Claude Code（MCP）、MuJoCo、Python
