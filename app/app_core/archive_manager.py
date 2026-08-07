"""Archive helpers for session and job downloads."""

from __future__ import annotations

import json
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .paths import ARCHIVES_DIR
from .session_manager import (
    get_job,
    get_session,
    job_archive_path,
    job_dir,
    list_jobs,
    session_archive_path,
    session_dir,
)

MERGED_CSV_DIR = "MergedCSV"


def _reset_archive(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    return target


def _job_output_dir(session_id: str, job_id: str) -> Path:
    job = get_job(session_id, job_id) or {}
    output_dir = job.get("output_dir")
    if output_dir:
        return Path(output_dir)
    return job_dir(session_id, job_id) / "run_output"


def _archive_name_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value)
    cleaned = cleaned.strip("._-")
    return cleaned or "item"


def _selected_archive_path(session_id: str, job_ids: list[str], *, flat: bool, include_merged_csv: bool) -> Path:
    suffix = "flat" if flat else "full"
    if include_merged_csv:
        suffix += "-merged"
    if len(job_ids) == 1:
        jobs_part = _archive_name_part(job_ids[0])
    else:
        jobs_part = f"{len(job_ids)}jobs"
    return ARCHIVES_DIR / f"selected-jobs-{_archive_name_part(session_id)}-{jobs_part}-{suffix}.zip"


def _merged_csv_archive_path(session_id: str, job_ids: list[str], *, flat: bool) -> Path:
    suffix = "flat" if flat else "full"
    if len(job_ids) == 1:
        jobs_part = _archive_name_part(job_ids[0])
    else:
        jobs_part = f"{len(job_ids)}jobs"
    return ARCHIVES_DIR / f"merged-csv-{_archive_name_part(session_id)}-{jobs_part}-{suffix}.zip"


def _add_tree(zip_file: zipfile.ZipFile, source: Path, archive_prefix: Path = Path()) -> None:
    if not source.exists():
        return
    for path in sorted(source.rglob("*")):
        if path.is_file():
            zip_file.write(path, (archive_prefix / path.relative_to(source)).as_posix())


def _write_json_snapshot(zip_file: zipfile.ZipFile, archive_path: Path, payload: dict | None) -> None:
    if not payload:
        return
    if archive_path.as_posix() in zip_file.namelist():
        return
    zip_file.writestr(
        archive_path.as_posix(),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _write_job_flat_archive(zip_file: zipfile.ZipFile, session_id: str, job_id: str, archive_prefix: Path = Path()) -> None:
    run_dir = _job_output_dir(session_id, job_id)
    _add_tree(zip_file, run_dir, archive_prefix)


def build_job_archive(session_id: str, job_id: str, *, flat: bool = False) -> Path:
    if not flat:
        source = job_dir(session_id, job_id)
        target = job_archive_path(session_id, job_id)
        _reset_archive(target)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            _add_tree(zip_file, source)
            _write_json_snapshot(zip_file, Path("job.json"), get_job(session_id, job_id))
        return target

    target = ARCHIVES_DIR / f"job-{_archive_name_part(session_id)}-{_archive_name_part(job_id)}-flat.zip"
    _reset_archive(target)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        _write_job_flat_archive(zip_file, session_id, job_id)
    return target


def build_session_archive(session_id: str) -> Path:
    source = session_dir(session_id)
    target = session_archive_path(session_id)
    _reset_archive(target)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        _add_tree(zip_file, source)
        _write_json_snapshot(zip_file, Path("session.json"), get_session(session_id))
        for job in list_jobs(session_id):
            _write_json_snapshot(
                zip_file,
                Path("jobs") / str(job["job_id"]) / "job.json",
                job,
            )
    return target


def _job_archive_file_map(session_id: str, job_id: str, *, flat: bool) -> dict[Path, str]:
    root = job_dir(session_id, job_id)
    run_dir = _job_output_dir(session_id, job_id)
    source = run_dir if flat else root
    prefix = Path(job_id)
    mapping: dict[Path, str] = {}
    if not source.exists():
        return mapping
    for path in sorted(source.rglob("*")):
        if path.is_file():
            try:
                relative = path.relative_to(source)
            except ValueError:
                continue
            mapping[path.resolve()] = (prefix / relative).as_posix()
    return mapping


def _csv_files_by_job(session_id: str, job_ids: list[str]) -> dict[str, list[tuple[str, Path]]]:
    grouped: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    for job_id in job_ids:
        run_dir = _job_output_dir(session_id, job_id)
        if not run_dir.exists():
            continue
        for path in sorted(run_dir.rglob("*.csv")):
            if path.is_file():
                grouped[path.name].append((job_id, path))
    return grouped


def _candidate_paths(value: str, csv_path: Path, job_root: Path, run_dir: Path) -> list[Path]:
    text = value.strip()
    if not text:
        return []
    raw = Path(text)
    candidates = [raw]
    if not raw.is_absolute():
        candidates.extend([
            csv_path.parent / raw,
            run_dir / raw,
            job_root / raw,
        ])
    return candidates


def _archive_path_for_value(value, csv_path: Path, job_root: Path, run_dir: Path, file_map: dict[Path, str]):
    if not isinstance(value, str):
        return value
    for candidate in _candidate_paths(value, csv_path, job_root, run_dir):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        archive_path = file_map.get(resolved)
        if archive_path:
            return archive_path
    return value


def _rewrite_dataframe_paths(df: pd.DataFrame, csv_path: Path, job_root: Path, run_dir: Path, file_map: dict[Path, str]) -> pd.DataFrame:
    if df.empty or not file_map:
        return df
    rewritten = df.copy()
    for column in rewritten.columns:
        if rewritten[column].dtype != object:
            continue
        rewritten[column] = rewritten[column].map(
            lambda value: _archive_path_for_value(value, csv_path, job_root, run_dir, file_map)
        )
    return rewritten


def _merged_csv_payloads(session_id: str, job_ids: list[str], *, flat: bool) -> dict[str, str]:
    grouped = _csv_files_by_job(session_id, job_ids)
    merged_payloads: dict[str, str] = {}
    
    # Preload file maps and job metadata once per selected job.
    file_maps = {}
    job_metas = {}
    for job_id in job_ids:
        file_maps[job_id] = _job_archive_file_map(session_id, job_id, flat=flat)
        job_metas[job_id] = get_job(session_id, job_id) or {}

    for csv_name, items in sorted(grouped.items()):
        source_jobs = {job_id for job_id, _ in items}
        if len(source_jobs) < 2:
            continue
        frames = []
        for job_id, csv_path in items:
            try:
                df = pd.read_csv(csv_path)
            except (OSError, pd.errors.ParserError, UnicodeDecodeError):
                continue
            job_root = job_dir(session_id, job_id)
            run_dir = _job_output_dir(session_id, job_id)
            df = _rewrite_dataframe_paths(df, csv_path, job_root, run_dir, file_maps.get(job_id, {}))
            
            meta = job_metas.get(job_id, {})
            overrides = meta.get("config_overrides", {})
            
            # Metadata columns intentionally overwrite same-named columns instead of failing.
            df["source_row"] = range(1, len(df) + 1)
            df["source_csv"] = file_maps.get(job_id, {}).get(csv_path.resolve(), f"{job_id}/{csv_path.name}")
            df["source_job_id"] = job_id
            df["job_workflow"] = meta.get("workflow", "")
            df["job_method"] = meta.get("method", "")
            df["job_charge"] = meta.get("charge", 0)
            df["multiplicity"] = meta.get("mult", overrides.get("MULT", 1))
            df["orbmol_version"] = overrides.get("ORBMOL_VERSION", "")
            df["tblite_method"] = meta.get("tblite_method", "")
            df["alpb_solvent"] = overrides.get("ALPB_SOLVENT", "")
            df["job_notes"] = meta.get("notes", "")

            meta_cols = [
                "source_job_id", "source_csv", "source_row",
                "job_workflow", "job_method", "job_charge",
                "multiplicity", "orbmol_version", "tblite_method", "alpb_solvent",
                "job_notes"
            ]
            
            original_cols = [c for c in df.columns if c not in meta_cols]
            
            df = df[meta_cols + original_cols]
            
            frames.append(df)
            
        if not frames:
            continue
        merged = pd.concat(frames, ignore_index=True, sort=False)
        merged_payloads[csv_name] = merged.to_csv(index=False)
    return merged_payloads


def build_merged_csv_archive(session_id: str, job_ids: list[str], *, flat: bool = False) -> Path | None:
    merged_payloads = _merged_csv_payloads(session_id, job_ids, flat=flat)
    if not merged_payloads:
        return None
    target = _merged_csv_archive_path(session_id, job_ids, flat=flat)
    _reset_archive(target)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for csv_name, payload in merged_payloads.items():
            zip_file.writestr(f"{MERGED_CSV_DIR}/{csv_name}", payload)
    return target


def build_selected_jobs_archive(
    session_id: str,
    job_ids: list[str],
    *,
    flat: bool = False,
    include_merged_csv: bool = False,
) -> Path:
    target = _selected_archive_path(session_id, job_ids, flat=flat, include_merged_csv=include_merged_csv)
    _reset_archive(target)

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for job_id in job_ids:
            prefix = Path(_archive_name_part(job_id))
            if flat:
                _write_job_flat_archive(zip_file, session_id, job_id, prefix)
            else:
                _add_tree(zip_file, job_dir(session_id, job_id), prefix)
                _write_json_snapshot(zip_file, prefix / "job.json", get_job(session_id, job_id))
        if include_merged_csv:
            merged_payloads = _merged_csv_payloads(session_id, job_ids, flat=flat)
            for csv_name, payload in merged_payloads.items():
                zip_file.writestr(f"{MERGED_CSV_DIR}/{csv_name}", payload)
    return target
