import os
import sys
import argparse
from pathlib import Path
from ase.io import write
from utils import read

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Concatenate ASE trajectory files and exit')
    parser.add_argument("-i", "--input", type=str, required=True, nargs='+', help="paths for input (space separated)")
    parser.add_argument("-o", "--output", type=str, required=True, help="path for output")
    args = parser.parse_args()
    if not Path(args.output).suffix in [".traj", ".xyz", ".json"]:
        sys.exit("error: incorrect output type")
    cattraj = []
    for name in args.input:
        cattraj += read(name, index=":")
    odir = os.path.dirname(args.output)
    if not os.path.isdir(odir):
        os.makedirs(odir)
    write(args.output, cattraj)
