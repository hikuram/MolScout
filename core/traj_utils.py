"""
traj_utils.py
Utility functions for processing ASE trajectory (.traj) files and extracting data.
"""

import os
import csv
import numpy as np
import pandas as pd
from typing import List, Optional
from ase import Atoms
from ase.io import write
from ase.io.trajectory import Trajectory
from scipy.signal import find_peaks
from utils import log, read
try:
    import rmsd
    HAS_RMSD = True
except ImportError:
    HAS_RMSD = False

# Project modules
import default_config as g
from ase_calculators import make_calculator

def extract_peaks_from_traj(trajfile: str, maxima_filename: str, prominence: float = 0.01) -> List[str]:
    """
    Read a trajectory file, find local energy maxima (peaks), and save them as .xyz files.
    Returns a list of generated .xyz filenames and their indices.
    """
    traj = read(trajfile, index=':')
    energies = []
    for i, atoms in enumerate(traj):
        try:
            energy = atoms.get_potential_energy()
        except Exception as e:
            log("Warn", f"Missing value for {trajfile} atom {i}: {e}")
            energy = np.nan
        energies.append(energy)
    energies = np.array(energies)

    # Fill NaN values to avoid breaking the peak finding algorithm
    def forward_fill_nan(arr):
        filled = arr.copy()
        last_valid = np.nan
        for i in range(len(filled)):
            if not np.isnan(filled[i]):
                last_valid = filled[i]
            else:
                filled[i] = last_valid
        return filled
    energies_filled = forward_fill_nan(energies)

    base_name = os.path.splitext(os.path.basename(maxima_filename))[0]

    # --- Added: Special handling for SCAN mode ---
    if getattr(g, 'INIT_PATH_METHOD', '') == 'SCAN':
        log("Info", "SCAN mode detected: Extracting global max and adjacent minima based on energy.")
        max_idx = int(np.argmax(energies_filled))
        
        # Forward minimum (from start to max_idx)
        if max_idx > 0:
            min_forward = int(np.argmin(energies_filled[:max_idx + 1]))
        else:
            min_forward = 0
            
        # Backward minimum (from max_idx to end)
        if max_idx < len(energies_filled) - 1:
            min_backward = int(max_idx + np.argmin(energies_filled[max_idx:]))
        else:
            min_backward = len(energies_filled) - 1
            
        g.HIGHEST_PEAK_FILE = f"{base_name}_{max_idx}.xyz"
        g.PEAK_IDX = np.unique([min_forward, max_idx, min_backward])
        log("Info", f"Selected indices for SCAN optpoints: {g.PEAK_IDX}")
        
    else:
        # Standard detection for DMF/NEB
        peaks, _ = find_peaks(energies_filled, prominence=prominence)
        log("Info", f"Detected {len(peaks)} peak(s) (excluding endpoints). Saving structures:")
        
        if len(peaks) > 0:
            max_peak_idx = peaks[np.argmax(energies_filled[peaks])]
            g.HIGHEST_PEAK_FILE = f"{base_name}_{max_peak_idx}.xyz"
        else:
            g.HIGHEST_PEAK_FILE = None

        # Always include the first and last frames as endpoints
        endpoints = np.array([0, len(traj) - 1])
        g.PEAK_IDX = np.unique(np.concatenate([peaks, endpoints]))

    peak_files = []
    for idx in g.PEAK_IDX:
        atoms = traj[idx]
        filename = f"{base_name}_{idx}.xyz"
        peak_files.append(filename)
        write(filename, atoms)
        log("I/O", f"Wrote {filename} (energy = {energies[idx]:.6f})")

    return peak_files, g.PEAK_IDX

def split_traj_to_xyz(trajfile: str, prefix: str) -> List[str]:
    """Split a trajectory file into multiple single-frame .xyz files."""
    traj = read(trajfile, index=":")
    xyz_files = []

    for i, atoms in enumerate(traj):
        filename = f"{prefix}_{i}.xyz"
        write(filename, atoms)
        xyz_files.append(filename)

    return xyz_files

def traj_to_xyz(traj, out_xyz_path):
    """Convert an ASE trajectory list to an .xyz file format."""
    try:
        for atoms in traj:
            atoms.info = {str(k): v for k, v in atoms.info.items()}
        write(out_xyz_path, traj)
    except Exception as e:
        log("Warn", f"An error occurred while writing {out_xyz_path}: {e}")

def write_energies(traj_name, csv_name=None, energy_recalc=False, previous_image=None):
    """
    Extract energies from a trajectory file and write them to a CSV file.
    Optionally recalculates the single-point energy for each frame.
    Now supports appending the SCAN coordinate (bond, angle, dihedral) if in SCAN mode.
    """
    if not csv_name:
        csv_name = os.path.splitext(traj_name)[0] + "_energy.csv"
    data = []
    tmp_name = traj_name + ".tmp"
    
    traj_out = Trajectory(tmp_name, "w") if energy_recalc else None
    traj_in = Trajectory(traj_name)
    
    calc_rmsd = getattr(g, 'CALC_RMSD_ON', False) and HAS_RMSD
    if getattr(g, 'CALC_RMSD_ON', False) and not HAS_RMSD:
        log("Warn", "rmsd module is not installed. Skipping RMSD calculation.")

    pos_0 = None
    pos_prev = None
    atomic_numbers_0 = None
    atomic_numbers_prev = None
    rmsd_vs_0_skipped = 0
    rmsd_vs_prev_skipped = 0
    
    # Setup for SCAN coordinate extraction
    is_scan = (getattr(g, 'INIT_PATH_METHOD', '') == 'SCAN')
    scan_type = getattr(g, 'SCAN_TYPE', 'bond')
    scan_indices = getattr(g, 'SCAN_INDICES', [])
    scan_col_name = None
    
    if is_scan:
        idx_str = "_".join(map(str, scan_indices))
        if scan_type == 'bond':
            scan_col_name = f"SCAN_{scan_type}_{idx_str} [Å]"
        elif scan_type in ['angle', 'dihedral']:
            scan_col_name = f"SCAN_{scan_type}_{idx_str} [deg]"
        else:
            scan_col_name = f"SCAN_value"
    
    try:
        for i, atoms in enumerate(traj_in):
            if energy_recalc:
                atoms.info = {"charge": g.CHARGE, "spin": g.MULT}
                atoms.calc = make_calculator(g.CALC_TYPE, atoms, "energy_recalc")
            try:
                energy_ev = atoms.get_potential_energy()
                energy_hartree = energy_ev * g.EV_TO_HARTREE
                energy_kcal = energy_ev * g.EV_TO_KCAL_MOL
            except Exception as e:
                log("Warn", f"Missing value for {traj_name} frame {i}: {e}")
                energy_ev, energy_hartree, energy_kcal = None, None, None

            # === RMSD calculation (Heavy atoms only) ===
            rmsd_0 = np.nan
            rmsd_prev = np.nan
            if calc_rmsd:
                pos = atoms.get_positions()
                atomic_numbers = atoms.get_atomic_numbers()

                # Hydrogen atoms are intentionally excluded from this metric.  The
                # remaining atom identity/order must still match because Kabsch RMSD
                # assumes a one-to-one correspondence between coordinate rows.
                heavy_mask = atomic_numbers > 1
                heavy_atomic_numbers = atomic_numbers[heavy_mask]
                heavy_pos = pos[heavy_mask]
                if len(heavy_pos) > 0:
                    heavy_pos_centered = heavy_pos - rmsd.centroid(heavy_pos)
                else:
                    heavy_pos_centered = np.empty((0, 3), dtype=float)

                if i == 0:
                    pos_0 = heavy_pos_centered.copy()
                    pos_prev = heavy_pos_centered.copy()
                    atomic_numbers_0 = heavy_atomic_numbers.copy()
                    atomic_numbers_prev = heavy_atomic_numbers.copy()
                    rmsd_0 = 0.0
                    rmsd_prev = 0.0
                else:
                    same_as_0 = np.array_equal(atomic_numbers_0, heavy_atomic_numbers)
                    same_as_prev = np.array_equal(atomic_numbers_prev, heavy_atomic_numbers)

                    if same_as_0:
                        rmsd_0 = (
                            0.0
                            if len(heavy_atomic_numbers) == 0
                            else rmsd.kabsch_rmsd(pos_0, heavy_pos_centered)
                        )
                    else:
                        rmsd_vs_0_skipped += 1

                    if same_as_prev:
                        rmsd_prev = (
                            0.0
                            if len(heavy_atomic_numbers) == 0
                            else rmsd.kabsch_rmsd(pos_prev, heavy_pos_centered)
                        )
                    else:
                        rmsd_vs_prev_skipped += 1

                    # Always advance the previous-frame reference.  This lets RMSD
                    # resume normally inside the next same-composition segment of a
                    # concatenated batch trajectory.
                    pos_prev = heavy_pos_centered.copy()
                    atomic_numbers_prev = heavy_atomic_numbers.copy()
            # ===========================================

            row = [i, energy_ev, energy_hartree, energy_kcal]
            if calc_rmsd:
                row.extend([rmsd_0, rmsd_prev])
                
            # Calculate and append the current SCAN coordinate value
            if is_scan:
                scan_val = np.nan
                try:
                    if scan_type == 'bond' and len(scan_indices) == 2:
                        scan_val = atoms.get_distance(scan_indices[0], scan_indices[1])
                    elif scan_type == 'angle' and len(scan_indices) == 3:
                        scan_val = atoms.get_angle(scan_indices[0], scan_indices[1], scan_indices[2])
                    elif scan_type == 'dihedral' and len(scan_indices) == 4:
                        scan_val = atoms.get_dihedral(scan_indices[0], scan_indices[1], scan_indices[2], scan_indices[3])
                except Exception as e:
                    log("Warn", f"Failed to compute SCAN coordinate for frame {i}: {e}")
                row.append(scan_val)
            
            data.append(row)
            
            if energy_recalc:
                traj_out.write(atoms)
                atoms.calc = None
                del atoms
    finally:
        traj_in.close()
        if traj_out is not None:
            traj_out.close()
            
    if energy_recalc:
        os.replace(tmp_name, traj_name)

    if calc_rmsd and (rmsd_vs_0_skipped or rmsd_vs_prev_skipped):
        log(
            "Warn",
            "Skipped heavy-atom RMSD for incompatible concatenated frames "
            f"(vs frame 0: {rmsd_vs_0_skipped}, vs previous frame: {rmsd_vs_prev_skipped}). "
            "The corresponding CSV values were written as NaN.",
        )
        
    cols = ["# image", "energy [eV]", "energy [hartree]", "energy [kcal/mol]"]
    if calc_rmsd:
        # Update column names to clarify heavy-atom usage
        cols.extend(["Heavy-RMSD vs frame 0 [Å]", "Heavy-RMSD vs prev frame [Å]"])
    
    # Append the dynamically named column for the SCAN coordinate
    if is_scan and scan_col_name:
        cols.append(scan_col_name)
        
    df = pd.DataFrame(data, columns=cols)
    
    if previous_image is not None:
        if len(previous_image) != len(df):
            raise ValueError("Length of previous_image must match the number of frames")
        df["previous_#image"] = previous_image
        cols_reorder = ["# image", "previous_#image"] + [c for c in df.columns if c not in ["# image", "previous_#image"]]
        df = df[cols_reorder]
        
    # Calculate relative energy vs reactant
    if df["energy [kcal/mol]"].notna().any():
        ref = df.loc[0, "energy [kcal/mol]"]
        df["Delta E vs. reactant [kcal/mol]"] = df["energy [kcal/mol]"] - ref
    else:
        df["Delta E vs. reactant [kcal/mol]"] = None
        
    df.to_csv(csv_name, index=False)

def generate_path_concat(file_paths: List[str], output_traj: str = 'init_path.traj', 
                         output_xyz: str = 'init_path.xyz', output_csv: str = 'concat_mapping.csv') -> List[Atoms]:
    """
    Concatenates multiple .xyz or .traj files into a single trajectory.
    This mode is specifically used for batch processing of existing frames.
    
    Original filenames and frame indices are stored in the Atoms.info dictionary 
    and exported to a mapping CSV file for traceability.
    """
    concatenated_atoms = []
    csv_records = []
    global_frame_index = 0
    
    for file_path in file_paths:
        filename = os.path.basename(file_path)
        log("Path", f"Reading and concatenating {filename} ...")
        
        # Use custom read() to bypass fragile extXYZ metadata parsing
        frames = read(file_path, index=':')
        if not isinstance(frames, list):
            frames = [frames]
            
        for local_index, atoms in enumerate(frames):
            # Preserve metadata
            atoms.info['original_file'] = filename
            atoms.info['original_frame'] = local_index
            atoms.info['charge'] = g.CHARGE
            atoms.info['spin'] = g.MULT
            
            concatenated_atoms.append(atoms)
            csv_records.append({
                'global_frame': global_frame_index,
                'original_file': filename,
                'original_frame': local_index
            })
            global_frame_index += 1

    if not concatenated_atoms:
        log("Fail", "No valid frames were found to concatenate.")
        raise ValueError("Concatenation failed: empty input lists or invalid files.")

    if output_traj:
        write(output_traj, concatenated_atoms)
    if output_xyz:
        write(output_xyz, concatenated_atoms)
        
    log("I/O", f"Wrote concatenated trajectory to {output_traj} and {output_xyz}")
        
    if output_csv:
        try:
            with open(output_csv, 'w', newline='', encoding='utf-8') as f:
                fieldnames = ['global_frame', 'original_file', 'original_frame']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(csv_records)
            log("I/O", f"Wrote mapping CSV to {output_csv}")
        except Exception as e:
            log("Warn", f"An error occurred while writing {output_csv}: {e}")
            
    g.SUGGESTIONS.append(f"ase gui {g.CURRENT_DIR}/{output_traj}")
    return concatenated_atoms
