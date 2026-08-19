# MolScout Streamlit App

[English](README.md)

`app/` directory には、MolScout job の投入、queue 管理、monitoring、停止、archive 作成を行う Streamlit front end が含まれます。本 application は共有 remote use を想定しており、単一の worker が queued calculations を順番に実行します。

## 実行モデル

- session、job、queue、application state の管理情報は PostgreSQL に保存されます。
- 各 user session の入力・計算結果・ログは `data/sessions/` 以下に保存されます。
- 各 job は session 内の `jobs/<job_id>/` に保存されます。
- 共有 queue worker は、GPU / CPU usage を予測しやすくするため job を 1 件ずつ実行します。
- runtime state は process exit code とは別に記録します。これにより、正常終了した process を「科学的に完了した job」と誤判定しにくくします。
- job は UI から個別削除できます。期限ベースの cleanup は PostgreSQL 移行中のため凍結しています。

## App の対象範囲

- full、IRC-only、VIB-only、figure-refresh、concatenation/batch workflow を 1 つの UI から投入できます。
- workflow-stage toggle と calculation settings を job ごとに設定できます。
- 新規 structure の upload、bundled sample input の利用、既存 session file の再利用に対応します。
- queue status、job log、server resources、NVIDIA GPU status を確認できます。
- 実行中 job の停止、queued job の削除、session 内 queue order の変更が可能です。
- 単一 job directory または session 全体を ZIP archive として download できます。
- 選択した成果物 file metadata を PostgreSQL に登録し、session 横断で検索できます。
- DB record と filesystem の欠損・未登録・孤立 directory を診断できます。

## Directory layout

- `app/streamlit_app.py`: 既定の英語 launcher
- `app/streamlit_app_ja.py`: 日本語補助 launcher
- `app/app_main.py`: 共通 application / navigation implementation
- `app/app_pages/`: Queue、Submit、Results、Chemiscope、Data、PySCF、About の page modules
- `app/app_ui/`: shared Streamlit UI helpers and reusable view functions
- `app/app_ui/locales/ja.json`: 日本語補助 UI の表示文字列。Python source では英語を正本とします
- `app/app_core/`: database、queue、session、artifact catalog、monitoring、archive、workflow helpers
- `data/sessions/`: session ごとの working directories
- `data/queue/`: worker PID
- `data/logs/`: queue worker logs
- `data/archives/`: generated ZIP archives
- PostgreSQL: session、job、shared queue、application state、artifact catalog metadata

## Language model

- `streamlit_app.py` を英語の正本・既定 UI とします。
- `streamlit_app_ja.py` は現行と同程度の英語混在を許容する日本語補助 UI です。
- application logic は `app_main.py` と共通 page / UI modules に一本化し、runtime の言語切替は行いません。

## Page layout

- `Queue`: 共有キューと選択中セッションの概要を表示します。
- `Submit`: 反応経路探索とファイル連結処理の job を投入します。
- `Results`: セッション内 job、ログ、結果ファイル、ZIP download を確認します。
- `Chemiscope`: 選択中セッション内の `.traj` / `.xyz` / `.extxyz` を chemiscope で可視化します。
- `Data`: 全セッションの成果物検索、再スキャン、DB/filesystem 整合性診断を行います。
- `PySCF`: 選択中セッションの PySCF 設定を編集します。
- `About`: application overview と page guide を表示します。

セッション作成・選択、monitoring、環境チェック、サンプル一覧、worker log は全 page 共通の sidebar にあります。Cleanup control は PostgreSQL 移行中のため無効化しています。Application title panel は通常操作画面から外し、About page にのみ配置しています。

## 推奨実行方法

標準環境では、repository root から PostgreSQL と app を Compose で起動します。

```bash
podman compose up -d --build
```

PostgreSQL の永続 volume は `molscout_postgres`、計算ファイルの bind mount は `./data` です。

local development に限り、依存関係を手動で install して repository root から Streamlit を起動できます。

```bash
pip install -r requirements.txt
pip install streamlit "chemiscope[streamlit]"
streamlit run app/streamlit_app.py
```

日本語補助 UI は次で起動します。

```bash
streamlit run app/streamlit_app_ja.py
```

## Notes

- app は `core/` 以下の source files を編集しません。
- 科学計算の default settings は `core/default_config.py` から読み取ります。
- job ごとの override は、`core/molscout.py` 実行前に `app_core.workflow_runner` が適用します。
- built-in sample reactant/product pair は `core/sample_input/` から読み取ります。
- figure-refresh job には trajectory (`.traj` または `.xyz`) と既存 result CSV の両方が必要です。
- 使用中の Streamlit version が fragments に対応している場合、monitoring panel は 5 秒ごとに更新されます。
- Chemiscope page には `chemiscope[streamlit]` が必要です。Dockerfile では Streamlit と Chemiscope を明示的に install します。

## Artifact catalog

- `.traj`、structure、CSV/TSV、figure、log/text、JSON/YAML/TOML、selected scientific data、ZIP を catalog 対象とします。
- file content は PostgreSQL に格納せず、`data/` からの relative path、type、role、size、modified time、manifest 由来 metadata を登録します。
- `*.runtime.json`、PID/lock/exit file、legacy `session.json` / `job.json` は catalog 対象外です。
- job が completed / failed / cancelled の terminal state に移る際、job directory を自動走査します。catalog 登録失敗は calculation status を変更せず、job metadata の `artifact_catalog_error` に記録します。
- 既存 data の一括登録は `python scripts/index_artifacts.py`、件数確認だけなら `--dry-run` を使用します。
- Data page の保守操作は catalog refresh と診断だけであり、file・DB record の削除や自動修復は行いません。

## SCAN GUI notes

- SCAN settings は、従来の `bond` / `angle` / `dihedral` 絶対値入力に加えて、quick preset と relative range をサポートします。
- Dihedral twist は 4 原子、angle wag / bend は 3 原子、bond stretch / compression は 2 原子を指定します。
- `current -> current + delta` や `current + start delta -> current + end delta` は、reactant XYZ の現在値を GUI 側で読み取り、job 投入時に `SCAN_START_VAL` / `SCAN_END_VAL` / `SCAN_STEPS` へ変換します。
- 刻み幅指定は、core の `SCAN_STEPS` 仕様に合わせて分割数へ変換します。たとえば 10 deg 刻みの -360 -> +360 deg は 72 分割、73 点として実行されます。

## Constraints integration policy

- 現段階では、SCAN 座標拘束と `FIXED_ATOMS` は独立した設定として扱い、core 既存の `FixInternals` scan と `FixAtoms` を組み合わせます。
- 次段階では、bond / angle / dihedral scan と fixed atoms を共通の constraints list として表現し、複数拘束のプレビュー、競合検出、保存形式を統一する方針です。
- 優先する検証は、同一内部座標への重複拘束、scan 対象原子の完全固定、bond 距離の非正値、angle の 0/180 deg 近傍、relative scan の現在値取得失敗です。
