"""Cross-session artifact catalog and storage diagnostics."""

from __future__ import annotations

import csv
import io

import streamlit as st

from app_core.artifact_manager import (
    artifact_path,
    diagnose_artifact_catalog,
    scan_all_artifacts,
)
from app_core.database import (
    artifact_filter_values,
    artifact_summary,
    search_artifact_records,
)
from app_core.session_manager import list_sessions
from app_ui.sidebar import (
    database_page_url,
    set_database_multi_selection,
)
from app_ui.views import format_app_time

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DIAGNOSTIC_STATE_KEY = "artifact_catalog_diagnostic"


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value)} B"


def issues_csv(issues: list[dict]) -> bytes:
    output = io.StringIO()
    fieldnames = ["issue", "session_id", "job_id", "relative_path", "detail"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(issues)
    return output.getvalue().encode("utf-8-sig")


def render_summary() -> None:
    summary = artifact_summary()
    cols = st.columns(5)
    cols[0].metric("Indexed Files", summary["artifact_count"])
    cols[1].metric("Indexed File Size", format_bytes(summary["available_bytes"]))
    cols[2].metric("Missing Files", summary["missing_count"])
    cols[3].metric("Sessions", summary["session_count"])
    cols[4].metric("Jobs", summary["job_count"])



def render_catalog() -> None:
    render_summary()
    filter_values = artifact_filter_values()
    sessions = list_sessions()
    session_ids = [str(item["session_id"]) for item in sessions]

    st.markdown("### :material/search: 横断検索")
    row1 = st.columns([2.5, 1.5, 1.3])
    search_text = row1[0].text_input(
        "検索",
        placeholder="ファイル名、パス、ジョブ名、workflow、method、owner",
    )
    selected_session = row1[1].selectbox(
        "セッション",
        [""] + session_ids,
        format_func=lambda value: "すべて" if not value else value,
    )
    selected_type = row1[2].selectbox(
        "ファイル種別",
        [""] + filter_values["artifact_types"],
        format_func=lambda value: "すべて" if not value else value,
    )

    row2 = st.columns([1.3, 1.3, 1])
    availability = row2[0].selectbox(
        "実体状態",
        ["", "available", "missing"],
        index=1,
        format_func=lambda value: {"": "すべて", "available": "利用可能", "missing": "欠損"}[value],
    )
    job_status = row2[1].selectbox(
        "ジョブ状態",
        [""] + filter_values["job_statuses"],
        format_func=lambda value: "すべて" if not value else value,
    )
    limit = row2[2].selectbox("表示上限", [100, 300, 500, 1000], index=2)

    records = search_artifact_records(
        text=search_text,
        session_id=selected_session,
        artifact_type=selected_type,
        availability_status=availability,
        job_status=job_status,
        limit=limit,
    )
    st.caption(f"{len(records)}件を表示しています。ファイル内容はDBに格納せず、dataディレクトリ上の実体を参照します。")

    table_rows = [
        {
            "modified": format_app_time(item.get("modified_at")),
            "session": item["session_id"],
            "job": item.get("job_id") or "-",
            "status": item.get("job_status") or "-",
            "workflow": item.get("workflow") or "-",
            "method": item.get("method") or "-",
            "type": item["artifact_type"],
            "role": item["artifact_role"],
            "size": format_bytes(item["size_bytes"]),
            "file": item["filename"],
            "path": item["relative_path"],
            "availability": item["availability_status"],
        }
        for item in records
    ]
    selection = st.dataframe(
        table_rows,
        hide_index=True,
        width="stretch",
        height=460,
        on_select="rerun",
        selection_mode="multi-row",
        key="artifact_catalog_search_results",
    )

    if not records:
        return

    path_options = [item["relative_path"] for item in records]
    selected_rows = [
        index
        for index in selection.selection.rows
        if isinstance(index, int) and 0 <= index < len(records)
    ]
    if selected_rows:
        st.session_state["artifact_catalog_selected_path"] = records[selected_rows[0]]["relative_path"]
    if st.session_state.get("artifact_catalog_selected_path") not in path_options:
        st.session_state["artifact_catalog_selected_path"] = path_options[0]

    selected_jobs: list[dict] = []
    seen_jobs: set[tuple[str, str]] = set()
    for index in selected_rows:
        item = records[index]
        session_id = str(item.get("session_id") or "")
        job_id = str(item.get("job_id") or "")
        key = (session_id, job_id)
        if not session_id or not job_id or key in seen_jobs:
            continue
        seen_jobs.add(key)
        selected_jobs.append(item)

    if selected_rows:
        st.caption(
            f"Selected artifacts: {len(selected_rows)} | Selected jobs: {len(selected_jobs)}"
        )

    if selected_jobs:
        st.markdown("### :material/open_in_new: Selected Jobs")
        st.caption("Each link opens in a new browser tab with an independent Database context.")
        for index, item in enumerate(selected_jobs):
            session_id = str(item.get("session_id") or "")
            job_id = str(item.get("job_id") or "")
            info_col, results_col, chemiscope_col = st.columns([2.4, 1, 1])
            info_col.markdown(
                f"`{job_id}`  \n{session_id} | {item.get('workflow') or '-'} | {item.get('job_status') or '-'}"
            )
            results_url = database_page_url("results", session_id, job_id)
            chemiscope_url = database_page_url("chemiscope", session_id, job_id)
            if results_url:
                results_col.link_button(
                    "Results",
                    results_url,
                    icon=":material/open_in_new:",
                    width="stretch",
                )
            else:
                results_col.button(
                    "Results",
                    disabled=True,
                    width="stretch",
                    key=f"artifact_results_link_unavailable_{index}",
                )
            if chemiscope_url:
                chemiscope_col.link_button(
                    "Chemiscope",
                    chemiscope_url,
                    icon=":material/open_in_new:",
                    width="stretch",
                )
            else:
                chemiscope_col.button(
                    "Chemiscope",
                    disabled=True,
                    width="stretch",
                    key=f"artifact_chemiscope_link_unavailable_{index}",
                )

        selected_sessions = {str(item.get("session_id") or "") for item in selected_jobs}
        if len(selected_sessions) == 1:
            archive_session_id = next(iter(selected_sessions))
            archive_job_ids = [str(item.get("job_id") or "") for item in selected_jobs]
            if st.button(
                ":material/folder_zip: Use selected jobs in sidebar ZIP",
                width="stretch",
                key="artifact_use_selected_jobs_for_archive",
            ):
                set_database_multi_selection(archive_session_id, archive_job_ids)
                st.rerun()
        else:
            st.caption("Sidebar ZIP selection is session-scoped; selected jobs span multiple sessions.")

    st.markdown("### :material/draft: ファイル詳細")
    selected_path = st.selectbox(
        "対象ファイル",
        path_options,
        key="artifact_catalog_selected_path",
        format_func=lambda value: value,
    )
    selected = next(item for item in records if item["relative_path"] == selected_path)
    cols = st.columns(4)
    cols[0].metric("Artifact Type", selected["artifact_type"])
    cols[1].metric("Role", selected["artifact_role"])
    cols[2].metric("File Size", format_bytes(selected["size_bytes"]))
    cols[3].metric("Status", selected["availability_status"])
    st.code(selected["relative_path"], language="text")

    target_job_id = str(selected.get("job_id") or "")
    if target_job_id:
        target_session_id = str(selected.get("session_id") or "")
        results_url = database_page_url("results", target_session_id, target_job_id)
        chemiscope_url = database_page_url("chemiscope", target_session_id, target_job_id)
        action_cols = st.columns(2)
        if results_url:
            action_cols[0].link_button(
                "Open Job in Results",
                results_url,
                icon=":material/open_in_new:",
                width="stretch",
            )
        if chemiscope_url:
            action_cols[1].link_button(
                "Open Job in Chemiscope",
                chemiscope_url,
                icon=":material/open_in_new:",
                width="stretch",
            )
        if not results_url or not chemiscope_url:
            st.caption("New-tab links are unavailable because the current app URL could not be resolved.")
    else:
        st.caption("この成果物にはJob IDがないため、Results / Chemiscopeへの導線はありません。")

    metadata = selected.get("metadata") or {}
    if metadata:
        with st.expander("登録メタデータ", expanded=False):
            st.json(metadata)

    try:
        path = artifact_path(selected["relative_path"])
    except ValueError as exc:
        st.error(str(exc))
        return
    if not path.exists() or not path.is_file():
        st.warning("ファイル実体が見つかりません。保守タブで診断または再スキャンしてください。")
        return
    current_size = path.stat().st_size
    if current_size > MAX_DOWNLOAD_BYTES:
        st.info(
            f"{format_bytes(current_size)}のため、この画面からのダウンロード対象外です。dataディレクトリ上の実体を利用してください。"
        )
        return
    st.download_button(
        ":material/download: このファイルをダウンロード",
        data=path.read_bytes(),
        file_name=path.name,
        mime="application/octet-stream",
        width="stretch",
    )


def render_maintenance() -> None:
    st.info(
        "このページの操作はカタログ登録と診断だけです。ファイル削除、DBレコード削除、孤立データの自動修復は行いません。"
    )
    cols = st.columns(2)
    if cols[0].button(":material/sync: dataを再スキャン", type="primary", width="stretch"):
        try:
            with st.spinner("dataディレクトリを走査しています..."):
                result = scan_all_artifacts(source="admin_page", dry_run=False)
        except Exception as exc:
            st.error(f"再スキャンに失敗しました: {type(exc).__name__}: {exc}")
        else:
            st.session_state.pop(DIAGNOSTIC_STATE_KEY, None)
            if result["errors"]:
                st.warning(
                    f"{result['artifacts_found']}件を検出しましたが、{len(result['errors'])}件のエラーがあります。"
                )
                st.code("\n".join(result["errors"]), language="text")
            else:
                st.success(
                    f"{result['sessions_scanned']}セッション、{result['jobs_scanned']}ジョブ、"
                    f"{result['artifacts_found']}ファイルを確認しました。"
                )

    if cols[1].button(":material/fact_check: 整合性を診断", width="stretch"):
        try:
            with st.spinner("DBとファイルを照合しています..."):
                st.session_state[DIAGNOSTIC_STATE_KEY] = diagnose_artifact_catalog()
        except Exception as exc:
            st.error(f"診断に失敗しました: {type(exc).__name__}: {exc}")

    report = st.session_state.get(DIAGNOSTIC_STATE_KEY)
    if not report:
        st.caption("診断は明示実行時のみ行います。大きなdataディレクトリでは走査に時間がかかります。")
        return

    st.markdown("### :material/health_and_safety: 診断結果")
    cols = st.columns(3)
    cols[0].metric("DB登録数", report["registered_count"])
    cols[1].metric("対象ファイル数", report["eligible_files_found"])
    cols[2].metric("問題候補", report["issue_count"])

    issues = report["issues"]
    if not issues:
        st.success("登録対象について不整合は見つかりませんでした。")
        return

    st.dataframe(issues, hide_index=True, width="stretch", height=430)
    st.download_button(
        ":material/download: 診断結果CSV",
        data=issues_csv(issues),
        file_name="molscout_artifact_diagnostics.csv",
        mime="text/csv",
        width="stretch",
    )
    with st.expander("問題種別ごとの件数", expanded=False):
        st.json(report["issue_counts"])


st.set_page_config(page_title="MolScout [Data]")
st.markdown("## :material/database: Data Catalog")
st.caption("全セッションの成果物を検索し、PostgreSQLの登録情報とdataディレクトリの整合性を確認します。")

catalog_tab, maintenance_tab = st.tabs(["横断閲覧", "保守・診断"])
with catalog_tab:
    render_catalog()
with maintenance_tab:
    render_maintenance()
