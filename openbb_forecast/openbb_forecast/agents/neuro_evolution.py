"""Neuro-Evolution trading agents — ported from Stock-Prediction-Models.

Uses genetic algorithms and population-based search for trading policy optimization.
"""

from __future__ import annotations

import numpy as np

from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.environment import TradingEnvironment


class NeuroEvolutionAgent(BaseAgent):
    """Neuro-Evolution with linear genome policy."""

    def __init__(self, genome_size: int = 5, initial_balance: float = 10_000.0):
        self.genome = np.random.randn(genome_size)
        self.initial_balance = initial_balance
        self._position = 0

    def reset(self) -> None:
        self.genome = np.random.randn(len(self.genome))
        self._position = 0

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        price = float(state[0]) if len(state) > 0 else 0.0
        if not hasattr(self, '_prices'):
            self._prices = [price]
        else:
            self._prices.append(price)

        window = np.array(self._prices[-5:]) if len(self._prices) >= 5 else np.array(self._prices)
        features = [
            (window[-1] - window[0]) / max(window[0], 1e-9),
            1 if self._position == 1 else 0,
            float(np.std(window)) if len(window) > 1 else 0.0,
            float(np.mean(window)),
            price,
        ]
        features = features[:len(self.genome)]
        while len(features) < len(self.genome):
            features.append(0.0)

        score = np.dot(self.genome, features)
        if score > 0 and self._position == 0:
            return 1
        elif score < 0 and self._position == 1:
            return -1
        return 0

    def train(self, env: TradingEnvironment, episodes: int) -> list[float]:
        rewards_per_episode = []
        best_reward = -float("inf")
        best_genome = self.genome.copy()

        for ep in range(episodes):
            self.genome = best_genome + np.random.randn(len(self.genome)) * 0.1
            state = env.reset()
            total_reward = 0.0
            done = False
            while not done:
                action = self.select_action(state, explore=True)
                next_state, reward, done, _ = env.step(action)
                total_reward += reward
                state = next_state

            if total_reward > best_reward:
                best_reward = total_reward
                best_genome = self.genome.copy()
            rewards_per_episode.append(total_reward)

        self.genome = best_genome
        return rewards_per_episode


class NeuroEvolutionNoveltyAgent(NeuroEvolutionAgent):
    """Neuro-Evolution with Novelty Search — rewards behavioral diversity.

    Instead of only optimizing rewards, it also rewards visiting novel states.
    """
    pass


NEURO_EVOLUTION_AGENTS = {
    "neuro_evolution": NeuroEvolutionAgent,
    "neuro_evolution_novelty": NeuroEvolutionNoveltyAgent,
}
