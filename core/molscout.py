import os
import sys
import shutil
import argparse
from datetime import datetime
from time import perf_counter as timepfc
from typing import List

# Third-party
import numpy as np
import scipy.constants as const
import torch
from orb_models.forcefield import pretrained
from orb_models.forcefield.inference.calculator import ORBCalculator
import pandas as pd
from ase import Atoms, units
from ase.io import write
from ase.io.trajectory import Trajectory
from ase.optimize import LBFGS
from ase.vibrations import Vibrations
from ase.thermochemistry import IdealGasThermo
from ase.constraints import FixAtoms, FixInternals
from ase.mep import NEB

# Project modules
import default_config as g
from instant_plot import instant_plot
from dmf import DirectMaxFlux, interpolate_fbenm
from sella import Sella, Constraints
from sella_ext_AdaptiveIRC import AdaptiveIRC
from pyscf_exporter import export_pyscf_single_point

# --- Separated Modules ---
from ase_calculators import make_calculator, get_pyscf_profile, get_solvation_info
from traj_utils import extract_peaks_from_traj, traj_to_xyz, write_energies, \
    split_traj_to_xyz, generate_path_concat
from utils import log, read
from config_manager import apply_config, apply_config_file, config_to_dict, save_config


def build_thermo_csv_output(time_vib, vib_values, energy_ll_kcal, thermal_corr_g_kcal):
    """Build the configured CSV output for one vibrational analysis."""
    columns = [
        'time_vib [s]', 'ZPE [kcal/mol]', 'E_0K [kcal/mol]', 'H [kcal/mol]',
        'G [kcal/mol]', 'G_std [kcal/mol]', 'G_floor [kcal/mol]'
    ]
    values = [time_vib] + vib_values

    optional_values = {
        'energy_LL_vib [kcal/mol]': energy_ll_kcal,
        'thermal_corr_G [kcal/mol]': thermal_corr_g_kcal,
    }
    for column in getattr(g, 'THERMO_EXTRA_CSV_COLUMNS', []):
        if column not in optional_values:
            log("Warn", f"Ignoring unsupported thermochemistry CSV column: {column}")
            continue
        columns.append(column)
        values.append(optional_values[column])

    return columns, values


def log_thermochemistry_context():
    """Log the standard-state and implicit-solvation assumptions used for thermochemistry."""
    pressure_atm = g.THERMO_ATOMOSPHERE / 101325.0
    log(
        "Thermo",
        f"Thermochemistry model: ideal-gas RRHO/qRRHO at "
        f"{g.THERMO_TEMPERATURE:.2f} K and {pressure_atm:.6g} atm."
    )

    vib_solvation = get_solvation_info(g.CALC_TYPE)
    if vib_solvation["enabled"]:
        log(
            "Thermo",
            f"Implicit solvation for vibrations: {vib_solvation['model']} "
            f"({vib_solvation['solvent']})."
        )
        log(
            "Thermo",
            "Translational, rotational, and standard-state terms remain ideal-gas at 1 atm; "
            "no 1 M correction is applied."
        )
    else:
        log("Thermo", "Implicit solvation for vibrations: disabled.")

    if not (getattr(g, 'VIB_ON', False) and getattr(g, 'REFINE_ENERGY_ON', False)):
        return

    refine_solvation = get_solvation_info(g.REFINE_CALC_TYPE)
    if refine_solvation["enabled"]:
        log(
            "Refine",
            f"Implicit solvation for refined electronic energies: "
            f"{refine_solvation['model']} ({refine_solvation['solvent']})."
        )
    else:
        log("Refine", "Implicit solvation for refined electronic energies: disabled.")

    if vib_solvation["enabled"] != refine_solvation["enabled"]:
        log(
            "Warn",
            "Solvation mismatch: vibrational and refined electronic energies do not use "
            "the same implicit-solvation state."
        )
    elif vib_solvation["enabled"]:
        vib_solvent = str(vib_solvation["solvent"]).strip().lower()
        refine_solvent = str(refine_solvation["solvent"]).strip().lower()
        if vib_solvent != refine_solvent:
            log(
                "Warn",
                f"Solvent mismatch: vibrations use {vib_solvation['model']}/"
                f"{vib_solvation['solvent']}, while refinement uses "
                f"{refine_solvation['model']}/{refine_solvation['solvent']}."
            )
        elif vib_solvation["model"] != refine_solvation["model"]:
            log(
                "Refine",
                f"G_refine combines {refine_solvation['model']} electronic energies with "
                f"{vib_solvation['model']} thermal corrections for the same solvent."
            )

    if g.REFINE_CALC_TYPE in ("pyscf", "pyscf_high"):
        refine_profile = get_pyscf_profile(g.REFINE_CALC_TYPE)
        if refine_profile.get("is_3c", False):
            log(
                "Refine",
                "Energy-only 3c refinement is enabled; nuclear gradients are skipped."
            )

# Overwrite global variables
#g.INIT_PATH_SEARCH_ON = False
# Example settings are described in README or default_config.py.

# FB-ENM/DMF optimization (first stage) -> Expanded for NEB/SCAN
def run_initial_path_search():
    method = getattr(g, 'INIT_PATH_METHOD', 'DMF')
    # Guard for CAT mode
    if method == "CAT":
        log("Fail", "run_initial_path_search should not be called in CAT mode.")
        sys.exit("abort: Invalid method route.")

    log("Path", "Reading reactant.xyz ...")
    reactant = read("reactant.xyz")
    
    product = None
    if getattr(g, 'INIT_PATH_METHOD', 'DMF') in ["DMF", "NEB"]:
        log("Path", "Reading product.xyz ...")
        product = read("product.xyz")

    # --- HYBRID MODE INTERCEPTION (Start) ---
    original_tblite_method = getattr(g, 'TBLITE_METHOD', 'GFN2-xTB')
    alpb_mode_active = "alpb" in str(getattr(g, 'CALC_TYPE', '')).lower()
    if alpb_mode_active and original_tblite_method == "hybrid":
        g.TBLITE_METHOD = "GFN1-xTB"
        log("Info", "Hybrid mode active: Temporarily downgrading TBLITE_METHOD to GFN1-xTB for initial path search (Opt & Path Gen).")
    # ----------------------------------------
    
    try:
        # == Refine input geometries ===================
        t_opt_start = timepfc()
        if g.REFINE_INPUT_ON:
            log("Opt", "Refining input geometries ...")
            if g.USE_SELLA_IN_OPT:
                reactant = opt_sella_img("reactant.xyz")
                if product is not None:
                    product = opt_sella_img("product.xyz")
            else:
                reactant = opt_img("reactant.xyz")
                if product is not None:
                    product = opt_img("product.xyz")
            t_opt = timepfc() - t_opt_start
            log("Opt", f"-> Input geometries refined in {t_opt:.2f} s")
            txt = f"* Optimize_Total        | {t_opt:>12.2f} s  *\n"
            write_line(g.TIME_LOG_NAME, txt)
        
        # == Run Initial Path Generation ===================
        t_path_start = timepfc()
        method = getattr(g, 'INIT_PATH_METHOD', 'DMF')
        log("Path", f"Generating initial path using {method} ...")
        
        if method == "DMF":
            mepopt_dmf(reactant, product)
        elif method == "NEB":
            generate_path_neb(reactant, product)
        elif method == "SCAN":
            generate_path_scan(reactant)
        elif method == "CAT":
            # Simple File Concatenation Mode
            # Read the list of target files from default_config.py
            # e.g. CONCAT_FILES = ["frag1.xyz", "scan.traj", "frag2.xyz"]
            concat_files = getattr(g, 'CONCAT_FILES', [])
            if not concat_files:
                # Fallback to standard input arguments if CONCAT_FILES is empty
                concat_files = ["reactant.xyz"]
                if product is not None:
                    concat_files.append("product.xyz")
            generate_path_concat(concat_files)
        else:
            sys.exit(f"abort: Unknown INIT_PATH_METHOD: {method}")
            
        t_path = timepfc() - t_path_start
        log("Path", f"-> {method} finished in {t_path:.2f} s")
        txt = f"* Path_Gen_Total        | {t_path:>12.2f} s  *\n"
        write_line(g.TIME_LOG_NAME, txt)
        
    finally:
        # --- HYBRID MODE INTERCEPTION (End) ---
        if alpb_mode_active and original_tblite_method == "hybrid":
            g.TBLITE_METHOD = "hybrid"
            log("Info", "Initial path search complete: Restored TBLITE_METHOD to hybrid (GFN2-xTB).")
        # --------------------------------------

# Repeat for each local maximum
def process_local_maxima():
    df_new = pd.read_csv(g.R_CSV)
    # Detect and save local maxima
    peak_files = []
    log("Info", f"Extracting peaks from {g.I_TRAJ} ...")
    peak_files, g.PEAK_IDX = extract_peaks_from_traj(g.I_TRAJ, "lmax.xyz", prominence=0.01)
    log("Info", f"Detected {len(peak_files)} peak(s) including endpoints.")

    # Write CSV (accepts a pair of elements or lists)
    def write_result(column_name, value):
        if not isinstance(column_name, list):
            column_name = [column_name]
            value = [value]
        for i, cn in enumerate(column_name):
            df_new.at[df_new.index[idx], column_name[i]] = value[i]
        try:
            df_new.to_csv(g.R_CSV, index=False)
        except Exception as e:
            log("Warn", f"An error occurred while writing {g.R_CSV}: {e}")

    # Sub-iteration 1: ignore endpoints.
    # A one-point or two-point set has no internal TS-like point, so it should
    # skip TSOPT/IRC and continue to downstream VIB/refinement processing.
    irc_trajs_str = ""
    t_tsopt_irc_start = timepfc()
    for i, peak_file in enumerate(peak_files):
        if i == 0 or i == len(peak_files) - 1:
            continue

        base_name = os.path.splitext(peak_file)[0]
        idx = int(base_name.split('_')[-1].split('.')[0])  # index of local maximum
        atoms = read(peak_file)
        atoms.info["charge"] = g.CHARGE
        atoms.info["spin"] = g.MULT

        # == Run TS optimization ===================
        if g.TSOPT_ON:
            t_tsopt_start = timepfc()
            log("TS", f"Optimizing TS for {base_name} ...")
            try:
                tsopt_img(base_name + ".xyz")
            except Exception as e:
                log("Warn", f"TSopt failed for {base_name}: {e}")
            t_tsopt = timepfc() - t_tsopt_start
            write_result('time_TSopt [s]', t_tsopt)
            log("TS", f"-> TS optimized in {t_tsopt:.2f} s")

        # == Run IRC ===================
        if g.IRC_ON:
            target_xyz = base_name + "_tsopt.xyz"
            
            if not os.path.exists(target_xyz):
                log("Warn", f"Skipping IRC for {base_name} (Missing TS structure).")
                write_result(['time_IRC [s]', 'deltaE_irc0 [kcal/mol]', 'deltaE_irc1 [kcal/mol]'], [None, None, None])
            else:
                t_irc_start = timepfc()
                log("IRC", f"Running IRC for {base_name} ...")
                try:
                    irc_result = irc_img(target_xyz)
                    
                    if os.path.exists(base_name + "_tsopt_irc0/irc.traj"):
                        write_energies(base_name + "_tsopt_irc0/irc.traj")
                        irc_trajs_str += f" {g.CURRENT_DIR}/{base_name}_tsopt_irc0/irc.traj"
                    if os.path.exists(base_name + "_tsopt_irc1/irc.traj"):
                        write_energies(base_name + "_tsopt_irc1/irc.traj")
                        irc_trajs_str += f" {g.CURRENT_DIR}/{base_name}_tsopt_irc1/irc.traj"
                        
                except Exception as e:
                    log("Warn", f"IRC failed for {base_name}: {e}")
                    irc_result = [None, None]
                    
                t_irc = timepfc() - t_irc_start
                write_result(
                    ['time_IRC [s]', 'deltaE_irc0 [kcal/mol]', 'deltaE_irc1 [kcal/mol]'],
                    [t_irc] + irc_result
                )
                log("IRC", f"-> IRC finished in {t_irc:.2f} s")
        # ==

    t_tsopt_irc = timepfc() - t_tsopt_irc_start
    txt = f"* TSopt/IRC_Total       | {t_tsopt_irc:>12.2f} s  *\n"
    write_line(g.TIME_LOG_NAME, txt)
    if irc_trajs_str.strip():
        g.SUGGESTIONS.append(f"python3 cattraj.py -i{irc_trajs_str} -o {g.CURRENT_DIR}/irc_cat/irc_cat.traj")

    # Optional workflow: pick representative optimized points for thermochemistry.
    vib_files = peak_files
    if g.PICK_OPTPOINTS_ON:
        if len(peak_files) < 2:
            log("Info", "Single-point trajectory detected. Bypassing PICK_OPTPOINTS_ON.")
            g.PICK_OPTPOINTS_ON = False
        else:
            log("Info", "Picking optimized points for thermochemistry ...")
            g.ORIG_R_CSV = g.R_CSV
            vib_files, opt_indices = make_optpoints_traj(peak_files)
            optpoints_csv = "optpoints/result_optpoints.csv"
            write_energies("optpoints/optpoints.traj", csv_name=optpoints_csv, previous_image=opt_indices, energy_recalc=True)
            df_new = pd.read_csv(optpoints_csv)
            g.R_CSV = optpoints_csv

    if g.VIB_ON or g.REFINE_ENERGY_ON:
        if g.PICK_OPTPOINTS_ON:
            log("Thermo", "Vibration/refinement targets: selected optimized structures.")
        else:
            log("Thermo", "Vibration/refinement targets: initial-path peak structures (pre-TSOPT).")
    if g.VIB_ON:
        log_thermochemistry_context()

    # Sub-iteration 2: include endpoints or reduced representative points
    t_vib_sum = 0
    t_refine_sum = 0
    for i, peak_file in enumerate(vib_files):
        base_name = os.path.splitext(peak_file)[0]
        idx = i if g.PICK_OPTPOINTS_ON else int(base_name.split('_')[-1].split('.')[0])
        atoms = read(peak_file)
        atoms.info["charge"] = g.CHARGE
        atoms.info["spin"] = g.MULT
        energy_ll_vib_kcal = None
        thermal_corr_G = None

        # == Vibrations and IdealGasThermo ===================
        if g.VIB_ON:
            t_vib_start = timepfc()
            log("Vib", f"Running vibrations for {base_name} ...")
            try:
                is_ts_point = (i != 0 and i != len(vib_files) - 1)
                vib_result, energy_ll_vib_kcal, thermal_corr_G = vib_img(
                    base_name + ".xyz", is_ts=is_ts_point
                )
            except Exception as e:
                log("Warn", f"Vibrations failed for {base_name}: {e}")
                vib_result = [None] * 6
            t_vib = timepfc() - t_vib_start
            t_vib_sum += t_vib
            
            thermo_columns, thermo_values = build_thermo_csv_output(
                t_vib, vib_result, energy_ll_vib_kcal, thermal_corr_G
            )
            write_result(thermo_columns, thermo_values)
            log("Vib", f"-> Vibrations finished in {t_vib:.2f} s")

        # == Refinement ===================
        if g.REFINE_ENERGY_ON:
            t_refine_start = timepfc()
            log("Refine", f"Running energy refinement for {base_name} ...")
            try:
                refine_result = refine_energy_img(base_name + ".xyz", refine_type=g.REFINE_CALC_TYPE)
                energy_ref_eV, energy_ref_kcal = refine_result
            except Exception as e:
                log("Warn", f"Refinement failed for {base_name}: {e}")
                energy_ref_eV = None
                energy_ref_kcal = None

            t_refine = timepfc() - t_refine_start
            t_refine_sum += t_refine
            write_result(
                ['time_refine [s]', 'energy_refine [eV]', 'energy_refine [kcal/mol]'],
                [t_refine, energy_ref_eV, energy_ref_kcal]
            )

            # Reuse the correction from this exact vibrational calculation.
            if thermal_corr_G is not None and energy_ref_kcal is not None:
                G_refine_kcal = energy_ref_kcal + thermal_corr_G
                write_result('G_refine [kcal/mol] (HL//LL)', G_refine_kcal)

            log("Refine", f"-> Refinement finished in {t_refine:.2f} s")
        # ==

    txt = f"* Vibrations_Total      | {t_vib_sum:>12.2f} s  *\n"
    write_line(g.TIME_LOG_NAME, txt)
    txt = f"* Refinement_Total      | {t_refine_sum:>12.2f} s  *\n"
    write_line(g.TIME_LOG_NAME, txt)


# Run MEP optimization with FB-ENM/DMF
def mepopt_dmf(reactant_atoms: Atoms, product_atoms: Atoms) -> None:
    # Read reactant and product
    ref_images = [reactant_atoms, product_atoms]
    
    # Generate initial path using FB-ENM
    quiet_stdout = {"print_level": 0, "file_print_level": 5}
    mxflx_fbenm = interpolate_fbenm(ref_images, correlated=True, ipopt_options=quiet_stdout)
    write('DMF_init.xyz', mxflx_fbenm.images)
    log("I/O", "Wrote DMF_init.xyz")
    
    # Write initial path and its coefficients
    write('DMF_init.traj', mxflx_fbenm.images)
    log("I/O", "Wrote DMF_init.traj")
    coefs = mxflx_fbenm.coefs.copy()
    np.save('DMF_init_coefs', coefs)
    
    # Set up and solve Direct MaxFlux
    mxflx = DirectMaxFlux(ref_images, coefs=coefs, nmove=g.NMOVE, update_teval=g.UPDATE_TEVAL)
    
    # Set up calculator
    for img in mxflx.images:
        img.info["charge"] = g.CHARGE
        img.info["spin"] = g.MULT
        img.calc = make_calculator(g.CALC_TYPE, img, "DMF_init")
        
    # Solve
    mxflx.add_ipopt_options({'output_file': 'DMF_ipopt.out', "print_level": 0, "file_print_level": 5})
    try:
        mxflx.solve(tol=g.DMF_CONVERGENCE)
    except Exception as e:
        # Restore state even if DMF fails, to prevent polluting subsequent workflow steps
        if original_tblite_method == "hybrid":
            g.TBLITE_METHOD = "hybrid"
        write("DMF_last_before_error.xyz", mxflx.images)
        write("DMF_last_before_error.traj", mxflx.images)
        log("Fail", f"abort: DirectMaxFlux.solve failed: {e}")
        sys.exit(f"abort: DirectMaxFlux.solve failed: {e}")

    # DMF_final.traj: Recompute SPC for mxflx.images (some frames lack energy)
    final_images = []
    for img in mxflx.images:
        # Copy atoms and info
        atoms = Atoms(positions=img.get_positions(), numbers=img.get_atomic_numbers())
        atoms.info["charge"] = g.CHARGE
        atoms.info["spin"] = g.MULT
        
        # Here, g.CALC_TYPE will correctly resolve to GFN2-xTB because g.TBLITE_METHOD was restored
        atoms.calc = make_calculator(g.CALC_TYPE, atoms, "DMF_final")
        try:
            # Explicitly calculate energy
            _ = atoms.get_potential_energy()
        except Exception as e:
            log("Warn", f"Failed to compute energy for image {len(final_images)}: {e}")
        final_images.append(atoms)
    
    # x(tmax): path and history
    images_tmax = mxflx.history.images_tmax
    write('DMF_tmax.traj', images_tmax)
    traj_to_xyz(images_tmax, 'DMF_tmax.xyz')
    log("I/O", "Wrote DMF_tmax.traj and .xyz")
    # final_images: save images to .traj
    write('init_path.traj', final_images)
    traj_to_xyz(final_images, 'init_path.xyz')
    log("I/O", "Wrote init_path.traj and .xyz")
    # Write results
    write_energies('init_path.traj', g.R_CSV)
    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/init_path.traj")


# Run Nudged Elastic Band (NEB)
def generate_path_neb(reactant_atoms: Atoms, product_atoms: Atoms) -> None:
    images = [reactant_atoms]
    for i in range(g.NEB_IMAGES - 2):
        images.append(reactant_atoms.copy())
    images.append(product_atoms)
    
    neb = NEB(images, k=g.NEB_SPRING_CONSTANT, climb=g.NEB_CLIMB)
    try:
        neb.interpolate('idpp')
    except Exception as e:
        log("Warn", f"IDPP interpolation failed ({e}). Falling back to linear interpolation.")
        neb.interpolate('linear')
    
    # --- Apply Fixed Atoms Constraints ---
    constraints = []
    if getattr(g, 'FIXED_ATOMS', []):
        constraints.append(FixAtoms(indices=g.FIXED_ATOMS))
        log("Path", f"Applied FixAtoms constraint to NEB intermediate images: {g.FIXED_ATOMS}")
    # -------------------------------------
    
    for i, img in enumerate(images[1:-1]):
        img.info["charge"] = g.CHARGE
        img.info["spin"] = g.MULT
        if constraints:
            img.set_constraint(constraints)
        img.calc = make_calculator(g.CALC_TYPE, img, f"NEB_img_{i+1}")
        
    opt = LBFGS(neb, trajectory='NEB_history.traj', logfile='NEB_opt.log')
    opt.run(fmax=g.OPT_FMAX, steps=1000)
    
    write('init_path.traj', images)
    traj_to_xyz(images, 'init_path.xyz')
    write_energies('init_path.traj', g.R_CSV)
    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/init_path.traj")
    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/NEB_history.traj")
    log("I/O", "Wrote init_path.traj (final path) and NEB_history.traj (optimization history)")


# Run Relaxed PES Scan using ASE constraints (Elongation / Torsion)
def _expected_scan_index_count(scan_type: str) -> int:
    if scan_type == "bond":
        return 2
    if scan_type == "angle":
        return 3
    if scan_type == "dihedral":
        return 4
    sys.exit(f"abort: Unknown SCAN_TYPE: {scan_type}")


def _get_scan_coordinate(atoms: Atoms, scan_type: str, indices: List[int]) -> float:
    if scan_type == "bond":
        return atoms.get_distance(indices[0], indices[1])
    if scan_type == "angle":
        return atoms.get_angle(indices[0], indices[1], indices[2])
    if scan_type == "dihedral":
        return atoms.get_dihedral(indices[0], indices[1], indices[2], indices[3])
    sys.exit(f"abort: Unknown SCAN_TYPE: {scan_type}")


def _set_scan_coordinate(atoms: Atoms, scan_type: str, indices: List[int], value: float) -> FixInternals:
    indices = list(indices)

    if scan_type == "bond":
        atoms.set_distance(indices[0], indices[1], value, fix=0)
        return FixInternals(bonds=[[value, indices]])
    if scan_type == "angle":
        atoms.set_angle(indices[0], indices[1], indices[2], value)
        return FixInternals(angles_deg=[[value, indices]])
    if scan_type == "dihedral":
        atoms.set_dihedral(indices[0], indices[1], indices[2], indices[3], value)
        return FixInternals(dihedrals_deg=[[value, indices]])
    sys.exit(f"abort: Unknown SCAN_TYPE: {scan_type}")

def generate_path_scan(reactant_atoms: Atoms) -> None:
    images = []
    current_atoms = reactant_atoms.copy()
    scan_type = g.SCAN_TYPE
    scan_indices = list(g.SCAN_INDICES)
    expected_count = _expected_scan_index_count(scan_type)

    if len(scan_indices) != expected_count:
        sys.exit(
            f"abort: {scan_type} SCAN requires {expected_count} atom indices, "
            f"but got {len(scan_indices)}: {scan_indices}"
        )

    if g.SCAN_STEPS < 1:
        sys.exit(f"abort: SCAN_STEPS must be 1 or larger: {g.SCAN_STEPS}")

    if g.SCAN_START_VAL is None:
        start_val = _get_scan_coordinate(current_atoms, scan_type, scan_indices)
    else:
        start_val = g.SCAN_START_VAL

    end_val = g.SCAN_END_VAL
    steps = g.SCAN_STEPS

    # --- Prepare Fixed Atoms Constraint ---
    fixed_constraint = None
    if getattr(g, 'FIXED_ATOMS', []):
        fixed_constraint = FixAtoms(indices=g.FIXED_ATOMS)
        log("Path", f"Applied FixAtoms constraint to SCAN: {g.FIXED_ATOMS}")
    # --------------------------------------

    # Write directly to Trajectory to preserve computed energies and forces
    traj_writer = Trajectory('init_path.traj', 'w')

    for step in range(steps + 1):
        val = start_val + (end_val - start_val) * step / steps
        log("SCAN", f"Step {step}/{steps} - Target {scan_type}: {val:.3f}")

        current_atoms.set_constraint()
        cons = _set_scan_coordinate(current_atoms, scan_type, scan_indices, val)

        all_constraints = [cons]
        if fixed_constraint:
            all_constraints.append(fixed_constraint)
        current_atoms.set_constraint(all_constraints)

        current_atoms.info["charge"] = g.CHARGE
        current_atoms.info["spin"] = g.MULT
        current_atoms.calc = make_calculator(g.CALC_TYPE, current_atoms, f"SCAN_opt_{step}")

        try:
            opt = LBFGS(current_atoms, logfile=f"SCAN_opt_{step}.log")
            opt.run(fmax=g.OPT_FMAX, steps=500)
            
            # Serialize the fully calculated state to disk immediately
            traj_writer.write(current_atoms)
            
            # Keep a lightweight copy for the XYZ export array
            images.append(current_atoms.copy())
            
        except Exception as e:
            log("Warn", f"SCAN optimization failed at step {step} (target: {val:.3f}). Error: {e}")
            log("Warn", "Stopping SCAN early, but preserving successfully generated path.")
            break

    traj_writer.close()

    if not images:
        log("Fail", "SCAN failed to generate any valid images.")
        sys.exit("abort: SCAN generated empty path.")

    traj_to_xyz(images, 'init_path.xyz')
    write_energies('init_path.traj', g.R_CSV)
    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/init_path.traj")
    log("I/O", f"Wrote init_path.traj and init_path.xyz (Total frames: {len(images)})")

# Write text file
def write_line(txtfile_name, txt):
    with open(txtfile_name, 'a', encoding='utf-8') as f:
        f.write(txt)


# Run optimization with ASE
def opt_img(xyz_name: str) -> Atoms:
    img = read(xyz_name)
    img_name = os.path.splitext(xyz_name)[0]
    img.info["charge"] = g.CHARGE
    img.info["spin"] = g.MULT
    # --- Apply Constraints ---
    if getattr(g, 'FIXED_ATOMS', []):
        img.set_constraint(FixAtoms(indices=g.FIXED_ATOMS))
        log("Opt", f"Applied FixAtoms constraint to indices: {g.FIXED_ATOMS}")
    # -------------------------
    img.calc = make_calculator(g.CALC_TYPE, img, img_name)
    # Set up an ASE optimizer (L-BFGS)
    opt = LBFGS(img, trajectory=img_name+"_opt.traj", logfile=img_name+"_opt.log")
    opt.run(fmax=g.OPT_FMAX, steps=10000)
    write(img_name+"_opt.xyz", img)
    images = read(img_name+"_opt.traj", index=':')
    traj_to_xyz(images, img_name+"_opt.traj.xyz")
    log("I/O", f"Wrote {img_name}_opt files")
    
    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/{img_name}_opt.traj")
    return img


# Run optimization with Sella
def opt_sella_img(xyz_name: str) -> Atoms:
    img = read(xyz_name)
    img_name = os.path.splitext(xyz_name)[0]
    img.info["charge"] = g.CHARGE
    img.info["spin"] = g.MULT
    
    # --- Apply Constraints ---
    if getattr(g, 'FIXED_ATOMS', []):
        img.set_constraint(FixAtoms(indices=g.FIXED_ATOMS))
        log("Opt", f"Applied FixAtoms constraint to indices: {g.FIXED_ATOMS}")
    # -------------------------
    img.calc = make_calculator(g.CALC_TYPE, img, img_name)
    # Set up a Sella Dynamics object (order=0)
    dyn = Sella(
        img, internal=g.SELLA_INTERNAL, order=0, constraints=None,
        trajectory=img_name+'_opt.traj', logfile=img_name+"_opt.log"
    )
    dyn.run(fmax=g.OPT_FMAX, steps=1000)
    write(img_name+"_opt.xyz", img)
    images = read(img_name+"_opt.traj", index=':')
    traj_to_xyz(images, img_name+"_opt.traj.xyz")
    log("I/O", f"Wrote {img_name}_opt files")
    
    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/{img_name}_opt.traj")
    return img


# Run TS optimization with Sella
def tsopt_img(xyz_name: str) -> Atoms:
    img = read(xyz_name)
    img_name = os.path.splitext(xyz_name)[0]
    img.info["charge"] = g.CHARGE
    img.info["spin"] = g.MULT
    # --- Apply Constraints ---
    if getattr(g, 'FIXED_ATOMS', []):
        img.set_constraint(FixAtoms(indices=g.FIXED_ATOMS))
        log("Opt", f"Applied FixAtoms constraint to indices: {g.FIXED_ATOMS}")
    # -------------------------
    img.calc = make_calculator(g.CALC_TYPE, img, img_name)
    if g.SELLA_INTERNAL_AUTO:
        # Check the symmetry of the initial structure
        _, _, g.SELLA_INTERNAL = get_symmetry_info(img, tol=1e-3)
    # Set up a Sella Dynamics object
    dyn = Sella(
        img, internal=g.SELLA_INTERNAL, order=1, constraints=None,
        trajectory=img_name+'_tsopt.traj', logfile=img_name+"_tsopt.log"
    )
    # Apply strict convergence criterion for TS optimization
    dyn.run(fmax=g.TSOPT_FMAX, steps=1000)
    
    write(img_name+"_tsopt.xyz", img)
    images = read(img_name+"_tsopt.traj", index=':')
    traj_to_xyz(images, img_name+"_tsopt.traj.xyz")
    log("I/O", f"Wrote {img_name}_tsopt files")
    
    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/{img_name}_tsopt.traj")
    return img


# Run IRC with Sella
def irc_img(xyz_name: str) -> List[float]:
    img = read(xyz_name)
    img_name = os.path.splitext(xyz_name)[0]
    img.info["charge"] = g.CHARGE
    img.info["spin"] = g.MULT
    # --- Apply Constraints ---
    if getattr(g, 'FIXED_ATOMS', []):
        img.set_constraint(FixAtoms(indices=g.FIXED_ATOMS))
        log("Opt", f"Applied FixAtoms constraint to indices: {g.FIXED_ATOMS}")
    # -------------------------
    img.calc = make_calculator(g.CALC_TYPE, img, img_name)
    # Set up a Sella IRC object
    opt = AdaptiveIRC(
        img, trajectory=img_name+'_irc.traj', logfile=img_name+"_irc.log",
        dx=g.IRC_DX_INIT, max_dx=g.IRC_DX_MAX, min_dx=g.IRC_DX_MIN,
        eta=1e-4, gamma=0.4
    )
    opt.run(fmax=1e-2, steps=1000, direction='forward')
    write(img_name+"_forward.xyz", img)
    hoge = read(img_name+"_irc.traj", index=':')[::-1]
    
    opt.run(fmax=1e-2, steps=1000, direction='reverse')
    write(img_name+"_reverse.xyz", img)
    fuga = read(img_name+"_irc.traj", index=':')[len(hoge):]
    
    rearr_images = hoge + fuga
    tgt_dir = img_name+"_irc0"
    if not os.path.exists(tgt_dir):
        os.mkdir(tgt_dir)
    write(tgt_dir+"/irc.traj", rearr_images)
    traj_to_xyz(rearr_images, tgt_dir+"/irc.traj.xyz")
    log("I/O", f"Wrote {tgt_dir}/irc.traj")
    
    rearr_images.reverse()
    tgt_dir = img_name+"_irc1"
    if not os.path.exists(tgt_dir):
        os.mkdir(tgt_dir)
    write(tgt_dir+"/irc.traj", rearr_images)
    traj_to_xyz(rearr_images, tgt_dir+"/irc.traj.xyz")
    log("I/O", f"Wrote {tgt_dir}/irc.traj")
    
    rearr_energies = []
    for rimg in rearr_images:
        rearr_energies.append(rimg.get_potential_energy())
    deltaE_irc0 = g.EV_TO_KCAL_MOL * (max(rearr_energies) - rearr_energies[-1])
    deltaE_irc1 = g.EV_TO_KCAL_MOL * (max(rearr_energies) - rearr_energies[0])
    
    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/{img_name}_irc0/irc.traj")
    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/{img_name}_irc1/irc.traj")
    g.SUGGESTIONS.append(f"python3 molscout.py -d {g.CURRENT_DIR}/{img_name}_irc0 -c {g.CHARGE} -i {g.CURRENT_DIR}/{img_name}_irc0/irc.traj")
    g.SUGGESTIONS.append(f"python3 molscout.py -d {g.CURRENT_DIR}/{img_name}_irc1 -c {g.CHARGE} -i {g.CURRENT_DIR}/{img_name}_irc1/irc.traj")
    
    return [deltaE_irc0, deltaE_irc1]


def refine_energy_img(xyz_name, refine_type="pyscf_high"):
    img = read(xyz_name)
    img_name = os.path.splitext(xyz_name)[0]
    img.info["charge"] = g.CHARGE
    img.info["spin"] = g.MULT

    try:
        img.calc = make_calculator(refine_type, img, img_name + "_refine")
        energy_eV = img.get_potential_energy()
        energy_kcal = energy_eV * g.EV_TO_KCAL_MOL

        try:
            export_pyscf_single_point(img, prefix=img_name+"_refine")
            log("I/O", f"Exported PySCF input to {img_name}_refine")
        except Exception as e:
            log("Warn", f"export_pyscf_single_point failed: {e}")

        return [energy_eV, energy_kcal]
    finally:
        img.calc = None

# 
def get_symmetry_info(atoms, tol=1e-3):
    """
    Analyze the point group of the molecule using PySCF and return
    the geometry type ('linear'/'nonlinear') and symmetry number (sigma)
    required for ASE's IdealGasThermo.
    """
    import re
    from pyscf import gto, symm
    
    if len(atoms) == 1:
        return 'monatomic', 1, False
    orig_tol = symm.geom.TOLERANCE
    symm.geom.TOLERANCE = tol
    
    try:
        # Convert ASE Atoms to PySCF atom list format
        atom_list = [[atom.symbol, atom.position] for atom in atoms]
        
        # Build a lightweight PySCF Mole object to detect symmetry
        mol = gto.Mole()
        mol.atom = atom_list
        mol.charge = g.CHARGE
        mol.spin = g.MULT - 1
        mol.basis = {'default': [[0, (1.0, 1.0)]]} # Dummy basis just to allow build() to pass
        mol.symmetry = True
        mol.verbose = 0     # Suppress PySCF output
        mol.build()
        
        pg = mol.topgroup
    except Exception as e:
        log("Warn", f"Failed to determine symmetry with PySCF ({e}). Falling back to nonlinear, sigma=1.")
        return 'nonlinear', 1, True
    finally:
        symm.geom.TOLERANCE = orig_tol
        
    # Determine if the molecule is linear
    linear_groups = ['Cinfv', 'Dinfh', 'Coov', 'Dooh']
    geometry = 'linear' if pg in linear_groups else 'nonlinear'
    
    # Calculate symmetry number from the Point Group symbol
    sym_num = 1
    if pg in ['Cinfv', 'Coov']:
        sym_num = 1
    elif pg in ['Dinfh', 'Dooh']:
        sym_num = 2
    elif pg in ['T', 'Td', 'Th']:
        sym_num = 12
    elif pg in ['O', 'Oh']:
        sym_num = 24
    elif pg in ['I', 'Ih']:
        sym_num = 60
    else:
        # Parse C_n, D_n, S_n groups (e.g., "C3v" -> letter="C", n=3)
        m = re.search(r'^([CDS])(\d+)', pg)
        if m:
            letter = m.group(1)
            n = int(m.group(2))
            if letter == 'C':
                sym_num = n
            elif letter == 'D':
                sym_num = 2 * n
            elif letter == 'S':
                sym_num = n // 2

    # Determine whether to use internal coordinates.
    # Internal coordinates mathematically fail for linear molecules.
    # High-symmetry planar/spherical groups (e.g., D3h, Oh) can also cause ODE solver singularities.
    # Cs, C2v, etc., are perfectly safe for internal coordinates.
    risky_point_groups = ['D3h', 'D4h', 'D6h', 'Td', 'Oh', 'Ih', 'C3v']
    internal_safe = True
    
    min_atoms = getattr(g, 'SELLA_INTERNAL_MIN_ATOMS', 16)
    if len(atoms) < min_atoms:
        internal_safe = False
    elif geometry == 'linear':
        internal_safe = False
    elif pg in risky_point_groups:
        internal_safe = False
    
    log("Thermo", f"Detected Point Group: {pg} -> geometry='{geometry}', symmetrynumber={sym_num}, internal_safe={internal_safe}")
    return geometry, sym_num, internal_safe


def generate_vibration_xyz(atoms, vib, mode_index, output, steps=5, scale=2.0):
    freqs = vib.get_frequencies()
    natoms = len(atoms)
    numbers = atoms.get_atomic_numbers()
    modes = [vib.get_mode(i) for i in range(len(freqs))]

    """
    Generate an .xyz animation of vibration along the selected mode.
    Parameters:
    - atoms: ASE Atoms object (original geometry)
    - vib: ASE Vibrations object
    - mode_index: Index of vibration mode to animate (default: 0)
    - steps: Number of steps for half cycle (default: 10)
    - scale: Scaling factor for mode displacement (default: 1.0)
    """
    mode = vib.get_mode(mode_index)  # (N_atoms, 3) displacement vectors
    mode = np.array(mode)
    original_positions = atoms.get_positions()
    images = []

    def generate_half_cycle(sign):
        for i in range(steps):
            factor = sign * (i + 1) / steps
            displaced = original_positions + factor * scale * mode
            new_atoms = atoms.copy()
            new_atoms.set_positions(displaced)
            images.append(new_atoms.copy())

        for i in range(steps):
            factor = sign * (steps - i - 1) / steps
            displaced = original_positions + factor * scale * mode
            new_atoms = atoms.copy()
            new_atoms.set_positions(displaced)
            images.append(new_atoms.copy())

    generate_half_cycle(+1)  # +mode -> original
    generate_half_cycle(-1)  # -mode -> original
    write(output, images)
    log("Info", f"Wrote {len(images)} frames to {output}")

def calc_qRRHO_entropy_correction(
    vib_energies_eV: list,
    atoms: Atoms,
    T: float,
    cutoff_cm1: float = 100.0,
    alpha: float = 4.0,
) -> float:
    """
    Calculate an ASE RRHOMode-like Grimme qRRHO entropy correction in eV/K.
    Returns the difference: S_qRRHO - S_harmonic.
    """
    if not vib_energies_eV:
        return 0.0

    if atoms.pbc.any():
        raise ValueError("Atoms object should not have periodic boundary conditions.")

    energies = np.array(vib_energies_eV, dtype=float)
    freqs_cm1 = energies / units.invcm
    freqs_cm1 = np.maximum(freqs_cm1, 1.0e-12)

    inertias = atoms.get_moments_of_inertia()
    mean_inertia = float(np.mean(inertias))

    kT_si = units._k * T
    R_si = units._k * units._Nav
    B_av = mean_inertia / (units.kg * units.m**2)

    x = energies / (units.kB * T)
    S_harm = units.kB * (
        x / (np.exp(x) - 1.0) - np.log(1.0 - np.exp(-x))
    )

    omega = units._c * freqs_cm1 * 1.0e2
    mu = units._hplanck / (8.0 * np.pi**2 * omega)
    mu_prime = (mu * B_av) / (mu + B_av)

    rotor_arg = np.sqrt(
        8.0 * np.pi**3 * mu_prime * kT_si / units._hplanck**2
    )
    rotor_arg = np.maximum(rotor_arg, 1.0e-300)

    S_rot = R_si * (0.5 + np.log(rotor_arg))
    S_rot *= units.J / units._Nav

    weight = 1.0 / (1.0 + (cutoff_cm1 / freqs_cm1) ** alpha)
    S_qRRHO = weight * S_harm + (1.0 - weight) * S_rot
    delta_S = np.sum(S_qRRHO - S_harm)

    return float(delta_S)


def calc_floor_entropy_correction(vib_energies_eV: list, T: float, cutoff_cm1: float = 100.0) -> float:
    """
    Calculate Truhlar's floor entropy correction (in eV/K).
    Returns the difference: S_floor - S_harmonic
    """
    if not vib_energies_eV:
        return 0.0
        
    cutoff_eV = cutoff_cm1 * units.invcm
    
    # 1. Harmonic oscillator entropy (S_v)
    x = np.array(vib_energies_eV) / (units.kB * T)
    S_v = units.kB * (x / (np.exp(x) - 1.0) - np.log(1.0 - np.exp(-x)))
    
    # 2. Floor entropy
    vib_floor = np.maximum(vib_energies_eV, cutoff_eV)
    x_floor = vib_floor / (units.kB * T)
    S_floor = units.kB * (x_floor / (np.exp(x_floor) - 1.0) - np.log(1.0 - np.exp(-x_floor)))
    
    delta_S = np.sum(S_floor - S_v)
    
    return delta_S


# Run vibrations and thermodynamics
def vib_img(xyz_name, is_ts=None):
    img = read(xyz_name)
    img_name = os.path.splitext(xyz_name)[0]
    img.info["charge"] = g.CHARGE
    img.info["spin"] = g.MULT
    img.calc = make_calculator(g.CALC_TYPE, img, img_name)
    #forces = img.get_forces()
    electronic_energy = img.get_potential_energy()
    vib = Vibrations(img, name=f"{img_name}_vib_temp")
    try:
        vib.run()
        vib.summary(log=img_name+'_vibsummary.txt')
        log("I/O", f"Wrote {img_name}_vibsummary.txt")
        vib.get_frequencies()
        #generate_vibration_xyz(atoms, vib, 0, steps, scale, vib_filename)
        for mode in range(0, 3):
            vib_filename = f"{img_name}_vib_{mode}.xyz"
            generate_vibration_xyz(img, vib, mode, output=vib_filename)
        g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/{img_name}_vib_*.xyz")

        # Ideal-gas limit
        raw_vib_energies = list(vib.get_energies()) # Units: eV

        # --- Explicit mode selection before thermochemistry ---

        # Threshold used only when is_ts is not provided by the caller.
        ts_recognition_threshold_cm1 = 40.0

        imag_indices = [
            idx for idx, e in enumerate(raw_vib_energies)
            if abs(e.imag) > 1e-10 or e.real < -1e-10
        ]

        ts_mode_index = None
        ts_mode_cm1 = None
        if imag_indices:
            ts_mode_index = max(imag_indices, key=lambda idx: abs(raw_vib_energies[idx]))
            ts_mode_cm1 = abs(raw_vib_energies[ts_mode_index]) / units.invcm

        if is_ts is None:
            is_ts = ts_mode_index is not None and ts_mode_cm1 > ts_recognition_threshold_cm1

        if is_ts:
            if ts_mode_index is not None:
                log("Thermo", f"Removing TS imaginary mode before mode selection: {ts_mode_cm1:.1f} i cm^-1")
                if len(imag_indices) > 1:
                    log("Thermo", f"Treating {len(imag_indices)-1} additional imaginary mode(s) as low-frequency noise.")
            else:
                log("Thermo", "TS mode requested, but no imaginary mode was found.")
                log("Thermo", "One extra low-frequency mode will be excluded by the TS mode count.")
        else:
            if ts_mode_index is None:
                log("Thermo", "No imaginary modes found (Assuming local minimum).")
            else:
                log("Thermo", f"Largest imaginary mode ({ts_mode_cm1:.1f} i cm^-1) is not treated as a TS mode.")
                log("Thermo", f"Treating all {len(imag_indices)} imaginary mode(s) as low-frequency noise.")

        # Convert all non-TS modes to positive magnitudes.
        vib_energies_all = []
        for idx, e in enumerate(raw_vib_energies):
            if is_ts and ts_mode_index is not None and idx == ts_mode_index:
                continue
            magnitude_eV = abs(e)
            if magnitude_eV > 1e-10:
                vib_energies_all.append(magnitude_eV)

        geom_type, sym_num, _ = get_symmetry_info(img, tol=1e-3)

        natoms = len(img)
        if geom_type == "nonlinear":
            n_external = 6
        elif geom_type == "linear":
            n_external = 5
        elif geom_type == "monatomic":
            n_external = 0
        else:
            raise ValueError(f"Unsupported geometry: {geom_type}")

        expected_modes = 3 * natoms - n_external
        if is_ts:
            expected_modes -= 1
        if expected_modes < 0:
            raise ValueError(f"Invalid thermochemistry mode count: {expected_modes}")
        if len(vib_energies_all) < expected_modes:
            raise ValueError(
                f"Too few vibration modes for thermochemistry: "
                f"expected {expected_modes}, got {len(vib_energies_all)}"
            )

        vib_energies_all.sort()
        if expected_modes == 0:
            vib_energies = []
        else:
            vib_energies = vib_energies_all[-expected_modes:]

        n_below_100 = sum(e < 100.0 * units.invcm for e in vib_energies)
        min_freq = min(vib_energies) / units.invcm if vib_energies else 0.0
        log(
            "Thermo",
            f"Mode selection: raw={len(raw_vib_energies)}, selected={len(vib_energies)}, "
            f"expected={expected_modes}, is_ts={is_ts}, min={min_freq:.1f} cm^-1, "
            f"below_100cm-1={n_below_100}"
        )
        # --------------------------------------------------------------

        # 1. Standard (No correction)
        thermo_std = IdealGasThermo(
            vib_energies=vib_energies, potentialenergy=electronic_energy,
            atoms=img, geometry=geom_type, symmetrynumber=sym_num, spin=(g.MULT-1)/2,
            vib_selection="all", ignore_imag_modes=False
        )
        
        # Get raw enthalpy and entropy once
        H_eV_std = thermo_std.get_enthalpy(temperature=g.THERMO_TEMPERATURE, verbose=False)
        S_eV_std = thermo_std.get_entropy(temperature=g.THERMO_TEMPERATURE, pressure=g.THERMO_ATOMOSPHERE, verbose=False)
        G_eV_std = H_eV_std - g.THERMO_TEMPERATURE * S_eV_std

        # 2. Grimme's qRRHO Correction
        delta_S_qRRHO = calc_qRRHO_entropy_correction(
            vib_energies, img, g.THERMO_TEMPERATURE, cutoff_cm1=100.0
        )
        S_eV_qRRHO = S_eV_std + delta_S_qRRHO
        G_eV_qRRHO = H_eV_std - g.THERMO_TEMPERATURE * S_eV_qRRHO
        
        log("Thermo", "Applied Grimme's qRRHO entropy correction (nu_0: 100 cm^-1)")
        
        # 3. Truhlar's Floor Correction (Entropy only)
        delta_S_floor = calc_floor_entropy_correction(vib_energies, g.THERMO_TEMPERATURE, cutoff_cm1=100.0)
        S_eV_floor = S_eV_std + delta_S_floor
        G_eV_floor = H_eV_std - g.THERMO_TEMPERATURE * S_eV_floor
        
        log("Thermo", "Applied Truhlar's Floor entropy correction (floor: 100 cm^-1)")

        # Convert everything to kcal/mol
        zpe_eV = 0.5 * sum(vib_energies)
        zpe_kcal = g.EV_TO_KCAL_MOL * zpe_eV
        E_0K_kcal = g.EV_TO_KCAL_MOL * (zpe_eV + electronic_energy)
        H_kcal = g.EV_TO_KCAL_MOL * H_eV_std
        G_kcal_std = g.EV_TO_KCAL_MOL * G_eV_std
        G_kcal_qRRHO = g.EV_TO_KCAL_MOL * G_eV_qRRHO
        G_kcal_floor = g.EV_TO_KCAL_MOL * G_eV_floor
        
        # The main Gibbs free energy G uses the qRRHO method
        G_kcal = G_kcal_qRRHO
        energy_ll_kcal = g.EV_TO_KCAL_MOL * electronic_energy
        thermal_corr_G_kcal = G_kcal - energy_ll_kcal

        return (
            [zpe_kcal, E_0K_kcal, H_kcal, G_kcal, G_kcal_std, G_kcal_floor],
            energy_ll_kcal,
            thermal_corr_G_kcal,
        )
    finally:
        try:
            vib.clean()
        finally:
            img.calc = None

def make_optpoints_traj(peak_files: List[str], out_traj: str = "optpoints/optpoints.traj") -> List[str]:
    """
    Build a reduced 3-point trajectory (start, highest TS-like peak, end) 
    for downstream VIB/refinement jobs.
    """

    out_dir = os.path.dirname(out_traj)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    if len(peak_files) < 2:
        raise ValueError("peak_files must contain at least the two endpoints")

    start_file = peak_files[0]
    end_file = peak_files[-1]
    
    middle_file = getattr(g, 'HIGHEST_PEAK_FILE', None)

    # Ensure unique files and preserve sequential order based on frame index
    def _get_idx(fname):
        return int(os.path.splitext(fname)[0].split('_')[-1].split('.')[0])

    branch_plan = [(start_file, _get_idx(start_file))]
    
    if middle_file is not None and middle_file != start_file and middle_file != end_file:
        branch_plan.append((middle_file, _get_idx(middle_file)))
        
    if end_file != start_file:
        branch_plan.append((end_file, _get_idx(end_file)))
        
    # Sort by frame index to maintain trajectory direction
    branch_plan.sort(key=lambda x: x[1])
    
    branch_indices = [prev_idx for _, prev_idx in branch_plan]
    
    branch_images = []
    for src_file, previous_idx in branch_plan:
        use_file = src_file
        if src_file == middle_file:
            base_name = os.path.splitext(middle_file)[0]
            tsopt_file = base_name + "_tsopt.xyz"
            if g.TSOPT_ON and os.path.exists(tsopt_file):
                use_file = tsopt_file
        
        # Optimize again if requested
        if g.OPT_OPTPOINTS_AGAIN_ON:
            if src_file == middle_file:
                atoms = tsopt_img(use_file)
            else:
                if g.USE_SELLA_IN_OPT:
                    atoms = opt_sella_img(use_file)
                else:
                    atoms = opt_img(use_file)
        else :
            atoms = read(use_file)
            atoms.info["charge"] = g.CHARGE
            atoms.info["spin"] = g.MULT
        
        atoms.calc = None
        branch_images.append(atoms)

    write(out_traj, branch_images)
    traj_to_xyz(branch_images, out_traj + ".xyz")
    log("I/O", f"Wrote {out_traj} and .xyz")

    xyz_prefix = os.path.splitext(out_traj)[0]
    branch_xyz_files = split_traj_to_xyz(out_traj, xyz_prefix)

    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/{out_traj}")
    g.SUGGESTIONS.append(
        f"python3 molscout.py -d {g.CURRENT_DIR}/optpoints "
        f"-c {g.CHARGE} -m {g.CALC_TYPE} -i {g.CURRENT_DIR}/{out_traj}"
    )

    return branch_xyz_files, branch_indices
    

# Finishing steps
def finalize_run():
    log("System", "Finalizing run and updating CSV files ...")
    csv_targets = []
    if getattr(g, 'INIT_PATH_METHOD', '') == 'CAT':
        # CAT processes every concatenated frame and does not run peak extraction,
        # so g.PEAK_IDX is intentionally undefined in this workflow.
        csv_targets.append((g.R_CSV, None))
    elif g.PICK_OPTPOINTS_ON and hasattr(g, 'ORIG_R_CSV'):
        csv_targets.append((g.ORIG_R_CSV, g.PEAK_IDX))
        csv_targets.append((g.R_CSV, None))
    else:
        csv_targets.append((g.R_CSV, g.PEAK_IDX))

    for csv_file, peak_idx in csv_targets:
        if not os.path.exists(csv_file):
            continue

        # Write relative energy (kcal/mol)
        df = pd.read_csv(csv_file)
        if g.VIB_ON:
            try:
                if df["E_0K [kcal/mol]"].notna().any():
                    df["Delta E_0K vs. reactant [kcal/mol]"] = df["E_0K [kcal/mol]"] - df.loc[0, "E_0K [kcal/mol]"]
                if df["H [kcal/mol]"].notna().any():
                    df["Delta H vs. reactant [kcal/mol]"] = df["H [kcal/mol]"] - df.loc[0, "H [kcal/mol]"]
                if df["G [kcal/mol]"].notna().any():
                    df["Delta G vs. reactant [kcal/mol]"] = df["G [kcal/mol]"] - df.loc[0, "G [kcal/mol]"]
                if "G_refine [kcal/mol] (HL//LL)" in df.columns and df["G_refine [kcal/mol] (HL//LL)"].notna().any():
                    df["Delta G_refine vs. reactant [kcal/mol] (HL//LL)"] = df["G_refine [kcal/mol] (HL//LL)"] - df.loc[0, "G_refine [kcal/mol] (HL//LL)"]
                df.to_csv(csv_file, index=False)
                log("I/O", f"Updated {csv_file} with relative energies")
            except Exception as e:
                log("Warn", f"An error occurred while writing {csv_file}: {e}")
        
        # plot
        if g.SAVE_FIG_ON:
            figname = f"fig_{os.path.splitext(os.path.basename(csv_file))[0]}.png"
            instant_plot(df, peak_idx, figname)
            log("I/O", f"Saved plot to {figname}")
    
    # Suggest next steps
    if g.WRITE_SUGGESTIONS_ON and len(g.SUGGESTIONS)>0:
        log("Info", "Your next steps may be ...")
        with open("suggestions.txt", "a", encoding='utf-8') as f:
            for elem in g.SUGGESTIONS:
                print(f"  {elem}")
                f.write(f"{elem}\n")


def process_batch_frames():
    """
    Batch processing workflow dedicated to the CAT mode.
    Splits the concatenated trajectory into individual frames and applies 
    optimization, vibrational analysis, and/or high-level energy refinement 
    to all frames sequentially. Bypasses peak extraction and TS optimization.
    """
    df_new = pd.read_csv(g.R_CSV)
    
    log("Info", f"Splitting {g.I_TRAJ} into individual frames for batch processing ...")
    frame_files = split_traj_to_xyz(g.I_TRAJ, "frame")
    log("Info", f"Prepared {len(frame_files)} frames for processing.")

    def write_result(column_name, value, idx):
        if not isinstance(column_name, list):
            column_name = [column_name]
            value = [value]
        for i, cn in enumerate(column_name):
            df_new.at[df_new.index[idx], cn] = value[i]
        try:
            df_new.to_csv(g.R_CSV, index=False)
        except Exception as e:
            log("Warn", f"An error occurred while writing {g.R_CSV}: {e}")

    def optimized_energy_values(atoms, label):
        try:
            energy_ev = atoms.get_potential_energy()
        except Exception as e:
            log("Warn", f"Could not record optimized energy for {label}: {e}")
            return None
        return [
            energy_ev,
            energy_ev * g.EV_TO_HARTREE,
            energy_ev * g.EV_TO_KCAL_MOL,
        ]

    def refresh_relative_energies():
        if "energy [kcal/mol]" not in df_new.columns or not df_new["energy [kcal/mol]"].notna().any():
            df_new["Delta E vs. reactant [kcal/mol]"] = None
            return
        ref = df_new.loc[0, "energy [kcal/mol]"]
        df_new["Delta E vs. reactant [kcal/mol]"] = df_new["energy [kcal/mol]"] - ref

    t_opt_sum = 0
    t_vib_sum = 0
    t_refine_sum = 0

    if getattr(g, 'VIB_ON', False) or getattr(g, 'REFINE_ENERGY_ON', False):
        log("Thermo", "Vibration/refinement targets: CAT frame structures after optional optimization.")
    if getattr(g, 'VIB_ON', False):
        log_thermochemistry_context()

    for idx, frame_file in enumerate(frame_files):
        base_name = os.path.splitext(frame_file)[0]
        target_xyz = frame_file
        energy_ll_vib_kcal = None
        thermal_corr_G = None
        
        # == 1. Structure Optimization (Standard Opt, NOT TS Opt) ==
        if getattr(g, 'REFINE_INPUT_ON', False):
            t_opt_start = timepfc()
            log("Opt", f"Optimizing structure for {base_name} ...")
            optimized_img = None
            try:
                if getattr(g, 'USE_SELLA_IN_OPT', False):
                    optimized_img = opt_sella_img(frame_file)
                else:
                    optimized_img = opt_img(frame_file)
                target_xyz = base_name + "_opt.xyz"
            except Exception as e:
                log("Warn", f"Optimization failed for {base_name}: {e}")
            
            t_opt = timepfc() - t_opt_start
            t_opt_sum += t_opt
            if optimized_img is None:
                write_result('time_opt [s]', t_opt, idx)
            else:
                opt_energy = optimized_energy_values(optimized_img, target_xyz)
                if opt_energy is None:
                    write_result('time_opt [s]', t_opt, idx)
                else:
                    write_result(
                        ['time_opt [s]', 'energy [eV]', 'energy [hartree]', 'energy [kcal/mol]'],
                        [t_opt] + opt_energy,
                        idx
                    )
                optimized_img.calc = None
                optimized_img = None

        # == 2. Vibrational Analysis ==
        if getattr(g, 'VIB_ON', False):
            if not os.path.exists(target_xyz):
                log("Warn", f"Skipping Vibrations for {base_name} (Missing structure).")
            else:
                t_vib_start = timepfc()
                log("Vib", f"Running vibrations for {base_name} ...")
                try:
                    vib_result, energy_ll_vib_kcal, thermal_corr_G = vib_img(target_xyz)
                except Exception as e:
                    log("Warn", f"Vibrations failed for {base_name}: {e}")
                    vib_result = [None] * 6
                
                t_vib = timepfc() - t_vib_start
                t_vib_sum += t_vib
                thermo_columns, thermo_values = build_thermo_csv_output(
                    t_vib, vib_result, energy_ll_vib_kcal, thermal_corr_G
                )
                write_result(thermo_columns, thermo_values, idx)

        # == 3. High-Level Energy Refinement ==
        if getattr(g, 'REFINE_ENERGY_ON', False):
            if not os.path.exists(target_xyz):
                log("Warn", f"Skipping Refinement for {base_name} (Missing structure).")
            else:
                t_refine_start = timepfc()
                log("Refine", f"Running energy refinement for {base_name} ...")
                try:
                    refine_result = refine_energy_img(target_xyz, refine_type=g.REFINE_CALC_TYPE)
                    energy_ref_eV, energy_ref_kcal = refine_result
                except Exception as e:
                    log("Warn", f"Refinement failed for {base_name}: {e}")
                    energy_ref_eV, energy_ref_kcal = None, None

                t_refine = timepfc() - t_refine_start
                t_refine_sum += t_refine
                write_result(
                    ['time_refine [s]', 'energy_refine [eV]', 'energy_refine [kcal/mol]'],
                    [t_refine, energy_ref_eV, energy_ref_kcal],
                    idx
                )
                
                # Reuse the correction from this exact vibrational calculation.
                if thermal_corr_G is not None and energy_ref_kcal is not None:
                    G_refine_kcal = energy_ref_kcal + thermal_corr_G
                    write_result('G_refine [kcal/mol] (HL//LL)', G_refine_kcal, idx)

    refresh_relative_energies()
    try:
        df_new.to_csv(g.R_CSV, index=False)
    except Exception as e:
        log("Warn", f"An error occurred while writing {g.R_CSV}: {e}")

    # Log total times
    if getattr(g, 'REFINE_INPUT_ON', False):
        write_line(g.TIME_LOG_NAME, f"* Optimize_Total        | {t_opt_sum:>12.2f} s  *\n")
    if getattr(g, 'VIB_ON', False):
        write_line(g.TIME_LOG_NAME, f"* Vibrations_Total      | {t_vib_sum:>12.2f} s  *\n")
    if getattr(g, 'REFINE_ENERGY_ON', False):
        write_line(g.TIME_LOG_NAME, f"* Refinement_Total      | {t_refine_sum:>12.2f} s  *\n")


def csv_has_missing_initial_energies(csv_file):
    energy_columns = ["energy [eV]", "energy [hartree]", "energy [kcal/mol]"]
    try:
        df = pd.read_csv(csv_file)
    except Exception as e:
        log("Warn", f"Could not inspect initial energy CSV {csv_file}: {e}")
        return True

    if df.empty:
        return True
    if any(column not in df.columns for column in energy_columns):
        return True
    return df[energy_columns].isna().any().any()


def write_cat_initial_energies(traj_name, csv_name):
    write_energies(traj_name, csv_name)
    if getattr(g, 'REFINE_INPUT_ON', False):
        return
    if not csv_has_missing_initial_energies(csv_name):
        return

    log("Info", f"{csv_name} has missing CAT initial energies; recalculating frame energies.")
    write_energies(traj_name, csv_name, energy_recalc=True)
    if csv_has_missing_initial_energies(csv_name):
        log("Warn", f"{csv_name} still contains missing CAT initial energies after recalculation.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run full IRC jobs starting with reactant.xyz and product.xyz')
    parser.add_argument("-d", "--directory", type=str, required=True, help="path to the destination folder")
    parser.add_argument("--config", type=str, default=None, help="JSON configuration file applied before CLI overrides")
    parser.add_argument("-c", "--charge", type=int, required=True, help="system total charge")
    parser.add_argument("-m", "--method", type=str, default=None, help="calculation method of the PES")
    parser.add_argument("--orbmol-version", type=str, choices=["v1", "v2"], default=None, help="OrbMol model version")
    parser.add_argument("--alpb-solvent", type=str, default=None, help="ALPB solvent name")
    parser.add_argument("--tblite-accuracy", type=float, default=None, help="TBLite accuracy")
    parser.add_argument("-r", "--reactant", type=str, default=None, help="inputfile for the reactant .xyz file")
    parser.add_argument("-p", "--product", type=str, default=None, help="inputfile for the product .xyz file")
    parser.add_argument("-cat", "--catfiles", type=str, nargs='+', default=None, help="list of files to concatenate (.xyz/.traj)")
    parser.add_argument("-i", "--input", type=str, default=None, help="input .traj or .xyz file")
    parser.add_argument("-rs", "--result", type=str, default=None, help="resulting dataframe .csv file")
    args = parser.parse_args()
    apply_config_file(g, args.config)
    
    log("System", f"Starting MolScout at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    t_total_start = timepfc()
    if getattr(g, 'INIT_PATH_SEARCH_ON', True):
        if not os.path.exists(args.directory):
            os.makedirs(args.directory, exist_ok=True)
        else:
            log("Fail", f"Canceled: {args.directory} already exists")
            sys.exit()

        # File copy routing for CAT mode
        if getattr(g, 'INIT_PATH_METHOD', '') == 'CAT':
            if not args.catfiles:
                sys.exit("abort: -cat is required when INIT_PATH_METHOD is CAT")
            g.CONCAT_FILES = []
            for fpath in args.catfiles:
                if not os.path.exists(fpath):
                    sys.exit(f"abort: input file '{fpath}' does not exist")
                shutil.copy(fpath, args.directory)
                g.CONCAT_FILES.append(os.path.basename(fpath))
            log("I/O", f"Copied files for concatenation to {args.directory}")
        
        # Standard path search copy routing
        else:
            if not args.reactant:
                sys.exit(f"abort: -r is required for {getattr(g, 'INIT_PATH_METHOD', 'DMF')} path search method")
            shutil.copy(args.reactant, args.directory)
            shutil.copy(args.reactant, args.directory+"/reactant.xyz")
            if getattr(args, 'product', None):
                shutil.copy(args.product, args.directory)
                shutil.copy(args.product, args.directory+"/product.xyz")
                log("I/O", f"Copied reactant and product to {args.directory}")
            else:
                log("I/O", f"Copied reactant to {args.directory}")
    else:
        if not args.input or not os.path.exists(args.input):
            log("Fail", f"Canceled: cannot load {args.input}")
            sys.exit()
        if not os.path.exists(args.directory):
            os.makedirs(args.directory, exist_ok=True)
        input_name = os.path.basename(args.input)
        if not os.path.exists(args.directory+"/"+input_name):
            shutil.copy(args.input, args.directory)
            log("I/O", f"Copied {input_name} to {args.directory}")
        g.I_TRAJ = input_name
        
    os.chdir(args.directory)
    cli_overrides = {
        "CURRENT_DIR": args.directory,
        "CHARGE": args.charge,
    }
    if args.method is not None:
        cli_overrides["CALC_TYPE"] = args.method
    if args.result is not None:
        cli_overrides["R_CSV"] = args.result
    if args.orbmol_version is not None:
        cli_overrides["ORBMOL_VERSION"] = args.orbmol_version
    if args.alpb_solvent is not None:
        cli_overrides["ALPB_SOLVENT"] = args.alpb_solvent
    if args.tblite_accuracy is not None:
        cli_overrides["TBLITE_ACCURACY"] = args.tblite_accuracy
    apply_config(g, cli_overrides)
    if not hasattr(g, "R_CSV"):
        apply_config(g, {"R_CSV": "result.csv"})
    log("System", f"Charge: {g.CHARGE}, Method: {g.CALC_TYPE}")
    if os.path.exists(g.R_CSV):
        log("Info", f"{g.R_CSV} will be overwritten")
    else:
        log("Info", f"{g.R_CSV} will be made")
    if "alpb" in g.CALC_TYPE.lower() and getattr(g, 'TBLITE_METHOD', '') == "hybrid":
        if not getattr(g, 'OPT_OPTPOINTS_AGAIN_ON', False):
            log("Info", "Hybrid + ALPB mode detected: Forcing OPT_OPTPOINTS_AGAIN_ON=True to re-optimize geometries on the GFN2-xTB PES.")
            g.OPT_OPTPOINTS_AGAIN_ON = True

    initial_path_method = str(getattr(g, 'INIT_PATH_METHOD', 'DMF')).upper()
    refine_input_applicable = (
        bool(getattr(g, 'INIT_PATH_SEARCH_ON', True))
        and initial_path_method in {"DMF", "NEB", "CAT"}
    )
    if not refine_input_applicable:
        g.REFINE_INPUT_ON = False

    save_config(config_to_dict(g), "resolved_config.json")
    log("System", "--- Global Configuration Dump ---")
    for key in dir(g):
        if key.isupper() and not key.startswith("_"):
            val = getattr(g, key)
            log("Config", f"{key} = {val}")
    log("System", "---------------------------------")
    
    # == Main Execution Flow ==
    if getattr(g, 'INIT_PATH_SEARCH_ON', True):
        # Branch for Batch Processing Route (CAT mode)
        if getattr(g, 'INIT_PATH_METHOD', '') == 'CAT':
            concat_files = getattr(g, 'CONCAT_FILES', [])
            generate_path_concat(concat_files, output_traj="init_path.traj")
            g.I_TRAJ = "init_path.traj"
            write_cat_initial_energies(g.I_TRAJ, g.R_CSV)
            process_batch_frames()
        # Standard Route (Path Generation -> Peak Extraction -> TS/IRC)
        else:
            run_initial_path_search()
            g.I_TRAJ = "init_path.traj" # ignores args.input, unified output
            write_energies(g.I_TRAJ, g.R_CSV)
            process_local_maxima()
            
    elif not getattr(g, 'PRESERVE_CSV_ON', False):
        if getattr(g, 'INIT_RECALC_MODE_ON', False):
            #Ignore the file's energy, strictly recalculate
            write_energies(g.I_TRAJ, g.R_CSV, energy_recalc=True)
        else:
            write_energies(g.I_TRAJ, g.R_CSV)
        process_local_maxima()
    
    # Finish
    finalize_run()
    t_total = timepfc() - t_total_start
    txt = f"* Total_Time            | {t_total:>12.2f} s  *\n"
    write_line(g.TIME_LOG_NAME, txt)
    log("Time", f"* Total_Time | {t_total:>12.2f} s *")
    log("System", f"Finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
