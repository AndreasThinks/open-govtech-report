from fasthtml import FastHTML, serve
from datetime import datetime, timedelta
import pandas as pd
import sys
import os

# Add parent directory to path to import db_operations
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_operations import DatabaseManager

app = FastHTML()
db = DatabaseManager()

def create_error_page(message):
    return f"""
    <article>
        <header>
            <h1>Data Not Available</h1>
        </header>
        <p class="error">{message}</p>
        <footer>
            <a href="/" role="button">Return to Dashboard</a>
        </footer>
    </article>
    """

def create_stats_card(title, value):
    return f"""
    <article>
        <header>
            <h3>{title}</h3>
        </header>
        <p class="stat-value">{value}</p>
    </article>
    """

def create_top_list(title, items):
    items_html = "\n".join([
        f"""
        <tr>
            <td>{name}</td>
            <td>{count}</td>
        </tr>
        """ for name, count in items.items()
    ])
    
    return f"""
    <article>
        <header>
            <h2>{title}</h2>
        </header>
        <table>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Count</th>
                </tr>
            </thead>
            <tbody>
                {items_html}
            </tbody>
        </table>
    </article>
    """

def create_repo_table(repos):
    rows = "\n".join([
        f"""
        <tr>
            <td><a href="{repo['html_url']}" target="_blank">{repo['name']}</a></td>
            <td>{repo['username']}</td>
            <td>{repo['stars']}</td>
            <td>{repo['forks']}</td>
            <td>{repo['language'] or 'N/A'}</td>
        </tr>
        """ for repo in repos
    ])
    
    return f"""
    <article>
        <header>
            <h2>Top Repositories</h2>
        </header>
        <table>
            <thead>
                <tr>
                    <th>Repository</th>
                    <th>Owner</th>
                    <th>Stars</th>
                    <th>Forks</th>
                    <th>Language</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </article>
    """

def create_dashboard(stats, top_languages, top_repos, top_countries, top_topics, current_days):
    days_options = "\n".join([
        f"""
        <option value="{d}" {"selected" if d == current_days else ""}>
            {f"{d} days" if d < 365 else "1 year"}
        </option>
        """ for d in [7, 30, 90, 180, 365]
    ])

    stats_cards = "\n".join([
        create_stats_card("Total Repositories", stats['total_repos']),
        create_stats_card("Total Stars", stats['total_stars']),
        create_stats_card("Total Forks", stats['total_forks']),
        create_stats_card("Average Stars", stats['avg_stars']),
        create_stats_card("Average Forks", stats['avg_forks'])
    ])

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Open GovTech Report - Statistics</title>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@1/css/pico.min.css">
    </head>
    <body>
        <main class="container">
            <header>
                <h1>Open GovTech Report</h1>
                <form>
                    <label for="days">Show data for last:</label>
                    <select name="days" id="days" onchange="this.form.submit()">
                        {days_options}
                    </select>
                </form>
            </header>

            <section class="grid">
                {stats_cards}
            </section>

            {create_repo_table(top_repos)}

            <div class="grid">
                {create_top_list("Top Languages", top_languages)}
                {create_top_list("Most Active Countries", top_countries)}
                {create_top_list("Top Topics", top_topics)}
            </div>

            <footer>
                <small>Data updated as of {stats['latest_update']}</small>
            </footer>
        </main>
    </body>
    </html>
    """

@app.route('/')
def index(request):
    try:
        days = int(dict(request.query_params).get('days', ['30'])[0])
        if days <= 0:
            raise ValueError("Days must be positive")
    except ValueError:
        return create_error_page("Invalid date range specified")
    
    df = db.get_latest_snapshot()
    cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
    
    df['scrape_timestamp'] = pd.to_datetime(df['scrape_timestamp'])
    filtered_df = df[df['scrape_timestamp'] >= cutoff_date]
    
    if filtered_df.empty:
        return create_error_page(f"No data available for the last {days} days")
    
    stats = {
        'total_repos': len(filtered_df),
        'total_stars': filtered_df['stars'].sum(),
        'total_forks': filtered_df['forks'].sum(),
        'avg_stars': round(filtered_df['stars'].mean(), 2),
        'avg_forks': round(filtered_df['forks'].mean(), 2)
    }
    
    top_languages = (filtered_df['language']
                    .value_counts()
                    .head(10)
                    .to_dict())
    
    top_repos = (filtered_df.nlargest(10, 'stars')
                [['name', 'username', 'stars', 'forks', 'language', 'html_url']]
                .to_dict('records'))
    
    # Get top topics
    def extract_topics(topics_str):
        try:
            if pd.isna(topics_str) or topics_str in ('[]', 'NaN', '"NaN"'):
                return []
            return eval(topics_str)  # Safe since we know it's a JSON array from our database
        except:
            return []
    
    all_topics = []
    for topics in filtered_df['topics']:
        all_topics.extend(extract_topics(topics))
    
    top_topics = (pd.Series(all_topics)
                 .value_counts()
                 .head(10)
                 .to_dict())
    
    top_countries = (filtered_df['country']
                    .value_counts()
                    .head(10)
                    .to_dict())
    
    latest_timestamp = filtered_df['scrape_timestamp'].max()
    stats['latest_update'] = latest_timestamp.strftime('%Y-%m-%d %H:%M UTC')

    return create_dashboard(stats, top_languages, top_repos, top_countries, top_topics, days)

if __name__ == '__main__':
    serve()
