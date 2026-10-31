"""A graphics-only plan is explicit, guarded and uniquely selected."""
import copy

import pytest

from localization_plan import merge_localization_plans
from translation_catalog import load_catalog


def fixture():
    graphics = load_catalog('translations/plans/pr34-journey-map.json')
    workspace = {'game_sha256': graphics['game_sha256'], 'entries': [
        {'id': 'static.1', 'category': 'static', 'location': {'tag_path': [1]}}]}
    text = {'schema_version': 1, 'game_sha256': graphics['game_sha256'],
            'entry_ids': ['static.1'], 'fonts': [], 'layouts': {}}
    return text, graphics, workspace


def test_graphics_only_plan_preserves_text_selection_and_the_exact_recipe():
    text, graphics, workspace = fixture()
    merged = merge_localization_plans([text, graphics], workspace)
    assert merged['entry_ids'] == ['static.1']
    assert merged['journey_map'] == graphics['journey_map']
    merged['journey_map']['size'][0] = 0
    assert graphics['journey_map']['size'] == [1440, 868]


@pytest.mark.parametrize('problem', ['duplicate', 'empty', 'wrong_game', 'wrong_tag', 'recipe_type'])
def test_graphics_selection_fails_closed(problem):
    text, graphics, workspace = fixture()
    plans = [text, graphics]
    if problem == 'duplicate':
        plans.append(copy.deepcopy(graphics))
    elif problem == 'empty':
        del graphics['journey_map']
    elif problem == 'wrong_game':
        graphics['journey_map']['game_sha256'] = '0' * 64
    elif problem == 'wrong_tag':
        graphics['journey_map']['tag_path'] = [751]
    else:
        graphics['journey_map'] = []
    with pytest.raises(ValueError):
        merge_localization_plans(plans, workspace)


def test_legacy_builder_rejects_graphics_instead_of_silently_building_text_only():
    from addressed_localization import build_localization
    _, graphics, _ = fixture()
    plan = {'entry_ids': ['static.3016'], 'journey_map': graphics['journey_map']}
    with pytest.raises(ValueError, match='Journey map.*Alegreya'):
        build_localization(b'', {}, plan, {})
