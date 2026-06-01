# gendosecalc.analysis.gamma CI stub
from dataclasses import dataclass

import numpy as np


@dataclass
class GammaResult:
    gamma_map: np.ndarray
    pass_rate_pct: float
    dose_threshold_pct: float
    distance_threshold_mm: float
    max_gamma: float
    local_gamma: bool


def compute_gamma(
    reference,
    evaluation,
    *,
    dose_pct: float = 2.0,
    dist_mm: float = 1.0,
    dose_threshold_pct: float = 10.0,
    lower_dose_cutoff=None,
    local_gamma: bool = False,
):
    """Stub — replaced by monkeypatch in tests."""
    raise NotImplementedError("CI stub")
