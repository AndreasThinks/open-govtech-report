"""Tests for taxonomy operations."""

import pytest
from govtech_scraper.tagger.taxonomy import Taxonomy, _pack_embedding, _unpack_embedding


def test_pack_unpack_roundtrip():
    original = [0.1, 0.2, 0.3, -0.5, 1.0]
    packed = _pack_embedding(original)
    unpacked = _unpack_embedding(packed)
    for a, b in zip(original, unpacked):
        assert abs(a - b) < 1e-6


def test_empty_taxonomy(tmp_db):
    taxonomy = Taxonomy(tmp_db)
    assert taxonomy.size == 0
    assert taxonomy.get_top_tags() == []


def test_add_tag(tmp_db):
    taxonomy = Taxonomy(tmp_db)
    embedding = [0.1, 0.2, 0.3]
    taxonomy.add_tag("open-data", embedding=embedding)
    assert taxonomy.has_tag("open-data")
    assert taxonomy.size == 1


def test_add_tag_increments_usage(tmp_db):
    taxonomy = Taxonomy(tmp_db)
    taxonomy.add_tag("api", embedding=[0.1, 0.2])
    taxonomy.add_tag("api", embedding=[0.1, 0.2])  # again
    entry = taxonomy.get_tag("api")
    assert entry.usage_count == 2


def test_increment_usage(tmp_db):
    taxonomy = Taxonomy(tmp_db)
    taxonomy.add_tag("test-tag", embedding=[0.5, 0.5])
    taxonomy.increment_usage("test-tag")
    assert taxonomy.get_tag("test-tag").usage_count == 2


def test_find_similar(tmp_db):
    taxonomy = Taxonomy(tmp_db)
    # Add two tags with known embeddings
    taxonomy.add_tag("machine-learning", embedding=[1.0, 0.0, 0.0])
    taxonomy.add_tag("web-development", embedding=[0.0, 1.0, 0.0])
    
    # Search for something close to machine-learning
    results = taxonomy.find_similar([0.95, 0.05, 0.0], threshold=0.9)
    assert len(results) >= 1
    assert results[0][0] == "machine-learning"
    assert results[0][1] > 0.9


def test_find_similar_no_match(tmp_db):
    taxonomy = Taxonomy(tmp_db)
    taxonomy.add_tag("test", embedding=[1.0, 0.0, 0.0])
    results = taxonomy.find_similar([0.0, 1.0, 0.0], threshold=0.9)
    assert len(results) == 0


def test_merge_tag(tmp_db):
    taxonomy = Taxonomy(tmp_db)
    taxonomy.add_tag("ml", embedding=[0.1, 0.2])
    taxonomy.add_tag("machine-learning", embedding=[0.1, 0.2])
    taxonomy.merge_tag("ml", "machine-learning")
    assert not taxonomy.has_tag("ml")
    assert taxonomy.has_tag("machine-learning")
    # Usage should be combined
    assert taxonomy.get_tag("machine-learning").usage_count == 2


def test_merge_updates_repo_tags(tmp_db, sample_repo):
    tmp_db.save_repositories([sample_repo])
    tmp_db.save_repo_tag(sample_repo.html_url, "ml", 0.9, "llm")
    taxonomy = Taxonomy(tmp_db)
    taxonomy.add_tag("ml", embedding=[0.1, 0.2])
    taxonomy.add_tag("machine-learning", embedding=[0.1, 0.2])
    taxonomy.merge_tag("ml", "machine-learning")
    # Check that repository_tags was updated
    rows = tmp_db.conn.execute(
        "SELECT tag FROM repository_tags WHERE html_url = ?",
        (sample_repo.html_url,)
    ).fetchall()
    tags = [r["tag"] for r in rows]
    assert "machine-learning" in tags
    assert "ml" not in tags


def test_get_top_tags(tmp_db):
    taxonomy = Taxonomy(tmp_db)
    taxonomy.add_tag("popular", embedding=[0.1, 0.2])
    for _ in range(9):
        taxonomy.increment_usage("popular")
    taxonomy.add_tag("rare", embedding=[0.3, 0.4])
    top = taxonomy.get_top_tags(n=2)
    assert top[0] == "popular"
    assert len(top) == 2


def test_taxonomy_persists_across_instances(tmp_db):
    taxonomy1 = Taxonomy(tmp_db)
    taxonomy1.add_tag("persistent", embedding=[0.5, 0.5])
    # Create a new instance from same DB
    taxonomy2 = Taxonomy(tmp_db)
    assert taxonomy2.has_tag("persistent")
    assert taxonomy2.size == 1
