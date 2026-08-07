"""Unit tests for the Q-learning agent and training loop."""

from __future__ import annotations

import numpy as np
import pytest

from rl_env.agent import QLearningAgent
from rl_env.train import train


def test_invalid_dimensions_raise():
    with pytest.raises(ValueError):
        QLearningAgent(n_states=0, n_actions=4)


def test_greedy_action_picks_max():
    agent = QLearningAgent(n_states=2, n_actions=3, rng=np.random.default_rng(0))
    agent.q_table[0] = [0.1, 0.9, 0.2]
    assert agent.select_action(0, greedy=True) == 1


def test_update_moves_value_toward_target():
    agent = QLearningAgent(
        n_states=2,
        n_actions=2,
        learning_rate=0.5,
        discount_factor=0.9,
        rng=np.random.default_rng(0),
    )
    agent.q_table[1] = [1.0, 3.0]
    agent.update(state=0, action=0, reward=1.0, next_state=1, done=False)
    # target = 1 + 0.9 * 3 = 3.7 ; new value = 0 + 0.5 * (3.7 - 0) = 1.85
    assert agent.q_table[0, 0] == pytest.approx(1.85)


def test_terminal_update_ignores_next_state():
    agent = QLearningAgent(n_states=2, n_actions=2, learning_rate=1.0)
    agent.q_table[1] = [5.0, 5.0]
    agent.update(state=0, action=1, reward=2.0, next_state=1, done=True)
    assert agent.q_table[0, 1] == pytest.approx(2.0)


def test_epsilon_decays_but_not_below_min():
    agent = QLearningAgent(
        n_states=1, n_actions=1, epsilon=0.02, epsilon_min=0.01, epsilon_decay=0.5
    )
    agent.decay_epsilon()
    assert agent.epsilon == pytest.approx(0.01)
    agent.decay_epsilon()
    assert agent.epsilon == pytest.approx(0.01)


def test_training_learns_frozenlake():
    """End-to-end: the agent should reliably solve deterministic FrozenLake."""
    result = train(env_id="FrozenLake-v1", episodes=2000, seed=0, log_every=0)
    assert result.final_success_rate == pytest.approx(1.0)
