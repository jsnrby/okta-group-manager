import os
import yaml


def get_owned_groups(email: str) -> list[str]:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "group_owners.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    owners = config.get("owners") or {}
    return owners.get(email, [])
