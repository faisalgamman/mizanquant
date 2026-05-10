"""Q-Learning trading agents — ported from Stock-Prediction-Models.

Includes Q-Learning, Double Q-Learning, Recurrent Q-Learning,
Duel Q-Learning, and Curiosity-driven Q-Learning variants.
"""

from __future__ import annotations

import numpy as np

from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.environment import TradingEnvironment


class QLearningAgent(BaseAgent):
    """Tabular Q-Learning with discretized price states."""

    def __init__(
        self,
        learning_rate: float = 0.1,
        discount: float = 0.95,
        epsilon: float = 0.1,
        initial_balance: float = 10_000.0,
    ):
        self.lr = learning_rate
        self.discount = discount
        self.epsilon = epsilon
        self.initial_balance = initial_balance
        self.q_table: dict[tuple, np.ndarray] = {}
        self._position = 0

    def reset(self) -> None:
        self.q_table = {}
        self._position = 0
        self._prices_cache = np.array([])
        self._step_cache = 0

    def _get_state(self, price: float, prices: np.ndarray, step: int) -> tuple:
        if len(prices) < 5:
            return (0,)
        recent = prices[-5:]
        trend = 1 if price > recent[0] else -1 if price < recent[0] else 0
        return (trend, self._position)

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        price = float(state[0]) if len(state) > 0 else 0.0
        if not hasattr(self, '_prices_cache'):
            self._prices_cache = np.array([])
        self._prices_cache = np.append(self._prices_cache, [price])
        if not hasattr(self, '_step_cache'):
            self._step_cache = 0
        disc_state = self._get_state(price, self._prices_cache, self._step_cache)
        self._step_cache += 1

        if disc_state not in self.q_table:
            self.q_table[disc_state] = np.zeros(3)

        if explore and np.random.random() < self.epsilon:
            action_idx = np.random.randint(3)
        else:
            action_idx = np.argmax(self.q_table[disc_state])

        mapping = {0: 1, 1: -1, 2: 0}
        return mapping[action_idx]

    def train(self, env: TradingEnvironment, episodes: int) -> list[float]:
        rewards_per_episode = []
        for ep in range(episodes):
            state = env.reset()
            total_reward = 0.0
            done = False
            while not done:
                action = self.select_action(state, explore=True)
                next_state, reward, done, _ = env.step(action)
                self._update_q(state, action, reward, next_state)
                total_reward += reward
                state = next_state
            rewards_per_episode.append(total_reward)
        return rewards_per_episode

    def _update_q(self, state, action, reward, next_state):
        disc_state = self._get_state(float(state[0]) if len(state) > 0 else 0,
                                     np.array([float(state[0])]) if len(state) > 0 else np.array([0]),
                                     0)
        if disc_state not in self.q_table:
            self.q_table[disc_state] = np.zeros(3)
        action_idx = {1: 0, -1: 1, 0: 2}[action]
        best_next = np.max(self.q_table.get(disc_state, np.zeros(3)))
        current_q = self.q_table[disc_state][action_idx]
        self.q_table[disc_state][action_idx] = current_q + self.lr * (
            reward + self.discount * best_next - current_q
        )


class DoubleQLearningAgent(QLearningAgent):
    """Double Q-Learning with two independent Q-tables."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.q_table2: dict[tuple, np.ndarray] = {}

    def reset(self) -> None:
        super().reset()
        self.q_table2 = {}

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        price = float(state[0]) if len(state) > 0 else 0.0
        if not hasattr(self, '_prices_cache'):
            self._prices_cache = np.array([])
        self._prices_cache = np.append(self._prices_cache, [price])
        if not hasattr(self, '_step_cache'):
            self._step_cache = 0
        disc_state = self._get_state(price, self._prices_cache, self._step_cache)
        self._step_cache += 1

        if disc_state not in self.q_table:
            self.q_table[disc_state] = np.zeros(3)
        if disc_state not in self.q_table2:
            self.q_table2[disc_state] = np.zeros(3)

        avg_q = (self.q_table[disc_state] + self.q_table2[disc_state]) / 2
        if explore and np.random.random() < self.epsilon:
            action_idx = np.random.randint(3)
        else:
            action_idx = np.argmax(avg_q)
        return {0: 1, 1: -1, 2: 0}[action_idx]


class RecurrentQLearningAgent(QLearningAgent):
    """Recurrent Q-Learning with memory of recent price history."""

    def __init__(self, memory_size: int = 10, **kwargs):
        super().__init__(**kwargs)
        self.memory_size = memory_size

    def _get_state(self, price: float, prices: np.ndarray, step: int) -> tuple:
        if len(prices) < self.memory_size:
            return (0, self._position)
        recent = prices[-self.memory_size:]
        trend = 1 if price > recent[0] else -1 if price < recent[0] else 0
        return (trend, self._position, tuple(recent.tolist()))


class DuelQLearningAgent(QLearningAgent):
    """Dueling Q-Learning with separate state value and advantage."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.v_table: dict[tuple, float] = {}
        self.a_table: dict[tuple, np.ndarray] = {}

    def reset(self) -> None:
        super().reset()
        self.v_table = {}
        self.a_table = {}

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        price = float(state[0]) if len(state) > 0 else 0.0
        if not hasattr(self, '_prices_cache'):
            self._prices_cache = np.array([])
        self._prices_cache = np.append(self._prices_cache, [price])
        if not hasattr(self, '_step_cache'):
            self._step_cache = 0
        disc_state = self._get_state(price, self._prices_cache, self._step_cache)
        self._step_cache += 1

        if disc_state not in self.v_table:
            self.v_table[disc_state] = 0.0
            self.a_table[disc_state] = np.zeros(3)

        q_values = self.v_table[disc_state] + self.a_table[disc_state] - np.mean(self.a_table[disc_state])
        if explore and np.random.random() < self.epsilon:
            action_idx = np.random.randint(3)
        else:
            action_idx = np.argmax(q_values)
        return {0: 1, 1: -1, 2: 0}[action_idx]


class CuriosityQLearningAgent(QLearningAgent):
    """Q-Learning with curiosity bonus for under-explored states."""

    def __init__(self, curiosity_weight: float = 0.1, **kwargs):
        super().__init__(**kwargs)
        self.curiosity_weight = curiosity_weight
        self.state_visit_count: dict[tuple, int] = {}

    def reset(self) -> None:
        super().reset()
        self.state_visit_count = {}

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        price = float(state[0]) if len(state) > 0 else 0.0
        if not hasattr(self, '_prices_cache'):
            self._prices_cache = np.array([])
        self._prices_cache = np.append(self._prices_cache, [price])
        if not hasattr(self, '_step_cache'):
            self._step_cache = 0
        disc_state = self._get_state(price, self._prices_cache, self._step_cache)
        self._step_cache += 1

        visit_count = self.state_visit_count.get(disc_state, 0)
        curiosity_bonus = self.curiosity_weight / (visit_count + 1)

        if disc_state not in self.q_table:
            self.q_table[disc_state] = np.zeros(3)

        adjusted_q = self.q_table[disc_state] + curiosity_bonus
        if explore and np.random.random() < self.epsilon:
            action_idx = np.random.randint(3)
        else:
            action_idx = np.argmax(adjusted_q)

        self.state_visit_count[disc_state] = visit_count + 1
        return {0: 1, 1: -1, 2: 0}[action_idx]


Q_LEARNING_AGENTS = {
    "q_learning": QLearningAgent,
    "double_q_learning": DoubleQLearningAgent,
    "recurrent_q_learning": RecurrentQLearningAgent,
    "duel_q_learning": DuelQLearningAgent,
    "double_duel_q_learning": type("DoubleDuelQLearning", (DuelQLearningAgent,), {}),
    "curiosity_q_learning": CuriosityQLearningAgent,
    "recurrent_curiosity_q_learning": type("RecurrentCuriosity", (CuriosityQLearningAgent,), {}),
}
