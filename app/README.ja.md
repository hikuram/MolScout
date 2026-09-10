# MolScout Streamlit App

[English](README.md)

Current application version: **v0.4.3**  
Repository: <https://github.com/hikuram/MolScout/>

`app/` directory には、MolScout job の投入、queue 管理、monitoring、停止、archive 作成を行う Streamlit front end が含まれます。本 application は共有 remote use を想定しており、単一の worker が queued calculations を順番に実行します。

## v0.4.3 highlights

- Database sidebar の複数 job 選択と Share URL を追加し、Results / Chemiscope / Data で選択状態を再現しやすくしました。
- Chemiscope を複数 trajectory 比較へ拡張し、role-based companion CSV、共通 SCAN 軸、Quick plot、compact series label を統合しました。
- 複数trajectory比較の初期structure viewerは1つとし、multi-viewは必要時に Chemiscope 上で追加pinする方針へ整理しました。
- sidebar は複数job操作の配置、Job Note表示、Disk表示を整理しました。
- About page に application version、repository、用途、注意事項を追加しました。

## 実行モデル

- session、job、queue、application state の管理情報は PostgreSQL に保存されます。
- 各 user session の入力・計算結果・ログは `data/sessions/` 以下に保存されます。
- 各 job は session 内の `jobs/<job_id>/` に保存されます。
- 共有 queue worker は、GPU / CPU usage を予測しやすくするため job を 1 件ずつ実行します。
- runtime state は process exit code とは別に記録します。これにより、正常終了した process を「科学的に完了した job」と誤判定しにくくします。
- job は UI から個別削除できます。期限ベースの cleanup は PostgreSQL 移行中のため凍結しています。

## App の対象範囲

- full、IRC-only、VIB-only、figure-refresh、concatenation/batch workflow を 1 つの UI から投入できます。
- `Submit (JSON)` では、uploadしたJSONまたは既存jobから詳細・反復設定を再利用して投入できます。
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
- `Submit (JSON)`: MolScout の詳細設定を JSON から読み込み、対応する input structure を指定・再利用して投入します。
- `Results`: セッション内 job、ログ、結果ファイル、ZIP download を確認します。
- `Chemiscope`: 選択した1件以上の job について trajectory と対応する companion CSV property を比較します。
- `Data`: 全セッションの成果物検索、再スキャン、DB/filesystem 整合性診断を行います。
- `PySCF`: 選択中セッションの PySCF 設定を編集します。
- `About`: application version、repository、用途、注意事項、page guide を表示します。

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

## Submit (JSON)

`Submit (JSON)` は、GUIで再構築するより JSON として保持・確認・再利用しやすい詳細設定向けの route です。JSON file の直接 upload に加え、既存 job に保存された configuration を再利用できます。読み込んだ configuration は投入前に summary を表示し、必要であれば選択中 session の今後の defaults として反映できます。

input の指定方法は configuration の workflow mode に従います。reactant/product workflow では対応する structure、single-input workflow では XYZ または trajectory、concatenation workflow では複数 structure / trajectory を指定します。PySCF settings は current session、利用可能なら source job、または uploadした `pyscf_config.json` から選択できます。投入時の科学的 validation は通常GUIと同様に適用されます。

## Chemiscope comparison workflow

Chemiscope page は単なる trajectory viewer ではなく、複数結果の比較・確認用 surface として扱います。Database sidebar で1件以上の job を選択し、page 上で1件以上の trajectory row を選択します。複数 trajectory は1つの Chemiscope dataset に統合しつつ、`job`、`source`、`trajectory`、frame index、表示用 `series_label` を保持します。

Trajectory filter は compatible companion CSV の読み込みも制御します。

| Role | 代表trajectory | Companion CSV properties |
|---|---|---|
| Initial path | `init_path.traj` | `result.csv` |
| IRC | IRC trajectory | `irc_energy.csv` |
| Optpoints | `optpoints.traj` | `result_optpoints.csv` |
| MF-SCAN | `init_path.traj` | `result.csv` + `mfscan_trace.csv` のDFT anchor対応行 |

複数SCANでは `Unify SCAN targets` を既定でONにします。jobごとに原子indexが異なっても同じ coordinate type なら共通軸として比較できますが、bond / angle / dihedral は混在させません。共通SCAN property が得られた場合、Quick plot の初期X軸ではこれを優先します。Quick plot は long format のまま系列を分けるため、系列間で実測SCAN座標がわずかに異なっていても表示できます。

系列名の既定は `Compact note` です。Job Note の先頭行を短縮し、labelが重複した場合のみ短い Job ID を加えます。完全な Job Note、Job IDとの結合、source path、Custom編集も選択できます。

複数trajectory比較でも初期structure viewerは1つだけ表示します。必要なstructureは Chemiscope 上で追加pinしてmulti-viewにできます。複数trajectoryでは `Join points` を既定でOFFにします。ONにすると、combined dataset 内でsource境界も含めて点が接続されることに注意してください。

Results / Chemiscope / Data では、sidebar の `Refresh` を押した時点で複数job選択状態をURLへ確定します。その直下の code block に canonical Share URL を表示するので、標準copy controlから session、Target Job、選択job集合を含むURLを共有できます。

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
- `MF-SCAN` は独立した initial-path method ではなく、SCAN 内部の実行モードとして提供します。各 SCAN 点を OrbMol で拘束最適化し、DFT anchor 点では直後に PySCF 拘束最適化を行います。DFT 最適化された anchor 構造を次の SCAN step の初期構造として引き継ぎます。
- `MF-SCAN` 有効時は主計算レベルを `pyscf` に固定します。MLIP guide 用の OrbMol version と任意の ALPB solvent は常に表示し、通常の Method 選択値も保持して MF-SCAN を無効にすると元に戻します。最初と最後の SCAN 点は必ず DFT anchor とし、中間 anchor の間隔を設定できます。
- `init_path.traj` / `init_path.xyz` には DFT anchor だけを保存します。`mfscan_trace.csv` には全 SCAN step の MLIP/DFT 収束状態、計算時間、energy、出力 frame 対応を記録します。

## Constraints integration policy

- 現段階では、SCAN 座標拘束と `FIXED_ATOMS` は独立した設定として扱い、core 既存の `FixInternals` scan と `FixAtoms` を組み合わせます。
- 次段階では、bond / angle / dihedral scan と fixed atoms を共通の constraints list として表現し、複数拘束のプレビュー、競合検出、保存形式を統一する方針です。
- 優先する検証は、同一内部座標への重複拘束、scan 対象原子の完全固定、bond 距離の非正値、angle の 0/180 deg 近傍、relative scan の現在値取得失敗です。
