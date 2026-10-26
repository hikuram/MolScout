# Calculator Backends: MolScout

[English](../03_calculators.md)

MolScout は、energy、force、および関連する molecular properties を評価するために ASE-compatible calculators を使用します。backend selection は `CALC_TYPE` と、`core/default_config.py` または app-provided overrides に含まれる関連 settings によって制御します。

## 1. OrbMol and MLIP backends

`orbmol` は、`orb_models` を介して Orbital Materials models を使用し、高速な potential-energy-surface exploration を行います。この backend は initial path search、preliminary optimization、throughput が重要な workflow に適しています。

`orbmol+alpb` は、MLIP gas-phase energy と xTB solvation delta correction を組み合わせます。delta term は tblite-based calculation で評価し、可能な範囲で intermediate results を再利用して repeated SCF work を減らします。

## 2. xTB and ALPB handling

tblite path は、solvent correction workflow と method switching を支援し、early-stage path generation の安定化に利用できます。hybrid mode では、initial path generation で安定な xTB setting を使用し、downstream evaluations では production setting に戻すことができます。

## 3. PySCF and gpu4pyscf

PySCF backends は electronic-structure calculations と final refinement に使用します。CUDA support と gpu4pyscf が利用可能な場合、対応する calculation を GPU に offload できます。

`core/pyscf_3c.py` helper は、`r2scan-3c` や `b97-3c` などの composite functionals に対応し、ASE interface に必要な dispersion-related gradients と Hessians を扱います。

## 4. PySCF export

`core/pyscf_exporter.py` は、対応する PySCF job の後処理として selected electronic-structure information を抽出します。export には orbital energies、HOMO/LUMO values、Mulliken charges、dipole moments、JSON summaries、Molden files を含めることができます。

## 5. Configuration notes

backend behavior は installed packages、hardware、numerical settings に依存します。production use の前に、job log と `molscout.log` で selected backend、charge、multiplicity、solvent setting、CUDA availability を確認してください。

### GPU4PySCF 1.9.0 atomic charge analysis

The existing `mulliken_charge` field represents the historical **meta-Lowdin**
population from PySCF `analyze()`; `mulliken_raw_charge` represents conventional
Mulliken atomic charges via GPU4PySCF 1.9.0 `mulliken_pop()`. The latter runs
before CPU conversion. Optional MBIS/Hirshfeld atomic multipoles can be enabled
through advanced `POPULATION_ANALYSES`, `POPULATION_GRID_LEVEL` and
`POPULATION_HIRSHFELD_XC` settings. Defaults enable only direct Mulliken
(legacy fields and Molden export are still preserved). Avoid MBIS/Hirshfeld
with ECP; check residual charge sums and convergence before interpreting them.

`POPULATION_FAST_EXPORT=true` を指定すると、全 SCF オブジェクトの CPU 変換を
回避します。従来の meta-Löwdin 電荷と Molden 出力は省略されます。
既定値は `false` で、既存出力を維持します。Mulliken API 内部の行列転送は
残るため、実際の速度差は GPU 実機で計測してください。
