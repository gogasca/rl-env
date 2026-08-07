"""Train and evaluate a tabular Q-learning agent on a Gymnasium environment.

Example:
    python -m rl_env.train --episodes 5000 --env FrozenLake-v1
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import gymnasium as gym
import numpy as np

from rl_env.agent import QLearningAgent


@dataclass
class TrainResult:
    """Summary statistics produced by :func:`train`."""

    episodes: int
    rewards: list[float]
    final_success_rate: float

    @property
    def mean_last_100(self) -> float:
        window = self.rewards[-100:]
        return float(np.mean(window)) if window else 0.0


def make_env(env_id: str, *, seed: int | None = None) -> gym.Env:
    """Create a discrete Gymnasium environment suitable for tabular RL."""
    if env_id == "FrozenLake-v1":
        env = gym.make(env_id, is_slippery=False)
    else:
        env = gym.make(env_id)
    if seed is not None:
        env.reset(seed=seed)
        env.action_space.seed(seed)
    return env


def evaluate(agent: QLearningAgent, env: gym.Env, episodes: int = 100) -> float:
    """Return the average success (reward) rate of the greedy policy."""
    total = 0.0
    for _ in range(episodes):
        state, _ = env.reset()
        done = False
        while not done:
            action = agent.select_action(int(state), greedy=True)
            state, reward, terminated, truncated, _ = env.step(action)
            done = terminated or truncated
            total += float(reward)
    return total / episodes


def train(
    env_id: str = "FrozenLake-v1",
    episodes: int = 5000,
    max_steps: int = 200,
    seed: int = 42,
    log_every: int = 1000,
) -> TrainResult:
    """Train a Q-learning agent and return per-episode rewards."""
    env = make_env(env_id, seed=seed)
    n_states = int(env.observation_space.n)  # type: ignore[attr-defined]
    n_actions = int(env.action_space.n)  # type: ignore[attr-defined]

    agent = QLearningAgent(
        n_states=n_states,
        n_actions=n_actions,
        rng=np.random.default_rng(seed),
    )

    rewards: list[float] = []
    for episode in range(1, episodes + 1):
        state, _ = env.reset()
        state = int(state)
        episode_reward = 0.0
        for _ in range(max_steps):
            action = agent.select_action(state)
            next_state, reward, terminated, truncated, _ = env.step(action)
            next_state = int(next_state)
            done = terminated or truncated
            agent.update(state, action, float(reward), next_state, done)
            state = next_state
            episode_reward += float(reward)
            if done:
                break
        agent.decay_epsilon()
        rewards.append(episode_reward)

        if log_every and episode % log_every == 0:
            recent = float(np.mean(rewards[-log_every:]))
            print(
                f"episode {episode:>6d} | "
                f"mean reward (last {log_every}) = {recent:.3f} | "
                f"epsilon = {agent.epsilon:.3f}"
            )

    success_rate = evaluate(agent, env, episodes=100)
    env.close()
    return TrainResult(
        episodes=episodes,
        rewards=rewards,
        final_success_rate=success_rate,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="FrozenLake-v1", help="Gymnasium env id")
    parser.add_argument("--episodes", type=int, default=5000)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--log-every", type=int, default=1000)
    args = parser.parse_args()

    result = train(
        env_id=args.env,
        episodes=args.episodes,
        max_steps=args.max_steps,
        seed=args.seed,
        log_every=args.log_every,
    )

    print("-" * 48)
    print(f"environment:          {args.env}")
    print(f"episodes trained:     {result.episodes}")
    print(f"mean reward last 100: {result.mean_last_100:.3f}")
    print(f"greedy success rate:  {result.final_success_rate:.3f}")
    print("-" * 48)


if __name__ == "__main__":
    main()
