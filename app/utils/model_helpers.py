from typing import Any, Dict


def apply_model_fields(model: Any, data: Dict[str, Any]) -> None:
    """Copy matching fields from a discovery dict onto a SQLAlchemy model."""

    for key, value in data.items():
        if hasattr(model, key):
            setattr(model, key, value)
