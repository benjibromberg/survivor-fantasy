from .classic import ClassicScoring

SCORING_SYSTEMS = [ClassicScoring()]

_registry = {s.name: type(s) for s in SCORING_SYSTEMS}


def get_scoring_system(name, config=None):
    cls = _registry.get(name, ClassicScoring)
    if config:
        return cls(**config)
    return cls()
