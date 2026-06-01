# gendosecalc.analysis.dvh CI stub
from dataclasses import dataclass


@dataclass
class DVHMetrics:
    structure_name: str
    d95_gy: float
    d50_gy: float
    d2_gy: float
    dmean_gy: float
    volume_cc: float


def load_structure_masks_from_rtstruct(rtstruct_path, dose):
    """Stub — replaced by monkeypatch in tests."""
    raise NotImplementedError("CI stub")


def compute_dvh(dose, structure_masks):
    """Stub — replaced by monkeypatch in tests."""
    raise NotImplementedError("CI stub")
