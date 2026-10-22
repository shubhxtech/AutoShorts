"""Importing this module registers every task with the queue."""

from .. import (
    faceless,  # noqa: F401
    pipeline,  # noqa: F401
    titles,  # noqa: F401
)
from ..analytics import tasks as analytics_tasks  # noqa: F401
from ..community import tasks as community_tasks  # noqa: F401
from ..ingest import scout  # noqa: F401
from ..ops import tasks as ops_tasks  # noqa: F401
from ..publish import tasks as publish_tasks  # noqa: F401
