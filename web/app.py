import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sqlite3
import os
import logging
from datetime import datetime, timedelta, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("govtech-dashboard")
logger.info("Starting GovTech Dashboard...")

st.set_page_config(
    page_title="GovTech GitHub Explorer",
    page_icon="🏛️",
    layout="wide",
)


def get_db_path():
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "govtech.db"),
        os.path.join(os.path.dirname(__file__), "govtech.db"),
        "govtech.db",
        "../govtech.db",
    ]
    for p in candidates:
        if os.path.exists(p):
            logger.info(f"Found local DB: {os.path.abspath(p)}")
            return os.path.abspath(p)
    logger.info("No local DB found, downloading from HuggingFace Hub...")
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(
            repo_id="AndreasThinks/government-github-repos",
            filename="data/govtech.db",
            repo_type="dataset",
        )
        logger.info(f"Downloaded DB to: {path}")
        return path
    except Exception as e:
        logger.error(f"Failed to download DB: {e}")
        st.error(f"Could not find or download govtech.db: {e}")
        st.stop()


DB_PATH = get_db_path()


def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data(ttl=300)
def query_df(sql, params=None):
    conn = get_conn()
    df = pd.read_sql_query(sql, conn, params=params or [])
    conn.close()
    return df


@st.cache_data(ttl=300)
def query_one(sql, params=None):
    conn = get_conn()
    cur = conn.cursor()
    result = cur.execute(sql, params or []).fetchone()[0]
    conn.close()
    return result


@st.cache_data(ttl=600)
def load_filter_options():
    conn = get_conn()
    countries = pd.read_sql_query(
        "SELECT DISTINCT country FROM repositories WHERE country IS NOT NULL AND country != '' ORDER BY country", conn
    )["country"].tolist()
    languages = pd.read_sql_query(
        "SELECT DISTINCT language FROM repositories WHERE language IS NOT NULL AND language != '' ORDER BY language", conn
    )["language"].tolist()
    tags = pd.read_sql_query(
        "SELECT tag, COUNT(*) as c FROM repository_tags GROUP BY tag ORDER BY c DESC", conn
    )["tag"].tolist()
    orgs = pd.read_sql_query(
        "SELECT owner, COUNT(*) as c FROM repositories GROUP BY owner ORDER BY c DESC LIMIT 300", conn
    )["owner"].tolist()
    conn.close()
    return countries, languages, tags, orgs


# ==================== SIDEBAR FILTERS ====================
st.sidebar.title("🔭 Filters")
st.sidebar.caption("Applied across all tabs")

countries, languages, tags, orgs = load_filter_options()

sel_countries = st.sidebar.multiselect("🌍 Country", countries, key="g_country")
sel_orgs = st.sidebar.multiselect("🏢 Organisation", orgs, key="g_org")
sel_languages = st.sidebar.multiselect("💻 Language", languages, key="g_lang")
sel_tags = st.sidebar.multiselect("🏷️ Tag", tags, key="g_tag")

st.sidebar.divider()
st.sidebar.subheader("📅 Activity")
activity_options = {
    "All time": None,
    "Active last 3 months": 90,
    "Active last 6 months": 180,
    "Active last 12 months": 365,
    "Active last 2 years": 730,
}
activity_label = st.sidebar.selectbox("Last pushed", list(activity_options.keys()), index=0, key="g_activity")
activity_days = activity_options[activity_label]

st.sidebar.divider()
show_archived = st.sidebar.checkbox("Include archived", value=False, key="g_arch")
show_forks = st.sidebar.checkbox("Include forks", value=True, key="g_forks")
min_stars = st.sidebar.slider("Min stars", 0, 500, 0, key="g_stars")

st.sidebar.divider()
st.sidebar.markdown(
    "🔗 [GitHub Repo](https://github.com/AndreasThinks/open-govtech-report) &nbsp;|&nbsp; "
    "[Dataset on HF](https://huggingface.co/datasets/AndreasThinks/government-github-repos)",
    unsafe_allow_html=True,
)


def build_where(extra_conditions=None, base_table="r", tag_table="rt"):
    """Build a WHERE clause and params list from global sidebar filters."""
    conditions = list(extra_conditions or [])
    params = []

    if sel_countries:
        ph = ",".join(["?"] * len(sel_countries))
        conditions.append(f"{base_table}.country IN ({ph})")
        params.extend(sel_countries)

    if sel_orgs:
        ph = ",".join(["?"] * len(sel_orgs))
        conditions.append(f"{base_table}.owner IN ({ph})")
        params.extend(sel_orgs)

    if sel_languages:
        ph = ",".join(["?"] * len(sel_languages))
        conditions.append(f"{base_table}.language IN ({ph})")
        params.extend(sel_languages)

    if activity_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=activity_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conditions.append(f"{base_table}.pushed_at >= ?")
        params.append(cutoff)

    if not show_archived:
        conditions.append(f"({base_table}.archived = 0 OR {base_table}.archived IS NULL)")

    if not show_forks:
        conditions.append(f"({base_table}.fork = 0 OR {base_table}.fork IS NULL)")

    if min_stars > 0:
        conditions.append(f"{base_table}.stars >= ?")
        params.append(min_stars)

    return conditions, params


def build_tag_join_where(extra_conditions=None):
    """Build WHERE for queries that need to join repository_tags for tag filter."""
    conditions, params = build_where(extra_conditions)
    tag_join = ""
    if sel_tags:
        tag_join = "JOIN repository_tags rt ON r.html_url = rt.html_url"
        ph = ",".join(["?"] * len(sel_tags))
        conditions.append(f"rt.tag IN ({ph})")
        params.extend(sel_tags)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params, tag_join


# ==================== HEADER ====================
st.title("🏛️ GovTech GitHub Explorer")
st.caption("Exploring 70k+ government GitHub repositories worldwide")

# ==================== TABS ====================
tab_overview, tab_explorer, tab_tags, tab_insights, tab_trends, tab_about = st.tabs(
    ["📊 Overview", "🔍 Explorer", "🏷️ Tags", "💡 Insights", "📈 Trends", "ℹ️ About"]
)


# ==================== OVERVIEW ====================
with tab_overview:
    where, params, tag_join = build_tag_join_where()

    total_filtered = query_one(f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r {tag_join} {where}", params)
    account_count = query_one("SELECT COUNT(*) FROM accounts")
    country_count_val = query_one(
        f"SELECT COUNT(DISTINCT r.country) FROM repositories r {tag_join} {where}", params
    )

    # Active in last 12m within filtered set
    active_cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    active_conditions, active_params = build_where([f"r.pushed_at >= ?"])
    active_params_full = active_params.copy()
    active_params_full.insert(
        len(active_params) - 1 if active_params else 0, active_cutoff
    )
    # Simpler: just count directly
    conn = get_conn()
    conds_12m, p_12m = build_where()
    conds_12m.append("r.pushed_at >= ?")
    p_12m.append(active_cutoff)
    tj2 = "JOIN repository_tags rt ON r.html_url = rt.html_url" if sel_tags else ""
    if sel_tags:
        ph = ",".join(["?"] * len(sel_tags))
        conds_12m.append(f"rt.tag IN ({ph})")
        p_12m.extend(sel_tags)
    w12 = ("WHERE " + " AND ".join(conds_12m)) if conds_12m else ""
    active_12m = conn.execute(
        f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r {tj2} {w12}", p_12m
    ).fetchone()[0]
    conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Repositories", f"{total_filtered:,}")
    c2.metric("Accounts", f"{account_count:,}")
    c3.metric("Countries", country_count_val)
    c4.metric("Active last 12m", f"{active_12m:,}", help="Repos with a push in the last 12 months")

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Top Countries by Repositories")
        df_countries = query_df(
            f"SELECT r.country, COUNT(DISTINCT r.html_url) as count FROM repositories r {tag_join} {where} GROUP BY r.country ORDER BY count DESC LIMIT 20",
            params,
        )
        if not df_countries.empty:
            fig = px.bar(df_countries, x="country", y="count", color="count", color_continuous_scale="Blues")
            fig.update_layout(showlegend=False, xaxis_title="Country", yaxis_title="Repositories", coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.subheader("Top Languages")
        df_langs = query_df(
            f"""SELECT r.language, COUNT(DISTINCT r.html_url) as count
                FROM repositories r {tag_join} {where}
                {"AND" if where else "WHERE"} r.language IS NOT NULL AND r.language != ''
                GROUP BY r.language ORDER BY count DESC LIMIT 15""",
            params,
        )
        if not df_langs.empty:
            fig = px.bar(df_langs, x="count", y="language", orientation="h", color="count", color_continuous_scale="Greens")
            fig.update_layout(showlegend=False, yaxis=dict(autorange="reversed"), xaxis_title="Repositories", yaxis_title="", coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Repository Creation Timeline")
    df_timeline = query_df(
        f"""SELECT SUBSTR(r.created_at, 1, 4) as year, COUNT(DISTINCT r.html_url) as count
            FROM repositories r {tag_join} {where}
            {"AND" if where else "WHERE"} r.created_at IS NOT NULL
            GROUP BY year ORDER BY year""",
        params,
    )
    df_timeline = df_timeline[df_timeline["year"].str.match(r"^\d{4}$", na=False)]

    # Also pull active repos per year (pushed_at within 12m of each year-end — proxy: pushed in that year or later)
    df_pushed = query_df(
        f"""SELECT SUBSTR(r.pushed_at, 1, 4) as year, COUNT(DISTINCT r.html_url) as active
            FROM repositories r {tag_join} {where}
            {"AND" if where else "WHERE"} r.pushed_at IS NOT NULL
            GROUP BY year ORDER BY year""",
        params,
    )
    df_pushed = df_pushed[df_pushed["year"].str.match(r"^\d{4}$", na=False)]

    if not df_timeline.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_timeline["year"], y=df_timeline["count"],
            name="Created", fill="tozeroy", mode="lines",
            line=dict(color="#3b82f6"), fillcolor="rgba(59,130,246,0.2)"
        ))
        if not df_pushed.empty:
            fig.add_trace(go.Scatter(
                x=df_pushed["year"], y=df_pushed["active"],
                name="Last pushed", fill="tozeroy", mode="lines",
                line=dict(color="#10b981"), fillcolor="rgba(16,185,129,0.15)"
            ))
        fig.update_layout(xaxis_title="Year", yaxis_title="Repositories", legend=dict(orientation="h"))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("'Last pushed' shows when repositories last received a commit — a proxy for active maintenance.")


# ==================== EXPLORER ====================
with tab_explorer:
    where_e, params_e, tag_join_e = build_tag_join_where()

    # Extra local search
    search_text = st.text_input("Search name / description", key="exp_search")
    if search_text:
        where_e_conds, _ = build_where()
        where_e_conds.append("(r.name LIKE ? OR r.description LIKE ?)")
        params_e_local = params_e + [f"%{search_text}%", f"%{search_text}%"]
        where_e_local = ("WHERE " + " AND ".join(where_e_conds + (["(r.name LIKE ? OR r.description LIKE ?)"] if search_text else []))) if where_e_conds else ""
    else:
        params_e_local = params_e

    sort_col = st.selectbox("Sort by", ["stars", "forks", "pushed_at", "created_at"], key="exp_sort")

    conn = get_conn()
    count_sql = f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r {tag_join_e} {where_e}"
    if search_text:
        extra = " AND (r.name LIKE ? OR r.description LIKE ?)"
        total_results = conn.execute(count_sql + extra, params_e + [f"%{search_text}%", f"%{search_text}%"]).fetchone()[0]
    else:
        total_results = conn.execute(count_sql, params_e).fetchone()[0]
    conn.close()

    st.write(f"**{total_results:,}** repositories match current filters")

    page_size = 50
    total_pages = max(1, (total_results + page_size - 1) // page_size)
    page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, key="exp_page")
    offset = (page - 1) * page_size

    search_clause = " AND (r.name LIKE ? OR r.description LIKE ?)" if search_text else ""
    search_params = [f"%{search_text}%", f"%{search_text}%"] if search_text else []

    data_sql = f"""
        SELECT r.html_url, r.name, r.owner, r.country, r.language, r.stars, r.forks,
               r.license, r.created_at, r.pushed_at, r.archived, r.fork
        FROM repositories r {tag_join_e} {where_e} {search_clause}
        GROUP BY r.html_url
        ORDER BY r.{sort_col} DESC
        LIMIT ? OFFSET ?
    """
    df_results = query_df(data_sql, params_e + search_params + [page_size, offset])

    if not df_results.empty:
        st.dataframe(
            df_results,
            column_config={
                "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
                "name": st.column_config.TextColumn("Name"),
                "owner": st.column_config.TextColumn("Owner"),
                "country": st.column_config.TextColumn("Country"),
                "language": st.column_config.TextColumn("Language"),
                "stars": st.column_config.NumberColumn("⭐ Stars"),
                "forks": st.column_config.NumberColumn("🍴 Forks"),
                "license": st.column_config.TextColumn("License"),
                "created_at": st.column_config.TextColumn("Created"),
                "pushed_at": st.column_config.TextColumn("Last pushed"),
                "archived": st.column_config.CheckboxColumn("Archived"),
                "fork": st.column_config.CheckboxColumn("Fork"),
            },
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"Page {page} of {total_pages}")
    else:
        st.info("No repositories match the current filters.")


# ==================== TAGS ====================
with tab_tags:
    where_t, params_t, tag_join_t = build_tag_join_where()

    tagged_count_t = query_one("SELECT COUNT(DISTINCT html_url) FROM repository_tags")
    total_repos_t = query_one("SELECT COUNT(*) FROM repositories")

    if tagged_count_t < total_repos_t * 0.99:
        pct = tagged_count_t / total_repos_t * 100 if total_repos_t > 0 else 0
        st.info(
            f"🏗️ **Tagging in progress** — {tagged_count_t:,} of {total_repos_t:,} repositories tagged ({pct:.1f}%). "
            "Results below reflect partially tagged data."
        )

    col_tl, col_tr = st.columns(2)

    with col_tl:
        st.subheader("Top Tags")
        df_top_tags = query_df(
            f"""SELECT rt2.tag, COUNT(DISTINCT r.html_url) as count
                FROM repositories r
                JOIN repository_tags rt2 ON r.html_url = rt2.html_url
                {tag_join_t.replace("rt", "rt_f") if sel_tags else ""}
                {where_t.replace("rt.", "rt2.") if where_t else ""}
                {"AND" if where_t else "WHERE"} rt2.tag IS NOT NULL
                GROUP BY rt2.tag ORDER BY count DESC LIMIT 30""",
            params_t,
        )
        if not df_top_tags.empty:
            fig = px.bar(
                df_top_tags, x="count", y="tag", orientation="h",
                color="count", color_continuous_scale="Purples",
            )
            fig.update_layout(yaxis=dict(autorange="reversed"), showlegend=False, height=600,
                              xaxis_title="Repositories", yaxis_title="", coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

    with col_tr:
        st.subheader("Tags by Year Created")
        st.caption("Repos tagged with each technology, by creation year — shows technology adoption over time.")

        # Pick top 10 tags for the chart
        if not df_top_tags.empty:
            top10_tags = df_top_tags.head(10)["tag"].tolist()
            ph = ",".join(["?"] * len(top10_tags))
            conds_ty, params_ty = build_where(base_table="r")
            conds_ty.append(f"rt3.tag IN ({ph})")
            params_ty.extend(top10_tags)
            conds_ty.append("r.created_at IS NOT NULL")
            w_ty = ("WHERE " + " AND ".join(conds_ty)) if conds_ty else ""
            df_tag_time = query_df(
                f"""SELECT SUBSTR(r.created_at,1,4) as year, rt3.tag, COUNT(DISTINCT r.html_url) as count
                    FROM repositories r JOIN repository_tags rt3 ON r.html_url = rt3.html_url
                    {w_ty}
                    GROUP BY year, rt3.tag ORDER BY year""",
                params_ty,
            )
            df_tag_time = df_tag_time[df_tag_time["year"].str.match(r"^\d{4}$", na=False)]
            if not df_tag_time.empty:
                fig = px.line(df_tag_time, x="year", y="count", color="tag",
                              labels={"year": "Year", "count": "Repos created", "tag": "Tag"})
                fig.update_layout(legend=dict(orientation="h", y=-0.3))
                st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Browse Repos by Tag")
    browse_tags = df_top_tags["tag"].tolist() if not df_top_tags.empty else tags
    if browse_tags:
        sel_tag = st.selectbox("Select a tag", browse_tags, key="tag_browse")
        sort_tag = st.selectbox("Sort by", ["stars", "pushed_at", "created_at"], key="tag_sort")
        conds_br, params_br = build_where(base_table="r")
        conds_br.append("rt_b.tag = ?")
        params_br.append(sel_tag)
        w_br = ("WHERE " + " AND ".join(conds_br)) if conds_br else ""
        df_tag_repos = query_df(
            f"""SELECT r.name, r.owner, r.country, r.language, r.stars, r.pushed_at, rt_b.confidence, r.html_url
               FROM repository_tags rt_b JOIN repositories r ON rt_b.html_url = r.html_url
               {w_br} ORDER BY r.{sort_tag} DESC LIMIT 200""",
            params_br,
        )
        st.write(f"**{len(df_tag_repos)}** repos tagged with **{sel_tag}**")
        if not df_tag_repos.empty:
            st.dataframe(
                df_tag_repos,
                column_config={
                    "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
                    "confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1),
                    "stars": st.column_config.NumberColumn("⭐ Stars"),
                    "pushed_at": st.column_config.TextColumn("Last pushed"),
                },
                use_container_width=True,
                hide_index=True,
            )

    st.divider()
    st.subheader("Tag Groups")
    df_groups = query_df("SELECT id, name, description FROM tag_groups ORDER BY name")
    if not df_groups.empty:
        for _, grp in df_groups.iterrows():
            with st.expander(f"📁 {grp['name']}" + (f" — {grp['description']}" if grp["description"] else "")):
                df_members = query_df(
                    "SELECT tag FROM tag_group_members WHERE group_id = ? ORDER BY tag",
                    [int(grp["id"])]
                )
                if not df_members.empty:
                    st.write(", ".join(df_members["tag"].tolist()))
                else:
                    st.write("No tags in this group yet.")
    else:
        st.info("No tag groups defined yet.")


# ==================== INSIGHTS ====================
with tab_insights:
    where_i, params_i, tag_join_i = build_tag_join_where()

    col_ia, col_ib = st.columns(2)

    with col_ia:
        st.subheader("⭐ Most Starred")
        df_top = query_df(
            f"""SELECT r.name, r.owner, r.country, r.stars, r.language, r.pushed_at, r.html_url
                FROM repositories r {tag_join_i} {where_i}
                GROUP BY r.html_url ORDER BY r.stars DESC LIMIT 25""",
            params_i,
        )
        st.dataframe(
            df_top,
            column_config={
                "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
                "stars": st.column_config.NumberColumn("⭐ Stars"),
                "pushed_at": st.column_config.TextColumn("Last pushed"),
            },
            use_container_width=True,
            hide_index=True,
        )

    with col_ib:
        st.subheader("🚀 Rising Stars (active last 12m, sorted by stars)")
        rising_cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conds_r, params_r = build_where(base_table="r")
        conds_r.append("r.pushed_at >= ?")
        params_r.append(rising_cutoff)
        tj_r = "JOIN repository_tags rt ON r.html_url = rt.html_url" if sel_tags else ""
        if sel_tags:
            ph = ",".join(["?"] * len(sel_tags))
            conds_r.append(f"rt.tag IN ({ph})")
            params_r.extend(sel_tags)
        w_r = ("WHERE " + " AND ".join(conds_r)) if conds_r else ""
        df_rising = query_df(
            f"""SELECT r.name, r.owner, r.country, r.stars, r.language, r.pushed_at, r.html_url
                FROM repositories r {tj_r} {w_r}
                GROUP BY r.html_url ORDER BY r.stars DESC LIMIT 25""",
            params_r,
        )
        st.dataframe(
            df_rising,
            column_config={
                "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
                "stars": st.column_config.NumberColumn("⭐ Stars"),
                "pushed_at": st.column_config.TextColumn("Last pushed"),
            },
            use_container_width=True,
            hide_index=True,
        )

    st.divider()

    st.subheader("📈 Most Active Organisations")
    st.caption("Organisations ranked by number of repos with a push in the last 12 months.")
    conds_ao, params_ao = build_where(base_table="r")
    conds_ao.append("r.pushed_at >= ?")
    params_ao.append((datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    tj_ao = "JOIN repository_tags rt ON r.html_url = rt.html_url" if sel_tags else ""
    if sel_tags:
        ph = ",".join(["?"] * len(sel_tags))
        conds_ao.append(f"rt.tag IN ({ph})")
        params_ao.extend(sel_tags)
    w_ao = ("WHERE " + " AND ".join(conds_ao)) if conds_ao else ""
    df_active_orgs = query_df(
        f"""SELECT r.owner, r.country, COUNT(DISTINCT r.html_url) as active_repos,
                   SUM(r.stars) as total_stars
            FROM repositories r {tj_ao} {w_ao}
            GROUP BY r.owner ORDER BY active_repos DESC LIMIT 20""",
        params_ao,
    )
    col_org1, col_org2 = st.columns(2)
    with col_org1:
        if not df_active_orgs.empty:
            fig = px.bar(df_active_orgs, x="active_repos", y="owner", orientation="h",
                         color="active_repos", color_continuous_scale="Oranges",
                         labels={"active_repos": "Active repos (12m)", "owner": ""})
            fig.update_layout(yaxis=dict(autorange="reversed"), showlegend=False,
                              height=500, coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
    with col_org2:
        if not df_active_orgs.empty:
            st.dataframe(df_active_orgs, use_container_width=True, hide_index=True,
                         column_config={"total_stars": st.column_config.NumberColumn("⭐ Total stars"),
                                        "active_repos": st.column_config.NumberColumn("Active repos (12m)")})

    st.divider()

    col_ic, col_id = st.columns(2)

    with col_ic:
        st.subheader("📜 License Breakdown")
        df_lic = query_df(
            f"""SELECT r.license, COUNT(DISTINCT r.html_url) as count
                FROM repositories r {tag_join_i} {where_i}
                {"AND" if where_i else "WHERE"} r.license IS NOT NULL AND r.license != ''
                GROUP BY r.license ORDER BY count DESC""",
            params_i,
        )
        if not df_lic.empty:
            top_n = 10
            if len(df_lic) > top_n:
                top = df_lic.head(top_n)
                other = pd.DataFrame([{"license": "Other", "count": df_lic.iloc[top_n:]["count"].sum()}])
                df_lic_plot = pd.concat([top, other], ignore_index=True)
            else:
                df_lic_plot = df_lic
            fig = px.pie(df_lic_plot, names="license", values="count", hole=0.3)
            fig.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig, use_container_width=True)

    with col_id:
        st.subheader("🍴 Fork vs Original")
        fork_count = query_one(
            f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r {tag_join_i} {where_i} {'AND' if where_i else 'WHERE'} r.fork = 1",
            params_i,
        )
        original_count = query_one(
            f"SELECT COUNT(DISTINCT r.html_url) FROM repositories r {tag_join_i} {where_i} {'AND' if where_i else 'WHERE'} (r.fork = 0 OR r.fork IS NULL)",
            params_i,
        )
        m1, m2 = st.columns(2)
        m1.metric("Original", f"{original_count:,}")
        m2.metric("Forked", f"{fork_count:,}")
        fig = px.pie(
            pd.DataFrame({"type": ["Original", "Fork"], "count": [original_count, fork_count]}),
            names="type", values="count", hole=0.4,
            color_discrete_sequence=["#2ecc71", "#e74c3c"],
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("🌍 Language × Country Heatmap")
    df_heat = query_df(
        f"""SELECT r.country, r.language, COUNT(DISTINCT r.html_url) as count
            FROM repositories r {tag_join_i} {where_i}
            {"AND" if where_i else "WHERE"} r.language IS NOT NULL AND r.language != ''
              AND r.country IN (
                SELECT country FROM repositories GROUP BY country ORDER BY COUNT(*) DESC LIMIT 15
              )
              AND r.language IN (
                SELECT language FROM repositories WHERE language IS NOT NULL AND language != ''
                GROUP BY language ORDER BY COUNT(*) DESC LIMIT 12
              )
            GROUP BY r.country, r.language""",
        params_i,
    )
    if not df_heat.empty:
        pivot = df_heat.pivot_table(index="country", columns="language", values="count", fill_value=0)
        fig = px.imshow(pivot, text_auto=True, color_continuous_scale="YlOrRd",
                        labels=dict(x="Language", y="Country", color="Repos"), aspect="auto")
        fig.update_layout(height=500)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough data for heatmap.")


# ==================== TRENDS ====================
with tab_trends:
    st.subheader("📈 Trends Over Time")
    st.caption("How government open source has evolved — new activity, rising tags, and shifting languages.")

    # ---- 1. New repos per month (last 24 months) ----
    st.markdown("### 🗓️ New Repositories per Month")
    st.caption("Monthly repo creation over the last 2 years.")

    cutoff_24m = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conds_m, params_m = build_where(base_table="r")
    conds_m.append("r.created_at >= ?")
    params_m.append(cutoff_24m)
    w_m = ("WHERE " + " AND ".join(conds_m)) if conds_m else ""
    tj_m = "JOIN repository_tags rt ON r.html_url = rt.html_url" if sel_tags else ""
    if sel_tags:
        ph = ",".join(["?"] * len(sel_tags))
        conds_m.append(f"rt.tag IN ({ph})")
        params_m.extend(sel_tags)
        w_m = ("WHERE " + " AND ".join(conds_m)) if conds_m else ""

    df_monthly = query_df(
        f"""SELECT SUBSTR(r.created_at, 1, 7) as month, COUNT(DISTINCT r.html_url) as new_repos
            FROM repositories r {tj_m} {w_m}
            GROUP BY month ORDER BY month""",
        params_m,
    )
    df_monthly = df_monthly[df_monthly["month"].str.match(r"^\d{{4}}-\d{{2}}$", na=False)]
    if not df_monthly.empty:
        fig = px.bar(
            df_monthly, x="month", y="new_repos",
            labels={"month": "Month", "new_repos": "New repositories"},
            color="new_repos", color_continuous_scale="Blues",
        )
        fig.update_layout(coloraxis_showscale=False, xaxis_title="Month", yaxis_title="New repos")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough data for monthly chart.")

    st.divider()

    # ---- 2. Tag momentum ----
    st.markdown("### 🚀 Tag Momentum")
    st.caption(
        "Tags ranked by growth — repos tagged with each label in the last 12 months vs the previous 12 months. "
        "Higher ratio = faster-growing category."
    )

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_12m = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_24m_str = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%SZ")

    conds_base, params_base = build_where(base_table="r")
    w_base = (" AND ".join(conds_base)) if conds_base else ""
    and_base = ("AND " + w_base) if w_base else ""

    df_momentum = query_df(
        f"""
        SELECT
            rt.tag,
            COUNT(DISTINCT CASE WHEN r.created_at >= ? THEN r.html_url END) as recent,
            COUNT(DISTINCT CASE WHEN r.created_at >= ? AND r.created_at < ? THEN r.html_url END) as prior
        FROM repository_tags rt
        JOIN repositories r ON rt.html_url = r.html_url
        WHERE r.created_at IS NOT NULL {and_base}
        GROUP BY rt.tag
        HAVING recent >= 5 AND prior >= 5
        ORDER BY (CAST(recent AS FLOAT) / prior) DESC
        LIMIT 30
        """,
        [cutoff_12m, cutoff_24m_str, cutoff_12m] + params_base,
    )

    if not df_momentum.empty:
        df_momentum["growth_ratio"] = (df_momentum["recent"] / df_momentum["prior"]).round(2)
        df_momentum["growth_pct"] = ((df_momentum["growth_ratio"] - 1) * 100).round(1)

        col_m1, col_m2 = st.columns(2)

        with col_m1:
            st.markdown("**Fastest growing tags** (last 12m vs prior 12m)")
            fig = px.bar(
                df_momentum.head(20), x="growth_ratio", y="tag", orientation="h",
                color="growth_ratio", color_continuous_scale="Greens",
                labels={"growth_ratio": "Growth ratio (recent / prior)", "tag": "Tag"},
                hover_data={"recent": True, "prior": True, "growth_pct": True},
            )
            fig.update_layout(
                yaxis=dict(autorange="reversed"), height=550,
                coloraxis_showscale=False, xaxis_title="Growth ratio", yaxis_title="",
            )
            fig.add_vline(x=1.0, line_dash="dash", line_color="grey", annotation_text="no change")
            st.plotly_chart(fig, use_container_width=True)

        with col_m2:
            st.markdown("**Top 20 by absolute recent volume**")
            df_recent_top = query_df(
                f"""
                SELECT rt.tag, COUNT(DISTINCT r.html_url) as recent_count
                FROM repository_tags rt JOIN repositories r ON rt.html_url = r.html_url
                WHERE r.created_at >= ? {and_base}
                GROUP BY rt.tag ORDER BY recent_count DESC LIMIT 20
                """,
                [cutoff_12m] + params_base,
            )
            if not df_recent_top.empty:
                fig2 = px.bar(
                    df_recent_top, x="recent_count", y="tag", orientation="h",
                    color="recent_count", color_continuous_scale="Purples",
                    labels={"recent_count": "Repos (last 12m)", "tag": "Tag"},
                )
                fig2.update_layout(
                    yaxis=dict(autorange="reversed"), height=550,
                    coloraxis_showscale=False, xaxis_title="Repos (last 12m)", yaxis_title="",
                )
                st.plotly_chart(fig2, use_container_width=True)

        # Emerging tags table
        st.markdown("**Emerging tags** — ratio > 1.5, sorted by growth")
        df_emerging = df_momentum[df_momentum["growth_ratio"] >= 1.5][
            ["tag", "recent", "prior", "growth_ratio", "growth_pct"]
        ].rename(columns={
            "tag": "Tag", "recent": "Last 12m", "prior": "Prior 12m",
            "growth_ratio": "Ratio", "growth_pct": "Growth %"
        })
        if not df_emerging.empty:
            st.dataframe(df_emerging, use_container_width=True, hide_index=True)
        else:
            st.info("No tags with >50% growth in this period.")
    else:
        st.info("Not enough tagged data yet to compute momentum.")

    st.divider()

    # ---- 3. Language trends ----
    st.markdown("### 💻 Language Trends")
    st.caption("Year-over-year share of new repositories by primary language — top 10 languages.")

    df_lang_year = query_df(
        f"""
        SELECT SUBSTR(r.created_at, 1, 4) as year, r.language,
               COUNT(DISTINCT r.html_url) as count
        FROM repositories r
        WHERE r.language IS NOT NULL AND r.language != ''
          AND r.created_at IS NOT NULL
          AND r.language IN (
              SELECT language FROM repositories
              WHERE language IS NOT NULL AND language != ''
              GROUP BY language ORDER BY COUNT(*) DESC LIMIT 10
          )
          AND SUBSTR(r.created_at, 1, 4) BETWEEN '2015' AND SUBSTR(?, 1, 4)
        GROUP BY year, r.language ORDER BY year
        """,
        [now_str],
    )
    df_lang_year = df_lang_year[df_lang_year["year"].str.match(r"^\d{4}$", na=False)]

    if not df_lang_year.empty:
        # Absolute count line chart
        fig_lang = px.line(
            df_lang_year, x="year", y="count", color="language",
            labels={"year": "Year", "count": "New repositories", "language": "Language"},
            markers=True,
        )
        fig_lang.update_layout(legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig_lang, use_container_width=True)

        # Share / normalised stacked area
        st.caption("As a share of all new repos that year (top 10 languages).")
        df_totals = df_lang_year.groupby("year")["count"].sum().reset_index().rename(columns={"count": "total"})
        df_share = df_lang_year.merge(df_totals, on="year")
        df_share["share"] = (df_share["count"] / df_share["total"] * 100).round(1)

        fig_share = px.area(
            df_share, x="year", y="share", color="language",
            labels={"year": "Year", "share": "Share of new repos (%)", "language": "Language"},
            groupnorm="",
        )
        fig_share.update_layout(legend=dict(orientation="h", y=-0.25), yaxis_title="Share (%)")
        st.plotly_chart(fig_share, use_container_width=True)
    else:
        st.info("Not enough data for language trends.")


# ==================== ABOUT ====================
with tab_about:
    st.subheader("About GovTech GitHub Explorer")
    st.write(
        """
        **GovTech GitHub Explorer** maps the global landscape of government open source software.
        It discovers, scrapes, and automatically categorises every public GitHub repository
        belonging to government organisations worldwide — updated weekly.
        """
    )

    st.subheader("How it works")
    col_a1, col_a2, col_a3, col_a4 = st.columns(4)
    with col_a1:
        st.markdown("### 🔍 Discover")
        st.write("Government GitHub accounts are sourced from the [government.github.com](https://github.com/github/government.github.com) registry — ~2,000 organisations across 100+ countries.")
    with col_a2:
        st.markdown("### 🕷️ Scrape")
        st.write("Repository metadata is collected via the GitHub API using a GitHub App installation, giving high-throughput authenticated access.")
    with col_a3:
        st.markdown("### 🏷️ Tag")
        st.write("An LLM pipeline (Qwen3-32B via OpenRouter) reads each repository's metadata and README, then assigns structured tags and categories.")
    with col_a4:
        st.markdown("### 📊 Explore")
        st.write("Tags are clustered into groups using embedding similarity, and the full dataset is published to HuggingFace for anyone to use.")

    st.divider()

    st.subheader("Data")
    col_d1, col_d2, col_d3 = st.columns(3)
    total_a = query_one("SELECT COUNT(*) FROM repositories")
    tagged_a = query_one("SELECT COUNT(DISTINCT html_url) FROM repository_tags")
    tag_count_a = query_one("SELECT COUNT(*) FROM tags")
    col_d1.metric("Repositories", f"{total_a:,}")
    col_d2.metric("Tagged", f"{tagged_a:,}")
    col_d3.metric("Unique tags", f"{tag_count_a:,}")

    st.write(
        "The full dataset — including repo metadata, tags, and tag groups — is available on "
        "[HuggingFace](https://huggingface.co/datasets/AndreasThinks/government-github-repos) "
        "in CSV, Parquet, and SQLite formats. Updated every Sunday."
    )

    st.divider()

    st.subheader("Source")
    st.write(
        "The scraper, tagger, and dashboard are all open source. "
        "Pull requests and issues welcome."
    )
    st.markdown("[github.com/AndreasThinks/open-govtech-report](https://github.com/AndreasThinks/open-govtech-report)")

    st.divider()
    st.markdown(
        "✨ A project by [AndreasThinks](https://andreasthinks.me), built with ❤️ using Streamlit, "
        "and some ✨vibes✨",
        unsafe_allow_html=True,
    )


st.divider()
st.caption(
    "Data sourced from government GitHub accounts worldwide. Updated weekly. "
    "| [GitHub](https://github.com/AndreasThinks/open-govtech-report) "
    "| [Dataset](https://huggingface.co/datasets/AndreasThinks/government-github-repos) "
    "| ✨ A project by [AndreasThinks](https://andreasthinks.me)"
)
