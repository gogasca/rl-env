# rl-env

A minimal, dependency-light **reinforcement learning** playground. It ships a
tabular [Q-learning](https://en.wikipedia.org/wiki/Q-learning) agent that learns
to solve discrete [Gymnasium](https://gymnasium.farama.org/) environments such
as `FrozenLake-v1` — no GPU or deep-learning frameworks required.

## Requirements

- Python 3.10+
- Dependencies pinned in [`pyproject.toml`](pyproject.toml)
  (`gymnasium`, `numpy`; `pytest` for the `dev` extra)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Train an agent

```bash
python -m rl_env.train --episodes 5000 --env FrozenLake-v1
```

Example output:

```
episode   5000 | mean reward (last 1000) = 0.998 | epsilon = 0.010
------------------------------------------------
environment:          FrozenLake-v1
episodes trained:     5000
mean reward last 100: 1.000
greedy success rate:  1.000
```

A `greedy success rate` near `1.000` means the trained policy reaches the goal
on essentially every evaluation episode.

## Run the tests

```bash
pytest -q
```

## Project layout

```
rl_env/
  agent.py     # epsilon-greedy tabular Q-learning agent
  train.py     # training / evaluation loop + CLI entry point
tests/
  test_agent.py
```

## License

[Apache 2.0](LICENSE)
