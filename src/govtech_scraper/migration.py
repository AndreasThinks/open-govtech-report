"""Offline idempotent repair. Keeps original labels in an audit table; no paid calls."""
import re

LANGUAGES = frozenset('javascript python java typescript html css php ruby shell r scala c# kotlin go rust c c++ perl swift matlab bash json xml yaml sql makefile scss'.split())


def normalize_label(label):
    return re.sub(r'\s+', '-', label.strip().lower())


def tag_kind(label):
    return 'language' if normalize_label(label) in LANGUAGES else 'technology'


def migrate(db):
    with db.conn:
        for row in db.conn.execute("SELECT * FROM repository_tags WHERE source='tech-stack'").fetchall():
            label = normalize_label(row['tag'])
            # Empty historical labels are preserved for manual review, not deleted.
            if not label:
                label = row['tag']
            if label != row['tag']:
                db.conn.execute('INSERT OR IGNORE INTO tag_label_history VALUES (?,?,?,?,?)',
                                (row['html_url'], row['tag'], label, row['source'], row['confidence']))
                db.conn.execute('INSERT OR IGNORE INTO repository_tags VALUES (?,?,?,?)',
                                (row['html_url'], label, row['confidence'], row['source']))
                db.conn.execute('DELETE FROM repository_tags WHERE html_url=? AND tag=?', (row['html_url'], row['tag']))
        for row in db.conn.execute('SELECT tag, MIN(source), MAX(source) FROM repository_tags GROUP BY tag').fetchall():
            label, first_source, last_source = row
            has_llm = db.conn.execute("SELECT 1 FROM repository_tags WHERE tag=? AND source='llm' LIMIT 1", (label,)).fetchone()
            kind = 'concept' if has_llm else tag_kind(label)
            db.conn.execute('INSERT INTO tags(tag, kind) VALUES (?,?) ON CONFLICT(tag) DO UPDATE SET kind=excluded.kind', (label, kind))
        db.conn.execute('UPDATE tags SET usage_count=(SELECT COUNT(*) FROM repository_tags rt WHERE rt.tag=tags.tag)')
        # Legacy zero was never measured by this scraper; leave historical snapshots verbatim.
        db.conn.execute('UPDATE repositories SET commit_count=NULL WHERE commit_count=0')
