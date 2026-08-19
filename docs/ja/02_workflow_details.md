# Workflow Details: MolScout

[English](../02_workflow_details.md)

MolScout は、明示的な workflow flags に基づいて reaction-path generation と後続の molecular analysis を制御します。app wrapper が job ごとに flags を設定し、`core/molscout.py` が選択された stage を実行します。

## 1. Initial path search

initial path stage は、主に `INIT_PATH_SEARCH_ON` と `INIT_PATH_METHOD` で制御します。

- `DMF` は FB-ENM interpolation と DirectMaxFlux optimization により、reactant / product structures から initial path を生成します。
- `NEB` は ASE NEB を用い、final path と optimization history を出力します。
- `SCAN` は指定した bond、angle、dihedral coordinate に沿って constrained optimization を行います。
- `CAT` は user-provided trajectories または coordinate files を連結し、batch-oriented processing に利用します。

生成された path は `init_path.traj` として保存され、必要に応じて coordinate files に変換され、result CSV に要約されます。

## 2. Peak extraction

path が得られた後、MolScout は energy profile を評価し、local maxima を transition-state candidates として抽出します。endpoints と selected frames も保持され、optional optimization、vibrational analysis、plotting に使用できます。

## 3. TS optimization and IRC

有効化されている場合、transition-state optimization は Sella-based routines によって実行します。高対称または線形に近い構造で coordinate-system issue が起きる場合に備え、internal-coordinate setup が不安定なときは Cartesian coordinates に fallback できるようにしています。

IRC calculation では `AdaptiveIRC` extension を使用します。step size は動的に調整され、convergence failure により path が不安定になった場合は rollback を用います。

## 4. Vibrational analysis and thermochemistry

vibrational analysis では、selected structures に対して thermal corrections と free-energy terms を計算します。low-frequency artifacts には floor correction を適用し、小さな imaginary modes は化学的に意味のある transition-state mode ではなく numerical noise として扱えるようにしています。

thermochemistry temperature と関連 options は、`core/default_config.py` を編集せずに app job ごとに override できます。

## 5. Energy refinement and export

optional refinement stage では、selected structures をより高精度な settings で評価できます。PySCF-based runs では、選択した backend が対応している場合に molecular orbital information、Mulliken charges、dipole moments、JSON summaries、Molden files を export できます。

## 6. Finalization

run の終了時、MolScout は result CSV を更新し、log と timing information を書き出し、follow-up inspection の候補を記録し、figure が有効な場合は図を生成します。app は expected output files を検証してから、queued job を complete として扱います。
