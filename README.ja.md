# MolScout

[English](README.md)

MolScout は、反応経路探索と後続の分子計算を自動化するための Python toolkit です。初期経路生成、transition-state optimization、intrinsic reaction coordinate 計算、vibrational analysis、熱化学量評価、および任意の高精度 energy refinement を一連の workflow として扱います。

本リポジトリは、共有実行用の Streamlit application と、科学計算 workflow を格納する `core/` directory を中心に構成されています。従来の個別 workflow script は整理済みであり、workflow stage の選択は application wrapper と `core/default_config.py` によって制御します。

## 主な機能

- DMF、NEB、SCAN、または連結済み trajectory による初期経路生成
- OrbMol（任意で ALPB 補正）で中間 step を guide し、PySCF DFT anchor を周期的に挿入する Multi-Fidelity SCAN (MF-SCAN)
- ASE / Sella を用いた transition-state optimization と adaptive IRC 計算
- 低振動数・数値誤差に配慮した vibrational analysis と熱化学量評価
- OrbMol、xTB/ALPB delta correction、PySCF、gpu4pyscf 支援計算に対応した calculator backend
- `.traj`、`.xyz`、`.csv`、log、figure などによる file-based result handoff
- 共有 workstation または remote server での実行を想定した Streamlit queue
- PostgreSQL artifact catalog による session 横断の成果物検索と filesystem 整合性診断

## リポジトリ構成

```text
MolScout/
|-- app/                 # Streamlit UI、queue、session、archive 関連
|-- core/                # 科学計算 workflow modules と sample input
|-- docs/                # architecture、workflow、backend、environment notes
|-- scripts/             # metadata migration / artifact catalog utilities
|-- requirements.txt     # 非 Docker 環境向け Python dependencies
`-- Dockerfile           # 推奨 environment definition
```

## Installation

通常利用では、同梱の Dockerfile を用いることを推奨します。計算環境には CUDA、compiled library、optimizer backend に依存する package が含まれるため、Docker image を利用することで app と command-line workflow の動作差を抑えられます。

リポジトリ root で image を build します。

```bash
docker build -t molscout .
```

GPU を利用する interactive container を起動します。

```bash
docker run --gpus all -it --rm -p 8501:8501 molscout
```

container 内で既定の英語 Streamlit app を起動します。

```bash
streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

日本語補助 UI を起動する場合は次を使用します。

```bash
streamlit run /opt/MolScout/app/streamlit_app_ja.py --server.address 0.0.0.0
```

開発や軽量な確認に限り、pip による直接 setup も可能です。ただし backend の挙動は platform に依存する場合があります。

```bash
pip install -r requirements.txt
```

## 動作確認環境

現在の確認環境では、以下の package version を使用しました。

| package | version |
|---|---|
| ase | 3.29.0 |
| streamlit | 1.60.0 |
| pydmf | 1.2.2 |
| sella | 2.5.0 |
| orb-models | 0.7.0 |
| pyscf | 2.14.0 |
| tblite | 0.7.0 |
| torch | 2.13.0a0+9186a08b2c.nv26.7.59513937 |
| gpu4pyscf | 1.8.0 |
| cupy | 13.6.0 |
| cupytensor | 2.3.1 |

## Streamlit app の起動

PostgreSQL と Streamlit app は Compose で起動します。

```bash
podman compose up -d --build
```

初回検証後は `.env` で `POSTGRES_PASSWORD` を設定し、Compose file の既定値をそのまま本運用に使用しないでください。

session、job、queue、application state の管理情報は PostgreSQL に保存します。入力構造、trajectory、計算結果、logs、generated archives などの実体ファイルは project root の `data/` 以下に保存します。科学計算の default settings は `core/default_config.py` に保持され、job ごとの override は `app_core.workflow_runner` が適用します。`core/` 以下の source file は app から書き換えません。

local checkout から直接起動する場合も、`PGHOST`、`PGDATABASE`、`PGUSER`、`PGPASSWORD` を設定し、接続可能な PostgreSQL を用意してください。

### 既存成果物のカタログ登録

成果物の実体は `data/` に維持し、検索に必要な file metadata だけを PostgreSQL の `artifacts` table に登録します。既存 data は次の単機能 script で登録できます。

```bash
podman compose exec molscout \
  python /opt/MolScout/scripts/index_artifacts.py --dry-run

podman compose exec molscout \
  python /opt/MolScout/scripts/index_artifacts.py
```

新規 job は終了処理で自動登録されます。Streamlit の `Data` page から session 横断検索、手動再スキャン、欠損 file・未登録 file・孤立 directory の診断ができます。再スキャンと診断は file を削除または移動しません。

## core workflow の直接実行

command-line 操作が必要な場合は、reactant/product workflow を直接起動できます。

```bash
python core/molscout.py -d <dest_dir> -c <charge> -m orbmol -r reactant.xyz -p product.xyz
```

IRC-only、VIB-only、figure refresh などの stage-specific run では、workflow flags を一貫して設定するため、Streamlit app または app wrapper の利用を推奨します。

```bash
PYTHONPATH=app:core python -m app_core.workflow_runner \
  --workflow "VIB workflow only" \
  --directory <dest_dir> \
  --charge <charge> \
  --method orbmol \
  --input input.traj \
  --vib
```

## Documentation

- `docs/01_architecture.md`: repository structure と data flow
- `docs/02_workflow_details.md`: workflow stage ごとの動作
- `docs/03_calculators.md`: calculator backend notes
- `docs/04_environment.md`: installation policy と確認済み package versions

Colab example は `colab_notebook_example.ipynb`（English）と `colab_notebook_example_ja.ipynb`（日本語補助）を用意しています。

## License

本 project は GPL-3.0 license の下で配布されます。詳細は `LICENSE` を参照してください。

## Acknowledgments

本 workflow 設計の一部は ColabReaction および redox_benchmark を参考にしています。production calculation で利用する third-party project と dependencies については、それぞれの license と citation requirements を確認してください。
