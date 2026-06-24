
import math

from config import ACTIVITY_WEIGHT, AMOUNT_WEIGHT


def compute_score(score_amount_total: float, active_days_count: int) -> float:
    amount_component = math.sqrt(max(score_amount_total, 0.0)) * AMOUNT_WEIGHT
    activity_component = max(active_days_count, 0) * ACTIVITY_WEIGHT
    return round(amount_component + activity_component, 4)
