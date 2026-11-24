# SPDX-License-Identifier: Apache-2.0
"""Small dependency-free semantic vocabulary shared across PHOS boundaries."""
from enum import Enum


class VisualSource(str, Enum):
    """Semantic source allowed to establish PHOS's persistent visual intent."""
    MANUAL = "manual"
    ENVIRONMENT = "environment"
    STATE = "state"
