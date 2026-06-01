# gendosecalc.service.machines CI stub


def add_machine(machine_dict: dict) -> None:
    """Stub — no-op in CI."""


def update_machine(machine_id: str, machine_dict: dict) -> None:
    """Stub — raises KeyError when machine not found (matches real API contract)."""
    raise KeyError(machine_id)
