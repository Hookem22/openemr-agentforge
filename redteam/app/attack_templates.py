"""Loads evals/seed_attacks.json as the Red Team Agent's per-category attack template library --
shared between the manual eval runner (evals/run_redteam_eval.py) and the compiled graph
(app/graph.py's red_team_node), so there's one place that knows what "attack this category" means,
not two copies that can drift apart.
"""
from __future__ import annotations

import json
import os

from app.schemas import AttackCategory

_SEEDS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "evals", "seed_attacks.json"
)


def get_template(category: AttackCategory) -> dict:
    with open(_SEEDS_PATH) as f:
        seeds = json.load(f)
    for seed in seeds:
        if seed["attack_category"] == category.value:
            return seed
    raise KeyError(
        f"No seed template for category={category.value!r} in {_SEEDS_PATH} -- add one to "
        "evals/seed_attacks.json before the Orchestrator can pick this category."
    )
