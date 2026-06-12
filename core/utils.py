from ase import Atoms
from ase.io import read as read_ase
from datetime import datetime
import default_config as g

def log(tag: str, msg: str):
    """
    Helper function to standardize terminal output format and automatically 
    append it to molscout.log in the current working directory.
    """
    print(f"[{tag:<6}] {msg}")
    
    log_file = "molscout.log"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{ts} [{tag:<6}] {msg}\n")
    except Exception as e:
        pass

def read(filename, index=None):
    """
    Universal file reader that enforces clean parsing.
    For .traj files, it uses the standard ASE reader.
    For .xyz files, it manually parses the file block by block,
    strictly ignoring the second line (comment line) of every frame
    to prevent ASE's extXYZ parser from crashing on corrupted metadata.
    """
    # .traj files are safe to read with ASE
    if filename.lower().endswith(".traj"):
        idx = index if index is not None else -1
        return read_ase(filename, index=idx)

    # Manual parser for .xyz files
    atoms_list = []
    with open(filename, 'r', encoding='utf-8') as f:
        while True:
            line = f.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            try:
                natoms = int(line)
            except ValueError:
                # Skip invalid lines between frames if any
                continue

            # Read the comment line and completely ignore it
            _ = f.readline()

            symbols = []
            positions = []
            for _ in range(natoms):
                coord_line = f.readline().split()
                if len(coord_line) >= 4:
                    symbols.append(coord_line[0])
                    positions.append([
                        float(coord_line[1]), 
                        float(coord_line[2]), 
                        float(coord_line[3])
                    ])

            atoms = Atoms(symbols=symbols, positions=positions)
            atoms_list.append(atoms)

    if not atoms_list:
        raise ValueError(f"Could not read any valid XYZ frames from {filename}")

    # Handle indexing similar to ASE's read function
    if index == ":" or index == slice(None):
        return atoms_list
    elif isinstance(index, slice):
        return atoms_list[index]
    else:
        # Default single-frame retrieval (e.g., -1 for the last frame)
        idx = index if index is not None else -1
        return atoms_list[idx]

def rescue_xyz_read(file_name, index=-1):
    """
    Attempt to read an XYZ/extXYZ file with rescue fallbacks.

    This function is intended for damaged XYZ/extXYZ files,
    especially cases where manual text edits have broken the
    extended XYZ header or metadata.

    Fallback policy:
    1. Try normal ASE read().
    2. Try forcing plain XYZ format, ignoring extXYZ metadata.
    3. As a last resort, manually parse a single-frame XYZ structure
       from the first frame of the file.

    Notes:
    - The final manual fallback supports only a single-frame structure.
    - If the file contains multiple frames, the manual fallback does
      not honor the requested index and will parse only the first frame.
    - Rescue fallbacks discard calculator and metadata information and
      keep only atomic symbols and Cartesian coordinates.
    """
    try:
        # Attempt 1: Standard ASE read.
        # ASE will automatically detect extxyz when possible.
        atoms = read_ase(file_name, index=index)
        return atoms

    except Exception as e:
        print(
            f"Warning: Standard read failed for '{file_name}' ({e}). "
            "Falling back to plain XYZ parsing..."
        )

        try:
            # Attempt 2: Force plain XYZ format.
            # This treats the file as plain XYZ and ignores extended XYZ metadata.
            atoms = read_ase(file_name, index=index, format="xyz")
            atoms.calc = None
            return atoms

        except Exception as e2:
            print(
                f"Warning: Plain XYZ read also failed for '{file_name}' ({e2}). "
                "Attempting manual single-frame parsing..."
            )

            try:
                with open(file_name, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                if len(lines) < 2:
                    raise ValueError("File is too short to be a valid XYZ file.")

                natoms = int(lines[0].strip())
                coord_lines = lines[2:2 + natoms]

                if len(coord_lines) < natoms:
                    raise ValueError(
                        f"Header says {natoms} atoms, but only "
                        f"{len(coord_lines)} coordinate lines are available."
                    )

                symbols = []
                positions = []

                for line_no, line in enumerate(coord_lines, start=3):
                    parts = line.split()
                    if len(parts) < 4:
                        raise ValueError(
                            f"Invalid coordinate line at line {line_no}: '{line.strip()}'"
                        )

                    symbol = parts[0]
                    x = float(parts[1])
                    y = float(parts[2])
                    z = float(parts[3])

                    symbols.append(symbol)
                    positions.append([x, y, z])

                if len(symbols) != natoms:
                    raise ValueError(
                        f"Parsed {len(symbols)} atoms, but header says {natoms}."
                    )

                atoms = Atoms(symbols=symbols, positions=positions)
                atoms.calc = None
                return atoms

            except Exception as e3:
                raise RuntimeError(
                    f"Failed to rescue XYZ structure from '{file_name}'. "
                    f"Final error: {e3}"
                ) from e3
