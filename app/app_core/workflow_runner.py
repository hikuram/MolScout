"""Wrapper for applying GUI workflow options before running MolScout."""

from __future__ import annotations

import argparse
import json
import runpy
import sys

from .config import WORKFLOW_LABELS
from .paths import CORE_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a MolScout workflow with GUI-provided overrides.")
    parser.add_argument("--workflow", choices=list(WORKFLOW_LABELS.keys()), required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--charge", type=int, required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--orbmol-version", choices=["v1", "v2"], default=None)
    parser.add_argument("--result", default="result.csv")
    parser.add_argument("--temperature", type=float, default=298.15)
    parser.add_argument("--tblite-method", default="hybrid")
    parser.add_argument("--initial-path", action="store_true")
    parser.add_argument("--ts-opt", action="store_true")
    parser.add_argument("--irc", action="store_true")
    parser.add_argument("--vib", action="store_true")
    parser.add_argument("--refine", action="store_true")
    parser.add_argument("--reactant")
    parser.add_argument("--product")
    parser.add_argument("--input")
    parser.add_argument("--catfiles", nargs="+")
    parser.add_argument("--config-json", default="")
    return parser.parse_args()


def apply_workflow_flags(args: argparse.Namespace) -> None:
    if str(CORE_DIR) not in sys.path:
        sys.path.insert(0, str(CORE_DIR))

    import default_config as g

    is_cat_mode = bool(args.catfiles)
    g.INIT_PATH_SEARCH_ON = bool(args.initial_path or is_cat_mode)
    g.INIT_RECALC_MODE_ON = False
    g.REFINE_INPUT_ON = bool(args.initial_path or is_cat_mode)
    g.USE_SELLA_IN_OPT = False
    g.TSOPT_ON = bool(args.ts_opt)
    g.IRC_ON = bool(args.irc)
    g.VIB_ON = bool(args.vib)
    g.REFINE_ENERGY_ON = bool(args.refine)
    g.OTHER_JOBS_EXAMPLE_ON = False
    g.WRITE_SUGGESTIONS_ON = bool(args.irc or args.initial_path or is_cat_mode)
    g.SAVE_FIG_ON = True
    g.PRESERVE_CSV_ON = args.workflow == "Figure refresh only"
    g.THERMO_TEMPERATURE = float(args.temperature)
    g.TBLITE_METHOD = args.tblite_method

    if args.workflow == "Figure refresh only":
        g.TSOPT_ON = False
        g.IRC_ON = False
        g.VIB_ON = False
        g.REFINE_ENERGY_ON = False

    if args.config_json:
        overrides = json.loads(args.config_json)
        for key, value in overrides.items():
            setattr(g, key, value)


def build_script_argv(args: argparse.Namespace) -> list[str]:
    argv = [
        str(CORE_DIR / "molscout.py"),
        "-d",
        args.directory,
        "-c",
        str(args.charge),
        "-m",
        args.method,
        "-rs",
        args.result,
    ]
    if args.orbmol_version is not None:
        argv.extend(["--orbmol-version", args.orbmol_version])
    if args.catfiles:
        argv.extend(["-cat", *args.catfiles])
    elif args.initial_path:
        if not args.reactant:
            raise SystemExit("reactant input is required when initial path is enabled")
        argv.extend(["-r", args.reactant])
        if args.product:
            argv.extend(["-p", args.product])
    else:
        if not args.input:
            raise SystemExit("this workflow requires an input trajectory or xyz file")
        argv.extend(["-i", args.input])
    return argv


def main() -> None:
    args = parse_args()
    apply_workflow_flags(args)
    sys.argv = build_script_argv(args)
    runpy.run_path(str(CORE_DIR / "molscout.py"), run_name="__main__")


if __name__ == "__main__":
    main()
