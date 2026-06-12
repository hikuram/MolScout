"""About page."""

from __future__ import annotations

import streamlit as st

from app_core.paths import AUTO_REFRESH_SECONDS, SESSION_RETENTION_DAYS


st.markdown("# :material/science: MolScout Remote Queue")
st.caption(
    "長時間を要するワークフローを共有実行するための laboratory queue です。"
    "ユーザーセッションとサーバーモニタリング機能を備えています。"
)
st.markdown(
    '<span class="app-badge">1 worker</span>'
    f'<span class="app-badge">{AUTO_REFRESH_SECONDS}s refresh</span>'
    f'<span class="app-badge">{SESSION_RETENTION_DAYS} day retention</span>',
    unsafe_allow_html=True,
)

st.divider()

st.markdown("### Page guide")
st.markdown(
    """
- **Queue**: 共有キューと選択中セッションの概要を確認します。
- **Submit**: 反応経路探索とファイル連結処理の job を投入します。
- **Results**: セッション内 job、ログ、結果ファイル、ZIP download を確認します。
- **PySCF**: 選択中セッションの PySCF 設定を編集します。
"""
)

st.markdown("### Sidebar")
st.markdown(
    """
セッション作成・選択、monitoring、環境チェック、サンプル一覧、worker log、cleanup は全 page 共通の sidebar にあります。
Cleanup は確認 dialog から実行します。
"""
)
