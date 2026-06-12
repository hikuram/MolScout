# MolScout

MolScout は、反応経路探索と後続の分子計算を自動化するための Python toolkit です。初期経路生成、transition-state optimization、intrinsic reaction coordinate 計算、vibrational analysis、熱化学量評価、および任意の高精度 energy refinement を一連の workflow として扱います。

本リポジトリは、共有実行用の Streamlit application と、科学計算 workflow を格納する `core/` directory を中心に構成されています。従来の個別 workflow script は整理済みであり、workflow stage の選択は application wrapper と `core/default_config.py` によって制御します。

## 主な機能

- DMF、NEB、SCAN、または連結済み trajectory による初期経路生成
- ASE / Sella を用いた transition-state optimization と adaptive IRC 計算
- 低振動数・数値誤差に配慮した vibrational analysis と熱化学量評価
- OrbMol、xTB/ALPB delta correction、PySCF、gpu4pyscf 支援計算に対応した calculator backend
- `.traj`、`.xyz`、`.csv`、log、figure などによる file-based result handoff
- 共有 workstation または remote server での実行を想定した Streamlit queue

## リポジトリ構成

```text
MolScout/
|-- app/                 # Streamlit UI、queue、session、archive 関連
|-- core/                # 科学計算 workflow modules と sample input
|-- docs/                # architecture、workflow、backend、environment notes
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

container 内で Streamlit app を起動します。

```bash
streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

開発や軽量な確認に限り、pip による直接 setup も可能です。ただし backend の挙動は platform に依存する場合があります。

```bash
pip install -r requirements.txt
pip install -r app/requirements.txt
```

## 動作確認環境

現在の確認環境では、以下の package version を使用しました。

| Package | Version |
|---|---:|
| ase | 3.28.0 |
| matplotlib | 3.10.9 |
| numpy | 2.4.6 |
| orb-models | 0.7.0 |
| pandas | 3.0.3 |
| pillow | 12.2.0 |
| pydmf | 1.2.1 |
| pyscf | 2.13.0 |
| rmsd | 1.6.5 |
| scipy | 1.17.1 |
| seaborn | 0.13.2 |
| sella | 0.0.1.dev386+g21c6dc7bf |
| streamlit | 1.58.0 |
| tblite | 0.6.0 |
| torch | 2.12.0 |

## Streamlit app の起動

Docker image から起動する場合:

```bash
streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

依存関係を導入済みの local checkout から起動する場合:

```bash
streamlit run app/streamlit_app.py
```

app は session data、queued jobs、logs、generated archives を `app/data/` 以下に書き込みます。科学計算の default settings は `core/default_config.py` に保持され、job ごとの override は `app_core.workflow_runner` が適用します。`core/` 以下の source file は app から書き換えません。

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

## License

本 project は GPL-3.0 license の下で配布されます。詳細は `LICENSE` を参照してください。

## Acknowledgments

本 workflow 設計の一部は ColabReaction および redox_benchmark を参考にしています。production calculation で利用する third-party project と dependencies については、それぞれの license と citation requirements を確認してください。
