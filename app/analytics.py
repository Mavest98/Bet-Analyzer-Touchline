import math

import numpy as np
from scipy.stats import poisson


def calculate_poisson_probabilities(
    home_expected_goals: float, away_expected_goals: float, max_goals: int = 10
) -> dict[str, dict[str, float | None] | list[dict[str, float | str]]]:
    """Compute match probabilities, common goal markets and likely scorelines."""
    if (
        not math.isfinite(home_expected_goals)
        or not math.isfinite(away_expected_goals)
        or home_expected_goals < 0
        or away_expected_goals < 0
    ):
        raise ValueError("Expected goals must be finite, non-negative numbers.")
    if max_goals < 1:
        raise ValueError("max_goals must be a positive integer.")

    home_probs = [poisson.pmf(i, home_expected_goals) for i in range(max_goals)]
    away_probs = [poisson.pmf(i, away_expected_goals) for i in range(max_goals)]
    score_matrix = np.outer(home_probs, away_probs)

    prob_home_win = float(np.sum(np.tril(score_matrix, -1)))
    prob_draw = float(np.sum(np.diag(score_matrix)))
    prob_away_win = float(np.sum(np.triu(score_matrix, 1)))
    total_expected_goals = home_expected_goals + away_expected_goals
    scorelines = [
        {
            "score": f"{home_goals}-{away_goals}",
            "probability": float(score_matrix[home_goals, away_goals]),
        }
        for home_goals in range(max_goals)
        for away_goals in range(max_goals)
    ]
    top_scorelines = sorted(
        scorelines, key=lambda scoreline: scoreline["probability"], reverse=True
    )[:5]
    prob_both_teams_score = float(
        (1 - poisson.pmf(0, home_expected_goals))
        * (1 - poisson.pmf(0, away_expected_goals))
    )

    return {
        "1X2": {
            "home_win": round(prob_home_win, 4),
            "draw": round(prob_draw, 4),
            "away_win": round(prob_away_win, 4),
        },
        "true_odds": {
            "home_win": round(1 / prob_home_win, 2) if prob_home_win > 0 else None,
            "draw": round(1 / prob_draw, 2) if prob_draw > 0 else None,
            "away_win": round(1 / prob_away_win, 2) if prob_away_win > 0 else None,
        },
        "over_under": {
            "over_1_5": round(1.0 - float(poisson.cdf(1, total_expected_goals)), 4),
            "over_2_5": round(1.0 - float(poisson.cdf(2, total_expected_goals)), 4),
            "over_3_5": round(1.0 - float(poisson.cdf(3, total_expected_goals)), 4),
            "under_1_5": round(float(poisson.cdf(1, total_expected_goals)), 4),
            "under_2_5": round(float(poisson.cdf(2, total_expected_goals)), 4),
            "under_3_5": round(float(poisson.cdf(3, total_expected_goals)), 4),
        },
        "over_under_2_5": {
            "over": round(1.0 - float(poisson.cdf(2, total_expected_goals)), 4),
            "under": round(float(poisson.cdf(2, total_expected_goals)), 4),
        },
        "both_teams_to_score": {
            "yes": round(prob_both_teams_score, 4),
            "no": round(1.0 - prob_both_teams_score, 4),
        },
        "top_scorelines": [
            {
                "score": scoreline["score"],
                "probability": round(scoreline["probability"], 4),
            }
            for scoreline in top_scorelines
        ],
    }


def calculate_value_edge(model_prob: float, bookmaker_odds: float) -> float:
    """Calculate expected value as a percentage."""
    if not math.isfinite(model_prob) or not 0 <= model_prob <= 1:
        raise ValueError("model_prob must be a finite probability between 0 and 1.")
    if not math.isfinite(bookmaker_odds) or bookmaker_odds <= 0:
        raise ValueError("bookmaker_odds must be a finite number greater than zero.")

    return round(((model_prob * bookmaker_odds) - 1.0) * 100, 2)
