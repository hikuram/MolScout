# MolScout Streamlit App

`app/` directory には、MolScout job の投入、queue 管理、monitoring、停止、archive 作成を行う Streamlit front end が含まれます。本 application は共有 remote use を想定しており、単一の worker が queued calculations を順番に実行します。

## 実行モデル

- 各 user session は `app/data/sessions/` 以下に保存されます。
- 各 job は session 内の `jobs/<job_id>/` に保存されます。
- 共有 queue worker は、GPU / CPU usage を予測しやすくするため job を 1 件ずつ実行します。
- runtime state は process exit code とは別に記録します。これにより、正常終了した process を「科学的に完了した job」と誤判定しにくくします。
- session は標準で 30 日保持され、UI から手動削除できます。

## App の対象範囲

- full、IRC-only、VIB-only、figure-refresh、concatenation/batch workflow を 1 つの UI から投入できます。
- workflow-stage toggle と calculation settings を job ごとに設定できます。
- 新規 structure の upload、bundled sample input の利用、既存 session file の再利用に対応します。
- queue status、job log、server resources、NVIDIA GPU status を確認できます。
- 実行中 job の停止、queued job の削除、session 内 queue order の変更が可能です。
- 単一 job directory または session 全体を ZIP archive として download できます。

## Directory layout

- `app/streamlit_app.py`: main Streamlit entry point and top navigation
- `app/app_pages/`: Queue、Submit、Results、Chemiscope、PySCF、About の page modules
- `app/app_ui/`: shared Streamlit UI helpers and reusable view functions
- `app/app_core/`: queue、session、monitoring、cleanup、archive、workflow helpers
- `app/data/sessions/`: session ごとの working directories
- `app/data/queue/`: shared queue state と worker PID
- `app/data/logs/`: queue worker logs
- `app/data/archives/`: generated ZIP archives

## Page layout

- `Queue`: 共有キューと選択中セッションの概要を表示します。
- `Submit`: 反応経路探索とファイル連結処理の job を投入します。
- `Results`: セッション内 job、ログ、結果ファイル、ZIP download を確認します。
- `Chemiscope`: 選択中セッション内の `.traj` / `.xyz` / `.extxyz` を chemiscope で可視化します。
- `PySCF`: 選択中セッションの PySCF 設定を編集します。
- `About`: application overview と page guide を表示します。

セッション作成・選択、monitoring、環境チェック、サンプル一覧、worker log、cleanup は全 page 共通の sidebar にあります。Cleanup は確認 dialog から実行します。Application title panel は通常操作画面から外し、About page にのみ配置しています。

## 推奨実行方法

標準環境では、repository の Dockerfile を使用してください。repository root で image を build します。

```bash
docker build -t molscout .
```

container から app を起動します。

```bash
docker run --gpus all -it --rm -p 8501:8501 molscout \
  streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

local development に限り、依存関係を手動で install して repository root から Streamlit を起動できます。

```bash
pip install -r requirements.txt
pip install -r app/requirements.txt
streamlit run app/streamlit_app.py
```

## Notes

- app は `core/` 以下の source files を編集しません。
- 科学計算の default settings は `core/default_config.py` から読み取ります。
- job ごとの override は、`core/molscout.py` 実行前に `app_core.workflow_runner` が適用します。
- built-in sample reactant/product pair は `core/sample_input/` から読み取ります。
- figure-refresh job には trajectory (`.traj` または `.xyz`) と既存 result CSV の両方が必要です。
- 使用中の Streamlit version が fragments に対応している場合、monitoring panel は 5 秒ごとに更新されます。
- Chemiscope page には `chemiscope[streamlit]` が必要です。`app/requirements.txt` に app 追加依存をまとめています。

## SCAN GUI notes

- SCAN settings は、従来の `bond` / `angle` / `dihedral` 絶対値入力に加えて、quick preset と relative range をサポートします。
- Dihedral twist は 4 原子、angle wag / bend は 3 原子、bond stretch / compression は 2 原子を指定します。
- `current -> current + delta` や `current + start delta -> current + end delta` は、reactant XYZ の現在値を GUI 側で読み取り、job 投入時に `SCAN_START_VAL` / `SCAN_END_VAL` / `SCAN_STEPS` へ変換します。
- 刻み幅指定は、core の `SCAN_STEPS` 仕様に合わせて分割数へ変換します。たとえば 10 deg 刻みの -360 -> +360 deg は 72 分割、73 点として実行されます。

## Constraints integration policy

- 現段階では、SCAN 座標拘束と `FIXED_ATOMS` は独立した設定として扱い、core 既存の `FixInternals` scan と `FixAtoms` を組み合わせます。
- 次段階では、bond / angle / dihedral scan と fixed atoms を共通の constraints list として表現し、複数拘束のプレビュー、競合検出、保存形式を統一する方針です。
- 優先する検証は、同一内部座標への重複拘束、scan 対象原子の完全固定、bond 距離の非正値、angle の 0/180 deg 近傍、relative scan の現在値取得失敗です。
