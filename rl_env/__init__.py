"""rl-env: a minimal, dependency-light reinforcement learning playground.

Provides a tabular Q-learning agent and a training entry point that learns to
solve discrete Gymnasium environments (e.g. FrozenLake) without any GPU or
heavyweight deep-learning dependencies.
"""

from rl_env.agent import QLearningAgent

__all__ = ["QLearningAgent"]
__version__ = "0.1.0"
