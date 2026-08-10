"""Arena Hero benchmark platform contracts and tooling."""

from arena_hero_bench.configuration import ConfigResolver, ConfigSchema, FrozenConfig
from arena_hero_bench.contestant import ContestantManifest, ContestantRegistry
from arena_hero_bench.manifest import ArtifactManifest, ArtifactStatus, RunManifest
from arena_hero_bench.orchestration import (
    ArtifactStore,
    DistributedExecutor,
    LocalExecutor,
    RunStatus,
    ShardPlan,
    ShardResult,
    merge_shards,
)

__all__ = [
    "ArtifactManifest",
    "ArtifactStatus",
    "ArtifactStore",
    "ConfigResolver",
    "ConfigSchema",
    "ContestantManifest",
    "ContestantRegistry",
    "DistributedExecutor",
    "FrozenConfig",
    "LocalExecutor",
    "RunManifest",
    "RunStatus",
    "ShardPlan",
    "ShardResult",
    "merge_shards",
]
__version__ = "0.2.0"
