import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sqlite3
import os
import json

st.set_page_config(
    page_title="GovTech GitHub Explorer",
    page_icon="🏛️",
    layout="wide",
)

st.title("🏛️ GovTech GitHub Explorer")
st.caption("Exploring 70k+ government GitHub repositories worldwide")


def get_db_path():
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "govtech.db"),
        os.path.join(os.path.dirname(__file__), "govtech.db"),
        "govtech.db",
        "../govtech.db",
    ]
    for p in candidates:
        if os.path.exists(p):
            return os.path.abspath(p)
    # Download from HuggingFace
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(
            repo_id="AndreasThinks/government-github-repos",
            filename="data/govtech.db",
            repo_type="dataset",
        )
        return path
    except Exception as e:
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
def query_one(sql):
    conn = get_conn()
    cur = conn.cursor()
    result = cur.execute(sql).fetchone()[0]
    conn.close()
    return result


# --- TABS ---
tab_overview, tab_explorer, tab_tags, tab_insights = st.tabs(
    ["📊 Overview", "🔍 Explorer", "🏷️ Tags", "💡 Insights"]
)

# ==================== OVERVIEW ====================
with tab_overview:
    repo_count = query_one("SELECT COUNT(*) FROM repositories")
    account_count = query_one("SELECT COUNT(*) FROM accounts")
    country_count = query_one("SELECT COUNT(DISTINCT country) FROM repositories")
    tagged_count = query_one("SELECT COUNT(DISTINCT html_url) FROM repository_tags")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Repositories", f"{repo_count:,}")
    c2.metric("Accounts", f"{account_count:,}")
    c3.metric("Countries", country_count)
    c4.metric("Tagged Repos", f"{tagged_count:,}")

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Top 20 Countries by Repository Count")
        df_countries = query_df(
            "SELECT country, COUNT(*) as count FROM repositories GROUP BY country ORDER BY count DESC LIMIT 20"
        )
        fig = px.bar(
            df_countries, x="country", y="count",
            color="count", color_continuous_scale="Blues",
        )
        fig.update_layout(showlegend=False, xaxis_title="Country", yaxis_title="Repositories")
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.subheader("Top 15 Languages")
        df_langs = query_df(
            "SELECT language, COUNT(*) as count FROM repositories WHERE language IS NOT NULL AND language != '' GROUP BY language ORDER BY count DESC LIMIT 15"
        )
        fig = px.bar(
            df_langs, x="count", y="language", orientation="h",
            color="count", color_continuous_scale="Greens",
        )
        fig.update_layout(showlegend=False, yaxis=dict(autorange="reversed"), xaxis_title="Repositories", yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Repository Creation Timeline")
    df_timeline = query_df(
        "SELECT SUBSTR(created_at, 1, 4) as year, COUNT(*) as count FROM repositories WHERE created_at IS NOT NULL GROUP BY year ORDER BY year"
    )
    df_timeline = df_timeline[df_timeline["year"].str.match(r"^\d{4}$", na=False)]
    fig = px.area(
        df_timeline, x="year", y="count",
        labels={"year": "Year", "count": "Repos Created"},
    )
    fig.update_layout(xaxis_title="Year", yaxis_title="Repositories Created")
    st.plotly_chart(fig, use_container_width=True)


# ==================== EXPLORER ====================
with tab_explorer:
    with st.expander("🔧 Filters", expanded=True):
        fc1, fc2, fc3 = st.columns(3)

        all_countries = query_df(
            "SELECT DISTINCT country FROM repositories ORDER BY country"
        )["country"].tolist()
        all_languages = query_df(
            "SELECT DISTINCT language FROM repositories WHERE language IS NOT NULL AND language != '' ORDER BY language"
        )["language"].tolist()

        with fc1:
            sel_countries = st.multiselect("Country", all_countries, key="exp_country")
            sel_languages = st.multiselect("Language", all_languages, key="exp_lang")

        with fc2:
            min_stars = st.slider("Minimum Stars", 0, 1000, 0, key="exp_stars")
            search_text = st.text_input("Search name/description", key="exp_search")

        with fc3:
            show_archived = st.checkbox("Include archived", value=True, key="exp_arch")
            show_forks = st.checkbox("Include forks", value=True, key="exp_forks")

    # Build query
    conditions = []
    params = []

    if sel_countries:
        placeholders = ",".join(["?"] * len(sel_countries))
        conditions.append(f"country IN ({placeholders})")
        params.extend(sel_countries)

    if sel_languages:
        placeholders = ",".join(["?"] * len(sel_languages))
        conditions.append(f"language IN ({placeholders})")
        params.extend(sel_languages)

    if min_stars > 0:
        conditions.append("stars >= ?")
        params.append(min_stars)

    if not show_archived:
        conditions.append("(archived = 0 OR archived IS NULL)")

    if not show_forks:
        conditions.append("(fork = 0 OR fork IS NULL)")

    if search_text:
        conditions.append("(name LIKE ? OR description LIKE ?)")
        params.extend([f"%{search_text}%", f"%{search_text}%"])

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    count_sql = f"SELECT COUNT(*) FROM repositories {where}"
    conn = get_conn()
    total_results = pd.read_sql_query(count_sql, conn, params=params or []).iloc[0, 0]
    conn.close()

    st.write(f"**{total_results:,}** repositories found")

    page_size = 50
    total_pages = max(1, (total_results + page_size - 1) // page_size)
    page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, key="exp_page")
    offset = (page - 1) * page_size

    data_sql = f"""
        SELECT html_url, name, owner, country, language, stars, forks, license, created_at
        FROM repositories {where}
        ORDER BY stars DESC
        LIMIT ? OFFSET ?
    """
    params_page = params + [page_size, offset]

    df_results = query_df(data_sql, params_page)

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
            },
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"Page {page} of {total_pages}")
    else:
        st.info("No repositories match the current filters.")


# ==================== TAGS ====================
with tab_tags:
    tagged_count_t = query_one("SELECT COUNT(DISTINCT html_url) FROM repository_tags")
    total_repos_t = query_one("SELECT COUNT(*) FROM repositories")

    if tagged_count_t < total_repos_t * 0.5:
        pct = tagged_count_t / total_repos_t * 100 if total_repos_t > 0 else 0
        st.info(
            f"🏗️ **Tagging in progress** — {tagged_count_t:,} of {total_repos_t:,} repositories tagged ({pct:.1f}%). "
            f"Results below reflect partially tagged data."
        )

    st.subheader("Top 30 Tags")
    df_top_tags = query_df(
        "SELECT tag, COUNT(*) as count FROM repository_tags GROUP BY tag ORDER BY count DESC LIMIT 30"
    )
    if not df_top_tags.empty:
        fig = px.bar(
            df_top_tags, x="count", y="tag", orientation="h",
            color="count", color_continuous_scale="Purples",
        )
        fig.update_layout(yaxis=dict(autorange="reversed"), showlegend=False, height=600, xaxis_title="Repositories", yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("No tags found yet.")

    st.subheader("Tag Groups")
    df_groups = query_df("SELECT id, name, description FROM tag_groups ORDER BY name")
    if not df_groups.empty:
        for _, grp in df_groups.iterrows():
            with st.expander(f"📁 {grp['name']}" + (f" — {grp['description']}" if grp['description'] else "")):
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

    st.subheader("Browse Repos by Tag")
    all_tags = query_df("SELECT DISTINCT tag FROM repository_tags ORDER BY tag")["tag"].tolist()
    if all_tags:
        sel_tag = st.selectbox("Select a tag", all_tags, key="tag_browse")
        df_tag_repos = query_df(
            """SELECT r.name, r.owner, r.country, r.language, r.stars, rt.confidence, r.html_url
               FROM repository_tags rt JOIN repositories r ON rt.html_url = r.html_url
               WHERE rt.tag = ? ORDER BY r.stars DESC LIMIT 100""",
            [sel_tag]
        )
        st.write(f"**{len(df_tag_repos)}** repos tagged with **{sel_tag}**")
        if not df_tag_repos.empty:
            st.dataframe(
                df_tag_repos,
                column_config={
                    "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
                    "confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1),
                },
                use_container_width=True,
                hide_index=True,
            )
    else:
        st.info("No tagged repositories available yet.")


# ==================== INSIGHTS ====================
with tab_insights:
    st.subheader("⭐ Top 50 Most Starred Repositories")
    df_top = query_df(
        "SELECT name, owner, country, stars, language, html_url FROM repositories ORDER BY stars DESC LIMIT 50"
    )
    st.dataframe(
        df_top,
        column_config={
            "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
            "stars": st.column_config.NumberColumn("⭐ Stars"),
        },
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("📜 License Breakdown")
        df_lic = query_df(
            "SELECT license, COUNT(*) as count FROM repositories WHERE license IS NOT NULL AND license != '' GROUP BY license ORDER BY count DESC"
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

    with col_b:
        st.subheader("🍴 Fork vs Original")
        fork_count = query_one("SELECT COUNT(*) FROM repositories WHERE fork = 1")
        original_count = query_one("SELECT COUNT(*) FROM repositories WHERE fork = 0 OR fork IS NULL")
        m1, m2 = st.columns(2)
        m1.metric("Original Repos", f"{original_count:,}")
        m2.metric("Forked Repos", f"{fork_count:,}")
        fig = px.pie(
            pd.DataFrame({"type": ["Original", "Fork"], "count": [original_count, fork_count]}),
            names="type", values="count", hole=0.4,
            color_discrete_sequence=["#2ecc71", "#e74c3c"]
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    st.subheader("🚀 Most Recently Active Repos (by last push)")
    df_active = query_df(
        "SELECT name, owner, country, language, stars, pushed_at, html_url FROM repositories WHERE pushed_at IS NOT NULL ORDER BY pushed_at DESC LIMIT 20"
    )
    st.dataframe(
        df_active,
        column_config={
            "html_url": st.column_config.LinkColumn("URL", display_text="Open"),
        },
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.subheader("🌍 Language × Country Heatmap")

    df_heat = query_df("""
        SELECT country, language, COUNT(*) as count
        FROM repositories
        WHERE language IS NOT NULL AND language != ''
          AND country IN (SELECT country FROM repositories GROUP BY country ORDER BY COUNT(*) DESC LIMIT 10)
          AND language IN (SELECT language FROM repositories WHERE language IS NOT NULL AND language != '' GROUP BY language ORDER BY COUNT(*) DESC LIMIT 10)
        GROUP BY country, language
    """)
    if not df_heat.empty:
        pivot = df_heat.pivot_table(index="country", columns="language", values="count", fill_value=0)
        fig = px.imshow(
            pivot, text_auto=True,
            color_continuous_scale="YlOrRd",
            labels=dict(x="Language", y="Country", color="Repos"),
            aspect="auto",
        )
        fig.update_layout(height=500)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough data for heatmap.")

st.divider()
st.caption("Data sourced from government GitHub accounts worldwide. Built with Streamlit.")
