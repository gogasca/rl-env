"""Tabular Q-learning agent.

A small, well-tested implementation of epsilon-greedy Q-learning for
environments with discrete observation and action spaces.
"""

from __future__ import annotations

import numpy as np


class QLearningAgent:
    """Epsilon-greedy tabular Q-learning agent.

    The agent maintains a ``(n_states, n_actions)`` table of action-value
    estimates and updates it with the standard Q-learning rule.
    """

    def __init__(
        self,
        n_states: int,
        n_actions: int,
        learning_rate: float = 0.8,
        discount_factor: float = 0.95,
        epsilon: float = 1.0,
        epsilon_min: float = 0.01,
        epsilon_decay: float = 0.9995,
        rng: np.random.Generator | None = None,
    ) -> None:
        if n_states <= 0 or n_actions <= 0:
            raise ValueError("n_states and n_actions must be positive")

        self.n_states = n_states
        self.n_actions = n_actions
        self.learning_rate = learning_rate
        self.discount_factor = discount_factor
        self.epsilon = epsilon
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay
        self.rng = rng if rng is not None else np.random.default_rng()
        self.q_table = np.zeros((n_states, n_actions), dtype=np.float64)

    def select_action(self, state: int, *, greedy: bool = False) -> int:
        """Return an action for ``state`` using an epsilon-greedy policy.

        When ``greedy`` is True, exploration is disabled (used for evaluation).
        """
        if not greedy and self.rng.random() < self.epsilon:
            return int(self.rng.integers(self.n_actions))
        # Break ties randomly so the policy does not get stuck on action 0.
        row = self.q_table[state]
        best = np.flatnonzero(row == row.max())
        return int(self.rng.choice(best))

    def update(
        self,
        state: int,
        action: int,
        reward: float,
        next_state: int,
        done: bool,
    ) -> None:
        """Apply the Q-learning update for a single transition."""
        best_next = 0.0 if done else float(self.q_table[next_state].max())
        target = reward + self.discount_factor * best_next
        td_error = target - self.q_table[state, action]
        self.q_table[state, action] += self.learning_rate * td_error

    def decay_epsilon(self) -> None:
        """Decay the exploration rate toward ``epsilon_min``."""
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
