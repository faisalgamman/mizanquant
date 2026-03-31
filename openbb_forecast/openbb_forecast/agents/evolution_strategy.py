"""Natural Evolution Strategy agent — replaces agent/6.evolution-strategy-agent.ipynb.

Key differences from original:
  - Fitness = Sharpe ratio of episode (not raw return percentage)
  - Evaluates through TradingEnvironment (with costs and risk)
  - Uses antithetic sampling (mirrored noise) for lower variance
  - Weight decay for regularization
  - Pure NumPy (no deep learning framework needed)
"""

from __future__ import annotations

import numpy as np

from openbb_forecast.agents.base import BaseAgent
from openbb_forecast.agents.environment import TradingEnvironment


class ESModel:
    """Simple feedforward neural network using NumPy.

    Architecture: Linear(input, hidden) -> tanh -> Linear(hidden, output) -> softmax
    """

    def __init__(self, input_size: int, hidden_size: int, output_size: int):
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.output_size = output_size
        self._init_weights()

    def _init_weights(self) -> None:
        scale1 = np.sqrt(2.0 / self.input_size)
        scale2 = np.sqrt(2.0 / self.hidden_size)
        self.w1 = np.random.randn(self.input_size, self.hidden_size).astype(np.float32) * scale1
        self.b1 = np.zeros(self.hidden_size, dtype=np.float32)
        self.w2 = np.random.randn(self.hidden_size, self.output_size).astype(np.float32) * scale2
        self.b2 = np.zeros(self.output_size, dtype=np.float32)

    def predict(self, state: np.ndarray) -> np.ndarray:
        """Forward pass: state -> action probabilities."""
        h = np.tanh(state @ self.w1 + self.b1)
        logits = h @ self.w2 + self.b2
        # Stable softmax
        logits = logits - logits.max()
        exp_logits = np.exp(logits)
        return exp_logits / (exp_logits.sum() + 1e-8)

    def get_weights(self) -> np.ndarray:
        """Flatten all parameters into a single vector."""
        return np.concatenate([self.w1.ravel(), self.b1, self.w2.ravel(), self.b2])

    def set_weights(self, flat_weights: np.ndarray) -> None:
        """Restore parameters from a flat vector."""
        idx = 0
        w1_size = self.input_size * self.hidden_size
        self.w1 = flat_weights[idx : idx + w1_size].reshape(self.input_size, self.hidden_size)
        idx += w1_size
        self.b1 = flat_weights[idx : idx + self.hidden_size]
        idx += self.hidden_size
        w2_size = self.hidden_size * self.output_size
        self.w2 = flat_weights[idx : idx + w2_size].reshape(self.hidden_size, self.output_size)
        idx += w2_size
        self.b2 = flat_weights[idx : idx + self.output_size]

    @property
    def n_params(self) -> int:
        return (
            self.input_size * self.hidden_size
            + self.hidden_size
            + self.hidden_size * self.output_size
            + self.output_size
        )


class EvolutionStrategyAgent(BaseAgent):
    """Natural Evolution Strategy (NES) trading agent.

    Uses population-based optimization to evolve neural network weights.
    Fitness is measured by Sharpe ratio, not raw returns.

    Features:
      - Antithetic sampling: each noise vector z is paired with -z,
        halving variance at no extra cost.
      - Weight decay: prevents weight explosion.
      - Rank-based fitness shaping: robust to outliers.

    Args:
        state_size: State vector dimension.
        action_size: Number of actions.
        hidden_size: Hidden layer size for ESModel.
        population_size: Number of perturbations per generation.
        sigma: Noise standard deviation.
        learning_rate: Weight update step size.
        weight_decay: L2 regularization coefficient.
        device: Unused (pure NumPy), kept for API consistency.
    """

    def __init__(
        self,
        state_size: int,
        action_size: int = 3,
        hidden_size: int = 128,
        population_size: int = 15,
        sigma: float = 0.1,
        learning_rate: float = 0.03,
        weight_decay: float = 0.005,
        device: str = "auto",
    ):
        self.state_size = state_size
        self.action_size = action_size
        self.hidden_size = hidden_size
        self.population_size = population_size
        self.sigma = sigma
        self.learning_rate = learning_rate
        self.weight_decay = weight_decay

        self.model = ESModel(state_size, hidden_size, action_size)

    def reset(self) -> None:
        self.model = ESModel(self.state_size, self.hidden_size, self.action_size)

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        probs = self.model.predict(state)
        if explore:
            return int(np.random.choice(self.action_size, p=probs))
        return int(np.argmax(probs))

    def _evaluate_fitness(self, weights: np.ndarray, env: TradingEnvironment) -> float:
        """Evaluate fitness of a weight vector via Sharpe ratio.

        Using Sharpe ratio instead of raw returns encourages the evolution
        to find strategies that are consistently good, not just occasionally lucky.
        """
        self.model.set_weights(weights)
        state = env.reset()
        daily_returns = []
        prev_value = env.initial_capital

        while True:
            action = self.select_action(state, explore=False)
            state, reward, done, info = env.step(action)
            current_value = env._portfolio_value()
            if prev_value > 0:
                daily_returns.append((current_value - prev_value) / prev_value)
            prev_value = current_value
            if done:
                break

        if not daily_returns:
            return 0.0

        returns = np.array(daily_returns)
        if returns.std() < 1e-10:
            return float(returns.mean()) * 100  # No variance = use mean
        return float(returns.mean() / returns.std()) * np.sqrt(252)  # Annualized Sharpe

    def _rank_transform(self, fitness: np.ndarray) -> np.ndarray:
        """Rank-based fitness shaping — robust to outlier fitness values.

        Maps fitness to uniform ranks in [-0.5, 0.5], preventing
        a single lucky individual from dominating the gradient.
        """
        n = len(fitness)
        ranks = np.zeros(n)
        sorted_idx = np.argsort(fitness)
        for i, idx in enumerate(sorted_idx):
            ranks[idx] = i
        # Normalize to [-0.5, 0.5]
        ranks = (ranks / (n - 1)) - 0.5 if n > 1 else ranks
        return ranks

    def train(self, env: TradingEnvironment, episodes: int) -> list[float]:
        """Evolve weights using Natural Evolution Strategy.

        Each generation:
          1. Generate population_size noise vectors (antithetic: z and -z)
          2. Evaluate each perturbation's fitness
          3. Rank-transform fitness values
          4. Update weights using estimated gradient
          5. Apply weight decay

        Args:
            env: Training environment.
            episodes: Number of generations (not RL episodes).

        Returns:
            Best fitness per generation.
        """
        best_fitness_history = []
        base_weights = self.model.get_weights().copy()
        n_params = self.model.n_params

        for gen in range(episodes):
            # Generate antithetic noise
            noise = np.random.randn(self.population_size, n_params).astype(np.float32)

            # Evaluate fitness for +noise and -noise (antithetic)
            fitness_pos = np.zeros(self.population_size)
            fitness_neg = np.zeros(self.population_size)

            for i in range(self.population_size):
                # Positive perturbation
                perturbed = base_weights + self.sigma * noise[i]
                fitness_pos[i] = self._evaluate_fitness(perturbed, env)

                # Negative perturbation (antithetic)
                perturbed = base_weights - self.sigma * noise[i]
                fitness_neg[i] = self._evaluate_fitness(perturbed, env)

            # Combine and rank-transform
            all_fitness = np.concatenate([fitness_pos, fitness_neg])
            all_noise = np.concatenate([noise, -noise], axis=0)

            ranked = self._rank_transform(all_fitness)

            # Estimate gradient
            gradient = (all_noise.T @ ranked) / (2 * self.population_size * self.sigma)

            # Update with weight decay
            base_weights += self.learning_rate * gradient
            base_weights *= (1 - self.weight_decay)

            best_fitness_history.append(float(all_fitness.max()))

        # Set final weights
        self.model.set_weights(base_weights)
        return best_fitness_history
