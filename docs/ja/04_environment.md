# Environment and Installation: MolScout

[English](../04_environment.md)

MolScout の通常利用では、同梱の Dockerfile による installation を推奨します。計算環境は compiled extensions、CUDA-aware libraries、external numerical libraries を含む Python packages に依存します。単一の image definition を使用することで、local workstation、shared server、batch-style execution environment の間で生じる差を抑えられます。

## 1. 推奨 Docker workflow

repository root で image を build します。

```bash
docker build -t molscout .
```

GPU access を有効にした interactive container を起動します。

```bash
docker run --gpus all -it --rm -p 8501:8501 molscout
```

container 内で既定の英語 app を起動します。

```bash
streamlit run /opt/MolScout/app/streamlit_app.py --server.address 0.0.0.0
```

日本語補助 UI は次で起動します。

```bash
streamlit run /opt/MolScout/app/streamlit_app_ja.py --server.address 0.0.0.0
```

command-line job を実行する場合も、同じ container environment から `core/molscout.py` を直接起動してください。

## 2. Manual pip setup

manual installation は、開発または syntax-level check に利用できます。

```bash
pip install -r requirements.txt
pip install streamlit "chemiscope[streamlit]"
```

この方法では、CUDA、tblite、gpu4pyscf、compiled optimizer dependencies に関して Docker 環境と同一の動作は保証されません。manual installation 環境を production calculation に使う前に、job log で backend initialization を確認してください。

## 3. 確認済み package versions

現在の確認環境では、以下の package versions を使用しました。

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
