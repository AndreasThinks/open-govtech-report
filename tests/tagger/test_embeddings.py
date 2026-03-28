"""Tests for embedding utilities."""

import math
from govtech_scraper.tagger.embeddings import cosine_similarity


def test_cosine_identical():
    v = [1.0, 2.0, 3.0]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert abs(cosine_similarity(a, b)) < 1e-6


def test_cosine_opposite():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-6


def test_cosine_zero_vector():
    a = [0.0, 0.0, 0.0]
    b = [1.0, 2.0, 3.0]
    assert cosine_similarity(a, b) == 0.0


def test_cosine_known_value():
    a = [1.0, 2.0, 3.0]
    b = [4.0, 5.0, 6.0]
    # Manual: dot=32, |a|=sqrt(14), |b|=sqrt(77)
    expected = 32.0 / (math.sqrt(14) * math.sqrt(77))
    assert abs(cosine_similarity(a, b) - expected) < 1e-6
