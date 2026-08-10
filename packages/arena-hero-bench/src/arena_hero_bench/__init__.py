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
from arena_hero_bench.process_executor import (
    BackendProcessSpec,
    ProcessExecutor,
    ProcessExecutorError,
    reference_engine_process_executor,
)
from arena_hero_bench.storage import FilesystemArtifactStore

__all__ = [
    "ArtifactManifest",
    "ArtifactStatus",
    "ArtifactStore",
    "BackendProcessSpec",
    "ConfigResolver",
    "ConfigSchema",
    "ContestantManifest",
    "ContestantRegistry",
    "DistributedExecutor",
    "FilesystemArtifactStore",
    "FrozenConfig",
    "LocalExecutor",
    "ProcessExecutor",
    "ProcessExecutorError",
    "RunManifest",
    "RunStatus",
    "ShardPlan",
    "ShardResult",
    "merge_shards",
    "reference_engine_process_executor",
]
__version__ = "0.2.0"
