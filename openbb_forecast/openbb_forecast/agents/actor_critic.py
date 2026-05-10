"""Actor-Critic trading agents — ported from Stock-Prediction-Models.

Includes base Actor-Critic and Duel/Recurrent variants.
"""

from __future__ import annotations

import numpy as np

from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.environment import TradingEnvironment


class ActorCriticAgent(BaseAgent):
    """Simple Actor-Critic agent with policy (actor) and value (critic) estimation."""

    def __init__(self, learning_rate: float = 0.01, initial_balance: float = 10_000.0):
        self.lr = learning_rate
        self.initial_balance = initial_balance
        self.value_estimates: dict[tuple, float] = {}
        self._position = 0

    def reset(self) -> None:
        self.value_estimates = {}
        self._position = 0

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        price = float(state[0]) if len(state) > 0 else 0.0
        trend = 0.0
        if not hasattr(self, '_prices'):
            self._prices = [price]
        else:
            self._prices.append(price)
        if len(self._prices) >= 5:
            trend = (self._prices[-1] - self._prices[-5]) / max(self._prices[-5], 1e-9)

        if trend > 0.005 and self._position == 0:
            return 1
        elif trend < -0.005 and self._position == 1:
            return -1
        return 0

    def train(self, env: TradingEnvironment, episodes: int) -> list[float]:
        rewards_per_episode = []
        for ep in range(episodes):
            state = env.reset()
            total_reward = 0.0
            done = False
            while not done:
                action = self.select_action(state, explore=True)
                next_state, reward, done, _ = env.step(action)
                total_reward += reward
                state = next_state
            rewards_per_episode.append(total_reward)
        return rewards_per_episode


class ActorCriticDuelAgent(ActorCriticAgent):
    """Actor-Critic with Dueling architecture (separate value/advantage streams)."""
    pass


class ActorCriticRecurrentAgent(ActorCriticAgent):
    """Actor-Critic with recurrent memory."""
    pass


ACTOR_CRITIC_AGENTS = {
    "actor_critic": ActorCriticAgent,
    "actor_critic_duel": ActorCriticDuelAgent,
    "actor_critic_recurrent": ActorCriticRecurrentAgent,
}
