from unittest.mock import AsyncMock
import pytest
from govtech_scraper.tagger.taxonomy import Taxonomy
from govtech_scraper.tagger.hierarchy import TagGrouper


async def test_group_failures_propagate_without_destroying_existing(tmp_db):
    taxonomy = Taxonomy(tmp_db)
    for tag in ('a', 'b', 'c'):
        taxonomy.add_tag(tag, [1., 0.])
    group = tmp_db.save_tag_group('Existing', 'keep')
    tmp_db.save_tag_group_member('a', group)
    grouper = TagGrouper('offline')
    grouper._name_cluster = AsyncMock(side_effect=RuntimeError('offline'))
    with pytest.raises(RuntimeError):
        await grouper.build_groups(taxonomy, tmp_db, recalculate=True)
    assert tmp_db.get_group_members(group) == ['a']


async def test_changed_group_reuses_name_and_includes_new_tags(tmp_db):
    taxonomy = Taxonomy(tmp_db)
    for tag in ('a', 'b', 'c', 'd'):
        taxonomy.add_tag(tag, [1., 0.])
    group = tmp_db.save_tag_group('Existing', 'keep')
    for tag in ('a', 'b', 'c'):
        tmp_db.save_tag_group_member(tag, group)
    grouper = TagGrouper('offline')
    grouper._name_cluster = AsyncMock(side_effect=AssertionError('No paid naming needed'))
    await grouper.build_groups(taxonomy, tmp_db)
    assert set(tmp_db.get_group_members(group)) == {'a', 'b', 'c', 'd'}
    grouper._name_cluster.assert_not_called()


async def test_real_naming_path_does_not_hide_credit_failure():
    grouper = TagGrouper('offline')
    grouper._make_request = AsyncMock(side_effect=RuntimeError('HTTP 402'))
    with pytest.raises(RuntimeError):
        await grouper._name_cluster(['a', 'b', 'c'], session=object())
